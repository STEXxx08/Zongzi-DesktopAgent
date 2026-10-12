from dataclasses import dataclass


@dataclass
class SherpaONNXASRConfig:
    model_dir: str = "models/sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30"
    encoder: str = "encoder.int8.onnx"
    decoder: str = "decoder.onnx"
    joiner: str = "joiner.int8.onnx"
    tokens: str = "tokens.txt"
    provider: str = "cuda"
    num_threads: int = 1
    sample_rate: int = 16000
    feature_dim: int = 80
    decoding_method: str = "greedy_search"
    enable_endpoint_detection: bool = False
    tail_padding_seconds: float = 0.66