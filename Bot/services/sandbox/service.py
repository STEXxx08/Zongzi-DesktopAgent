from pathlib import Path

from services.sandbox.config import SandboxServiceConfig
from services.sandbox.filesystem import RestrictedFilesystem
from services.tool_runtime import ToolDefinition, ToolRuntime


class SandboxService:
    """将受限文件操作注册到共享工具运行时。"""

    def __init__(self, config: SandboxServiceConfig, runtime: ToolRuntime, *, project_root: Path):
        self.runtime = runtime
        self.project_root = project_root
        self.filesystem = RestrictedFilesystem(config, project_root=project_root)
        self.update_config(config)

    def update_config(self, config: SandboxServiceConfig):
        self.filesystem.update_config(config, project_root=self.project_root)
        # 刷新时也注销已废弃的清空工具，避免旧实例残留重复入口。
        for name in ("sandbox_list", "sandbox_read", "sandbox_write", "sandbox_mkdir", "sandbox_rename", "sandbox_copy", "sandbox_edit", "sandbox_clear"):
            self.runtime.unregister(name)
        if not config.enable:
            return
        operations = [
            ("sandbox_list", "列出 Sandbox 内的目录，不递归。", self.filesystem.list_directory, False),
            ("sandbox_read", "读取 Sandbox 内 UTF-8 文本文件。", self.filesystem.read_text, False),
        ]
        if config.allow_write:
            operations.extend([
                ("sandbox_write", "写入 UTF-8 文本文件，默认只创建新文件。", self.filesystem.write_text, True),
                ("sandbox_mkdir", "创建 Sandbox 内的子目录。", self.filesystem.make_directory, True),
                ("sandbox_copy", "复制 UTF-8 文件；目标必须不存在，修改副本用 sandbox_edit。", self.filesystem.copy_text, True),
            ])
            if config.allow_edit:
                operations.append((
                    "sandbox_edit",
                    '编辑已有 UTF-8 文件，不创建新文件。mode 默认 replace：省略 old_text 为整文件替换；'
                    '提供 old_text 则要求唯一匹配，replace_all=true 才替换全部。'
                    'overwrite 整文件覆盖、append 末尾插入、prepend 开头插入；这三种模式不传 old_text 或 replace_all=true。'
                    'new_text 必填，空字符串可清空内容并保留文件；插入不自动添加换行。',
                    self.filesystem.edit_text, True,
                ))
            if config.allow_rename:
                operations.append((
                    "sandbox_rename", "在 Sandbox 内重命名或移动文件和目录；目标必须不存在。",
                    self.filesystem.rename, True,
                ))
        for name, description, handler, write in operations:
            properties = {"path": {"type": "string", "description": "相对于 Sandbox 根目录的路径"}}
            required = [] if name == "sandbox_list" else ["path"]
            if name == "sandbox_list":
                properties["path"].update(default=".", description="相对于 Sandbox 根目录的目录路径；省略或 . 表示根目录，不要猜测子目录名称")
                properties.update({"offset": {"type": "integer", "minimum": 0},
                                   "descending": {"type": "boolean"}})
            elif name == "sandbox_write":
                properties.update({"content": {"type": "string"}, "overwrite": {"type": "boolean"}})
                required.append("content")
            elif name in {"sandbox_rename", "sandbox_copy"}:
                properties["destination"] = {"type": "string", "description": "相对于 Sandbox 根目录的新路径"}
                required.append("destination")
            elif name == "sandbox_edit":
                properties.update({
                    "mode": {"type": "string", "enum": ["overwrite", "append", "prepend", "replace"],
                             "default": "replace", "description": "覆盖、末尾插入、开头插入或替换"},
                    "old_text": {"type": "string", "minLength": 1,
                                 "description": "仅 replace 模式可选；省略为整文件替换，提供则匹配替换"},
                    "new_text": {"type": "string", "description": '要写入的新文本；允许空字符串 ""，不可省略'},
                    "replace_all": {"type": "boolean", "default": False,
                                    "description": "仅提供 old_text 时可设为 true；替换所有匹配"},
                })
                required.append("new_text")
            self.runtime.register(ToolDefinition(
                name=name, description=description,
                input_schema={"type": "object", "properties": properties, "required": required,
                              "additionalProperties": False},
                tags=frozenset({"sandbox", "write" if write else "read"}),
                model_invocable=True,
                handler=lambda args, operation=handler: operation(**args),
            ))

    def model_instruction(self) -> str | None:
        config = self.filesystem.config
        if not config.enable:
            return None
        permission = (
            "权限已预先授予：允许自主使用，包括主动交流期间，无需逐次向用户请求确认。"
            if config.allow_autonomous else "仅在当前用户任务需要时使用。"
        )
        return (
            "【Sandbox 公共规则】" + permission
            + "这是目录级文件权限，不是代码执行环境；路径相对于 Sandbox 根目录，不允许越界、删除或执行代码。"
            "预授权不绕过配置：write 覆盖须同时满足 allow_overwrite=true 和请求 overwrite=true；"
            "edit 修改已有文件使用 allow_edit，与 allow_overwrite 独立；所有写操作还须 allow_write 并受容量和条目限制。"
        )