import math
import os
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .analysis_audio import read_sources, source_span
from .analysis_types import (
    Diarization,
    DiarizationProvider,
    TranscriptionProvider,
    speech_intervals,
)
from .config import Settings
from .security import redact


def normalize(vector: list[float]) -> list[float] | None:
    if not vector or not all(math.isfinite(value) for value in vector):
        return None
    length = math.sqrt(sum(value * value for value in vector))
    return [value / length for value in vector] if length > 1e-8 else None


def similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return -1
    return sum(a * b for a, b in zip(left, right, strict=True))


def identify(
    output: Diarization,
    intervals: list,
    core_start: int,
    core_end: int,
    segments: list[dict],
    offsets: dict[str, int],
    previous: list[dict],
    profiles: list[dict],
    config: dict,
) -> dict[str, str | None]:
    if any(profile["namespace"] != output.embedding_namespace for profile in profiles):
        raise ValueError("人物嵌入模型版本发生变化，需创建新的分析版本，不能混用声纹。")
    first, clean = {}, {}
    for start, end, labels in intervals:
        for label in labels:
            if len(labels) == 1:
                clean[label] = clean.get(label, 0) + max(
                    0, min(end, core_end) - max(start, core_start)
                )
            if end > core_start and start < core_end:
                first.setdefault(label, max(start, core_start))
    evidence: dict[str, dict[str, int]] = {}
    for assignment in previous:
        if len(assignment["speaker_ids"]) != 1 or "speaker_uncertain" in assignment["flags"]:
            continue
        if assignment["segment_id"] not in offsets:
            continue
        begin = offsets[assignment["segment_id"]] + assignment["start_sample"]
        finish = offsets[assignment["segment_id"]] + assignment["end_sample"]
        for start, end, labels in intervals:
            if len(labels) == 1:
                overlap = max(0, min(finish, end) - max(begin, start))
                if overlap:
                    counts = evidence.setdefault(labels[0], {})
                    speaker_id = assignment["speaker_ids"][0]
                    counts[speaker_id] = counts.get(speaker_id, 0) + overlap
    mapped: dict[str, str | None] = {}
    used = set()
    for label in sorted(first, key=lambda item: (first[item], item)):
        scores = sorted(evidence.get(label, {}).items(), key=lambda item: item[1], reverse=True)
        chosen = None
        if scores and scores[0][1] >= 8_000 and scores[0][0] not in used:
            if len(scores) == 1 or scores[0][1] >= 2 * scores[1][1]:
                chosen = scores[0][0]
        vector = normalize(output.embeddings.get(label, []))
        reliable = clean.get(label, 0) >= round(config["speaker_min_seconds"] * 16_000)
        candidates = sorted(
            [
                (profile, similarity(vector, profile["embedding"]))
                for profile in profiles
                if vector is not None
            ],
            key=lambda item: item[1],
            reverse=True,
        )
        if chosen is None and reliable and candidates:
            margin = candidates[0][1] - (candidates[1][1] if len(candidates) > 1 else -1)
            if (
                candidates[0][1] >= config["speaker_similarity"]
                and margin >= config["speaker_margin"]
                and candidates[0][0]["id"] not in used
            ):
                chosen = candidates[0][0]["id"]
        # Borderline similarity is uncertainty, not evidence of a new person.
        if chosen is None and reliable and vector is not None:
            if not candidates or candidates[0][1] < 0.55:
                chosen = uuid4().hex
                profiles.append(
                    {
                        "id": chosen,
                        "number": max((p["number"] for p in profiles), default=0) + 1,
                        "namespace": output.embedding_namespace,
                        "embedding": vector,
                        "clean_samples": 0,
                    }
                )
        mapped[label] = chosen
        if chosen is not None:
            used.add(chosen)
            if reliable and vector is not None:
                profile = next(p for p in profiles if p["id"] == chosen)
                weight = clean[label]
                total = profile["clean_samples"] + weight
                combined = [
                    (old * profile["clean_samples"] + new * weight) / total
                    for old, new in zip(profile["embedding"], vector, strict=True)
                ]
                profile["embedding"] = normalize(combined) or profile["embedding"]
                profile["clean_samples"] = total
    return mapped


