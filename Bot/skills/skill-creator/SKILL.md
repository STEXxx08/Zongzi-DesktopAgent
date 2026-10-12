---
name: skill-creator
description: 创建新的可复用 Skill；当用户要求新增、沉淀或修改工作流程时使用
whenToUse: 用户明确要求创建 Skill，或希望把当前流程保存为以后可复用的能力时
metadata:
  version: 1
  format: deepseek-harness
disable-model-invocation: false
user-invocable: true
---

将用户需求整理成安全、清晰、可复用的 Skill。

创建前确认名称、用途、触发条件、必要步骤和限制；只有关键信息无法合理推断时才询问用户。

- `name` 使用 kebab-case，只能包含小写字母、数字和连字符。
- `description` 简洁说明用途，`when_to_use` 说明适用场景和触发条件。
- `content` 包含可直接执行的完整指令、真实限制和必要的失败处理，避免重复通用常识。
- 不得写入密码、令牌、个人隐私或要求绕过权限与安全限制。
- 默认允许模型和用户调用；用户明确要求时才修改调用控制字段。
- 每次只创建一个 Skill，不得覆盖已有同名 Skill。

信息确认后，在自然语言回复末尾输出合法的创建标记：

```text
<skill_create>{"name":"example-skill","description":"说明用途","when_to_use":"说明何时使用","content":"完整的 Skill 指令正文","metadata":{"version":1},"disable_model_invocation":false,"user_invocable":true}</skill_create>
```

标记内容必须是合法 JSON，正文换行使用 `\n` 转义，标记后不要追加文字。