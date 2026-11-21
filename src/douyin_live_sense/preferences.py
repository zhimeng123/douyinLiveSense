import json
import os
from pathlib import Path

MODES = ("record", "models", "feishu")
DEFAULT_SEGMENT_SECONDS = 30
MIN_SEGMENT_SECONDS = 1
MAX_SEGMENT_SECONDS = 14_400


def _valid_seconds(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and (
        MIN_SEGMENT_SECONDS <= value <= MAX_SEGMENT_SECONDS
    )


class Preferences:
    """站点偏好：识别方式与录音切片时长，单文件、原子写入。"""

    def __init__(self, path: Path, default_segment_seconds: float = DEFAULT_SEGMENT_SECONDS):
        self.path = path
        self.default_segment_seconds = default_segment_seconds

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        version = data.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version < 2:
            # v1 只有 recognition：开启即“下载模型识别”。
            return {"mode": "models" if data.get("recognition") is True else "record"}
        return data

    def mode(self) -> str:
        value = self._read().get("mode")
        return value if value in MODES else "record"

    def segment_seconds(self) -> float:
        value = self._read().get("segment_seconds")
        return float(value) if _valid_seconds(value) else self.default_segment_seconds

    def enabled(self) -> bool:
        return self.mode() == "models"

    def snapshot(self) -> dict:
        return {"mode": self.mode(), "segment_seconds": self.segment_seconds()}

    def update(self, *, mode: str | None = None, segment_seconds: int | None = None) -> dict:
        if mode is not None and mode not in MODES:
            raise ValueError("识别方式必须为 record、models 或 feishu。")
        if segment_seconds is not None and not _valid_seconds(segment_seconds):
            raise ValueError(
                f"切片时长必须是 {MIN_SEGMENT_SECONDS} 到 {MAX_SEGMENT_SECONDS} 之间的整数秒。"
            )
        payload = {"version": 2, "mode": self.mode() if mode is None else mode}
        stored = self._read().get("segment_seconds")
        if segment_seconds is not None:
            payload["segment_seconds"] = segment_seconds
        elif _valid_seconds(stored):
            payload["segment_seconds"] = stored
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False))
        os.replace(temporary, self.path)
        return self.snapshot()
