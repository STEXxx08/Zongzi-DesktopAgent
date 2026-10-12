import argparse
import yaml

from common.abs_app import AbstractApplication

parser = argparse.ArgumentParser()
parser.add_argument('service', type=str)
parser.add_argument('--config', type=str)
parser.add_argument('-memory', action='store_true', help='Run memory service on port 10010')
args = parser.parse_args()


def load_config():
    path = args.config if args.config else './config.yaml'
    with open(path, mode='r', encoding='utf-8') as f:
        return yaml.safe_load(f)


_config = load_config()


def apply_memory_service_overrides(service_config: dict) -> dict:
    if args.memory:
        service_config["port"] = 10010
    return service_config


def asr_app() -> AbstractApplication:
    from asr.app import ASRApplication

    asr_config = _config["ASR"]
    asr_id = asr_config["id"]
    model_cfg = asr_config["config"][asr_id]

    def get_model():
        print(asr_id)
        if asr_id == "iic/speech_paraformer_asr_nat-zh-cn-16k-common-vocab8358-tensorflow1":
            from asr.paraformer.model import SpeechParaformerModel as Model
            from asr.paraformer.config import SpeechParaformerModelConfig as Config
            return Model(Config(**model_cfg))
        elif asr_id == "Qwen3-ASR-0.6B":   # 新增分支
            from asr.Qwen3ASR06B.model import QwenAsrModel as Model
            from asr.Qwen3ASR06B.config import QwenAsrModelConfig as Config
            return Model(Config(**model_cfg))
        elif asr_id == "Sherpa-ONNX-Streaming-Zipformer-ZH-INT8":
            from asr.sherpa_onnx.model import SherpaONNXASRModel as Model
            from asr.sherpa_onnx.config import SherpaONNXASRConfig as Config
            return Model(Config(**model_cfg))
        else:
            raise NameError(f"No such model name (id) {asr_id}")

    asr = get_model()
    app = ASRApplication(
        model=asr, host=asr_config["host"], port=asr_config["port"])
    return app


def llm_app() -> AbstractApplication:
    from llm.app import LLMApplication

    llm_config = apply_memory_service_overrides(_config["LLM"])
    llm_id = llm_config["id"]
    model_cfg = llm_config["config"][llm_id]

    def get_model():
        if llm_id == "THUDM/chatglm3-6b":
            from llm.chatglm3.model import ChatGLM3_6B as Model
            from llm.chatglm3.config import ChatGLM3ModelConfig as Config
            return Model(Config(**model_cfg))
        elif llm_id == "Qwen/Qwen-7B-Chat":
            from llm.qwen.model import Qwen7BChat as Model
            from llm.qwen.config import QwenModelConfig as Config
            return Model(Config(**model_cfg))
        elif llm_id == "augmxnt/shisa-7b-v1":
            from llm.shisa.model import Shisa7B_V1 as Model
            from llm.shisa.config import ShisaModelConfig as Config
            return Model(Config(**model_cfg))
        elif llm_id == "01-ai/Yi-6B-Chat":
            from llm.yi.model import Yi6B_Chat as Model
            from llm.yi.config import YiModelConfig as Config
            return Model(Config(**model_cfg))
        elif llm_id == "THUDM/glm-4-9b-chat-hf":
            from llm.glm4.model import GLM4_9B_Chat_Hf as Model
            from llm.glm4.config import GLM4ModelConfig as Config
            return Model(Config(**model_cfg))
        elif llm_id in ["deepseek-ai/DeepSeek-R1-Distill-Llama-8B", "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B"]:
            from llm.deepseek.model import DeepSeekLLMModel as Model
            from llm.deepseek.config import DeepSeekModelConfig as Config
            return Model(Config(**model_cfg))
        elif llm_id in ["deepseek_online/DeepSeek-v4-flash", "deepseek_online/DeepSeek-v4-pro"]:
            from llm.deepseek_online.model import DeepSeekOnlineModel as Model
            from llm.deepseek_online.config import DeepSeekOnlineConfig as Config
            return Model(Config(**model_cfg))
        elif llm_id == "ollama":
            from llm.ollama.model import OllamaModel as Model
            from llm.ollama.config import OllamaModelConfig as Config
            return Model(Config(**model_cfg))
        else:
            raise NameError(f"No such model name (id) {llm_id}")

    llm = get_model()
    app = LLMApplication(
        model=llm, host=llm_config["host"], port=llm_config["port"])
    return app


