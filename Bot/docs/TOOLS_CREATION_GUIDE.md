# Tools 合法创建指引

Tool 是受信任代码提供的处理函数；Skill 是指导如何使用已有能力的指令。
本项目通过 ToolDefinition、ToolCall、ToolResult 和 ToolRuntime 注册及执行工具，
通过 ToolCoordinator 处理同一 turn 多轮调用，通过 ModelToolGateway 适配通用文本调用协议。

## 1. 先明确权限与入口

`ToolDefinition.model_invocable` 默认 false，只控制 `services/tool_model_gateway.py` 的通用文本入口。
它不是功能总开关，也不是“需要先加载 Skill 才能用”的开关。

| 场景 | model_invocable=false 的影响 |
| --- | --- |
| 通用模型目录与 `<tool_call>` | 不展示，并拒绝该工具的通用调用标记。 |
| 可信代码调用 ToolRuntime | 已注册工具仍可调用；处理函数继续检查权限。 |
| 已配置本地意图路由 | 仍能生成并执行 ToolCall，不由此标志决定。 |
| 原有 `<web_tool>` 联网协议 | 保持原协议的模式和路由控制，不由此标志决定。 |
| 原有 `<skill_tool>`、`<skill_create>` | 保持 Skills 的专用目录及创建权限控制，不由此标志决定。 |
| 仅仅加载一个 Skill | 不改变任何工具的通用开放状态或实际权限。 |

当前 Sandbox、MCP、浏览器工具显式 true；原有联网、Skills 内部工具保持 false，
因此从标签过滤改为显式元数据后，原有调用入口没有迁移或重复开放。
标记名称可由各服务配置调整。新工具没有特殊协议时，想允许模型直接调用应明确设为 true。

要真正禁用能力，应使用所属服务的功能/权限配置，在处理函数内拒绝禁用请求，或注销工具；
还需审核专用协议、远程服务和本地路由等其他入口。仅设置 false 不足以做到全局禁用。
注销不会保证已经开始执行的处理函数立刻停止；停止运行任务需要另外设计协作式取消。

## 2. 最小合法示例

把受信任的实现放在 `Bot/services/` 下合适的业务模块，在应用初始化阶段显式调用注册函数：

```python
from services.tool_runtime import ToolDefinition, ToolRuntime


def register_example_tool(runtime: ToolRuntime) -> None:
    def handler(args):
        if set(args) != {"text"}:
            raise ValueError("只接受 text 参数")
        text = args["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("text 必须为非空字符串且不超过 2000 字符")
        return {"text": text.upper()}

    runtime.register(ToolDefinition(
        name="uppercase_text",
        description="当用户需要将文本中的字母转换为大写时使用。输入最多 2000 字符，返回转换后的文本；不读写文件，不访问网络。",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 2000}},
            "required": ["text"],
            "additionalProperties": False,
        },
        tags=frozenset({"text", "read"}),
        model_invocable=True,
        handler=handler,
    ))
```

初始化时向 `register_example_tool` 传入 Bot 的共享 `tool_runtime`，不要另建一套运行时或对话循环。
模块放入目录本身不会自动注册，必须由可信应用初始化代码导入并调用注册函数。
模型在 Sandbox 中创建一个 `.py` 或 JSON 文件不会自动安装工具；当前没有自主代码执行或插件自动加载能力。
新增工具不需要修改通用网关，也不必增加识别它的专用标签判断。

## 3. 注册字段及处理约束

- `name`：匹配 `[a-z][a-z0-9_]{0,63}`，使用小写字母、数字和下划线，不使用 Skill 的连字符命名。
- `description`：说明适用场景、真实能力、权限、输出含义及失败处理；业务使用规则写在这里，不塞进通用网关。
- `input_schema`：明确类型、必填、长度和额外参数策略；当前运行时仅检查 required 是否缺失，
  不是完整 JSON Schema 验证器，处理函数必须再次验证类型、取值、长度和未知参数。
- `handler`：接受参数字典，推荐返回可 JSON 序列化的小对象。抛出异常由运行时记录为 ToolResult.error；
  禁止将令牌、密码或含凭据的异常信息返回模型或日志。
- `model_invocable`：必须为 Python 布尔值，默认 false；与标签无关。
- `execution_mode`：默认 buffered_merge，也可采用 exclusive；必须遵守项目调度器与上层模式选择。
- `tags`：分类用途，不能据此授予模型、网络、文件或执行权限。

工具名不得无意覆盖现有工具。默认重复注册报错；只有明确的配置刷新/替换流程使用 replace=True。
注销或服务禁用时及时更新注册表；权限校验要放在处理函数中，以覆盖已经解析或排队的旧请求。

