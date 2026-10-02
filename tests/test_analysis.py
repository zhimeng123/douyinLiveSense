import asyncio
import json
import sqlite3
from array import array
from dataclasses import replace
from datetime import datetime

import pytest
from conftest import FakeCapture, FakeSource, eventually
from fastapi.testclient import TestClient

from douyin_live_sense.analysis import AnalysisService
from douyin_live_sense.analysis_audio import read_sources
from douyin_live_sense.analysis_storage import AnalysisStorage
from douyin_live_sense.analysis_types import Diarization, Turn
from douyin_live_sense.analysis_worker import analyze_job
from douyin_live_sense.app import create_app
from douyin_live_sense.audio import SegmentWriter
from douyin_live_sense.models import pipeline_config
from douyin_live_sense.monitor import Monitor
from douyin_live_sense.storage import Storage, utc_now
from douyin_live_sense.transcripts import export_run


class SampleDiarizer:
    """Deterministic fixture: PCM amplitudes encode speakers; labels change each call."""

    def __init__(self):
        self.calls = 0

    def diarize(self, pcm, sample_rate):
        self.calls += 1
        samples = array("h", pcm)
        values = {1: ("a",), 2: ("b",), 3: ("a", "b"), 0: ()}
        turns, start = [], 0
        for end in range(1, len(samples) + 1):
            if end == len(samples) or samples[end] != samples[start]:
                for identity in values[samples[start]]:
                    turns.append(Turn(start, end, f"{self.calls}-{identity}"))
                start = end
        return Diarization(
            turns, {f"{self.calls}-a": [1, 0], f"{self.calls}-b": [0, 1]}, "fixture-v1"
        )


class SampleASR:
    def __init__(self):
        self.calls = []

    def transcribe(self, pcm, sample_rate):
        self.calls.append(pcm)
        return "普通话测试"


class FixtureWorker:
    def __init__(self, data_dir, fail=0):
        self.data_dir = data_dir
        self.diarizer, self.asr = SampleDiarizer(), SampleASR()
        self.fail, self.calls, self.closed = fail, 0, 0
        self.sequences = []

    async def execute(self, payload):
        self.calls += 1
        await asyncio.sleep(0)
        if self.calls <= self.fail:
            raise RuntimeError("测试进程退出")
        if payload["core"]:
            self.sequences.append(payload["core"]["sequence"])
        return analyze_job(payload, self.data_dir, self.diarizer, self.asr)

    async def close(self):
        self.closed += 1


@pytest.fixture
def analysis_settings(settings):
    return replace(settings, segment_seconds=4)


def recording(settings, values, close=True, store=None, session=None, timestamp=None):
    store = store or Storage(settings.data_dir)
    session = session or store.create_session("123", "live", "主播", "中文直播")
    writer = SegmentWriter(store, settings, session["id"])
    pcm = b"".join(array("h", [value]).tobytes() * samples for value, samples in values)
    writer.feed(pcm, timestamp or utc_now())
    if close:
        writer.close()
    return store, session, writer, pcm


def process_all(storage, run, settings, diarizer=None, asr=None):
    diarizer, asr = diarizer or SampleDiarizer(), asr or SampleASR()
    while job := storage.next_job(storage.run(run["id"]), live=False):
        storage.begin(job)
        result = analyze_job(json.loads(job["payload_json"]), settings.data_dir, diarizer, asr)
        storage.commit(job, result)
    return storage.utterances(run["id"], limit=500), diarizer, asr


def test_core_samples_once_cross_file_tail_and_stable_ids(analysis_settings):
    settings = analysis_settings
    values = [(1, 16_000 * 6), (2, 16_000 * 4), (1, 16_000 * 3)]
    store, session, _, pcm = recording(settings, values)
    storage = AnalysisStorage(store)
    run = storage.request(session["id"], pipeline_config(settings))
    rows, diarizer, asr = process_all(storage, run, settings)
    assert b"".join(asr.calls) == pcm
    assert len(rows) == 3 and len(rows[0]["sources"]) == 2
    assert rows[0]["speaker_ids"] == rows[2]["speaker_ids"] != rows[1]["speaker_ids"]
    assert [p["number"] for p in storage.profiles(run["id"])] == [1, 2]
    assert diarizer.calls == 4
    status = storage.status(session["id"])
    assert status["processed_seconds"] == status["target_seconds"] == 13
    identities = [row["id"] for row in rows]
    again = storage.request(session["id"], pipeline_config(settings))
    assert again["id"] == run["id"]
    assert storage.next_job(again, False) is None
    assert [row["id"] for row in storage.utterances(run["id"])] == identities
    store.close()


def test_silence_short_uncertain_and_empty_asr(analysis_settings):
    settings = analysis_settings
    store, session, _, _ = recording(settings, [(0, 64_000), (1, 8_000)])
    storage = AnalysisStorage(store)
    run = storage.request(session["id"], pipeline_config(settings))

    class EmptyASR(SampleASR):
        def transcribe(self, pcm, sample_rate):
            self.calls.append(pcm)
            return ""

    rows, _, asr = process_all(storage, run, settings, asr=EmptyASR())
    assert len(rows) == len(asr.calls) == 1
    assert set(rows[0]["flags"]) == {"speaker_uncertain", "unrecognized"}
    assert storage.status(session["id"])["processed_seconds"] == 4.5
    store.close()


