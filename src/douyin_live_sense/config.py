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

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data_dir=Path(os.environ.get("DLS_DATA_DIR", "data")).expanduser().resolve(),
            cookie=os.environ.get("DOUYIN_COOKIE", ""),
        )