def imgcap_app() -> AbstractApplication:
    from img_cap.app import ImgCapApplication

    imgcap_config = _config["ImgCap"]
    imgcap_id = imgcap_config["id"]
    model_cfg = imgcap_config["config"][imgcap_id]

    def get_model():
        if imgcap_id == "Salesforce/blip-image-captioning-large":
            from img_cap.blip.model import BlipImageCaptioningLarge as Model
            from img_cap.blip.config import BlipModelConfig as Config
            return Model(Config(**model_cfg))
        else:
            raise NameError(f"No such model name (id) {imgcap_id}")

    imgcap = get_model()
    app = ImgCapApplication(
        model=imgcap, host=imgcap_config["host"], port=imgcap_config["port"])
    return app


def ocr_app() -> AbstractApplication:
    from ocr.app import OCRApplication

    ocr_config = _config["OCR"]
    ocr_id = ocr_config['id']
    model_cfg = ocr_config["config"][ocr_id]

    def get_model():
        if ocr_id == "paddlepaddle/PaddleOCR":
            from ocr.paddle.model import PaddleOCRModel as Model
            from ocr.paddle.config import PaddleOCRModelConfig as Config
            return Model(Config(**model_cfg))
        else:
            raise NameError(f"No such model name (id) {ocr_id}")

    ocr = get_model()
    app = OCRApplication(
        model=ocr, host=ocr_config["host"], port=ocr_config["port"])
    return app


def tts_app() -> AbstractApplication:
    from tts.app import TTSApplication

    tts_config = _config["TTS"]
    tts_id = tts_config["id"]
    model_cfg = tts_config["config"][tts_id]

    def get_model():
        if tts_id == "GPT-SoVITS":
            from tts.gpt_sovits.model import GPT_SoVITS as Model
            return Model()

    tts = get_model()
    print(tts_config["host"])
    app = TTSApplication(
        model=tts, host=tts_config["host"], port=tts_config["port"])
    return app


# ==================== 新增 MLLM 服务 ====================
def mllm_app() -> AbstractApplication:
    from mllm.app import MLLMApplication
    from mllm.model import MLLMOnlineModel
    from mllm.config import MLLMPipelineConfig

    mllm_config = apply_memory_service_overrides(_config["MLLM"])
    mllm_id = mllm_config["id"]
    model_cfg = mllm_config["config"][mllm_id]

    # 直接从 YAML 构造配置对象，避免新增字段遗漏导致回退到 dataclass 默认值。
    config = MLLMPipelineConfig(**model_cfg)
    model = MLLMOnlineModel(config)
    app = MLLMApplication(
        model=model,
        host=mllm_config["host"],
        port=mllm_config["port"]
    )
    return app
# ========================================================


def web_search_app() -> AbstractApplication:
    from web_search.app import WebSearchApplication
    from web_search.config import WebSearchCoreConfig
    from web_search.model import SearXNGSearchModel
    from web_search.weather.config import WeatherCoreConfig
    from web_search.weather.model import OpenMeteoWeatherModel
    from web_search.news.config import NewsCoreConfig
    from web_search.news.model import SearXNGNewsModel
    from web_search.steam.config import SteamCoreConfig
    from web_search.steam.model import SteamNewsModel
    from web_search.github.config import GitHubCoreConfig
    from web_search.github.model import GitHubReleaseModel
    from web_search.exchange_rate.config import ExchangeRateCoreConfig
    from web_search.exchange_rate.model import ExchangeRateModel

    service_config = _config["WebSearch"]
    model = SearXNGSearchModel(WebSearchCoreConfig(**service_config.get("config", {})))
    weather_config = service_config.get("weather", {})
    weather_model = (
        OpenMeteoWeatherModel(WeatherCoreConfig(**weather_config))
        if weather_config.get("enable", True)
        else None
    )
    news_config = service_config.get("news", {})
    news_model = SearXNGNewsModel(NewsCoreConfig(**news_config)) if news_config.get("enable", True) else None
    steam_config = service_config.get("steam", {})
    steam_model = SteamNewsModel(SteamCoreConfig(**steam_config)) if steam_config.get("enable", True) else None
    github_config = service_config.get("github", {})
    github_model = GitHubReleaseModel(GitHubCoreConfig(**github_config)) if github_config.get("enable", True) else None
    exchange_config = service_config.get("exchange_rate", {})
    exchange_rate_model = ExchangeRateModel(ExchangeRateCoreConfig(**exchange_config)) if exchange_config.get("enable", True) else None
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


def get_app(service):
    if "asr" == service:
        return asr_app()
    elif "llm" == service:
        return llm_app()
    elif "imgcap" == service:
        return imgcap_app()
    elif "ocr" == service:
        return ocr_app()
    elif "tts" == service:
        return tts_app()
    elif "mllm" == service:          # 新增分支
        return mllm_app()
    elif service in {"web_search", "web-search"}:
        return web_search_app()
    else:
        raise NotImplementedError("Unsupported service.")


def run(service=None):
    service = args.service if service is None else service
    print(service)
    app = get_app(service)
    app.run()


run()