def test_overlap_preserves_mixed_audio_and_never_changes_committed_rows(analysis_settings):
    settings = analysis_settings
    store, session, _, pcm = recording(settings, [(1, 64_000), (3, 64_000), (2, 64_000)])
    storage = AnalysisStorage(store)
    run = storage.request(session["id"], pipeline_config(settings))
    rows, _, asr = process_all(storage, run, settings)
    assert b"".join(asr.calls) == pcm
    assert "overlap" in rows[1]["flags"]
    assert "speaker_uncertain" in rows[1]["flags"]
    assert len(rows[1]["speaker_ids"]) == 1
    assert len(storage.profiles(run["id"])) == 2
    assert rows[1] == storage.utterances(run["id"])[1]
    store.close()


def test_reconnect_keeps_gap_and_speaker_registry(analysis_settings):
    settings = analysis_settings
    store, session, _, _ = recording(settings, [(1, 64_000)], timestamp="2026-10-01T00:00:00+00:00")
    recording(
        settings, [(1, 64_000)], store=store, session=session, timestamp="2026-10-01T00:00:20+00:00"
    )
    storage = AnalysisStorage(store)
    run = storage.request(session["id"], pipeline_config(settings))
    rows, _, _ = process_all(storage, run, settings)
    assert len(rows) == 2 and rows[0]["speaker_ids"] == rows[1]["speaker_ids"]
    assert (
        datetime.fromisoformat(rows[1]["start_at"]) - datetime.fromisoformat(rows[0]["end_at"])
    ).total_seconds() == 16
    assert all(len(row["sources"]) == 1 for row in rows)
    store.close()


