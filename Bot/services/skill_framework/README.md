# 通用 Skills 框架

该模块采用与 DeepSeek Harness 兼容的本地 Skill 文件格式，并通过项目现有
`ToolRuntime` 按需加载完整指令。模型的常驻上下文只包含 Skill 名称和描述，
不会一次性注入所有正文。

从零创建、字段校验、调用权限和能力边界的完整指引见 `../../docs/SKILLS_CREATION_GUIDE.md`。

## 文件格式

Skill 可以使用目录包：

```text
skills/example-skill/SKILL.md
```

也可以使用根目录下的单文件：

```text
skills/example-skill.md
```

配置中的相对路径以 `Bot/` 应用项目根目录为基准，而不是以进程的当前工作目录
或外层仓库目录为基准。
因此无论从项目根目录、`Bot/`、快捷方式或服务管理器启动，都会读取同一组 Skill。
绝对路径仍按原样使用。

示例内容：

```markdown
---
name: example-skill
description: 在用户要求执行示例流程时使用
whenToUse: 用户明确要求示例任务或当前任务明显符合该流程时
metadata:
  version: 1
disable-model-invocation: false
user-invocable: true
---

1. 先确认输入条件。
2. 调用所需工具。
3. 汇总结果并说明限制。
```

`name` 必须使用 kebab-case。`name` 和 `description` 为必填字段，其余字段
可选。省略调用控制字段时，默认允许模型调用和用户调用。

## 运行流程

1. `SkillRegistry` 扫描配置的根目录并验证 YAML frontmatter。
2. `SkillService` 将可由模型调用的 Skill 摘要加入稳定目录。
3. 模型需要 Skill 时，通过 `skill` 工具按名称加载。
4. 完整正文以 Skill 指令上下文注入当前轮次。
5. Skill 目录发生变化后，下次模型请求会直接看到最新目录或正文。

用户也可以在输入中使用空白分隔的 `/example-skill` 显式加载允许用户调用的
Skill。设置 `user-invocable: false` 后，该命令不会生效；设置
`disable-model-invocation: true` 只会禁止模型自主加载，不影响用户显式调用。

## 创建接口

外部代码可以调用 `bot.skill_service.create_skill(...)` 创建新的目录包。
该接口执行名称校验、frontmatter 序列化和覆盖保护。配置
`allow_model_create: true` 时，模型还可以通过 `skill_create` 工具创建 Skill；
该工具始终禁止覆盖已有文件，并继续受创建目录、文件大小和格式校验约束。

项目内置的 `skill-creator` 是一个元 Skill 示例。加载它后，模型会先整理名称、
用途、触发条件和正文，再在回复末尾输出：

```text
<skill_create>{"name":"example-skill","description":"...","content":"..."}</skill_create>
```

成功创建的 Skill 默认位于 `skills/<name>/SKILL.md`，并会在后续请求中
自动进入 Skill 摘要目录。