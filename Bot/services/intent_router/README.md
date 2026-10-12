# BGE Intent Router

This optional Bot-side service performs local semantic routing before the first
cloud LLM request. It does not replace deterministic ASR noise filtering or safety
checks.

Accepted intents return a provider-neutral `RouteDecision`. Tool intents contain
a `ToolCall` that is validated against the shared `ToolRuntime`; the router never
executes tools itself. Use `action: tool` for tools, `action: vision` for local
screen handling, and `action: none` for negative examples. The legacy
`action: web_tool` value remains accepted for old configurations.

The caller sends every accepted `ToolCall` through the same runtime path. Routing
does not branch on tool categories or tags such as `web`; tags are reserved for
catalog filtering, permissions, logging, and other discovery concerns. Argument
requirements and execution failures belong to the tool definition/runtime rather
than the semantic router.

## Files

Download the INT8 ONNX model and tokenizer files into the path configured by
`service.intent_router.model_path`. The default layout is:

```text
Bot/models/bge-small-zh-v1.5-onnx/
├── config.json
├── tokenizer.json
├── tokenizer_config.json
├── special_tokens_map.json
├── vocab.txt
└── onnx/
    ├── model_quantized.onnx
    └── model_quantized.onnx_data
```

`model_quantized.onnx_data` holds the actual weights; the `.onnx` file is only
~165 KB of graph plus pointers. Downloading the model without it leaves a directory
that looks complete but cannot load, so always copy the whole `onnx/` folder:

```bash
hf download onnx-community/bge-small-zh-v1.5-ONNX \
  --include "config.json" "tokenizer*" "special_tokens_map.json" "vocab.txt" \
            "onnx/*" \
  --local-dir models/bge-small-zh-v1.5-onnx
```

The Bot environment needs `onnxruntime` and `transformers`. Keep the router
disabled until both the dependencies and model files are present. No network is
used when loading the model.

Install the runtime packages into the Bot's existing Python environment:

```bash
pip install onnxruntime transformers huggingface_hub
```

Then set `service.intent_router.enable: true` in `config.yaml` and start
the Bot normally. A successful load writes `BGE intent router initialized` to the
log. If a dependency or model file is missing, the router disables itself and the
legacy keyword behavior is available only when `fallback_to_keywords: true`.

## Calibration

Start with the configured thresholds, then record accepted and rejected scores
from real ASR text. Raise `confidence_threshold` to reduce false positives; raise
`margin_threshold` when semantically adjacent intents are confused.