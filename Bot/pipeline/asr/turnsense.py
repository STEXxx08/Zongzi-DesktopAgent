import importlib
import importlib.util
import re
import time
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

import numpy as np
import requests
from loguru import logger

from pipeline.asr.config import TurnSenseConfig


@dataclass
class TurnSenseResult:
    label: str
    confidence: float = 0.0
    latency: float = 0.0


class _TurnSenseAudioFrontend:
    def __init__(
        self,
        cmvn_file: str,
        fs: int = 16000,
        window: str = "hamming",
        n_mels: int = 80,
        frame_length: int = 25,
        frame_shift: int = 10,
        lfr_m: int = 7,
        lfr_n: int = 6,
        dither: float = 0.0,
    ):
        import kaldi_native_fbank as knf

        opts = knf.FbankOptions()
        opts.frame_opts.samp_freq = fs
        opts.frame_opts.dither = dither
        opts.frame_opts.window_type = window
        opts.frame_opts.frame_shift_ms = float(frame_shift)
        opts.frame_opts.frame_length_ms = float(frame_length)
        opts.mel_opts.num_bins = n_mels
        opts.energy_floor = 0
        opts.frame_opts.snip_edges = True
        opts.mel_opts.debug_mel = False
        self.opts = opts
        self.fs = fs
        self.lfr_m = lfr_m
        self.lfr_n = lfr_n
        self.cmvn = self._load_cmvn(cmvn_file)

    def extract_features(self, waveform: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        feat, _ = self._fbank(waveform)
        feat = self._apply_lfr(feat)
        feat = self._apply_cmvn(feat)
        feat_len = np.array(feat.shape[0]).astype(np.int32)
        return feat.astype(np.float32), feat_len

    def _fbank(self, waveform: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        import kaldi_native_fbank as knf

        waveform = waveform.astype(np.float32) * (1 << 15)
        fbank_fn = knf.OnlineFbank(self.opts)
        fbank_fn.accept_waveform(self.opts.frame_opts.samp_freq, waveform.tolist())
        frames = fbank_fn.num_frames_ready
        if frames <= 0:
            raise ValueError("TurnSense audio too short for fbank extraction")
        mat = np.empty([frames, self.opts.mel_opts.num_bins], dtype=np.float32)
        for frame_idx in range(frames):
            mat[frame_idx, :] = fbank_fn.get_frame(frame_idx)
        return mat.astype(np.float32), np.array(mat.shape[0]).astype(np.int32)

    def _apply_lfr(self, inputs: np.ndarray) -> np.ndarray:
        if self.lfr_m == 1 and self.lfr_n == 1:
            return inputs.astype(np.float32)
        lfr_inputs = []
        total_frames = inputs.shape[0]
        total_lfr_frames = int(np.ceil(total_frames / self.lfr_n))
        left_padding = np.tile(inputs[0], ((self.lfr_m - 1) // 2, 1))
        padded = np.vstack((left_padding, inputs))
        padded_frames = total_frames + (self.lfr_m - 1) // 2
        for frame_idx in range(total_lfr_frames):
            start = frame_idx * self.lfr_n
            if self.lfr_m <= padded_frames - start:
                lfr_inputs.append(padded[start:start + self.lfr_m].reshape(1, -1))
            else:
                num_padding = self.lfr_m - (padded_frames - start)
                frame = padded[start:].reshape(-1)
                for _ in range(num_padding):
                    frame = np.hstack((frame, padded[-1]))
                lfr_inputs.append(frame.reshape(1, -1))
        return np.vstack(lfr_inputs).astype(np.float32)

    def _apply_cmvn(self, inputs: np.ndarray) -> np.ndarray:
        frame, dim = inputs.shape
        means = np.tile(self.cmvn[0:1, :dim], (frame, 1))
        vars_ = np.tile(self.cmvn[1:2, :dim], (frame, 1))
        return (inputs + means) * vars_

    @staticmethod
    def _load_cmvn(cmvn_file: str) -> np.ndarray:
        with open(cmvn_file, "r", encoding="utf-8") as file:
            lines = file.readlines()
        means_list = []
        vars_list = []
        for idx, line in enumerate(lines):
            line_item = line.split()
            if not line_item:
                continue
            if line_item[0] == "<AddShift>" and idx + 1 < len(lines):
                next_item = lines[idx + 1].split()
                if next_item and next_item[0] == "<LearnRateCoef>":
                    means_list = list(next_item[3:len(next_item) - 1])
            elif line_item[0] == "<Rescale>" and idx + 1 < len(lines):
                next_item = lines[idx + 1].split()
                if next_item and next_item[0] == "<LearnRateCoef>":
                    vars_list = list(next_item[3:len(next_item) - 1])
        if not means_list or not vars_list:
            raise ValueError(f"Invalid TurnSense CMVN file: {cmvn_file}")
        return np.array([np.array(means_list).astype(np.float64), np.array(vars_list).astype(np.float64)])


class TurnSenseDetector:
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"

    def __init__(self, config: TurnSenseConfig):
        self._config = config
        requested_backend = config.backend.lower()
        self._backend = requested_backend
        self._callable = None
        if self._backend in {"auto", "module"}:
            self._init_module_backend()
        if self._backend in {"auto", "onnx"} and self._callable is None:
            self._init_onnx_backend()
        if self._backend in {"auto", "transformers"} and self._callable is None:
            self._init_transformers_backend()
        if self._backend == "http" and not config.api_url:
            raise ValueError("TurnSense http backend requires turnsense.api_url")
        if self._callable is None and self._backend != "http":
            if requested_backend == "heuristic":
                logger.info("TurnSense heuristic backend enabled.")
            else:
                logger.warning("TurnSense backend {} unavailable; using heuristic fallback.", config.backend)
            self._backend = "heuristic"

    def predict(self, text: str) -> TurnSenseResult:
        start = time.perf_counter()
        label = self.INVALID
        confidence = 0.0
        try:
            if self._backend == "http":
                label, confidence = self._predict_http(text)
            elif self._backend == "heuristic":
                label, confidence = self._predict_heuristic(text)
            elif self._backend == "audio_onnx":
                label, confidence = self.INCOMPLETE, 0.0
            else:
                raw = self._callable(text)
                label, confidence = self._normalize_raw(raw)
        except Exception as exc:
            logger.warning("TurnSense predict failed: {}", exc)
            if self._config.fail_open:
                label, confidence = self.COMPLETE, 0.0
            else:
                label, confidence = self.INCOMPLETE, 0.0
        return TurnSenseResult(label=label, confidence=confidence, latency=time.perf_counter() - start)

    def _init_module_backend(self):
        for module_name in ["turnsense", "TurnSense", "tensense", "TenSense"]:
            try:
                module = importlib.import_module(module_name)
            except Exception:
                continue
            for attr in ["predict", "infer", "classify"]:
                func = getattr(module, attr, None)
                if callable(func):
                    self._callable = func
                    self._backend = "module"
                    logger.info("TurnSense module backend loaded: {}.{}", module_name, attr)
                    return

    def _init_onnx_backend(self):
        model_dir = Path(self._config.model_path)
        onnx_path = model_dir / self._config.onnx_model
        if not onnx_path.exists():
            logger.warning("TurnSense ONNX model not found: {}", onnx_path)
            return
        if importlib.util.find_spec("onnxruntime") is None:
            logger.warning(
                "TurnSense ONNX dependency unavailable: missing onnxruntime. "
                "Install it in the bot runtime, or set turnsense.backend to 'heuristic'/'http'."
            )
            return
        try:
            import onnxruntime as ort
        except Exception as exc:
            logger.warning("TurnSense ONNX dependency import failed: {}", exc)
            return

        providers = [self._config.onnx_provider]
        available_providers = set(ort.get_available_providers())
        if self._config.onnx_provider not in available_providers:
            logger.warning("TurnSense ONNX provider {} unavailable, using ONNX Runtime defaults.", self._config.onnx_provider)
            providers = None

        session = ort.InferenceSession(str(onnx_path), providers=providers)
        input_names = {model_input.name for model_input in session.get_inputs()}
        output_name = session.get_outputs()[0].name
        logger.info("TurnSense ONNX inputs detected: {}", sorted(input_names))

        if {"feats", "feat_lengths"}.issubset(input_names):
            self._init_audio_onnx_backend(model_dir, onnx_path, session, output_name)
            return

        if "input_ids" in input_names:
            if importlib.util.find_spec("transformers") is None:
                logger.warning(
                    "TurnSense text ONNX dependency unavailable: missing transformers. "
                    "This ONNX model expects tokenizer inputs."
                )
                return
            tokenizer = self._load_tokenizer(model_dir)
            if tokenizer is None:
                logger.warning("TurnSense tokenizer not found under {} or its parents.", model_dir)
                return
            labels = {0: self.COMPLETE, 1: self.INCOMPLETE, 2: self.INVALID}

            def classify(text: str):
                encoded = tokenizer(
                    text,
                    return_tensors="np",
                    truncation=True,
                    max_length=self._config.max_length,
                )
                feed = {name: value for name, value in encoded.items() if name in input_names}
                missing_inputs = input_names - set(feed)
                if "token_type_ids" in missing_inputs:
                    feed["token_type_ids"] = np.zeros_like(feed["input_ids"])
                    missing_inputs.remove("token_type_ids")
                if missing_inputs:
                    raise ValueError(f"Unsupported TurnSense ONNX inputs: {sorted(missing_inputs)}")
                logits = session.run([output_name], feed)[0][0]
                probs = self._softmax(logits)
                idx = int(np.argmax(probs))
                return {"label": labels.get(idx, str(idx)), "confidence": float(probs[idx])}

            self._callable = classify
            self._backend = "onnx"
            logger.info("TurnSense text ONNX backend loaded from {}", onnx_path)
            return

        logger.warning("Unsupported TurnSense ONNX inputs: {}", sorted(input_names))

    def _init_audio_onnx_backend(self, model_dir: Path, onnx_path: Path, session, output_name: str):
        missing_packages = [
            package_name
            for package_name in ("kaldi_native_fbank",)
            if importlib.util.find_spec(package_name) is None
        ]
        if missing_packages:
            logger.warning(
                "TurnSense audio ONNX dependencies unavailable: missing {}. "
                "Install TurnSense frontend requirements in the bot runtime.",
                ", ".join(missing_packages),
            )
            return

        cmvn_path = self._find_audio_frontend_file(model_dir, "am.mvn")
        if cmvn_path is None:
            logger.warning("TurnSense audio frontend file am.mvn not found under {} or its parents.", model_dir)
            return

        frontend = _TurnSenseAudioFrontend(cmvn_file=str(cmvn_path))
        labels = {0: self.COMPLETE, 1: self.INCOMPLETE, 2: self.INVALID}

        def classify_audio(audio: np.ndarray, sample_rate: int = 16000):
            audio = self._prepare_audio(audio, sample_rate, frontend.fs)
            feats, feat_len = frontend.extract_features(audio)
            feed = {
                "feats": np.asarray(feats, dtype=np.float32)[None, ...],
                "feat_lengths": np.asarray([int(feat_len)], dtype=np.int64),
            }
            logits = session.run([output_name], feed)[0]
            probs_2d, preds = self._process_audio_logits(logits)
            idx = int(preds[0])
            confidence = float(probs_2d[0][idx])
            return {"label": labels.get(idx, str(idx)), "confidence": confidence}

        self._callable = classify_audio
        self._backend = "audio_onnx"
        logger.info("TurnSense audio ONNX backend loaded from {}, cmvn={}", onnx_path, cmvn_path)

    def predict_audio(self, audio: np.ndarray, sample_rate: int = 16000) -> TurnSenseResult:
        start = time.perf_counter()
        label = self.INVALID
        confidence = 0.0
        try:
            if self._backend == "audio_onnx":
                raw = self._callable(audio, sample_rate)
                label, confidence = self._normalize_raw(raw)
            else:
                label, confidence = self.INCOMPLETE, 0.0
        except Exception as exc:
            logger.warning("TurnSense audio predict failed: {}", exc)
            if self._config.fail_open:
                label, confidence = self.COMPLETE, 0.0
            else:
                label, confidence = self.INCOMPLETE, 0.0
        return TurnSenseResult(label=label, confidence=confidence, latency=time.perf_counter() - start)

    @property
    def supports_audio(self) -> bool:
        return self._backend == "audio_onnx"

    @staticmethod
    def _find_audio_frontend_file(model_dir: Path, filename: str) -> Optional[Path]:
        for candidate_dir in [model_dir, *model_dir.parents]:
            candidate = candidate_dir / filename
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _prepare_audio(audio: np.ndarray, sample_rate: int, target_sample_rate: int) -> np.ndarray:
        values = np.asarray(audio, dtype=np.float32).reshape(-1)
        if values.size == 0:
            raise ValueError("empty audio")
        max_abs = float(np.max(np.abs(values)))
        if max_abs > 1.5:
            values = values / 32768.0
        if sample_rate != target_sample_rate:
            try:
                import librosa
                values = librosa.resample(y=values, orig_sr=sample_rate, target_sr=target_sample_rate)
            except Exception as exc:
                raise ValueError(f"TurnSense audio resample requires librosa when sample_rate={sample_rate}: {exc}") from exc
        max_samples = 8 * target_sample_rate
        if values.shape[0] > max_samples:
            values = values[-max_samples:]
        return np.clip(values, -1.0, 1.0).astype(np.float32)

    @classmethod
    def _process_audio_logits(cls, logits: np.ndarray):
        output = np.asarray(logits)
        if not np.isfinite(output).all():
            raise ValueError("Non-finite values detected in TurnSense output")
        if output.ndim == 2 and output.shape[1] >= 2:
            row_sums = output.sum(axis=1)
            is_prob = np.all(output >= 0.0) and np.all(output <= 1.0) and np.allclose(row_sums, 1.0, atol=1e-4)
            probs_2d = output if is_prob else cls._softmax_axis(output, axis=1)
            preds = np.argmax(probs_2d, axis=1).astype(int)
            return probs_2d, preds
        probs = output.squeeze()
        if np.nanmin(probs) < 0 or np.nanmax(probs) > 1:
            probs = 1.0 / (1.0 + np.exp(-probs))
        probs = np.asarray(probs, dtype=np.float64).reshape(-1)
        probs_2d = np.stack([1.0 - probs, probs], axis=1)
        preds = np.argmax(probs_2d, axis=1).astype(int)
        return probs_2d, preds

    def _load_tokenizer(self, model_dir: Path):
        from transformers import AutoTokenizer

        tokenizer_markers = (
            "tokenizer_config.json",
            "tokenizer.json",
            "vocab.txt",
            "vocab.json",
            "spiece.model",
            "sentencepiece.bpe.model",
            "config.json",
        )
        checked_dirs = []
        for tokenizer_dir in [model_dir, *model_dir.parents]:
            checked_dirs.append(str(tokenizer_dir))
            if any((tokenizer_dir / marker).exists() for marker in tokenizer_markers):
                try:
                    return AutoTokenizer.from_pretrained(str(tokenizer_dir), trust_remote_code=True)
                except Exception as exc:
                    logger.warning("TurnSense tokenizer load failed from {}: {}", tokenizer_dir, exc)
                    continue
        logger.warning(
            "TurnSense tokenizer files not found. Checked dirs: {}. Expected one of: {}",
            checked_dirs,
            ", ".join(tokenizer_markers),
        )
        return None

    def _init_transformers_backend(self):
        model_path = Path(self._config.model_path)
        if not model_path.exists():
            return
        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            import torch
        except Exception:
            return
        tokenizer = AutoTokenizer.from_pretrained(str(model_path), trust_remote_code=True)
        model = AutoModelForSequenceClassification.from_pretrained(str(model_path), trust_remote_code=True)
        model.eval()
        labels = getattr(model.config, "id2label", {}) or {0: self.COMPLETE, 1: self.INCOMPLETE, 2: self.INVALID}

        def classify(text: str):
            with torch.no_grad():
                inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=self._config.max_length)
                outputs = model(**inputs)
                probs = torch.softmax(outputs.logits[0], dim=-1)
                idx = int(torch.argmax(probs).item())
                return {"label": labels.get(idx, str(idx)), "confidence": float(probs[idx].item())}

        self._callable = classify
        self._backend = "transformers"
        logger.info("TurnSense transformers backend loaded from {}", model_path)

    def _predict_http(self, text: str) -> tuple[str, float]:
        response = requests.post(self._config.api_url, json={"text": text}, timeout=1.5)
        response.raise_for_status()
        return self._normalize_raw(response.json())

    def _predict_heuristic(self, text: str) -> tuple[str, float]:
        normalized = re.sub(r"\s+", "", text or "")
        if len(normalized) < 2:
            return self.INVALID, 0.8
        if normalized in {"嗯", "嗯嗯", "啊", "啊啊", "呃", "哦", "说话", "我"}:
            return self.INVALID, 0.8
        if normalized.endswith(("，", ",", "、", "就是", "然后", "那个", "这个", "因为", "如果", "但是", "或者", "比如")):
            return self.INCOMPLETE, 0.6
        if normalized.endswith(("。", "？", "?", "！", "!")):
            return self.COMPLETE, 0.7
        if len(normalized) >= 12:
            return self.COMPLETE, 0.55
        return self.INCOMPLETE, 0.5

    def _normalize_raw(self, raw) -> tuple[str, float]:
        confidence = 0.0
        if isinstance(raw, dict):
            label = raw.get("label") or raw.get("state") or raw.get("result") or raw.get("prediction")
            confidence = float(raw.get("confidence") or raw.get("score") or 0.0)
        elif isinstance(raw, (list, tuple)) and raw:
            label = raw[0]
            if len(raw) > 1:
                confidence = float(raw[1] or 0.0)
        else:
            label = raw
        label = str(label or "").lower()
        if "invalid" in label or "无效" in label:
            return self.INVALID, confidence
        if "incomplete" in label or "未完成" in label or "continue" in label:
            return self.INCOMPLETE, confidence
        if "complete" in label or "完成" in label or "end" in label:
            return self.COMPLETE, confidence
        return self.INCOMPLETE, confidence

    @staticmethod
    def _softmax(logits):
        values = np.asarray(logits, dtype=np.float32)
        values = values - np.max(values)
        exp_values = np.exp(values)
        return exp_values / np.sum(exp_values)