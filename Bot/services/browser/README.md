# 详细网页研究与 Sandbox 资料

该服务接在现有 ToolRuntime、同一 turn 工具循环和 Sandbox 权限层上，不替换轻量联网查询。
工具注册时显式设置 `model_invocable=True`，使用规则放在各工具的 description 中；
通用入口 `services/tool_model_gateway.py` 不识别浏览器名称或标签，也不包含研究专用提示。
无需添加 Python 依赖。搜索发现仍使用已有 Core/SearXNG 接口，必须保证原有搜索服务可用。
也可以提供明确的网页 URL，跳过搜索阶段。

## 能力与工具

- `browser_status`：查看后端与存档权限；配置存在不等于服务已连通。
- `browser_search(query)`：搜索标题、链接、摘要，用于挑选来源，不是假定的全文。
- `browser_research(query, urls?, save_raw?)`：搜索候选网页或读取指定 URL，默认仅在内存临时保存正文，
  返回来源、正文预览与 page_id，不自动写入 Sandbox。显式 save_raw=true 可归档网页原文。
  一次工具调用内部处理最多 `max_pages` 个来源，避免搜索、逐页读取、存档耗尽工具轮数。
- `browser_read(url 或 page_id, offset?, limit?)`：指定 URL 读取正文，或用 page_id 分段读取同一临时快照，
  默认每段最多 10000 字符；next_offset 非空时可继续。不保存文件，也不把全文强行塞入模型上下文。
- `research_save(query, content, page_ids)`：只保存模型已经筛选、编辑的整理稿，并根据实际读取记录附带真实
  来源 URL、标题和抓取时间。不会复制原始网页正文；保存后进入 research_catalog。
- `research_catalog(query?)`：查找已存资料的主题和来源标题，不是语义或全文检索。
- `research_read(path, offset?, limit?)`：按字符分段读取资料，使用 `next_offset` 继续，直到为 null。

用户可以说：“请详细搜索某个主题，优先读取官方资料，只保存与任务有关的整理稿，供后续讨论。”
模型可以先用 `browser_search` 挑选来源，再传给 `browser_research` 的 `urls`；
也可直接由研究工具使用搜索结果。推荐流程为搜索 → 读取正文 → 模型筛选与编辑 → 保存整理稿。
模型仍需自行检查来源质量、事实一致性和资料是否充分；来源元数据存在不代表模型验证过每个结论。

资料默认结构（以 Bot 应用目录为基准，与启动目录无关）：

```text
Sandbox/research/<UTC时间>-<唯一标识>/
  notes.md                 # research_save 保存的模型整理稿
  manifest.json
```

仅当启用原文归档时，目录内会保存 source-01.md、source-02.md 等原文资料，不是浏览器原始页面备份，
也不包含图片或附件。默认研究结果留在有数量与有效期上限的内存缓存；page_id 过期、被淘汰、重载配置
或重启后失效，需要重新按 URL 读取。建议 max_cached_pages 不小于 max_pages，避免一批来源被立即淘汰。
临时正文不计入 Sandbox 容量，也不会自动变成长期记忆。

只有用户或模型明确保存的资料才持久存在。保存后的 Markdown 与索引带有来源、UTC 抓取时间与截断信息，
后续使用 research_read 读取。紧凑索引插入动态环境上下文，不改变系统提示的缓存前缀；重启后仍可发现。

网页或存档失败逐项报告；部分成功返回 partial，全部失败返回 failed；只读成功返回 read，存档成功返回 saved。
若正文保存成功但容量不足导致索引写入失败，返回已存文件路径，供直接读取；不会自动删除已存文件。
每次使用独立目录，不覆盖已有资料；所有写入受 Sandbox 的启用、写入、容量与条目限制约束。
禁用 Sandbox 写入后不注册 research_save，原文存档请求也会被拒绝，但 browser_read 和默认非持久研究仍可用。
禁用 Sandbox 不关闭网页只读工具；只有禁用 browser 服务才撤销所有网页工具。

