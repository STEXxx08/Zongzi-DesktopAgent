from pydantic import BaseModel, Field


class SandboxServiceConfig(BaseModel):
    enable: bool = Field(default=True, description="是否开放 Sandbox 文件工具。")
    root: str = Field(default="Sandbox", description="文件空间根目录，相对于 Bot 应用目录。")
    allow_write: bool = Field(default=True, description="是否允许创建文件和子目录。")
    allow_rename: bool = Field(default=True, description="是否允许在文件空间内重命名或移动，始终禁止覆盖目标。")
    allow_autonomous: bool = Field(default=True, description="是否预先授权模型自主使用 Sandbox，无需逐次请求确认。")
    allow_overwrite: bool = Field(default=False, description="是否允许显式覆盖已有文件。")
    allow_edit: bool = Field(default=False, description="是否允许 sandbox_edit 覆盖、前插入、后插入或替换已有文件内容（含替换为空）；独立于 sandbox_write 的覆盖权限，不创建或删除文件。")
    max_file_bytes: int = Field(default=10485760, ge=1, le=10485760, description="单个文本文件大小上限，默认 10 MiB。")
    max_total_bytes: int = Field(default=268435456, ge=1, description="文件空间总字节上限，默认 256 MiB。")
    max_entries: int = Field(default=1000, ge=1, le=100000, description="文件空间的文件和子目录数量上限。")
    max_list_entries: int = Field(default=100, ge=1, le=1000, description="单次列目录返回的数量上限。")