涉及文件的工具应复用 Sandbox 的 RestrictedFilesystem，不接受任意系统路径，继续执行容量、覆盖和重命名保护。
涉及网页时复用现有公共 URL 检查与正文读取适配，不允许通过自建 requests 调用绕开既有边界。
涉及 MCP 时复用服务配置与白名单，不把 Sandbox 授权当成远程服务授权。
涉及用户数据或外部写入时，必须明确授权、错误反馈和超时，不在默认示例中开放 shell 或任意代码执行。

## 4. 调用与验证

模型的合法请求示例：

```text
<tool_call>{"name":"uppercase_text","arguments":{"text":"hello"}}</tool_call>
```

标记位于回复末尾，每次最多一个，不与其他专用协议混用。模型只应在执行结果成功后声称操作完成。
`service.tool.model_marker` 可配置标记，示例使用默认值。文本目录采用 name、description、arguments_schema；
不要把原生 tools API 的 type/function/parameters 包装当作文本调用，标记内不允许 XML 子标签。
这是项目独立定义、对所有模型一致的文本协议；外层标记不决定内部格式，内部固定为 name/arguments JSON。
`services.tool_model_gateway` 中的公共文本统一提供格式、安全和真实性规则。Bot 组合各入口的
`model_instruction(include_common=False)`，公共规则仅出现一次；入口单独使用时默认仍包含公共规则。
文本工具用 `tool_call`（name/arguments），联网工具用 `web_tool`（tool/query），
Skills 用 `skill_tool`（name）；标记名可配置，同一回复最多一个机器标记，各入口互斥。
匹配且尚未加载的 Skill 优先加载，后续工具在下一轮请求；目录与参数契约不变。
分组说明由各服务的 `model_instruction()` 提供，并在初始化网关时通过 `policy_providers`
集中接入一次；不逐工具附加公共说明，也不为提示词给 ToolDefinition 增加字段。
服务配置更新后提供最新说明，禁用时不输出对应分组。
上一版 `<harness_tool_call>` 合法 JSON 保留解析兼容，提示只展示当前配置标记。
业务服务无需为此工具写独立的标记解析器。

可信内部代码可创建 `ToolCall(name="uppercase_text", arguments={"text":"hello"})` 并调用共享运行时；
需要对话追踪时由上层附加 conversation_id、turn_id、round，勿在工具内自行创建新对话轮次。
同一 turn 的工具轮数、并发数和结果上下文截断由 `service.tool` 配置控制。
通用工具的执行记录包含工具名、请求参数、成功/失败状态和结果；参数与结果分别受
`service.tool.max_result_chars` 限制。同一 turn 的中间回复和执行记录保存在
`SearchRequest.turn_history` 中，随工具回调传递给下一次模型请求；不进入共享对话历史或 memory。
turn 结束后仅将原始用户输入与最终助手回复写入对话历史并触发记忆保存。
下一 turn 不继承上一 turn 的工具缓存；当前时间和屏幕环境也不进入缓存。
工具运行时仍将每轮的实际参数、结果、状态及关联 ID 写入 Harness 日志，与 memory 保存互相独立。
这些记录明确标记为应用工具数据，不是用户指令；执行状态以工具返回值为依据。
通用协议提示只定义调用契约和权限边界，不加入针对某个用户任务的补救要求。

上线前至少验证：正常输入；缺失、错误类型及超长输入；重复注册；异常结果；禁用/注销后的资格；
写入/网络权限；配置刷新；跨轮结果和 TTS/历史；工具描述与实际返回的一致性。
大内容应存档并提供受限分段读取，而不是无上限地注入模型上下文。
通用协议解析失败的纠正次数由 service.tool.max_parse_retries 控制；业务工具不应自行解释损坏 JSON。
Sandbox 编辑与复制应复用 sandbox_edit、sandbox_copy；allow_edit 和 allow_overwrite 是独立权限，不能互相绕过。
文件编辑统一使用 sandbox_edit：overwrite 整文件覆盖、append 末尾插入、prepend 开头插入、replace 替换。
replace 省略 old_text 为整文件替换，提供 old_text 为精确匹配替换；旧调用无需改变。
清空文件内容使用 `{"path":"note.md","mode":"replace","new_text":""}`，无需回传原文。
所有编辑模式均依赖 allow_edit 和 allow_write，保留已有文件；allow_overwrite 只控制 sandbox_write 的覆盖操作。
JSON 空字符串使用 `""`，不得省略参数值。文件操作返回成功后才向用户确认已完成。
网页任务默认先用 browser_read 或非持久 browser_research 返回正文，再由模型选择、编辑并用 research_save 保存，
不要把“搜到了网页”理解为“用户授权将所有原文永久保存”。

## 5. Skill 与 Tool 配合

先由可信开发者实现并注册安全工具，再创建 Skill 描述使用步骤。
Skill 无法将内部工具变为通用模型工具，也无法创建新的执行权限；
如果确有必要，可由一个已开放的受控组合工具在处理函数中调用内部工具，并自行校验权限。
这是组合工具的显式实现，不是通过 Skill 规避隐藏工具限制。