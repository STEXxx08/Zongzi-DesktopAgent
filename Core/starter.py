import argparse
import importlib
from typing import Any

import yaml

from common.abs_app import AbstractApplication


MODEL_REGISTRY: dict[str, dict[str, str]] = {
    "ASR": {
        "iic/speech_paraformer_asr_nat-zh-cn-16k-common-vocab8358-tensorflow1": (
            "asr.paraformer.model:SpeechParaformerModel,"
            "asr.paraformer.config:SpeechParaformerModelConfig"
        ),
        "Qwen3-ASR-0.6B": (
            "asr.Qwen3ASR06B.model:QwenAsrModel,"
            "asr.Qwen3ASR06B.config:QwenAsrModelConfig"
        ),
        "Sherpa-ONNX-Streaming-Zipformer-ZH-INT8": (
            "asr.sherpa_onnx.model:SherpaONNXASRModel,"
            "asr.sherpa_onnx.config:SherpaONNXASRConfig"
        ),
    },
    "LLM": {
        "THUDM/chatglm3-6b": (
            "llm.chatglm3.model:ChatGLM3_6B,"
            "llm.chatglm3.config:ChatGLM3ModelConfig"
        ),
        "Qwen/Qwen-7B-Chat": (
            "llm.qwen.model:Qwen7BChat,"
            "llm.qwen.config:QwenModelConfig"
        ),
        "augmxnt/shisa-7b-v1": (
            "llm.shisa.model:Shisa7B_V1,"
            "llm.shisa.config:ShisaModelConfig"
        ),
        "01-ai/Yi-6B-Chat": (
            "llm.yi.model:Yi6B_Chat,"
            "llm.yi.config:YiModelConfig"
        ),
        "THUDM/glm-4-9b-chat-hf": (
            "llm.glm4.model:GLM4_9B_Chat_Hf,"
            "llm.glm4.config:GLM4ModelConfig"
        ),
        "deepseek-ai/DeepSeek-R1-Distill-Llama-8B": (
            "llm.deepseek.model:DeepSeekLLMModel,"
            "llm.deepseek.config:DeepSeekModelConfig"
        ),
        "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B": (
            "llm.deepseek.model:DeepSeekLLMModel,"
            "llm.deepseek.config:DeepSeekModelConfig"
        ),
        "deepseek_online/DeepSeek-v4-flash": (
            "llm.deepseek_online.model:DeepSeekOnlineModel,"
            "llm.deepseek_online.config:DeepSeekOnlineConfig"
        ),
        "deepseek_online/DeepSeek-v4-pro": (
            "llm.deepseek_online.model:DeepSeekOnlineModel,"
            "llm.deepseek_online.config:DeepSeekOnlineConfig"
        ),
        "ollama": (
            "llm.ollama.model:OllamaModel,"
            "llm.ollama.config:OllamaModelConfig"
        ),
    },
    "ImgCap": {
        "Salesforce/blip-image-captioning-large": (
            "img_cap.blip.model:BlipImageCaptioningLarge,"
            "img_cap.blip.config:BlipModelConfig"
        ),
    },
    "OCR": {
        "paddlepaddle/PaddleOCR": (
            "ocr.paddle.model:PaddleOCRModel,"
            "ocr.paddle.config:PaddleOCRModelConfig"
        ),
    },
    "TTS": {
        "GPT-SoVITS": "tts.gpt_sovits.model:GPT_SoVITS",
    },
}


def _parse_model_spec(spec: str) -> tuple[tuple[str, str], tuple[str, str] | None]:
    """解析模型加载规范，支持两种写法：

    1. ``"model_module:ModelClass,config_module:ConfigClass"``
    2. ``"module_path:ModelClass,ConfigClass"``（或无 ConfigClass 的 ``"module_path:ModelClass"``）
    """
    raw = (spec or "").strip()
    if ":" not in raw:
        raise ValueError(f"非法模型入口格式（缺少 ':'）：{spec!r}")
    parts = [item.strip() for item in raw.split(",", 1)]
    model_mod, model_cls = (item.strip() for item in parts[0].split(":", 1))
    if len(parts) == 1 or not parts[1]:
        return (model_mod, model_cls), None
    second = parts[1]
    if ":" in second:
        config_mod, config_cls = (item.strip() for item in second.split(":", 1))
    else:
        config_mod, config_cls = model_mod, second
    return (model_mod, model_cls), (config_mod, config_cls)


