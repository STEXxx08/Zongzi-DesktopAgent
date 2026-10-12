import os
import re
import stat
import sys
import tempfile
import threading
from pathlib import Path, PureWindowsPath

from loguru import logger

from services.sandbox.config import SandboxServiceConfig


class RestrictedFilesystem:
    """应用层文件权限边界；不提供任意代码执行或操作系统级隔离。"""

    def __init__(self, config: SandboxServiceConfig, *, project_root: Path):
        self._lock = threading.RLock()
        self.update_config(config, project_root=project_root)

    def update_config(self, config: SandboxServiceConfig, *, project_root: Path):
        with self._lock:
            base = project_root.resolve()
            configured = Path(config.root).expanduser()
            root = configured if configured.is_absolute() else base / configured
            if root.is_symlink() or root.resolve() != root.absolute():
                raise ValueError("文件空间根目录不得包含符号链接或路径跳转")
            root = root.resolve()
            if root == Path(root.anchor) or root == base or root in base.parents:
                raise ValueError("文件空间不能设置为系统根目录、应用根目录或其父目录")
            self.config = config
            self.root = root

    def _path(self, value: str, *, allow_root: bool = False) -> Path:
        if not self.config.enable:
            raise PermissionError("文件空间已禁用")
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError("路径必须为有效字符串")
        if "\\" in value or ":" in value or PureWindowsPath(value).drive:
            raise PermissionError("路径必须使用 Sandbox 内的相对路径和正斜杠")
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise PermissionError("拒绝访问文件空间之外的路径")
        for part in relative.parts:
            if part.endswith((".", " ")) or re.fullmatch(
                r"(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\..*)?", part
            ):
                raise ValueError("路径包含不支持的文件名")
        path = self.root / relative
        if path == self.root and not allow_root:
            raise ValueError("必须指定文件或子目录")
        for candidate in (self.root, *path.parents, path):
            if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
                raise PermissionError("拒绝通过链接访问文件空间")
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise PermissionError("拒绝访问文件空间之外的路径")
        if path.exists():
            info = path.stat()
            if not stat.S_ISDIR(info.st_mode) and not stat.S_ISREG(info.st_mode):
                raise PermissionError("仅支持普通文件和目录")
            if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
                raise PermissionError("拒绝访问硬链接文件")
        return path

    def list_directory(self, path: str = ".", offset: int = 0, descending: bool = False) -> dict:
        with self._lock:
            if type(offset) is not int or offset < 0 or type(descending) is not bool:
                raise ValueError("offset 必须是非负整数，descending 必须是布尔值")
            directory = self._path(path, allow_root=True)
            if not directory.exists() and directory == self.root:
                return {"path": ".", "entries": [], "truncated": False}
            if not directory.is_dir():
                raise NotADirectoryError(path)
            entries = []
            truncated = False
            skipped_links = 0
            children = sorted(directory.iterdir(), key=lambda child: child.name, reverse=descending)
            for child in children[offset:]:
                if len(entries) >= self.config.max_list_entries:
                    truncated = True
                    break
                try:
                    self._path(child.relative_to(self.root).as_posix())
                except PermissionError:
                    # 单个符号链接/联接只跳过它自己，不再让整个列目录失败（P1-10）
                    skipped_links += 1
                    continue
                entries.append({
                    "path": child.relative_to(self.root).as_posix(),
                    "type": "directory" if child.is_dir() else "file",
                    "bytes": child.stat().st_size if child.is_file() else 0,
                })
            if skipped_links:
                logger.warning(
                    f"列目录时跳过了 {skipped_links} 个链接类条目（符号链接/联接）：{path}"
                )
            return {"path": path, "entries": entries, "truncated": truncated,
                    "skipped_symlinks": skipped_links,
                    "next_offset": offset + len(entries) if truncated else None}

    def read_text(self, path: str) -> dict:
        with self._lock:
            target = self._path(path)
            with target.open("rb") as stream:
                data = stream.read(self.config.max_file_bytes + 1)
            if len(data) > self.config.max_file_bytes:
                raise ValueError("文件超过单文件大小上限")
            return {"path": path, "content": data.decode("utf-8"), "bytes": len(data)}

    def _usage(self) -> tuple[int, int]:
        total = 0
        count = 0
        skipped_links = 0
        if not self.root.exists():
            return total, count
        for directory, subdirs, files in os.walk(self.root, followlinks=False):
            for name in [*subdirs, *files]:
                try:
                    target = self._path((Path(directory) / name).relative_to(self.root).as_posix())
                except PermissionError:
                    # 链接类条目不计入容量统计，也不让整体统计失败（P1-10）
                    skipped_links += 1
                    continue
                count += 1
                if target.is_file():
                    total += target.stat().st_size
                if count > self.config.max_entries or total > self.config.max_total_bytes:
                    raise ValueError("文件空间已超过容量或条目上限")
        if skipped_links:
            logger.warning(f"容量统计跳过了 {skipped_links} 个链接类条目（符号链接/联接）。")
        return total, count

    def _check_write(self):
        if not self.config.allow_write:
            raise PermissionError("当前文件空间只允许读取")

    @staticmethod
    def _rename_no_replace(source: Path, destination: Path):
        if os.name == "nt":
            os.rename(source, destination)
            return
        if not sys.platform.startswith("linux"):
            raise NotImplementedError("当前平台尚未提供保证不覆盖目标的安全重命名实现")
        import ctypes

        library = ctypes.CDLL(None, use_errno=True)
        operation = getattr(library, "renameat2", None)
        if operation is None:
            raise NotImplementedError("当前系统不支持不覆盖目标的 renameat2")
        operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        operation.restype = ctypes.c_int
        result = operation(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
        if result != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(source))

    def rename(self, path: str, destination: str) -> dict:
        with self._lock:
            source = self._path(path)
            target = self._path(destination)
            self._check_write()
            if not self.config.allow_rename:
                raise PermissionError("当前配置不允许重命名")
            if not source.exists():
                raise FileNotFoundError("重命名源文件或目录不存在")
            if source == target:
                raise ValueError("重命名目标必须与源路径不同")
            if target.exists():
                raise FileExistsError("重命名目标已存在，禁止覆盖")
            if not target.parent.is_dir():
                raise FileNotFoundError("目标父目录不存在，请先调用创建目录工具")
            if source.is_dir() and target.is_relative_to(source):
                raise ValueError("不能把目录移动到自身的子目录")
            self._rename_no_replace(source, target)
            return {"path": path, "destination": destination, "renamed": True}

    def make_directory(self, path: str) -> dict:
        with self._lock:
            target = self._path(path)
            self._check_write()
            _, count = self._usage()
            missing = sum(not parent.exists() for parent in (target, *target.parents)
                          if parent != self.root and parent.is_relative_to(self.root))
            if count + missing > self.config.max_entries:
                raise ValueError("创建目录会超过条目上限")
            target.mkdir(parents=True, exist_ok=True)
            return {"path": path, "created": True}

    def copy_text(self, path: str, destination: str) -> dict:
        """复制 UTF-8 文本，目标必须不存在；避免让模型重新生成整个文件。"""
        with self._lock:
            content = self.read_text(path)["content"]
            result = self.write_text(destination, content, overwrite=False)
            return {**result, "source": path, "destination": destination}

    def edit_text(self, path: str, old_text: str | None = None, new_text: str | None = None,
                  replace_all: bool = False, mode: str = "replace") -> dict:
        """编辑已有文件；保留旧位置参数，支持整文件替换、追加、前置和精确替换。"""
        with self._lock:
            if not self.config.allow_edit:
                raise PermissionError("当前配置不允许修改已有文件，请开启 allow_edit")
            self._check_write()
            if not isinstance(new_text, str) or type(replace_all) is not bool:
                raise ValueError('new_text 必须为字符串（清空请显式传入 ""），replace_all 必须为布尔值')
            if not isinstance(mode, str) or mode not in {"overwrite", "append", "prepend", "replace"}:
                raise ValueError("mode 必须为 overwrite、append、prepend 或 replace")
            if old_text is not None and (not isinstance(old_text, str) or not old_text):
                raise ValueError("old_text 若提供，必须为非空字符串；整文件替换请省略 old_text")
            if mode != "replace" and old_text is not None:
                raise ValueError("只有 replace 模式允许 old_text；其他模式针对整个文件")
            if replace_all and (mode != "replace" or old_text is None):
                raise ValueError("replace_all 仅用于提供 old_text 的匹配替换")
            content = self.read_text(path)["content"]
            replacements = None
            if mode == "replace" and old_text is not None:
                matches = content.count(old_text)
                if matches == 0 or (not replace_all and matches != 1):
                    raise ValueError(f"原文匹配数为 {matches}；默认必须唯一匹配，请先读取文件再修改")
                updated = content.replace(old_text, new_text, -1 if replace_all else 1)
                replacements = matches if replace_all else 1
            elif mode == "append":
                updated = content + new_text
            elif mode == "prepend":
                updated = new_text + content
            else:
                updated = new_text
            result = self._write_text(path, updated, overwrite=True, editing=True)
            return {**result, "mode": mode, "scope": "matching_text" if old_text is not None else "file",
                    **({"replacements": replacements} if replacements is not None else {})}

    def write_text(self, path: str, content: str, overwrite: bool = False) -> dict:
        return self._write_text(path, content, overwrite=overwrite)

    def _write_text(self, path: str, content: str, overwrite: bool = False, *, editing: bool = False) -> dict:
        with self._lock:
            target = self._path(path)
            self._check_write()
            if not isinstance(content, str) or not isinstance(overwrite, bool):
                raise ValueError("content 必须为字符串，overwrite 必须为布尔值")
            data = content.encode("utf-8")
            if len(data) > self.config.max_file_bytes:
                raise ValueError("内容超过单文件大小上限")
            existed = target.exists()
            if editing and not existed:
                raise FileNotFoundError("编辑目标不存在，未创建新文件")
            if editing and not self.config.allow_edit:
                raise PermissionError("当前配置不允许编辑文件")
            if existed and not (overwrite and (self.config.allow_overwrite or (editing and self.config.allow_edit))):
                raise FileExistsError("文件已存在；当前配置或请求未允许覆盖")
            total, count = self._usage()
            old_bytes = target.stat().st_size if existed else 0
            if total - old_bytes + len(data) > self.config.max_total_bytes:
                raise ValueError("写入会超过文件空间总容量上限")
            if count + int(not existed) > self.config.max_entries:
                raise ValueError("写入会超过条目上限")
            if target.parent == self.root:
                self.root.mkdir(parents=True, exist_ok=True)
            if not target.parent.is_dir():
                raise FileNotFoundError("父目录不存在，请先调用创建目录工具")
            if not existed:
                with target.open("xb") as stream:
                    stream.write(data)
            else:
                descriptor, temp_path = tempfile.mkstemp(prefix=".sandbox-", dir=target.parent)
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(data)
                    self._path(path)
                    os.replace(temp_path, target)
                finally:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
            return {"path": path, "bytes": len(data), "created": not existed}