`save_raw_by_default` 默认 false，可配置为 true 恢复此前自动归档行为。单次显式 save_raw 参数优先于配置。
`max_cached_pages` 默认 20，`cache_ttl_seconds` 默认 900 秒；整理稿使用的 page_ids 必须仍在有效期内。
普通 sandbox_write 也可保存模型编辑的文本，但不会自动加入研究目录索引；需要后续目录发现时推荐 research_save。

## 两种读取后端

`service.browser.backend` 可设为 `http`、`mcp` 或 `auto`（默认）。

- HTTP：通过标准库读取 HTML/纯文本，优先提取 article/main，过滤常见脚本、导航等。
  不执行 JavaScript，不是控制用户浏览器。动态页面、登录墙、反爬页面可能失败或提取不完整；
  提取非空不等于信息充分，模型必须检查正文。PDF、二进制下载暂不支持。
- MCP：连接已配置的 Playwright MCP，先导航，再通过固定只读函数提取当前可见正文。
  不接受模型提供的 JavaScript；不支持本研究接口内点击、表单提交、登录或下载。
  导航和提取共享会话锁，防止本客户端并发操作交叉。
- 自动：只有指定 MCP 服务已启用且白名单含两个必需工具时才选择 MCP，否则使用 HTTP。
  已配置的 MCP 若连接失败会如实报告，不会悄悄改称 HTTP 为浏览器结果。

单页下载大小、正文长度、超时、跳转、预览、读取分段、目录条目数量等均在
`Bot/config.yaml` 的 `service.browser` 设置。HTTP 网络读取期限不包含系统 DNS 查询耗时；
MCP 的请求超时和响应大小由 `service.mcp` 控制。超出正文长度上限会标注截断，下载超限则失败。

## 可选 Playwright MCP 配置

需要在用户机器上另行安装、启动受信任的 Playwright MCP 服务；Bot 不自动下载或运行它。
例如启动独立、未登录的浏览器会话：

```bash
npx @playwright/mcp@latest --port 8931 --isolated --headless --snapshot-mode none
```

将 `MCP/servers.example.json` 的 `playwright` 配置合并到 `MCP/servers.json`，将该项
`enabled` 设为 true，然后重启 Bot 或触发配置重载。不要覆盖文件中其他已配置服务。
当前适配器需要 `browser_navigate` 和 `browser_evaluate` 两个工具，HTTP 连接地址为
`http://127.0.0.1:8931/mcp`。不要使用 stdio 配置；本项目 MCP 客户端仅支持 Streamable HTTP。
`--snapshot-mode none` 避免导航时附带过大的可访问性快照；正文由后续固定函数读取。
实际部署时应固定经验证的服务版本，不依赖 latest 保证兼容。

## 权限边界

HTTP URL 仅限公共 HTTP/HTTPS、80/443 端口；拒绝凭据、本机、内网、保留 IP 和混合 DNS。
每次跳转重新校验，连接固定到已校验 IP，HTTPS 仍校验原域名证书；不使用系统代理绕开校验。
但这是应用层读取约束，不是操作系统级隔离。

MCP 目标和最终 URL 也会校验，但远程浏览器的跳转、页面子请求、下载及网络访问
不能由本地 URL 检查完全约束。应使用独立的无登录会话及服务端网络出口隔离；
不要连接用户已登录的个人浏览器。Playwright MCP 本身不构成安全边界。
MCP 白名单开放 browser_evaluate 同时意味着通用 mcp_call 可调用它；
本研究适配器的固定提取函数并不限制用户显式授权的通用 MCP 接口能力。

外部正文、标题和存档索引都视为不可信数据，不能作为系统指令或新权限来源。
本服务不绕过登录、访问限制或反爬措施。Sandbox 权限不扩展到远程浏览器所在的文件系统。