def test_pending_turn_restores_and_transaction_is_atomic(analysis_settings):
    settings = analysis_settings
    store, session, writer, _ = recording(settings, [(1, 128_000)], close=False)
    storage = AnalysisStorage(store)
    run = storage.request(session["id"], pipeline_config(settings))
    job = storage.next_job(run, True)
    storage.begin(job)
    diarizer, asr = SampleDiarizer(), SampleASR()
    result = analyze_job(json.loads(job["payload_json"]), settings.data_dir, diarizer, asr)
    assert result["pending"] and not result["utterances"]
    storage.commit(job, result)
    writer.close()
    store.close()
    store = Storage(settings.data_dir)
    storage = AnalysisStorage(store)
    storage.recover()
    job = storage.next_job(storage.run(run["id"]), False)
    storage.begin(job)
    result = analyze_job(json.loads(job["payload_json"]), settings.data_dir, diarizer, asr)
    store.db.execute(
        "CREATE TRIGGER fail_insert BEFORE INSERT ON utterances "
        "BEGIN SELECT RAISE(ABORT,'crash'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="crash"):
        storage.commit(job, result)
    assert storage.run(run["id"])["processed_sequence"] == 1
    assert not storage.utterances(run["id"])
    store.db.execute("DROP TRIGGER fail_insert")
    storage.recover()
    replay = storage.next_job(storage.run(run["id"]), False)
    assert replay["id"] == job["id"]
    storage.begin(replay)
    storage.commit(replay, result)
    rows = storage.utterances(run["id"])
    assert len(rows) == 1 and len(rows[0]["sources"]) == 2
    assert len(read_sources(settings.data_dir, rows[0]["sources"])) == 128_000 * 2
    store.close()


async def test_manual_snapshot_then_live_backfill_and_disable_drain(analysis_settings):
    settings = analysis_settings
    store, session, writer, _ = recording(settings, [(1, 64_000), (2, 32_000)], close=False)
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    monitor.session, monitor.enabled = session, True
    worker = FixtureWorker(settings.data_dir)
    service = AnalysisService(monitor, settings, worker, lambda: {"ready": True, "reason": ""})
    try:
        await service.start()
        await asyncio.sleep(0.02)
        assert worker.calls == 0  # Manual is the default, regardless of recordings.
        first = service.request(session["id"])
        await eventually(lambda: service.storage.status(session["id"])["state"] == "complete")
        assert service.storage.status(session["id"])["processed_seconds"] == 4
        writer.feed(array("h", [2]).tobytes() * 32_000, utc_now())
        await service.set_realtime(True)
        assert service.storage.latest(session["id"])["id"] == first["id"]
        # Last open segment needs lookahead; disabling takes a snapshot and flushes it.
        await service.set_realtime(False)
        await eventually(lambda: service.storage.status(session["id"])["state"] == "complete")
        assert worker.sequences == [1, 2]
        writer.feed(array("h", [1]).tobytes() * 64_000, utc_now())
        await asyncio.sleep(0.03)
        assert worker.sequences == [1, 2]
        service.request(session["id"])
        await eventually(lambda: service.storage.status(session["id"])["processed_sequence"] == 3)
        assert worker.sequences == [1, 2, 3]
    finally:
        writer.close()
        await service.shutdown()
        store.close()


async def test_missing_models_preserves_requests_and_capture(analysis_settings):
    settings = analysis_settings
    store, session, _, _ = recording(settings, [(1, 64_000)])
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    ready = {"ready": False, "reason": "缺少模型"}
    worker = FixtureWorker(settings.data_dir)
    service = AnalysisService(monitor, settings, worker, lambda: ready)
    try:
        await service.start()
        run = service.request(session["id"])
        await eventually(lambda: service.storage.status(session["id"])["state"] == "needs_setup")
        assert worker.calls == 0 and store.totals(session["id"])[1] == 4
        ready.update(ready=True, reason="")
        service.wake.set()
        await eventually(lambda: service.storage.status(session["id"])["state"] == "complete")
        assert service.storage.latest(session["id"])["id"] == run["id"]
    finally:
        await service.shutdown()
        store.close()


async def test_worker_failure_once_then_attention_and_explicit_retry(analysis_settings):
    settings = analysis_settings
    store, session, _, _ = recording(settings, [(1, 64_000)])
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    worker = FixtureWorker(settings.data_dir, fail=2)
    service = AnalysisService(monitor, settings, worker, lambda: {"ready": True, "reason": ""})
    try:
        await service.start()
        service.request(session["id"])
        await eventually(
            lambda: service.storage.status(session["id"])["state"] == "needs_attention", 3
        )
        assert worker.calls == 2 and not service.storage.utterances(
            service.storage.latest(session["id"])["id"]
        )
        service.request(session["id"], retry=True)
        await eventually(lambda: service.storage.status(session["id"])["state"] == "complete")
        assert worker.calls == 3
        assert service.storage.status(session["id"])["utterance_count"] == 1
    finally:
        await service.shutdown()
        store.close()


def test_documents_are_rebuildable_and_audio_spans_match(analysis_settings):
    settings = analysis_settings
    store, session, _, pcm = recording(settings, [(1, 100_000)])
    storage = AnalysisStorage(store)
    run = storage.request(session["id"], pipeline_config(settings))
    rows, _, _ = process_all(storage, run, settings)
    paths = export_run(storage, storage.run(run["id"]))
    text = paths["txt"].read_text()
    assert "音频接收时间" in text and "人物 1" in text and "普通话测试" in text
    assert len(paths["jsonl"].read_text().splitlines()) == 1
    paths["txt"].write_text("被中断的旧文档")
    export_run(storage, storage.run(run["id"]))
    assert paths["txt"].read_text() == text
    assert read_sources(settings.data_dir, rows[0]["sources"]) == pcm
    store.close()


def test_analysis_api_incremental_docs_and_audio(analysis_settings):
    import io
    import time
    import wave

    settings = analysis_settings
    store, session, _, pcm = recording(settings, [(1, 100_000)])
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    worker = FixtureWorker(settings.data_dir)
    service = AnalysisService(monitor, settings, worker, lambda: {"ready": True, "reason": ""})
    with TestClient(create_app(settings, monitor, service), base_url="http://127.0.0.1") as client:
        assert client.get("/api/analysis/status").json()["worker_state"] == "idle"
        assert client.put("/api/analysis/realtime", json={"enabled": True}).status_code == 409
        assert client.post("/api/sessions/missing/analysis").status_code == 404
        assert (
            client.get(f"/api/sessions/{session['id']}/analysis").json()["state"] == "not_requested"
        )
        run = client.post(f"/api/sessions/{session['id']}/analysis").json()
        repeated = client.post(f"/api/sessions/{session['id']}/analysis").json()
        assert run["id"] == repeated["id"]
        deadline = time.monotonic() + 3
        while client.get(f"/api/sessions/{session['id']}/analysis").json()["state"] != "complete":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        page = client.get(f"/api/sessions/{session['id']}/utterances").json()
        assert len(page["items"]) == 1
        assert (
            client.get(
                f"/api/sessions/{session['id']}/utterances?after={page['next_cursor']}"
            ).json()["items"]
            == []
        )
        audio = client.get(f"/api/utterances/{page['items'][0]['id']}/audio")
        with wave.open(io.BytesIO(audio.content)) as wav:
            assert wav.readframes(wav.getnframes()) == pcm
        for extension in ("txt", "md", "jsonl"):
            result = client.get(f"/api/sessions/{session['id']}/transcript?format={extension}")
            assert (
                result.status_code == 200 and "attachment" in result.headers["content-disposition"]
            )
        assert client.get(f"/api/sessions/{session['id']}/transcript?format=exe").status_code == 422
        assert client.get("/api/utterances/missing/audio").status_code == 404
        assert (
            client.post(
                f"/api/sessions/{session['id']}/analysis", headers={"Origin": "https://evil.test"}
            ).status_code
            == 403
        )
    store.close()
