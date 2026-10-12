# 通用模型工具入口

实现位于同级模块 `tool_model_gateway.py`，类名保持 `ModelToolGateway`。
原模块 `model_tools.py` 已改名，不保留旧导入别名。

## 职责边界

入口只负责从 ToolRuntime 生成模型工具目录、提供通用文本调用协议、解析 `<tool_call>`、
检查调用资格并分离可见回复。执行、调度、工具轮次、文件权限分别由原有运行时、调度器和服务负责。
工具参数 Schema 会提供给模型，但入口不宣称实现完整的 JSON Schema 校验；实际处理函数仍需验证输入。

默认标记为 `tool_call`，由 `service.tool.model_marker` 配置并支持热更新。
它只是请求边界，内部固定为 `{"name":"工具名","arguments":{}}` JSON。
协议由项目独立定义，对所有模型一致；常规提示与格式纠正共用 protocol_instruction() 的同一份契约。
文本目录统一使用 ToolDefinition.text_schema() 的 name、description、arguments_schema，
不使用原生 tools API 的 type/function/parameters 包装；ToolDefinition.model_schema() 保留给原生 API 使用。
通过明确外层标记、顶层字段和参数类型区分调用格式，而非依赖标记名称区分。
上一版 `<harness_tool_call>` 中的合法 JSON 仍兼容；模型提示只展示当前配置的标记。
开闭标记必须匹配；发现 XML 格式时给出专门诊断及当前协议的纠正示例，不猜测、转换或执行 XML 参数。

通用标记使用 JSON；内容换行、引号需转义。兼容 JSON 代码块及字符串中的未转义控制字符，
但不猜测缺失引号、单引号对象或任意损坏结构，也不执行部分解析结果。
普通错误日志只提供语法位置/结构错误，不记录参数正文；独立 Harness 日志按配置记录原始回复和详细异常。
Bot 会按 service.tool.max_parse_retries
请求模型纠正，默认 1 次，0 关闭；纠正不执行工具、不消耗工具轮数、不记录错误调用到历史或 TTS。
LLM/MLLM 均支持此流程，超过次数仍失败时明确提示本次未执行，而不只输出“正在处理”。

JSON 错误发生在模型回复的协议解析阶段，还没有进入文件处理函数；清空文件本身不解析 JSON。
错误日志提供位置及有限的中文语法原因，不包含文件正文或模型参数。
缺失标记的工具形状 JSON、损坏或未闭合的通用标记不会被猜测执行；只能请求模型重新输出合法调用。

可见输出统一经过机器协议隐藏处理。Bot 按实际配置传入联网、Skill 加载及创建标记，
通用入口不写入这些业务协议名称。普通输出、流式日志、TTS 和历史使用同一个出口规则；
流式片段中的标签、JSON 和代码围栏在判定前暂存，避免半截指令泄漏。
发现机器标记后保守隐藏从标记开始的尾部（标记应位于回复末尾），未标记的 name/arguments
工具形状对象也隐藏但不执行；普通 JSON 数据与代码块不受此规则删除，可能延迟到完整后才输出。

## 注册新工具

```python
runtime.register(ToolDefinition(
    name="example_tool",
    description="说明用途、适用场景、限制、输出含义和必要的后续步骤。",
    input_schema={"type": "object", "properties": {}},
    handler=handle_example,
    model_invocable=True,
))
```

`model_invocable` 默认 false：内部工具和仍使用旧专用调用协议的工具不会自动暴露。
显式设为 true 后，无论标签或类别是什么，都会通过该通用入口提供给模型。
标签仅用于分类，不授予调用资格。该标志不替代处理函数里的运行时权限检查。

false 仅关闭通用文本调用的目录展示与解析资格，不注销工具，不改变
ToolRuntime 内部调用、本地路由或旧专用模型协议。原有联网工具继续使用 `<web_tool>`，
Skills 加载与创建继续使用 `<skill_tool>`、`<skill_create>`（标记可配置）。
加载 Skill 不会自动开放隐藏工具；Skill 自身的调用控制字段是另一层独立设置。
要禁用整项能力，应使用所属服务的 enable/权限配置，或注销工具并确保其他入口也被关闭。

独立创建指引见 `../docs/SKILLS_CREATION_GUIDE.md` 和 `../docs/TOOLS_CREATION_GUIDE.md`。

浏览器、MCP 和 Sandbox 已在自己的服务注册中明确开放。
浏览器使用规则写在各工具的 description 中；Sandbox 的自主授权说明由其配置生成描述，
MCP 的远程权限说明由 MCP 服务提供。通用入口不包含这些具体名称、类别或业务规则。
注销工具后目录与解析资格立即更新；服务禁用后也不会残留该服务的使用提示。