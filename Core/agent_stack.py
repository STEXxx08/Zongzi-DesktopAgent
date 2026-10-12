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
PID_WAIT_SECONDS = 20.0
STOP_GRACE_SECONDS = 8.0


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


def find_conda() -> str | None:
    """Locate the conda executable the way the PowerShell launcher does."""
    for variable in ("CONDA_EXE", "CONDA_BAT"):
        value = os.environ.get(variable)
        if value and Path(value).exists():
            return value
    found = shutil.which("conda") or shutil.which("conda.bat")
    if found:
        return found
    home = Path.home()
    candidates = [
        home / "miniconda3" / "condabin" / "conda.bat",
        home / "anaconda3" / "condabin" / "conda.bat",
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


def host_main(payload: dict) -> int:
    name = payload["name"]
    argv = payload["argv"]
    cwd = payload["cwd"]
    combined_log = Path(payload["combined_log"])
    pid_file = Path(payload["pid_file"])
    environment = os.environ.copy()
    environment.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})

    combined_log.parent.mkdir(parents=True, exist_ok=True)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")

    creationflags = 0
    if IS_WINDOWS:
        creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        # The parent already opened a console window for this host; the service
        # itself must not create another one.
    try:
        child = subprocess.Popen(
            argv,
            cwd=cwd,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
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
    try:
        assert child.stdout is not None
        for line in child.stdout:
            text = line.rstrip("\r\n")
            write_log_line(combined_log, name, text)
            print(text, flush=True)
    except (KeyboardInterrupt, ValueError):
        pass
    except OSError:
        pass
    finally:
        code = child.wait()
        write_log_line(combined_log, name, f"child exited with code {code}")
    pid_file.unlink(missing_ok=True)
    return 0


# --------------------------------------------------------------------------- #
# starting services
# --------------------------------------------------------------------------- #

def build_service_argv(registry: Registry, definition: dict, marker_file: Path):
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
    conda = find_conda()
    if not conda:
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


def start_host(registry: Registry, definition: dict, payload_argv: list, marker_file: Path | None,
               hidden: bool) -> int:
    name = str(definition["name"])
    payload = {
        "name": name,
        "argv": payload_argv,
        "cwd": str(registry.resolve(definition.get("working_directory") or ".")),
        "combined_log": str(registry.combined_log),
        "pid_file": str(registry.runtime_dir / f"{name}.host.pid"),
    }
    command = [sys.executable, str(registry.host_script), HOST_MODE, encode_payload(payload)]
    creationflags = 0
    start_new_session = False
    if IS_WINDOWS:
        if hidden:
            creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        else:
            creationflags = subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]
    else:
        start_new_session = True
    subprocess.Popen(
        command,
        creationflags=creationflags,
        start_new_session=start_new_session,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    pid_file = Path(payload["pid_file"])
    deadline = time.time() + PID_WAIT_SECONDS
    while time.time() < deadline:
        if pid_file.is_file():
            try:
                return int(pid_file.read_text(encoding="utf-8").strip())
            except ValueError:
                pass
        time.sleep(0.1)
    raise LauncherError(f"Service host for '{name}' did not report its pid in time.")


def wait_activation(registry: Registry, definition: dict, marker_file: Path, host_pid: int) -> None:
    timeout = float(registry.setting("activation_timeout_seconds", 60))
    deadline = time.time() + timeout
    while time.time() < deadline:
        if marker_file.is_file():
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


def health_ok(registry: Registry, definition: dict) -> bool:
    name = str(definition.get("name"))
    entry = state_entry(read_state(registry), name)
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


def start_one(registry: Registry, definition: dict, state: list, hidden: bool) -> None:
    name = str(definition["name"])
    runner = definition.get("runner")
    log(f"[start] {name:<14} runner={runner}")
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
    argv = build_service_argv(registry, definition, marker_file or Path(os.devnull))
    pid = start_host(registry, definition, argv, marker_file, hidden)
    if marker_file is not None:
        wait_activation(registry, definition, marker_file, pid)
    state.append({
        "name": name,
        "runner": runner,
        "pid": pid,
        "terminal_mode": "hidden" if (IS_WINDOWS and hidden) else ("console" if IS_WINDOWS else "background"),
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
            if health_ok(registry, definition):
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

def action_validate(registry: Registry) -> None:
    log(
        f"Registry is valid: {len(registry.services)} enabled service(s), "
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

    ports = [
        (item.get("health") or {}).get("port")
        for item in registry.services
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


def action_start(registry: Registry, hidden: bool, startup_timeout: float) -> None:
    registry.runtime_dir.mkdir(parents=True, exist_ok=True)
    registry.log_dir.mkdir(parents=True, exist_ok=True)
    services = registry.services

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
        healthy = [item for item in services if health_ok(registry, item)]
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
    if IS_WINDOWS:
        log("Terminal mode: " + ("hidden" if hidden else "one console window per service"))
    else:
        log("Terminal mode: background processes (output goes to the combined log)")

    # Optional infrastructure first: failures are warnings only, never a rollback.
    start_optional_dependencies(registry)

    if startup_timeout > 0:
        registry.settings["startup_timeout_seconds"] = startup_timeout

    state = []
    orders = sorted({DEFAULT_START_ORDER if item.get("start_order") is None else int(item["start_order"])
                     for item in services})
    try:
        for order in orders:
            group = [
                item for item in services
                if (DEFAULT_START_ORDER if item.get("start_order") is None else int(item["start_order"])) == order
            ]
            for definition in group:
                start_one(registry, definition, state, hidden)
            wait_services(registry, group)
        log("")
        log("All enabled registry services are running.")
    except Exception as exc:  # noqa: BLE001 - always roll back like the PowerShell launcher
        warn(str(exc))
        stop_all(registry, quiet=True)
        raise


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
        elif health_ok(registry, definition):
            status = "running"
        elif definition.get("runner") in ("conda", "process") and process_running(int(entry.get("pid") or 0)):
            status = "starting"
        else:
            status = "failed"
        log(f"{name:<16} {status:<9} runner={definition.get('runner')}")
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
                        choices=["start", "stop", "restart", "status", "logs", "validate"])
    parser.add_argument("--registry", default="agent-stack/registry.json",
                        help="Path to registry.json (default: agent-stack/registry.json).")
    parser.add_argument("--service", default="", help="Only show logs of this service (with 'logs').")
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
            action_validate(registry)
        elif arguments.action == "start":
            action_start(registry, arguments.hidden, arguments.startup_timeout)
        elif arguments.action == "stop":
            stop_all(registry)
        elif arguments.action == "restart":
            stop_all(registry, quiet=True)
            action_start(registry, arguments.hidden, arguments.startup_timeout)
        elif arguments.action == "status":
            action_status(registry)
        elif arguments.action == "logs":
            action_logs(registry, arguments.service or None)
    except LauncherError as exc:
        warn(str(exc))
        return 1
    except KeyboardInterrupt:
        warn("interrupted")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