def load_model_from_spec(spec: str, model_cfg: dict[str, Any] | None = None):
    """按规范延迟导入并实例化模型（仅加载选中分支，避免跨 Conda 环境导入报错）。"""
    (model_mod_name, model_cls_name), config_target = _parse_model_spec(spec)
    model_cls = getattr(importlib.import_module(model_mod_name), model_cls_name)
    if config_target is None:
        return model_cls()
    config_mod_name, config_cls_name = config_target
    config_cls = getattr(importlib.import_module(config_mod_name), config_cls_name)
    cfg_kwargs = {
        k: v for k, v in (model_cfg or {}).items()
        if k not in {"entry", "model_spec"}
    }
    return model_cls(config_cls(**cfg_kwargs))


def resolve_model_spec(service_section: str, model_id: str, service_config: dict[str, Any]) -> str:
    """按配置内声明 -> 服务注册表 -> 内置注册表的顺序解析模型入口。"""
    model_cfg = (service_config.get("config") or {}).get(model_id)
    if isinstance(model_cfg, dict):
        inline = model_cfg.get("entry") or model_cfg.get("model_spec")
        if isinstance(inline, str) and inline.strip():
            return inline.strip()
    custom_registry = service_config.get("registry") or {}
    if isinstance(custom_registry, dict) and model_id in custom_registry:
        return str(custom_registry[model_id])
    builtin = MODEL_REGISTRY.get(service_section, {})
    if model_id in builtin:
        return builtin[model_id]
    raise NameError(f"No such model name (id) {model_id}")


def _build_registered_model(service_section: str, service_config: dict[str, Any]):
    model_id = service_config["id"]
    model_cfg = (service_config.get("config") or {}).get(model_id, {})
    spec = resolve_model_spec(service_section, model_id, service_config)
    return load_model_from_spec(spec, model_cfg)


