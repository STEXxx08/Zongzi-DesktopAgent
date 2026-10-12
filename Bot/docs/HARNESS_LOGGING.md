# Harness 调用诊断日志

独立日志默认位于 `Bot/logs/harness.jsonl`。路径相对于 Bot 应用代码目录解析，不依赖启动目录。
启动 Bot 时初始化；设置位于 `Bot/config.yaml` 的 `service.harness`，支持配置刷新。
每行是一个 JSON 对象，中文使用 UTF-8 保存。该文件不传播到 GUI、聊天日志、TTS、历史或模型上下文。

## 记录范围

- `kind=tool`：共享 ToolRuntime 中的所有工具实际执行，包括内部调用、本地路由、Skills、MCP、浏览器及 Sandbox。
- `kind=model`：仅记录模型调用元数据、耗时、状态和 token 用量；不记录请求正文、system prompt、历史、环境、记忆或回复正文。本地 BGE 路由也记录耗时，用量为未知。
- `kind=protocol`：工具调用协议及其解析错误；普通聊天回复不生成通用工具协议记录。联网、Skill、MLLM 流式数据的解析错误仍保留诊断。

通常每次调用有 `event=started` 和 `event=finished`，使用相同的 call_id 关联。
工具开始记录包含参数；结束记录包含状态、耗时、结果和错误。工具沿用 ToolCall.call_id。
同一 turn 的中间工具调用也逐次记录，不因中间结果只进入 turn 缓存而省略；
不需要把这些结果写入 memory 才能记日志。正文仍受 include_payloads 和长度上限控制。
模型记录不包含 request/response 正文，模型 HTTP 错误也不保存响应正文。
对话模型记录 conversation_id、turn_id、round；通用协议记录 model_call_id，用于关联产生该回复的模型请求。
每次格式纠正是新的模型调用，单独记录耗时和 token，而不是混入前一次或虚增工具执行轮数。
旧协议的错误记录可能没有模型关联 ID；不伪造缺失的上下文。

工具耗时从进入 invoke 到返回，不包含执行器排队等待；流式模型耗时从开始消费请求到流结束，包含客户端消费时间。
未进入运行时的缓冲或准入拒绝仍由调度日志报告，不算实际执行。
父类/子类包装同一模型请求时不会重复记录或统计 token。

## 模型用量

LLMPrediction 和 MLLMPrediction 增加可选 usage 字段，保留服务商实际返回值。
Bot 的直连模型客户端、Core 的 DeepSeek 和 DashScope 适配器会透传该字段。
日志统一提供 usage.input_tokens、output_tokens、total_tokens，并保留 provider_usage 的缓存、推理等字段。
未提供 total_tokens 而输入/输出计数都存在时，只将两者相加；不根据字符数估算 token。
未返回用量时 status=unknown，计数为 null，不能当作零消耗；本地路由目前也不提供计数。
流式用量保留最后一次非空的累计 usage，不逐块累加。空回复的用量数据块也会处理。
失败或中断请求未必有最终计数，因此不保证可从日志得到全部计费消耗。

## 错误排查

通用工具 JSON 解析失败时，独立日志记录原始回复，以及 JSONDecodeError 的 reason、line、column、position 和 traceback。
工具处理函数抛出异常时，记录异常类型、消息及堆栈；HTTP 异常若带 response，额外记录状态码和响应正文。
普通控制台仍只输出简短提示，不把损坏指令送入用户输出。完整堆栈不包含局部变量转储。
标记缺失等无底层异常的协议失败记录原因和原始请求，不伪造堆栈。

## 设置与隐私

```yaml
service:
  harness:
    enable: true
    path: "logs/harness.jsonl"
    include_payloads: true
    max_payload_chars: 32000
    max_bytes: 10485760
    backup_count: 5
```

工具和协议诊断的文本字段默认最多 32000 字符，截断会标注原长度。模型流式调用只统计正文字符数，不缓存正文。
默认 10 MiB 轮转，保留 5 个备份：harness.jsonl.1 等。单个记录较大时，文件可能略超过阈值。
include_payloads=false 不记录请求/响应正文；enable=false 完全关闭写入。
常见密码、API Key、Authorization 和访问令牌字段自动隐藏，但无法保证识别任意自然语言中的秘密。
工具参数、结果及损坏的调用协议仍可能包含文件内容、网址或异常消息，请保护日志并在分享前检查。
以上限制只影响新写入的日志，不会自动清理旧文件或轮转备份。
`Bot/logs/` 已加入 `Bot/.gitignore`（规则 `logs/`）；配置自定义路径时请同步设置忽略规则。
注意：忽略规则只作用于新文件，**已经被提交过的日志仍留在 git 历史里**，需要单独清理历史。
线程内通过锁保证完整行写入；不支持多个 Bot 进程共用同一文件轮转，多实例应配置不同路径。
写入失败只报告简短诊断，不改变业务调用结果。

部署时需要同步更新共享 condata 包以及 Core、Bot；旧服务没有 usage 字段时客户端仍兼容，用量显示未知。