"""Provider boundary: sample offsets at 16 kHz, window-local labels, plain values."""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Turn:
    start: int
    end: int
    speaker: str


@dataclass
class Diarization:
    turns: list[Turn]
    embeddings: dict[str, list[float]] = field(default_factory=dict)
    embedding_namespace: str = ""


class DiarizationProvider(Protocol):
    def diarize(self, pcm: bytes, sample_rate: int) -> Diarization: ...


class TranscriptionProvider(Protocol):
    def transcribe(self, pcm: bytes, sample_rate: int) -> str: ...


class ModelsNotReady(RuntimeError):
    pass


def speech_intervals(output: Diarization, size: int) -> list[tuple[int, int, tuple[str, ...]]]:
    changes: dict[int, list[tuple[str, int]]] = {}
    for turn in output.turns:
        start, end = max(0, turn.start), min(size, turn.end)
        if end <= start:
            continue
        changes.setdefault(start, []).append((turn.speaker, 1))
        changes.setdefault(end, []).append((turn.speaker, -1))
    active: dict[str, int] = {}
    previous = 0
    intervals = []
    for point in sorted(changes):
        labels = tuple(sorted(label for label, count in active.items() if count > 0))
        if labels and point > previous:
            intervals.append((previous, point, labels))
        for label, delta in changes[point]:
            active[label] = active.get(label, 0) + delta
        previous = point
    return intervals
