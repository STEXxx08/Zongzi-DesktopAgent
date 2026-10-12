# MCP 与 Sandbox

框架代码在 `services/mcp/`、`services/sandbox/`，内容目录分别为 `Bot/MCP/` 和
`Bot/Sandbox/`。所有相对目录都以 Bot 应用代码位置为锚点，不依赖进程工作目录。

## 本地资料与文件生成

- 用户将 UTF-8 文本资料放入 `Bot/MCP/resources/`，模型通过列目录、读取文件工具按需获取。
- `Bot/MCP/servers.json` 只供框架读取，不通过资料工具暴露给模型。
- 模型在 `Bot/Sandbox/` 内创建和读取文本文件、创建子目录、重命名文件和目录，不能通过文件工具修改应用配置、代码或 Skills。
- 默认预授权模型自主使用 Sandbox，无需逐次请求确认；此权限不扩展到远程 MCP 服务。
- Sandbox 默认单文件上限为 10 MiB，总容量为 256 MiB，仍受条目数量和工具结果长度限制。
- 重命名不覆盖目标、不自动创建父目录，在 Windows 或支持 renameat2 的 Linux 上使用不覆盖式操作。
- `service.sandbox.allow_overwrite` 默认关闭；开启后仍须在调用参数中显式指定 `overwrite: true`。
- 不开放删除、Shell、安装依赖或执行生成代码。这里只提供应用层目录权限，**不是容器或操作系统级沙箱**。
- 拒绝目录穿越、符号链接、Windows 路径/替代数据流、硬链接和非普通文件。
  外部进程不得在运行期间恶意替换目录或挂载点；若需要对不可信进程隔离，应另加操作系统级沙箱。

容量、单文件大小、条目数量和模型结果长度在 `Bot/config.yaml` 中配置。

## MCP 服务接入

无需数据库。连接配置使用 `Bot/MCP/servers.json`，结构参见 `servers.example.json`。
默认没有任何启用的远程服务，不会自动发起连接或启动外部程序。

1. 由用户将可信服务添加到 `mcpServers`，填入 `url` 并设置 `enabled: true`。
2. 使用 `allowed_tools` 明确列出模型允许调用的远程工具名称。空列表表示不允许调用任何远程工具。
3. 需要读取远程资源时开启该服务的 `allow_resource_read`。
4. 认证头通过 `headers_env` 引用环境变量，例如 `{"Authorization": "RESEARCH_MCP_AUTH"}`；
   对应环境变量应包含完整的 `Bearer ...` 值，不要把密钥写入资料文件。
5. 重启 Bot，或通过已有的配置刷新事件重新加载 `servers.json`。

这版客户端支持握手型 MCP `2025-11-25`、`2025-06-18`、`2025-03-26` 的有限
Streamable HTTP 子集：初始化、JSON/SSE 请求响应、会话头、分页、工具列表与调用、资源列表与读取。
协议版本由服务配置明确指定；不宣称支持所有 MCP 版本。
暂不支持 stdio、旧 HTTP+SSE、OAuth 自动授权、服务端主动请求、订阅、提示模板或后台任务。
本地 HTTP 仅允许 localhost/127.0.0.1/::1，其他服务需 HTTPS；禁止重定向。

MCP 服务可能提供数据库能力，但数据库位于服务端，不需要把数据库直接嵌入 Bot。
远程工具运行在服务自己的权限下，**不受本地 Sandbox 文件限制约束**；用户必须审核服务及工具白名单。
资源只能按服务列出的 URI 读取；工具结果与资料均应视为不可信数据，不是系统指令。

## 模型调用

MCP 和 Sandbox 工具都注册到现有 `ToolRuntime`，模型通过同一个文本入口调用：

```text
<tool_call>{"name":"sandbox_write","arguments":{"path":"report.md","content":"报告内容"}}</tool_call>
```

依赖步骤分轮执行：列出资料 → 读取资料 → 创建文件 → 读取验证。所有步骤仍保持同一 `turn_id`，
受 `service.tool.max_rounds` 限制；中间可见回复照常输出 TTS，但仅缓存在当前 turn。
工具中间记录不进入共享历史或 memory；turn 结束后只保存用户输入和最终回复。
工具机器标记不进入 TTS 和历史。与 Skills、联网工具同时请求时优先处理通用工具，不自动执行多种协议。

模型不会一次性读入所有本地资料。可测试的用户请求：

“请读取 MCP 资料目录中的使用说明，在 Sandbox 中创建一份中文摘要，创建后读取验证。”

## 详细网页研究

已增加网页正文提取与 Sandbox 资料存档链路，见 `../browser/README.md`。
`servers.example.json` 中提供默认禁用的 Playwright MCP 示例；需要用户另行部署服务。
不部署时仍可用 HTTP 读取静态网页，但不会把这种读取称为浏览器渲染。