def load_config(config_path: str | None = None):
    path = config_path if config_path else './config.yaml'
    with open(path, mode='r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def apply_memory_service_overrides(service_config: dict, *, memory_mode: bool = False) -> dict:
    if memory_mode:
        service_config["port"] = 10010
    return service_config


def asr_app(config: dict) -> AbstractApplication:
    from asr.app import ASRApplication

    asr_config = config["ASR"]
    print(asr_config["id"])
    asr = _build_registered_model("ASR", asr_config)
    return ASRApplication(model=asr, host=asr_config["host"], port=asr_config["port"])


def llm_app(config: dict, *, memory_mode: bool = False) -> AbstractApplication:
    from llm.app import LLMApplication

    llm_config = apply_memory_service_overrides(config["LLM"], memory_mode=memory_mode)
    llm = _build_registered_model("LLM", llm_config)
    return LLMApplication(model=llm, host=llm_config["host"], port=llm_config["port"])


def imgcap_app(config: dict) -> AbstractApplication:
    from img_cap.app import ImgCapApplication

    imgcap_config = config["ImgCap"]
    imgcap = _build_registered_model("ImgCap", imgcap_config)
    return ImgCapApplication(model=imgcap, host=imgcap_config["host"], port=imgcap_config["port"])


def ocr_app(config: dict) -> AbstractApplication:
    from ocr.app import OCRApplication

    ocr_config = config["OCR"]
    ocr = _build_registered_model("OCR", ocr_config)
    return OCRApplication(model=ocr, host=ocr_config["host"], port=ocr_config["port"])


def tts_app(config: dict) -> AbstractApplication:
    from tts.app import TTSApplication

    tts_config = config["TTS"]
    tts = _build_registered_model("TTS", tts_config)
    print(tts_config["host"])
    return TTSApplication(model=tts, host=tts_config["host"], port=tts_config["port"])


# ==================== 新增 MLLM 服务 ====================
def mllm_app(config: dict, *, memory_mode: bool = False) -> AbstractApplication:
    from mllm.app import MLLMApplication
    from mllm.model import MLLMOnlineModel
    from mllm.config import MLLMPipelineConfig

    mllm_config = apply_memory_service_overrides(config["MLLM"], memory_mode=memory_mode)
    mllm_id = mllm_config["id"]
    model_cfg = mllm_config["config"][mllm_id]

    # 直接从 YAML 构造配置对象，避免新增字段遗漏导致回退到 dataclass 默认值。
    mllm_pipeline_config = MLLMPipelineConfig(**model_cfg)
    model = MLLMOnlineModel(mllm_pipeline_config)
    return MLLMApplication(
        model=model,
        host=mllm_config["host"],
        port=mllm_config["port"],
    )
# ========================================================


def web_search_app(config: dict) -> AbstractApplication:
    from web_search.app import WebSearchApplication

    service_config = config["WebSearch"]
    from web_search.config import WebSearchCoreConfig
    from web_search.model import SearXNGSearchModel

    model = SearXNGSearchModel(WebSearchCoreConfig(**(service_config.get("config") or {})))

    weather_config = service_config.get("weather", {})
    if weather_config.get("enable", True):
        from web_search.weather.config import WeatherCoreConfig
        from web_search.weather.model import OpenMeteoWeatherModel

        weather_model = OpenMeteoWeatherModel(WeatherCoreConfig(**weather_config))
    else:
        weather_model = None

    news_config = service_config.get("news", {})
    if news_config.get("enable", True):
        from web_search.news.config import NewsCoreConfig
        from web_search.news.model import SearXNGNewsModel

        news_model = SearXNGNewsModel(NewsCoreConfig(**news_config))
    else:
        news_model = None

    steam_config = service_config.get("steam", {})
    if steam_config.get("enable", True):
        from web_search.steam.config import SteamCoreConfig
        from web_search.steam.model import SteamNewsModel

        steam_model = SteamNewsModel(SteamCoreConfig(**steam_config))
    else:
        steam_model = None

    github_config = service_config.get("github", {})
    if github_config.get("enable", True):
        from web_search.github.config import GitHubCoreConfig
        from web_search.github.model import GitHubReleaseModel

        github_model = GitHubReleaseModel(GitHubCoreConfig(**github_config))
    else:
        github_model = None

    exchange_config = service_config.get("exchange_rate", {})
    if exchange_config.get("enable", True):
        from web_search.exchange_rate.config import ExchangeRateCoreConfig
        from web_search.exchange_rate.model import ExchangeRateModel

        exchange_rate_model = ExchangeRateModel(ExchangeRateCoreConfig(**exchange_config))
    else:
        exchange_rate_model = None

    return WebSearchApplication(
        model=model,
        host=service_config["host"],
        port=service_config["port"],
        weather_model=weather_model,
        news_model=news_model,
        steam_model=steam_model,
        github_model=github_model,
        exchange_rate_model=exchange_rate_model,
    )


def get_app(service: str, config: dict | None = None, *, memory_mode: bool = False):
    cfg = config if config is not None else load_config()
    if service == "asr":
        return asr_app(cfg)
    elif service == "llm":
        return llm_app(cfg, memory_mode=memory_mode)
    elif service == "imgcap":
        return imgcap_app(cfg)
    elif service == "ocr":
        return ocr_app(cfg)
    elif service == "tts":
        return tts_app(cfg)
    elif service == "mllm":
        return mllm_app(cfg, memory_mode=memory_mode)
    elif service in {"web_search", "web-search"}:
        return web_search_app(cfg)
    else:
        raise NotImplementedError("Unsupported service.")


def run(service=None, config_path=None, *, memory_mode: bool = False):
    print(service)
    cfg = load_config(config_path)
    app = get_app(service, cfg, memory_mode=memory_mode)
    app.run()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('service', type=str)
    parser.add_argument('--config', type=str)
    parser.add_argument('-memory', action='store_true', help='Run memory service on port 10010')
    args = parser.parse_args()
    run(args.service, args.config, memory_mode=args.memory)


if __name__ == "__main__":
    main()
