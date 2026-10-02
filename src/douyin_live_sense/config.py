import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    cookie: str = ""
    poll_seconds: float = 30
    idle_seconds: float = 20
    resolve_seconds: float = 40
    segment_seconds: float = 30
    min_free_bytes: int = 1_000_000_000
    retry_delays: tuple[float, ...] = (2, 5, 10, 30)
    ffmpeg: str = "ffmpeg"
    sample_rate: int = 16_000
    analysis_threads: int = 4
    diarization_device: str = "auto"
    speaker_similarity: float = 0.75
    speaker_margin: float = 0.08

    @classmethod
    def from_env(cls) -> "Settings":
        device = os.environ.get("DLS_DIARIZATION_DEVICE", "auto")
        if device not in {"auto", "cpu", "mps", "cuda"}:
            raise ValueError("DLS_DIARIZATION_DEVICE 必须为 auto、cpu、mps 或 cuda。")
        return cls(
            data_dir=Path(os.environ.get("DLS_DATA_DIR", "data")).expanduser().resolve(),
            cookie=os.environ.get("DOUYIN_COOKIE", ""),
            analysis_threads=max(1, int(os.environ.get("DLS_ANALYSIS_THREADS", "4"))),
            diarization_device=device,
        )