def analyze_job(
    payload: dict, data_dir: Path, diarizer: DiarizationProvider, transcriber: TranscriptionProvider
) -> dict:
    config = payload["config"]
    profiles = payload["profiles"]
    pending = payload["pending"]
    utterances, assignments = [], []
    pause = config["pause_seconds"]
    maximum = round(config["max_turn_seconds"] * 16_000)

    def finish():
        nonlocal pending
        if pending is None:
            return
        sources = pending["sources"]
        text = transcriber.transcribe(read_sources(data_dir, sources), 16_000)
        flags = list(pending["flags"])
        if not text:
            flags.append("unrecognized")
        utterances.append(
            {
                "id": uuid4().hex,
                "start_at": sources[0]["start_at"],
                "end_at": sources[-1]["end_at"],
                "speaker_ids": pending["speaker_ids"],
                "flags": flags,
                "sources": sources,
                "text": text,
            }
        )
        pending = None

    core = payload["core"]
    if core is not None:
        if pending and pending["epoch_id"] != core["epoch_id"]:
            finish()
        chunks, offsets, count = [], {}, 0
        for segment in payload["segments"]:
            offsets[segment["id"]] = count
            pcm = read_sources(data_dir, [source_span(segment, 0, segment["sample_count"])])
            chunks.append(pcm)
            count += segment["sample_count"]
        core_start = offsets[core["id"]]
        core_end = core_start + core["sample_count"]
        output = diarizer.diarize(b"".join(chunks), 16_000)
        intervals = speech_intervals(output, count)
        identities = identify(
            output,
            intervals,
            core_start,
            core_end,
            payload["segments"],
            offsets,
            payload["previous_assignments"],
            profiles,
            config,
        )
        for start, end, labels in intervals:
            start, end = max(start, core_start) - core_start, min(end, core_end) - core_start
            if end <= start:
                continue
            speaker_ids = sorted(
                {identities[label] for label in labels if identities[label] is not None}
            )
            flags = []
            if len(labels) > 1:
                flags.append("overlap")
            if any(identities[label] is None for label in labels):
                flags.append("speaker_uncertain")
            assignment = {
                "segment_id": core["id"],
                "start_sample": start,
                "end_sample": end,
                "speaker_ids": speaker_ids,
                "flags": flags,
            }
            assignments.append(assignment)
            source = source_span(core, start, end)
            voice_key = sorted(
                identities[label] or f"uncertain:{core['id']}:{label}" for label in labels
            )
            if pending:
                gap = (
                    datetime.fromisoformat(source["start_at"])
                    - datetime.fromisoformat(pending["sources"][-1]["end_at"])
                ).total_seconds()
                if (
                    pending.get("voice_key") != voice_key
                    or pending["speaker_ids"] != speaker_ids
                    or pending["flags"] != flags
                    or gap > pause
                    or gap < -1 / 16_000
                ):
                    finish()
            while start < end:
                if pending is None:
                    pending = {
                        "epoch_id": core["epoch_id"],
                        "speaker_ids": speaker_ids,
                        "flags": flags,
                        "voice_key": voice_key,
                        "sources": [],
                    }
                size = sum(s["end_sample"] - s["start_sample"] for s in pending["sources"])
                finish_at = min(end, start + maximum - size)
                span = source_span(core, start, finish_at)
                if (
                    pending["sources"]
                    and pending["sources"][-1]["segment_id"] == core["id"]
                    and pending["sources"][-1]["end_sample"] == start
                ):
                    pending["sources"][-1]["end_sample"] = finish_at
                    pending["sources"][-1]["end_at"] = span["end_at"]
                else:
                    pending["sources"].append(span)
                start = finish_at
                if size + finish_at - span["start_sample"] == maximum:
                    finish()
        if pending:
            trailing = (
                datetime.fromisoformat(core["end_at"])
                - datetime.fromisoformat(pending["sources"][-1]["end_at"])
            ).total_seconds()
            if trailing >= pause:
                finish()
    if payload["flush"]:
        finish()
    return {
        "profiles": profiles,
        "utterances": utterances,
        "pending": pending,
        "assignments": assignments,
    }


def worker_main(connection, settings: Settings) -> None:
    from .providers import FunASRProvider, PyannoteProvider, offline_environment

    offline_environment(settings)
    # Deliberately lazy: manual recording never loads a model.
    diarizer = transcriber = None
    active_models = None
    while True:
        try:
            command = connection.recv()
        except EOFError:
            break
        if command is None:
            break
        started = time.monotonic()
        try:
            if active_models != command["config"]["models"]:
                import torch

                torch.set_num_threads(settings.analysis_threads)
                diarizer = PyannoteProvider(settings, command["config"])
                transcriber = FunASRProvider(settings, command["config"])
                active_models = command["config"]["models"]
            result = analyze_job(command, settings.data_dir, diarizer, transcriber)
            result["elapsed_seconds"] = time.monotonic() - started
            connection.send({"result": result})
        except Exception as error:
            # The process boundary reports a failed job; the parent persists/retries it.
            connection.send(
                {"error": redact(f"{type(error).__name__}: {error}", os.getenv("HF_TOKEN", ""))}
            )
    connection.close()
