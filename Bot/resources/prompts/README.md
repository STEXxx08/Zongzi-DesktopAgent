# TTS 参考音频放这里

把你自己录的参考音频放进 `tts/` 子目录，命名格式：

```text
[语言][情绪]文本内容.wav
```

例如 `[zh][Default]你好呀.wav`、`[en][happy] What a good day today.wav`。

## 规则

- `语言` 只支持 `zh` / `en` / `ja`；
- `情绪` 必须能在 `character.emotions` 白名单里找到，默认是
  `Default / happy / sad / angry / shy / surprised`；
- 找不到对应情绪的音频时会**自动回落到 `Default`**，所以至少要有一条 `[zh][Default]`。

## 音频要求

3~10 秒干声、无背景音乐。这段音频就是角色的音色来源；**仓库不附带任何参考音频**，需要你自己准备。

> `Bot/config.yaml` 的 `character.speech.prompts_dir` 默认指向 `resources/prompts/tts`，
> 该目录里没有符合命名规则的文件时，TTS 启动会报「没有可用的 TTS 提示音频」。

更多说明见根目录 [`README.md`](../../../README.md) §3.8。
