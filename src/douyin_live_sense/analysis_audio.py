import io
import wave
from pathlib import Path

from .storage import add_seconds


def read_pcm(path: Path, start: int = 0, end: int | None = None) -> bytes:
    with wave.open(str(path), "rb") as audio:
        end = audio.getnframes() if end is None else end
        if audio.getparams()[:3] != (1, 2, 16_000):
            raise ValueError("分析音频必须为 16 kHz 单声道 PCM16。")
        if not 0 <= start <= end <= audio.getnframes():
            raise ValueError("发言采样范围超出音频文件。")
        audio.setpos(start)
        pcm = audio.readframes(end - start)
        if len(pcm) != (end - start) * 2:
            raise ValueError("音频文件缺少登记的采样。")
        return pcm


def read_sources(data_dir: Path, sources: list[dict]) -> bytes:
    chunks = []
    for source in sources:
        path = (data_dir / source["relative_path"]).resolve()
        if not path.is_relative_to(data_dir.resolve()):
            raise ValueError("音频引用超出数据目录。")
        chunks.append(read_pcm(path, source["start_sample"], source["end_sample"]))
    return b"".join(chunks)


def source_span(segment: dict, start: int, end: int) -> dict:
    return {
        "segment_id": segment["id"],
        "relative_path": segment["relative_path"],
        "start_sample": start,
        "end_sample": end,
        "sample_rate": segment["sample_rate"],
        "start_at": add_seconds(segment["start_at"], start / segment["sample_rate"]),
        "end_at": add_seconds(segment["start_at"], end / segment["sample_rate"]),
    }


def wav_bytes(pcm: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, 16_000, 0, "NONE", "not compressed"))
        audio.writeframes(pcm)
    return output.getvalue()
