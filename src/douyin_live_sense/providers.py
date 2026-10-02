"""SDK adapters. Only the analysis child imports this module's heavy dependencies."""

import os

from .analysis_types import Diarization, ModelsNotReady, Turn, speech_intervals
from .config import Settings
from .models import read_manifest


def offline_environment(settings: Settings) -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
    os.environ.setdefault("MPLCONFIGDIR", str(settings.data_dir / "models" / ".matplotlib"))
    os.environ.setdefault("NUMBA_CACHE_DIR", str(settings.data_dir / "models" / ".numba"))
    os.environ.setdefault("XDG_CACHE_HOME", str(settings.data_dir / "models" / ".cache"))
    os.environ["OMP_NUM_THREADS"] = str(settings.analysis_threads)


def model_path(settings: Settings, name: str, config: dict | None = None):
    entry = read_manifest(settings.data_dir)["models"][name]
    revision = config["models"][name] if config else entry["revision"]
    path = settings.data_dir / "models" / name / revision
    if not path.resolve().is_relative_to((settings.data_dir / "models").resolve()):
        raise ValueError("模型版本路径超出本地模型目录。")
    if not (path / "config.yaml").is_file():
        raise ModelsNotReady(f"缺少 {name} 的已固定模型版本 {revision}。")
    return path, entry["repo"] + "@" + revision


def diarization_device(settings: Settings):
    import torch

    requested = settings.diarization_device
    available = {
        "cpu": True,
        "cuda": torch.cuda.is_available(),
        "mps": torch.backends.mps.is_available(),
    }
    if requested == "auto":
        requested = next(name for name in ("cuda", "mps", "cpu") if available[name])
    if not available[requested]:
        raise ModelsNotReady(
            f"此机器不支持人物判断设备 {requested}，请调整 DLS_DIARIZATION_DEVICE。"
        )
    return torch.device(requested)


class PyannoteProvider:
    def __init__(self, settings: Settings, config: dict | None = None):
        from pyannote.audio import Pipeline
        from pyannote.audio.pipelines.speaker_verification import PretrainedSpeakerEmbedding

        path, self.namespace = model_path(settings, "diarization", config)
        self.pipeline = Pipeline.from_pretrained(str(path))
        if self.pipeline is None:
            raise ModelsNotReady("人物判断模型加载失败，请重新执行 prepare-models。")
        device = diarization_device(settings)
        self.pipeline.segmentation_batch_size = 8
        self.pipeline.embedding_batch_size = 8
        self.pipeline.to(device)
        self.pipeline.embedding_exclude_overlap = True
        self.embedding = PretrainedSpeakerEmbedding(self.pipeline.embedding, device=device)

    def diarize(self, pcm: bytes, sample_rate: int) -> Diarization:
        import numpy as np
        import torch

        waveform = torch.from_numpy(np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768)
        with torch.inference_mode():
            output = self.pipeline({"waveform": waveform.unsqueeze(0), "sample_rate": sample_rate})
        annotation = output.speaker_diarization
        turns = [
            Turn(round(turn.start * sample_rate), round(turn.end * sample_rate), str(label))
            for turn, _, label in annotation.itertracks(yield_label=True)
        ]
        # Pipeline centroids may include overlap when a chunk lacks enough clean
        # speech. Build identity vectors only from explicitly non-overlapping audio.
        clean = {}
        for start, end, labels in speech_intervals(Diarization(turns), waveform.numel()):
            if len(labels) == 1:
                clean.setdefault(labels[0], []).append(waveform[start:end])
        embeddings = {}
        for label, slices in clean.items():
            speech = torch.cat(slices)[: 8 * sample_rate]
            if speech.numel() >= max(2 * sample_rate, self.embedding.min_num_samples):
                embeddings[label] = self.embedding(speech.reshape(1, 1, -1))[0].tolist()
        return Diarization(turns, embeddings, self.namespace)


class FunASRProvider:
    def __init__(self, settings: Settings, config: dict | None = None):
        from funasr import AutoModel

        options = {
            "device": "cpu",
            "ncpu": settings.analysis_threads,
            "disable_update": True,
            "disable_pbar": True,
            "trust_remote_code": False,
        }
        asr_path, _ = model_path(settings, "asr", config)
        punc_path, _ = model_path(settings, "punctuation", config)
        self.asr = AutoModel(model=str(asr_path), **options)
        self.punctuation = AutoModel(model=str(punc_path), **options)

    def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        import numpy as np
        import torch

        if sample_rate != 16_000:
            raise ValueError("普通话模型仅接受 16 kHz 音频。")
        waveform = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        with torch.inference_mode():
            result = self.asr.generate(input=waveform, fs=sample_rate, disable_pbar=True)
            if not isinstance(result, list) or not result or "text" not in result[0]:
                raise RuntimeError("Paraformer 返回结构不符合已验证的 text 协议。")
            text = result[0]["text"].strip()
            if text:
                punctuated = self.punctuation.generate(input=text, disable_pbar=True)
                if (
                    not isinstance(punctuated, list)
                    or not punctuated
                    or "text" not in punctuated[0]
                ):
                    raise RuntimeError("标点模型返回结构不符合已验证的 text 协议。")
                text = punctuated[0]["text"].strip()
        return text
