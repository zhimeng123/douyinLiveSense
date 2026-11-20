import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ALLOWED_HOSTS = ("127.0.0.1", "localhost")
SECURE_COOKIE_MODES = {"auto", "always", "never"}


def _parse_bool(name: str) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _parse_allowed_hosts() -> tuple[str, ...]:
    raw = os.environ.get("DLS_ALLOWED_HOSTS", "")
    hosts = tuple(item.strip() for item in raw.split(",") if item.strip())
    return hosts or DEFAULT_ALLOWED_HOSTS


def _parse_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} 必须是整数。") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间。")
    return value


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
    host: str = "127.0.0.1"
    port: int = 8787
    allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS
    trust_proxy: bool = False
    secure_cookies: str = "auto"
    session_days: int = 7

    @property
    def auth_path(self) -> Path:
        return self.data_dir / "auth.json"

    @classmethod
    def from_env(cls) -> "Settings":
        device = os.environ.get("DLS_DIARIZATION_DEVICE", "auto")
        if device not in {"auto", "cpu", "mps", "cuda"}:
            raise ValueError("DLS_DIARIZATION_DEVICE 必须为 auto、cpu、mps 或 cuda。")
        host = os.environ.get("DLS_HOST", "").strip() or "127.0.0.1"
        secure_cookies = os.environ.get("DLS_SECURE_COOKIES", "auto").strip().lower() or "auto"
        if secure_cookies not in SECURE_COOKIE_MODES:
            raise ValueError("DLS_SECURE_COOKIES 必须为 auto、always 或 never。")
        return cls(
            data_dir=Path(os.environ.get("DLS_DATA_DIR", "data")).expanduser().resolve(),
            cookie=os.environ.get("DOUYIN_COOKIE", ""),
            analysis_threads=max(1, int(os.environ.get("DLS_ANALYSIS_THREADS", "4"))),
            diarization_device=device,
            host=host,
            port=_parse_int("DLS_PORT", 8787, 1, 65_535),
            allowed_hosts=_parse_allowed_hosts(),
            trust_proxy=_parse_bool("DLS_TRUST_PROXY"),
            secure_cookies=secure_cookies,
            session_days=_parse_int("DLS_SESSION_DAYS", 7, 1, 90),
        )
