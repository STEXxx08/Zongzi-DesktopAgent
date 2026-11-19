#!/usr/bin/env python3
"""Cross-platform launcher for the Core service stack.

This is the OS-independent counterpart of the Windows launcher
(``agent-stack.cmd`` / ``agent-stack.ps1``): it reads the very same
``agent-stack/registry.json`` and implements the same actions, runners,
health checks, log aggregation and ``optional_dependencies`` semantics, so the
same registry works on Windows, Linux and WSL.

    python agent_stack.py start
    python agent_stack.py status
    python agent_stack.py logs --service asr
    python agent_stack.py restart
    python agent_stack.py stop
    python agent_stack.py validate

Only the standard library is used, so it runs in any of the project's conda
environments. Each service is hosted by a small detached child process
(``__host__`` mode) that spawns the real command, prefixes its output into
``agent-stack/runtime/logs/combined.log`` and records its pid, exactly like the
PowerShell ``service-host.ps1`` does on Windows.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

IS_WINDOWS = os.name == "nt"
HOST_MODE = "__host__"
SUPPORTED_SERVICE_RUNNERS = ("conda", "process", "wsl-systemd")
DEFAULT_START_ORDER = 100
DEFAULT_CONFIG_FILE = "config.yaml"
STARTUP_SECTION = "Startup"
PID_WAIT_SECONDS = 20.0
STOP_GRACE_SECONDS = 8.0

# Windows SetPriorityClass values. Deliberately capped at ABOVE_NORMAL: threads
# whose base priority lands in 16..31 stop receiving the scheduler's dynamic
# priority boosts, so HIGH/REALTIME makes a real-time service *worse*, not
# better, under contention.
PRIORITY_CLASSES = {
    "idle": 0x00000040,
    "below_normal": 0x00004000,
    "normal": 0x00000020,
    "above_normal": 0x00008000,
    "high": 0x00000080,
    "realtime": 0x00000100,
}


class LauncherError(RuntimeError):
    """Raised for anything the user has to fix (registry, startup, timeouts)."""


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

def log(message: str) -> None:
    print(message, flush=True)


def warn(message: str) -> None:
    print("WARNING: " + message, file=sys.stderr, flush=True)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def encode_payload(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def decode_payload(value: str) -> dict:
    return json.loads(base64.b64decode(value.encode("ascii")).decode("utf-8"))


def quote_cmd_token(token: str) -> str:
    """Quote one token for a cmd.exe command line.

    cmd.exe has no backslash escaping: an embedded quote is doubled.
    """
    return '"' + str(token).replace('"', '""') + '"'


def _current_process_handle():
    """Pseudo-handle for the calling process, for SetPriorityClass.

    ``None`` would be marshalled as NULL and rejected with winerror 6, so the
    documented ``GetCurrentProcess()`` pseudo-handle (-1) is used instead.
    Returns ``None`` on failure, which callers treat as "skip".
    """
    if not IS_WINDOWS:
        return None
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        return kernel32.GetCurrentProcess()
    except Exception:  # noqa: BLE001 - advisory only
        return None


def _process_handle(pid: int):
    """Open a handle that allows SetPriorityClass on ``pid``.

    Returns ``None`` when the handle cannot be opened, in which case the caller
    falls back to ``SetPriorityClass(NULL, ...)`` semantics on the caller's own
    process instead. The handle stays open for the process lifetime on purpose:
    it is a single kernel handle per service and the host exits with the child.
    """
    if not IS_WINDOWS:
        return None
    try:
        import ctypes

        PROCESS_SET_INFORMATION = 0x0200
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(PROCESS_SET_INFORMATION, False, int(pid))
        return handle or None
    except Exception:  # noqa: BLE001 - advisory only
        return None


def apply_process_priority(handle, priority: str | None) -> str:
    """Raise (or lower) one process's priority class on Windows.

    ``priority`` comes from a service's registry entry (``process_priority``).
    Latency-sensitive services (ASR runs its model on the CPU) get
    ``above_normal`` so a game saturating the machine cannot starve them.

    Returns a short human-readable outcome for the combined log. Non-Windows
    platforms and unknown values are reported but never fatal: a launcher must
    not fail to start a service just because a nicety is unavailable.
    """
    if not priority:
        return "unchanged"
    name = str(priority).strip().lower()
    klass = PRIORITY_CLASSES.get(name)
    if klass is None:
        return f"ignored unknown process_priority {priority!r}"
    if not IS_WINDOWS:
        return f"ignored on this platform (requested {name})"
    if handle is None:
        # OpenProcess can be denied (sandboxed shells, protected processes); a
        # NULL handle would only surface as a confusing winerror 6.
        return f"skipped: could not open the process (requested {name})"
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.SetPriorityClass.restype = ctypes.c_int
        if not kernel32.SetPriorityClass(handle, klass):
            return f"failed with winerror {ctypes.get_last_error()}"
        return f"set to {name}"
    except Exception as exc:  # noqa: BLE001 - advisory only
        return f"failed: {exc}"


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #

class Registry:
    def __init__(self, path: Path, data: dict, runtime_dir: Path) -> None:
        self.path = path
        self.data = data
        self.runtime_dir = runtime_dir
        self.log_dir = runtime_dir / "logs"
        self.state_file = runtime_dir / "state.json"
        self.combined_log = self.log_dir / str(
            (data.get("settings") or {}).get("combined_log_file") or "combined.log"
        )

    # -- settings ---------------------------------------------------------- #
    @property
    def settings(self) -> dict:
        return self.data.get("settings") or {}

    def setting(self, name: str, default):
        value = self.settings.get(name)
        return default if value is None else value

    @property
    def services(self) -> list:
        return [item for item in (self.data.get("services") or []) if item.get("enabled") is not False]

    @property
    def optional_dependencies(self) -> list:
        declared = self.settings.get("optional_dependencies") or []
        return [item for item in declared if item and item.get("enabled") is not False]

    def get_service(self, name: str) -> dict | None:
        for item in self.data.get("services") or []:
            if str(item.get("name")) == name:
                return item
        return None

    # -- validation -------------------------------------------------------- #
    def validate(self) -> None:
        if self.data.get("version") != 1:
            raise LauncherError(
                f"Unsupported registry version {self.data.get('version')!r}. Expected version 1."
            )
        seen = set()
        for item in self.data.get("services") or []:
            name = item.get("name")
            if not name:
                raise LauncherError("Every service must have a name.")
            if name in seen:
                raise LauncherError(f"Duplicate service name: {name}")
            seen.add(name)
            runner = item.get("runner")
            if runner not in SUPPORTED_SERVICE_RUNNERS:
                raise LauncherError(f"Service '{name}' has unsupported runner '{runner}'.")
            if runner == "conda" and not item.get("environment"):
                raise LauncherError(f"Conda service '{name}' must define environment.")
            if runner in ("conda", "process"):
                if not item.get("command"):
                    raise LauncherError(f"Service '{name}' must define command.")
                workdir = self.resolve(item.get("working_directory") or ".")
                if not workdir.is_dir():
                    raise LauncherError(
                        f"Working directory for '{name}' does not exist: {item.get('working_directory')}"
                    )
            if runner == "wsl-systemd" and not item.get("unit"):
                raise LauncherError(f"WSL service '{name}' must define unit.")
            priority = item.get("process_priority")
            if priority is not None and str(priority).strip().lower() not in PRIORITY_CLASSES:
                raise LauncherError(
                    f"Service '{name}' has unsupported process_priority '{priority}'. "
                    f"Expected one of: {', '.join(sorted(PRIORITY_CLASSES))}."
                )
            health = item.get("health") or {}
            if health.get("type") == "tcp" and not health.get("port"):
                raise LauncherError(f"TCP health check for '{name}' must define port.")
        for dependency in self.settings.get("optional_dependencies") or []:
            if not dependency.get("name"):
                raise LauncherError("Every optional dependency must have a name.")
            if dependency.get("runner") != "wsl-systemd":
                raise LauncherError(
                    f"Optional dependency '{dependency.get('name')}' has unsupported runner "
                    f"'{dependency.get('runner')}'."
                )
            if not dependency.get("unit"):
                raise LauncherError(
                    f"Optional dependency '{dependency.get('name')}' must define unit."
                )

    # -- paths ------------------------------------------------------------- #
    @property
    def root(self) -> Path:
        return self.path.parent.parent

    def resolve(self, value) -> Path:
        path = Path(str(value))
        return path if path.is_absolute() else (self.root / path).resolve()

    @property
    def host_script(self) -> Path:
        return Path(__file__).resolve()


def read_registry(registry_argument: str) -> Registry:
    path = Path(registry_argument)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.is_file():
        raise LauncherError(f"Registry file not found: {registry_argument}")
    try:
        # utf-8-sig: tolerate a BOM, which Windows editors happily add.
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001 - surfaced to the user
        raise LauncherError(f"Registry JSON is invalid: {exc}") from exc
    runtime_value = (data.get("settings") or {}).get("runtime_directory") or ".agent-stack"
    runtime_dir = Path(str(runtime_value))
    if not runtime_dir.is_absolute():
        runtime_dir = (path.parent.parent / runtime_dir).resolve()
    registry = Registry(path, data, runtime_dir)
    registry.validate()
    return registry


# --------------------------------------------------------------------------- #
# state
# --------------------------------------------------------------------------- #

def read_state(registry: Registry) -> list:
    if not registry.state_file.is_file():
        return []
    try:
        data = json.loads(registry.state_file.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001 - a corrupt state file only means "nothing recorded"
        return []
    return data if isinstance(data, list) else []


def write_state(registry: Registry, state: list) -> None:
    registry.state_file.parent.mkdir(parents=True, exist_ok=True)
    registry.state_file.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def state_entry(state: list, name: str) -> dict | None:
    for entry in state:
        if str(entry.get("name")) == name:
            return entry
    return None


# --------------------------------------------------------------------------- #
# platform helpers
# --------------------------------------------------------------------------- #

def _open_process(pid: int):
    """Open a pid with query rights; returns (handle, last_error)."""
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    return kernel32, handle, ctypes.get_last_error()


def process_running(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    if IS_WINDOWS:
        # tasklist can be denied on locked-down machines, so ask the kernel
        # directly instead of shelling out.
        import ctypes

        kernel32, handle, error = _open_process(pid)
        if not handle:
            # 5 = ERROR_ACCESS_DENIED: the process exists but is not ours to query.
            return error == 5
        try:
            code = ctypes.c_uint32()
            if not kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def terminate_process(pid: int) -> None:
    if not pid or pid <= 0 or not IS_WINDOWS:
        return
    import ctypes

    kernel32, handle, _ = _open_process(pid)
    if not handle:
        return
    try:
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.TerminateProcess(ctypes.c_void_p(handle), 1)
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def kill_tree(pid: int) -> None:
    if not pid or pid <= 0:
        return
    if IS_WINDOWS:
        result = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                capture_output=True, text=True, errors="replace")
        if result.returncode != 0 and process_running(pid):
            # taskkill can be unavailable or denied; fall back to the kernel.
            terminate_process(pid)
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
    deadline = time.time() + STOP_GRACE_SECONDS
    while time.time() < deadline:
        if not process_running(pid):
            return
        time.sleep(0.2)
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except OSError:
        pass


def tcp_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _usable_conda_bat(raw) -> str | None:
    """Return a ``conda.bat`` that can activate, or ``None``.

    Anaconda / Miniconda ship two batch wrappers::

        <root>/condabin/conda.bat     self-contained: sets up the shell, then
                                     `conda activate <env>` works
        <root>/Library/bin/conda.bat  only works in a shell that conda already
                                     initialised; elsewhere it dies with
                                     "CondaError: Run 'conda init' before 'conda activate'"

    ``Library/bin`` comes before ``condabin`` on ``PATH``, so
    ``shutil.which("conda.bat")`` finds the broken one. Redirect it to its
    ``condabin`` sibling instead of letting cmd.exe fail seconds later with a
    message that says nothing about the real cause.
    """
    if not raw:
        return None
    path = Path(str(raw))
    if not path.is_file() or path.suffix.lower() != ".bat":
        return None
    # ...\<root>\Library\bin\conda.bat: the wrapper that needs `conda init`.
    if path.name.lower() == "conda.bat" and [part.lower() for part in path.parts[-3:-1]] == ["library", "bin"]:
        hook = path.parent.parent.parent / "condabin" / "conda.bat"
        return str(hook) if hook.is_file() else None
    return str(path)


def find_conda_bat() -> str | None:
    """Locate ``conda.bat`` in the same order as the PowerShell launcher."""
    candidates = [os.environ.get("CONDA_BAT")]
    conda_exe = os.environ.get("CONDA_EXE")
    if conda_exe:
        # <root>/Scripts/conda.exe  ->  <root>/condabin/conda.bat
        candidates.append(str(Path(conda_exe).parent.parent / "condabin" / "conda.bat"))
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        # CONDA_PREFIX is the base install or one of its envs; both cases the
        # conda root is one of these two directories.
        prefix = Path(conda_prefix)
        for root in (prefix, prefix.parent.parent):
            candidates.append(str(root / "condabin" / "conda.bat"))
    home = Path.home()
    candidates += [
        str(home / "miniconda3" / "condabin" / "conda.bat"),
        str(home / "anaconda3" / "condabin" / "conda.bat"),
        shutil.which("conda.bat"),
        shutil.which("conda"),
    ]
    for candidate in candidates:
        usable = _usable_conda_bat(candidate)
        if usable:
            return usable
    return None


def find_conda() -> str | None:
    """Locate the conda entry point: conda.bat on Windows, the binary elsewhere."""
    if IS_WINDOWS:
        return find_conda_bat()
    for variable in ("CONDA_EXE", "CONDA_BAT"):
        value = os.environ.get(variable)
        if value and Path(value).exists():
            return value
    found = shutil.which("conda") or shutil.which("conda.bat")
    if found:
        return found
    home = Path.home()
    candidates = [
        home / "miniconda3" / "bin" / "conda",
        home / "anaconda3" / "bin" / "conda",
        Path("/opt/conda/bin/conda"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def run_wsl(distro: str, arguments: list, quiet: bool = False) -> int:
    if not IS_WINDOWS:
        raise LauncherError("runner 'wsl-systemd' is only available on Windows hosts.")
    command = ["wsl.exe"]
    if distro:
        command += ["-d", distro]
    command += ["--"] + list(arguments)
    try:
        result = subprocess.run(
            command,
            capture_output=quiet,
            text=False if quiet else None,
            check=False,
        )
    except FileNotFoundError as exc:
        raise LauncherError("wsl.exe was not found.") from exc
    return result.returncode


def wsl_unit_state(distro: str, unit: str) -> str:
    try:
        code = run_wsl(distro, ["systemctl", "is-active", "--quiet", unit], quiet=True)
    except LauncherError:
        return "unreachable"
    if code == 0:
        return "running"
    if code == 3:
        return "stopped"
    return "unreachable"


def listening_pids(ports: list) -> list:
    """PIDs listening on the given ports, used to sweep orphans after a failure."""
    wanted = {int(port) for port in ports if port}
    if not wanted:
        return []
    pids = set()
    if IS_WINDOWS:
        result = subprocess.run(["netstat", "-ano", "-p", "tcp"],
                                capture_output=True, text=True, errors="replace")
        for line in (result.stdout or "").splitlines():
            match = re.match(r"^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$", line)
            if match and int(match.group(1)) in wanted:
                pids.add(int(match.group(2)))
    else:
        tool = shutil.which("ss") or shutil.which("lsof")
        if tool and Path(tool).name == "ss":
            for port in wanted:
                result = subprocess.run(["ss", "-ltnpH", f"sport = :{port}"],
                                        capture_output=True, text=True, errors="replace")
                for match in re.finditer(r"pid=(\d+)", result.stdout or ""):
                    pids.add(int(match.group(1)))
        elif tool:
            for port in wanted:
                result = subprocess.run(["lsof", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
                                        capture_output=True, text=True, errors="replace")
                for line in (result.stdout or "").splitlines():
                    if line.strip().isdigit():
                        pids.add(int(line.strip()))
    pids.discard(os.getpid())
    return sorted(pids)


# --------------------------------------------------------------------------- #
# host process (one per service)
# --------------------------------------------------------------------------- #

def write_log_line(path: Path, name: str, line: str) -> None:
    """Append one prefixed line.

    The file is opened per line on purpose: holding it open would stop
    ``logs`` / ``status`` (and the user's editor) from reading combined.log
    while services are running.
    """
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    record = f"[{stamp}][{name}] {line}\n"
    for _ in range(20):
        try:
            with path.open("a", encoding="utf-8", errors="replace") as handle:
                handle.write(record)
            return
        except OSError:
            time.sleep(0.05)


def read_log_text(path: Path) -> str:
    for _ in range(20):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            time.sleep(0.05)
    return ""


def install_interrupt_forwarder(child_pid: int):
    """Kill the service tree when this console receives Ctrl+C / Ctrl+Break.

    The console delivers the event to every process attached to it, but a
    service that handles it slowly (or not at all) would leave the host blocked
    on a pipe that never closes. Handling it here mirrors what the .ps1 host
    does in its ``finally`` block: ``taskkill /T`` the whole cmd.exe -> Python
    tree. Returns the handler (which must stay referenced) or ``None``.
    """
    if not IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def _handler(event):
            # 0 = CTRL_C_EVENT, 1 = CTRL_BREAK_EVENT. Return True: the host keeps
            # running long enough to flush its log line and reap the child.
            if event in (0, 1):
                try:
                    kill_tree(child_pid)
                except Exception:  # noqa: BLE001 - a handler must never raise
                    pass
                return True
            return False

        handler = handler_type(_handler)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetConsoleCtrlHandler.argtypes = [handler_type, wintypes.BOOL]
        kernel32.SetConsoleCtrlHandler.restype = wintypes.BOOL
        if not kernel32.SetConsoleCtrlHandler(handler, True):
            return None
        return handler
    except Exception:  # noqa: BLE001 - advisory only; Ctrl+C still hits the child
        return None


def set_console_title(title: str) -> None:
    """Name the service console/tab like the .ps1 host does."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes

        ctypes.WinDLL("kernel32", use_last_error=True).SetConsoleTitleW(ctypes.c_wchar_p(title))
    except Exception:  # noqa: BLE001 - purely cosmetic
        pass


def host_main(payload: dict) -> int:
    name = payload["name"]
    argv = payload["argv"]
    cwd = payload["cwd"]
    priority = payload.get("process_priority")
    combined_log = Path(payload["combined_log"])
    pid_file = Path(payload["pid_file"])
    environment = os.environ.copy()
    environment.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})

    set_console_title(f"Agent Stack - {name}")
    combined_log.parent.mkdir(parents=True, exist_ok=True)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")

    # The host forwards stdout; keeping it responsive avoids stalling the service
    # on a full pipe when the machine is busy.
    host_priority = apply_process_priority(_current_process_handle(), priority)
    if host_priority != "unchanged":
        write_log_line(combined_log, name, f"host process priority {host_priority}")

    try:
        child = subprocess.Popen(
            argv,
            cwd=cwd,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            bufsize=1,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        write_log_line(combined_log, name, f"failed to start: {exc}")
        pid_file.unlink(missing_ok=True)
        return 1

    write_log_line(combined_log, name, f"host pid={os.getpid()} child pid={child.pid}")
    child_priority = apply_process_priority(_process_handle(child.pid), priority)
    if child_priority != "unchanged":
        write_log_line(combined_log, name, f"service process priority {child_priority}")
    # The child intentionally shares this host's console (the .ps1 host does the
    # same with CreateNoWindow = $false), so Ctrl+C in the service tab reaches
    # cmd.exe and the Python service instead of only killing the logger.
    _keepalive_handler = install_interrupt_forwarder(child.pid)
    interrupted = False
    code = None
    try:
        assert child.stdout is not None
        for line in child.stdout:
            text = line.rstrip("\r\n")
            write_log_line(combined_log, name, text)
            print(text, flush=True)
    except KeyboardInterrupt:
        interrupted = True
        write_log_line(combined_log, name, "interrupt received; stopping the service process tree")
    except (ValueError, OSError):
        pass
    finally:
        if interrupted and child.poll() is None:
            kill_tree(child.pid)
        try:
            code = child.wait()
        except KeyboardInterrupt:
            kill_tree(child.pid)
            code = child.wait()
        write_log_line(combined_log, name, f"child exited with code {code}")
    pid_file.unlink(missing_ok=True)
    return 0


# --------------------------------------------------------------------------- #
# starting services
# --------------------------------------------------------------------------- #

_UNSET = object()


def build_service_argv(registry: Registry, definition: dict, marker_file: Path, *, conda_info=_UNSET):
    """Return the command to run: a list (POSIX / plain process) or, on Windows
    with conda, a complete command-line *string*.

    The Windows conda path has to chain ``call conda.bat activate`` with the
    service command. Handing that line to ``cmd.exe`` as one argv element would
    make Python re-quote every embedded quote as ``\\"``, which cmd.exe does not
    understand, so the string is passed to CreateProcess verbatim instead.
    """
    runner = definition.get("runner")
    command = str(definition.get("command") or "")
    arguments = [str(item) for item in (definition.get("arguments") or [])]
    if runner == "process":
        return [command] + arguments

    environment = str(definition.get("environment") or "")
    conda = find_conda() if conda_info is _UNSET else conda_info
    if not conda:
        if IS_WINDOWS:
            raise LauncherError(
                "Cannot find a usable conda.bat. Set CONDA_BAT to <conda root>\\condabin\\conda.bat "
                "or make that condabin directory available on PATH."
            )
        raise LauncherError(
            "Cannot find conda. Set CONDA_EXE or make conda available on PATH."
        )
    if IS_WINDOWS:
        tokens = [command] + arguments
        service_command = " ".join(quote_cmd_token(token) for token in tokens)
        return (
            "cmd.exe /d /s /c chcp 65001 >nul && set \"PYTHONUTF8=1\" && set \"PYTHONIOENCODING=utf-8\""
            " && call " + quote_cmd_token(conda) + " activate " + quote_cmd_token(environment)
            + " && echo activated > " + quote_cmd_token(str(marker_file))
            + " && " + service_command
        )
    # POSIX: `conda run` needs no shell hook, and --no-capture-output keeps the
    # service's stdout streaming instead of buffering it until exit.
    return [conda, "run", "--no-capture-output", "-n", environment, command] + arguments


def resolve_terminal_mode(requested: str, hidden: bool) -> str:
    """Pick how service consoles are shown, mirroring the .ps1 launcher.

    ``tabs``     one Windows Terminal tab per service (``auto`` when wt.exe exists)
    ``windows``  one console window per service
    ``hidden``   no window at all (``-Hidden`` / ``--hidden``)
    ``background``  POSIX: plain detached processes, output only in combined.log
    """
    if not IS_WINDOWS:
        return "background"
    if hidden:
        return "hidden"
    has_terminal = bool(shutil.which("wt.exe"))
    if requested == "tabs":
        if not has_terminal:
            raise LauncherError("Terminal mode 'tabs' requires Windows Terminal (wt.exe).")
        return "tabs"
    if requested == "auto" and has_terminal:
        return "tabs"
    return "windows"


CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def _console_host_flags(base: int) -> int:
    """Console flags that let a service host outlive the launcher.

    Terminals (Windows Terminal, VS Code, CI runners, sandboxes) often wrap
    their children in a job object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``;
    without breaking away, every service would die the moment the launcher
    exits -- which is right after ``start`` returns. Jobs that forbid breakaway
    simply reject the flag, and ``_popen_host`` retries without it.
    """
    return base | CREATE_BREAKAWAY_FROM_JOB


def _popen_host(command: list, options: dict, name: str):
    try:
        return subprocess.Popen(command, **options)
    except OSError:
        flags = int(options.get("creationflags") or 0)
        if not flags & CREATE_BREAKAWAY_FROM_JOB:
            raise
        # The hosting job does not allow breakaway: fall back to plain flags so
        # the stack still starts (services then live as long as that job).
        options = dict(options)
        options["creationflags"] = flags & ~CREATE_BREAKAWAY_FROM_JOB
        log(f"[note] {name:<14} job does not allow breakaway; keeping the console job")
        return subprocess.Popen(command, **options)


def start_host(registry: Registry, definition: dict, payload_argv: list, marker_file: Path | None,
               terminal_mode: str) -> int:
    name = str(definition["name"])
    payload = {
        "name": name,
        "argv": payload_argv,
        "cwd": str(registry.resolve(definition.get("working_directory") or ".")),
        "combined_log": str(registry.combined_log),
        "pid_file": str(registry.runtime_dir / f"{name}.host.pid"),
        "process_priority": definition.get("process_priority"),
    }
    command = [sys.executable, str(registry.host_script), HOST_MODE, encode_payload(payload)]
    options = {
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if IS_WINDOWS:
        if terminal_mode == "tabs":
            # wt.exe returns immediately and owns the tab console, so the host
            # must not create a console of its own. Output is visible in the tab
            # because the host writes it there (see host_main).
            wt = shutil.which("wt.exe")
            if not wt:
                raise LauncherError("Terminal mode 'tabs' requires Windows Terminal (wt.exe).")
            command = [wt, "-w", "AgentStack", "new-tab", "--title", name] + command
        elif terminal_mode == "hidden":
            options["creationflags"] = _console_host_flags(subprocess.CREATE_NO_WINDOW)  # type: ignore[attr-defined]
            options["stdout"] = subprocess.DEVNULL
            options["stderr"] = subprocess.DEVNULL
        else:
            # New console window: leaving stdout/stderr unset lets the host write
            # its service output into that window, exactly like the .ps1 host's
            # Write-Host loop. Log propagation to combined.log is unaffected.
            options["creationflags"] = _console_host_flags(subprocess.CREATE_NEW_CONSOLE)  # type: ignore[attr-defined]
    else:
        options["start_new_session"] = True
        options["stdout"] = subprocess.DEVNULL
        options["stderr"] = subprocess.DEVNULL
    process = _popen_host(command, options, name)

    pid_file = Path(payload["pid_file"])
    owns_host = terminal_mode != "tabs"
    deadline = time.time() + PID_WAIT_SECONDS
    while time.time() < deadline:
        if pid_file.is_file():
            try:
                return int(pid_file.read_text(encoding="utf-8").strip())
            except ValueError:
                pass
        exit_code = process.poll()
        if owns_host:
            if exit_code is not None:
                raise LauncherError(
                    f"Service host for '{name}' exited with code {exit_code} before publishing its pid. "
                    f"Check 'logs --service {name}'."
                )
        elif exit_code not in (None, 0):
            raise LauncherError(
                f"Windows Terminal failed to create the '{name}' tab (exit code {exit_code})."
            )
        time.sleep(0.1)
    raise LauncherError(f"Service host for '{name}' did not report its pid in time.")


def wait_activation(registry: Registry, definition: dict, marker_file: Path, host_pid: int) -> None:
    timeout = float(registry.setting("activation_timeout_seconds", 60))
    deadline = time.time() + timeout
    while time.time() < deadline:
        if marker_file.is_file():
            # Consumed like the .ps1 launcher: leaving it behind makes a later
            # `start` believe activation already happened.
            marker_file.unlink(missing_ok=True)
            log(f"[active] {str(definition.get('name')):<13} conda environment ready")
            return
        if not process_running(host_pid):
            raise LauncherError(
                f"Conda activation failed for service '{definition.get('name')}'. Check the combined log."
            )
        time.sleep(0.2)
    raise LauncherError(
        f"Conda activation timed out after {timeout:g} seconds for '{definition.get('name')}'."
    )


def log_tail(registry: Registry, name: str, lines: int = 25) -> str:
    if not registry.combined_log.is_file():
        return "No error log was produced."
    marker = f"[{name}]"
    content = read_log_text(registry.combined_log).splitlines()
    return "\n".join([line for line in content if marker in line][-lines:])


def health_ok(registry: Registry, definition: dict, *, state: list | None = None) -> bool:
    name = str(definition.get("name"))
    entry = state_entry(state if state is not None else read_state(registry), name)
    if entry is None:
        return False
    if definition.get("runner") in ("conda", "process") and not process_running(int(entry.get("pid") or 0)):
        return False
    health = definition.get("health") or {}
    kind = health.get("type") or "process"
    if kind == "process":
        return True
    if kind == "tcp":
        return tcp_open(str(health.get("host") or "127.0.0.1"), int(health.get("port")))
    if kind == "systemd":
        return wsl_unit_state(str(definition.get("distro") or ""), str(definition.get("unit") or "")) == "running"
    raise LauncherError(f"Unsupported health check for '{name}': {kind}")


def start_one(registry: Registry, definition: dict, state: list, terminal_mode: str, *, conda_info=_UNSET) -> None:
    name = str(definition["name"])
    runner = definition.get("runner")
    log(f"[start] {name:<14} runner={runner} terminal={terminal_mode}")
    if runner == "wsl-systemd":
        arguments = ["sudo", "systemctl", "start", str(definition.get("unit"))]
        code = run_wsl(str(definition.get("distro") or ""), arguments)
        if code != 0:
            raise LauncherError(
                f"Failed to start WSL unit '{definition.get('unit')}' (exit code {code})."
            )
        state.append({
            "name": name,
            "runner": "wsl-systemd",
            "distro": str(definition.get("distro") or ""),
            "unit": str(definition.get("unit") or ""),
            "sudo": True,
            "started_at": now_iso(),
        })
        write_state(registry, state)
        return

    marker_file = None
    if runner == "conda":
        marker_file = registry.runtime_dir / f"{name}.activated"
        marker_file.unlink(missing_ok=True)
    argv = build_service_argv(registry, definition, marker_file or Path(os.devnull), conda_info=conda_info)
    pid = start_host(registry, definition, argv, marker_file, terminal_mode)
    if marker_file is not None:
        wait_activation(registry, definition, marker_file, pid)
    state.append({
        "name": name,
        "runner": runner,
        "pid": pid,
        "terminal_mode": terminal_mode,
        "started_at": now_iso(),
    })
    write_state(registry, state)


def wait_services(registry: Registry, definitions: list) -> None:
    timeout = float(registry.setting("startup_timeout_seconds", 300))
    poll = float(registry.setting("poll_interval_seconds", 1))
    grace = 0
    for definition in definitions:
        value = (definition.get("health") or {}).get("grace_seconds")
        if value and float(value) > grace:
            grace = float(value)
    if grace:
        time.sleep(grace)
    state = read_state(registry)
    pending = list(definitions)
    deadline = time.time() + timeout
    while pending and time.time() < deadline:
        remaining = []
        for definition in pending:
            entry = state_entry(state, str(definition["name"]))
            if (
                definition.get("runner") in ("conda", "process")
                and entry is not None
                and not process_running(int(entry.get("pid") or 0))
            ):
                name = str(definition["name"])
                raise LauncherError(
                    f"Service '{name}' exited during startup.\n" + log_tail(registry, name)
                )
            if health_ok(registry, definition, state=state):
                log(f"[ready] {definition['name']}")
            else:
                remaining.append(definition)
        pending = remaining
        if pending:
            time.sleep(poll)
    if pending:
        names = ", ".join(str(item.get("name")) for item in pending)
        raise LauncherError(f"Startup timed out after {timeout:g} seconds: {names}")


# --------------------------------------------------------------------------- #
# service selection: the Startup block in Core/config.yaml, registry as fallback
# --------------------------------------------------------------------------- #

TRUTHY = ("true", "yes", "on", "1")
FALSY = ("false", "no", "off", "0")
_TOP_LEVEL_KEY = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_-]*):\s*(?:#.*)?$")
_STARTUP_ENTRY = re.compile(r"^\s+(?P<name>[A-Za-z0-9_.-]+):\s*(?P<value>[^#]*?)\s*(?:#.*)?$")


def read_startup_flags(path: Path):
    """Read the dedicated ``Startup:`` block of Core/config.yaml.

    The launcher has to run in any conda environment, so PyYAML stays out of the
    picture: this is a line scanner for one flat mapping, and
    ``agent-stack/launcher.txt`` implements exactly the same rules -- keep the
    two in sync. Returns ``({service name: bool}, notes)``.
    """
    flags: dict = {}
    notes: list = []
    if not path.is_file():
        return flags, [f"config file not found: {path} (falling back to registry 'enabled')"]
    inside = False
    seen_section = False
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line[:1].isspace():
            match = _TOP_LEVEL_KEY.match(line)
            inside = bool(match) and match.group("key") == STARTUP_SECTION
            seen_section = seen_section or inside
            continue
        if not inside:
            continue
        match = _STARTUP_ENTRY.match(line)
        if not match:
            notes.append(
                f"{path.name}:{number}: cannot parse '{stripped}', expected '<service>: true|false'"
            )
            continue
        name = match.group("name")
        value = match.group("value").strip().strip('"').strip("'").lower()
        if value in TRUTHY:
            flags[name] = True
        elif value in FALSY:
            flags[name] = False
        else:
            notes.append(f"{path.name}:{number}: '{name}' has a non-boolean value '{value}'")
    if not seen_section:
        notes.append(
            f"no '{STARTUP_SECTION}:' block in {path} (falling back to registry 'enabled')"
        )
    return flags, notes


def resolve_enabled(definition: dict, flags: dict, config_name: str = DEFAULT_CONFIG_FILE):
    """Return ``(enabled, source)`` for one registry entry."""
    name = str(definition.get("name"))
    if name in flags:
        return flags[name], f"{config_name} Startup.{name}"
    enabled = definition.get("enabled") is not False
    return enabled, f"registry enabled={str(enabled).lower()}"


def check_port_conflicts(services: list) -> None:
    """Refuse a set in which two services would bind the same TCP port.

    This is what makes ``llm``/``mllm`` (both 11000 in Core/config.yaml) and
    ``llm-memory``/``mllm-memory`` (both 10010) mutually exclusive without
    hard-coding the pairs: enable both and the second one dies on bind.
    """
    seen: dict = {}
    for item in services:
        health = item.get("health") or {}
        if health.get("type") != "tcp" or not health.get("port"):
            continue
        port = int(health["port"])
        name = str(item.get("name"))
        previous = seen.get(port)
        if previous:
            raise LauncherError(
                f"Services '{previous}' and '{name}' would both listen on port {port}. "
                f"Turn one of them off in the Startup block of {DEFAULT_CONFIG_FILE} "
                "(or in registry.json's \"enabled\")."
            )
        seen[port] = name


def select_services(registry: Registry, only: str = "", config_path=None) -> dict:
    """Resolve the effective service set for this run.

    ``Startup:`` in Core/config.yaml wins; services it does not mention fall
    back to the registry's own ``enabled`` flag. ``--only`` overrides both.
    """
    definitions = [item for item in (registry.data.get("services") or []) if item.get("name")]
    config_file = registry.resolve(config_path or DEFAULT_CONFIG_FILE)
    flags, warnings = read_startup_flags(config_file)
    chosen: list = []
    report: list = []
    for definition in definitions:
        enabled, source = resolve_enabled(definition, flags, config_file.name)
        report.append((str(definition.get("name")), enabled, source))
        if enabled:
            chosen.append(definition)
    if only.strip():
        wanted = [name.strip() for name in only.split(",") if name.strip()]
        known = {str(item.get("name")) for item in definitions}
        unknown = [name for name in wanted if name not in known]
        if unknown:
            raise LauncherError(
                f"Unknown service name(s): {', '.join(unknown)}. "
                f"Registry declares: {', '.join(sorted(known))}."
            )
        chosen = [item for item in definitions if str(item.get("name")) in wanted]
        report = [(name, name in wanted, "explicit --only" if name in wanted else "explicit --only (skipped)")
                  for name, _, _ in report]
    return {
        "config": config_file,
        "services": chosen,
        "report": report,
        "warnings": warnings,
    }


def print_selection(selection: dict, verbose: bool) -> None:
    for message in selection["warnings"]:
        warn(message)
    if verbose:
        log(f"Startup switches from {selection['config']}:")
        for name, enabled, source in selection["report"]:
            log(f"  {name:<13} {'on ' if enabled else 'off'}  ({source})")
        log("")
        return
    disabled = [name for name, enabled, _ in selection["report"] if not enabled]
    if disabled:
        log("Off (startup switches): " + ", ".join(disabled))


def service_command_line(definition: dict) -> str:
    tokens = [str(definition.get("command") or "")]
    tokens += [str(item) for item in (definition.get("arguments") or [])]
    return " ".join(tokens).strip()


# --------------------------------------------------------------------------- #
# optional dependencies (best effort, not part of the stack lifecycle)
# --------------------------------------------------------------------------- #

def start_optional_dependencies(registry: Registry) -> None:
    for dependency in registry.optional_dependencies:
        name = str(dependency.get("name"))
        unit = str(dependency.get("unit"))
        arguments = []
        if dependency.get("sudo") is not False:
            arguments += ["sudo", "-n"]  # never block on a password prompt
        arguments += ["systemctl", "start", unit]
        log(f"[deps]  {name:<14} runner={dependency.get('runner')} unit={unit}")
        try:
            code = run_wsl(str(dependency.get("distro") or ""), arguments)
        except LauncherError as exc:
            warn(f"[deps] {name} skipped: {exc}")
            continue
        if code != 0:
            warn(f"[deps] {name} skipped (exit code {code}); the stack continues without it.")


def show_optional_dependency_status(registry: Registry) -> None:
    for dependency in registry.optional_dependencies:
        name = str(dependency.get("name"))
        unit = str(dependency.get("unit"))
        state = wsl_unit_state(str(dependency.get("distro") or ""), unit)
        log(f"{name:<16} {state:<9} runner={dependency.get('runner')} unit={unit}")


# --------------------------------------------------------------------------- #
# actions
# --------------------------------------------------------------------------- #

def action_validate(registry: Registry, config_path=None) -> None:
    selection = select_services(registry, "", config_path)
    # A port clash means `start` would fail on the second bind, so validate is
    # the right place to shout about it instead of half-starting the stack.
    check_port_conflicts(selection["services"])
    print_selection(selection, verbose=True)
    log(
        f"Registry is valid: {len(selection['services'])} service(s) to start, "
        f"{len(registry.optional_dependencies)} optional dependency(ies)."
    )


def stop_all(registry: Registry, quiet: bool = False) -> None:
    state = read_state(registry)
    for entry in reversed(state):
        name = str(entry.get("name"))
        runner = entry.get("runner")
        if runner == "wsl-systemd":
            arguments = ["sudo", "systemctl", "stop", str(entry.get("unit"))]
            log(f"[stop]  {name:<14} unit={entry.get('unit')}")
            try:
                code = run_wsl(str(entry.get("distro") or ""), arguments)
            except LauncherError as exc:
                warn(str(exc))
                continue
            if code != 0:
                warn(f"Failed to stop '{entry.get('unit')}' (exit code {code}).")
            continue
        pid = int(entry.get("pid") or 0)
        if process_running(pid):
            log(f"[stop]  {name:<14} pid={pid}")
            kill_tree(pid)
        else:
            log(f"[gone]  {name:<14} pid={pid}")

    for pid_file in registry.runtime_dir.glob("*.host.pid"):
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            pid_file.unlink(missing_ok=True)
            continue
        if process_running(pid):
            log(f"[cleanup] uncommitted host pid={pid}")
            kill_tree(pid)
        pid_file.unlink(missing_ok=True)

    # Also sweep the ports of services that were started through `--only` while
    # the Startup block keeps them off: their pid came from state.json above,
    # but a half-registered one only shows up as a listener.
    sweep = list(registry.services)
    known = {str(item.get("name")) for item in sweep}
    for entry in state:
        name = str(entry.get("name"))
        if name in known:
            continue
        definition = registry.get_service(name)
        if definition is not None:
            sweep.append(definition)
            known.add(name)
    ports = [
        (item.get("health") or {}).get("port")
        for item in sweep
        if (item.get("health") or {}).get("type") == "tcp"
    ]
    for pid in listening_pids([port for port in ports if port]):
        log(f"[cleanup] listener pid={pid}")
        kill_tree(pid)

    write_state(registry, [])
    registry.state_file.unlink(missing_ok=True)
    for marker in registry.runtime_dir.glob("*.activated"):
        marker.unlink(missing_ok=True)

    for dependency in registry.optional_dependencies:
        log(f"[deps]  left running: {dependency.get('name')} (unit={dependency.get('unit')})")
    if not quiet:
        log("")


def action_start(registry: Registry, terminal_mode: str, startup_timeout: float,
                 only: str = "", config_path=None, selection=None) -> None:
    registry.runtime_dir.mkdir(parents=True, exist_ok=True)
    registry.log_dir.mkdir(parents=True, exist_ok=True)
    if selection is None:
        selection = select_services(registry, only, config_path)
    print_selection(selection, verbose=False)
    services = selection["services"]
    if not services:
        raise LauncherError(
            "Nothing to start: the effective service set is empty. "
            f"Check the Startup block in {selection['config']}."
        )
    check_port_conflicts(services)
    log("Starting: " + ", ".join(str(item.get("name")) for item in services))

    state = read_state(registry)
    running = []
    for entry in state:
        if entry.get("runner") in ("conda", "process") and process_running(int(entry.get("pid") or 0)):
            running.append(entry)
        elif entry.get("runner") == "wsl-systemd" and wsl_unit_state(
            str(entry.get("distro") or ""), str(entry.get("unit") or "")
        ) == "running":
            running.append(entry)
    if running:
        healthy = [item for item in services if health_ok(registry, item, state=state)]
        if len(healthy) == len(services):
            names = ", ".join(str(entry.get("name")) for entry in running)
            raise LauncherError(
                f"Stack already has running services: {names}. Use restart or stop first."
            )
        warn("Incomplete previous startup detected; cleaning recorded and listening processes before retry.")
        stop_all(registry, quiet=True)

    write_state(registry, [])
    registry.combined_log.parent.mkdir(parents=True, exist_ok=True)
    registry.combined_log.write_text("", encoding="utf-8")
    if terminal_mode == "hidden":
        log("Terminal mode: hidden")
    elif terminal_mode == "tabs":
        log("Terminal mode: one Windows Terminal tab per service")
    elif terminal_mode == "windows":
        log("Terminal mode: one console window per service")
    else:
        log("Terminal mode: background processes (output goes to the combined log)")

    # Optional infrastructure first: failures are warnings only, never a rollback.
    start_optional_dependencies(registry)

    if startup_timeout > 0:
        registry.settings["startup_timeout_seconds"] = startup_timeout

    state = []
    conda_info = find_conda() if any(item.get("runner") == "conda" for item in services) else _UNSET
    orders = sorted({DEFAULT_START_ORDER if item.get("start_order") is None else int(item["start_order"])
                     for item in services})
    try:
        for order in orders:
            group = [
                item for item in services
                if (DEFAULT_START_ORDER if item.get("start_order") is None else int(item["start_order"])) == order
            ]
            for definition in group:
                start_one(registry, definition, state, terminal_mode, conda_info=conda_info)
            wait_services(registry, group)
        log("")
        log(f"All {len(services)} selected service(s) are running.")
    except Exception as exc:  # noqa: BLE001 - always roll back like the PowerShell launcher
        warn(str(exc))
        stop_all(registry, quiet=True)
        raise


def action_plan(registry: Registry, only: str = "", config_path=None) -> None:
    """Print what a `start` would launch here, without touching anything."""
    selection = select_services(registry, only, config_path)
    services = selection["services"]
    check_port_conflicts(services)
    print_selection(selection, verbose=True)
    log(f"Effective startup set: {len(services)} service(s)")
    log(f"  {'name':<13} {'port':<6} {'runner':<8} {'environment':<24} command")
    for item in sorted(services, key=lambda entry: (
        DEFAULT_START_ORDER if entry.get("start_order") is None else int(entry["start_order"]),
        str(entry.get("name")),
    )):
        health = item.get("health") or {}
        port = str(health.get("port") or "-") if health.get("type") == "tcp" else "-"
        log(
            f"  {str(item.get('name')):<13} {port:<6} {str(item.get('runner')):<8} "
            f"{str(item.get('environment') or '-'):<24} {service_command_line(item)}"
        )


def action_status(registry: Registry) -> None:
    state = read_state(registry)
    definitions = sorted(registry.services, key=lambda item: (item.get("start_order") or DEFAULT_START_ORDER,
                                                              str(item.get("name"))))
    for definition in definitions:
        name = str(definition.get("name"))
        entry = state_entry(state, name)
        if entry is None:
            if definition.get("runner") == "wsl-systemd" and wsl_unit_state(
                str(definition.get("distro") or ""), str(definition.get("unit") or "")
            ) == "running":
                status = "external"
            else:
                status = "stopped"
        elif health_ok(registry, definition, state=state):
            status = "running"
        elif definition.get("runner") in ("conda", "process") and process_running(int(entry.get("pid") or 0)):
            status = "starting"
        else:
            status = "failed"
        log(f"{name:<16} {status:<9} runner={definition.get('runner')}")
    # A `start --only` run can leave services running that the Startup block
    # turns off; they must not look invisible here.
    known = {str(definition.get("name")) for definition in definitions}
    for entry in state:
        name = str(entry.get("name"))
        if name in known:
            continue
        if entry.get("runner") in ("conda", "process"):
            status = "running" if process_running(int(entry.get("pid") or 0)) else "stale"
        else:
            status = wsl_unit_state(str(entry.get("distro") or ""), str(entry.get("unit") or ""))
        log(f"{name:<16} {status:<9} runner={entry.get('runner')} (not enabled in registry)")
    show_optional_dependency_status(registry)


def action_logs(registry: Registry, service: str | None) -> None:
    if not registry.combined_log.is_file():
        log("No matching logs found.")
        return
    lines = read_log_text(registry.combined_log).splitlines()
    if service:
        marker = f"[{service}]"
        lines = [line for line in lines if marker in line][-80:]
    else:
        lines = lines[-120:]
    for line in lines:
        log(line)


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cross-platform launcher for the Core service stack (reads agent-stack/registry.json)."
    )
    parser.add_argument("action", nargs="?", default="start",
                        choices=["start", "stop", "restart", "status", "logs", "validate", "plan"])
    parser.add_argument("--registry", default="agent-stack/registry.json",
                        help="Path to registry.json (default: agent-stack/registry.json).")
    parser.add_argument("--service", default="", help="Only show logs of this service (with 'logs').")
    parser.add_argument("--only", default="",
                        help="Start/restart only these comma-separated services (e.g. --only asr). "
                             "Overrides the Startup block in config.yaml for this run.")
    parser.add_argument("--config", default=DEFAULT_CONFIG_FILE,
                        help="Core config file read for the Startup switches "
                             f"(default: {DEFAULT_CONFIG_FILE}, relative to the Core directory).")
    parser.add_argument("--terminal", default="auto", choices=["auto", "tabs", "windows"],
                        help="Windows: one Windows Terminal tab per service (tabs), one console "
                             "window each (windows), or tabs when wt.exe exists (auto).")
    parser.add_argument("--startup-timeout", type=float, default=0.0,
                        help="Override settings.startup_timeout_seconds.")
    parser.add_argument("--hidden", action="store_true",
                        help="Windows: do not open a console window per service.")
    return parser


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == HOST_MODE:
        payload = decode_payload(argv[1])
        return host_main(payload)
    arguments = build_parser().parse_args(argv)
    try:
        registry = read_registry(arguments.registry)
        if arguments.action == "validate":
            action_validate(registry, arguments.config)
        elif arguments.action == "start":
            action_start(registry, resolve_terminal_mode(arguments.terminal, arguments.hidden),
                         arguments.startup_timeout, arguments.only, arguments.config)
        elif arguments.action == "plan":
            action_plan(registry, arguments.only, arguments.config)
        elif arguments.action == "stop":
            stop_all(registry)
        elif arguments.action == "restart":
            # Resolve the target set before stopping anything: an unreadable
            # config must not leave the user with a stopped stack.
            selection = select_services(registry, arguments.only, arguments.config)
            stop_all(registry, quiet=True)
            action_start(registry, resolve_terminal_mode(arguments.terminal, arguments.hidden),
                         arguments.startup_timeout, arguments.only, arguments.config,
                         selection=selection)
        elif arguments.action == "status":
            action_status(registry)
        elif arguments.action == "logs":
            action_logs(registry, arguments.service or None)
    except LauncherError as exc:
        warn(str(exc))
        return 1
    except OSError as exc:
        # Runtime directory permissions, a locked log, a vanished working
        # directory: report the path instead of dumping a traceback.
        warn(f"I/O error: {exc}")
        return 1
    except KeyboardInterrupt:
        warn("interrupted")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
