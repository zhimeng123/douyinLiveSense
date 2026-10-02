import asyncio
import json
import sqlite3
import time
from dataclasses import replace

import pytest
from conftest import FakeCapture, FakeSource, eventually
from fastapi.testclient import TestClient
from test_analysis import FixtureWorker, process_all, recording

from douyin_live_sense.analysis import AnalysisService
from douyin_live_sense.analysis_audio import read_sources
from douyin_live_sense.analysis_storage import AnalysisStorage
from douyin_live_sense.analysis_worker import analyze_job
from douyin_live_sense.app import create_app
from douyin_live_sense.models import pipeline_config
from douyin_live_sense.monitor import Monitor
from douyin_live_sense.storage import Storage, utc_now
from douyin_live_sense.transcripts import export_run


def test_single_core_uses_neighbors_but_only_commits_selected_audio(settings):
    settings = replace(settings, segment_seconds=4)
    store, session, _, pcm = recording(settings, [(1, 192_000)])
    storage = AnalysisStorage(store)
    segments = store.segments(session["id"])
    run = storage.request_segment(segments[1]["id"], pipeline_config(settings))
    job = storage.next_job(run, False)
    payload = json.loads(job["payload_json"])
    assert [s["sequence"] for s in payload["segments"]] == [1, 2, 3]
    assert payload["flush"] and not payload["profiles"] and not payload["pending"]
    from test_analysis import SampleASR, SampleDiarizer

    storage.begin(job)
    asr = SampleASR()
    storage.commit(job, analyze_job(payload, settings.data_dir, SampleDiarizer(), asr))
    assert storage.next_job(storage.run(run["id"]), False) is None
    rows = storage.utterances(run["id"])
    assert b"".join(asr.calls) == pcm[128_000:256_000]
    assert {s["segment_id"] for row in rows for s in row["sources"]} == {segments[1]["id"]}
    assert storage.latest(session["id"]) is None
    assert storage.status(session["id"])["state"] == "not_requested"
    assert storage.segment_status(segments[1]["id"])["available_revision"] == 1
    store.close()


def test_review_versions_keep_previous_success_and_never_overwrite_whole(settings):
    settings = replace(settings, segment_seconds=30)
    store, session, _, _ = recording(settings, [(1, 600_000)])
    storage = AnalysisStorage(store)
    whole = storage.request(session["id"], pipeline_config(settings))
    whole_rows, _, _ = process_all(storage, whole, settings)
    whole_profiles = storage.profiles(whole["id"])
    segment = store.segments(session["id"])[0]
    review = storage.request_segment(segment["id"], pipeline_config(settings))
    assert (
        storage.request_segment(segment["id"], pipeline_config(settings), True)["id"]
        == review["id"]
    )
    review_rows, _, _ = process_all(storage, review, settings)
    assert set(review_rows[0]["speaker_ids"]).isdisjoint(whole_rows[0]["speaker_ids"])
    new = storage.request_segment(segment["id"], pipeline_config(settings), True)
    assert new["revision"] == 2 and new["id"] != review["id"]
    assert storage.segment_status(segment["id"])["available_run_id"] == review["id"]
    job = storage.next_job(new, False)
    for _ in range(2):
        storage.begin(job)
        storage.fail(job, "复核失败")
    assert storage.segment_status(segment["id"])["state"] == "needs_attention"
    assert storage.segment_status(segment["id"])["available_revision"] == 1
    _, rows = storage.segment_utterances(segment["id"], "segment")
    assert rows == review_rows
    storage.request_segment(segment["id"], pipeline_config(settings), retry=True)
    assert storage.next_job(storage.run(new["id"]), False)["id"] == job["id"]
    new_rows, _, _ = process_all(storage, storage.run(new["id"]), settings)
    assert storage.segment_status(segment["id"])["available_revision"] == 2
    assert new_rows != review_rows
    assert storage.latest(session["id"])["id"] == whole["id"]
    assert storage.utterances(whole["id"]) == whole_rows
    assert storage.profiles(whole["id"]) == whole_profiles
    store.close()


def test_cross_slice_index_and_single_export_scope(settings):
    settings = replace(settings, segment_seconds=4)
    store, session, _, pcm = recording(settings, [(1, 100_000)])
    storage = AnalysisStorage(store)
    whole = storage.request(session["id"], pipeline_config(settings))
    whole_rows, _, _ = process_all(storage, whole, settings)
    assert len(whole_rows) == 1
    for segment in store.segments(session["id"]):
        run, rows = storage.segment_utterances(segment["id"], "session")
        assert run["id"] == whole["id"] and rows == whole_rows
    first = store.segments(session["id"])[0]
    review = storage.request_segment(first["id"], pipeline_config(settings))
    process_all(storage, review, settings)
    store.event(session["id"], "gap", "测试缺口")
    paths = export_run(storage, storage.run(review["id"]))
    assert "单片复核 v1" in paths["txt"].read_text()
    assert "采集缺口" not in paths["txt"].read_text()
    assert read_sources(settings.data_dir, whole_rows[0]["sources"]) == pcm
    store.close()


def test_queue_counts_unmaterialized_jobs_active_flush_and_failure(settings):
    settings = replace(settings, segment_seconds=4)
    store, session, _, _ = recording(settings, [(1, 192_000)])
    storage = AnalysisStorage(store)
    whole = storage.request(session["id"], pipeline_config(settings))
    segments = store.segments(session["id"])
    review = storage.request_segment(segments[2]["id"], pipeline_config(settings))
    queue = storage.queue(None, limit=2)
    assert queue["waiting_count"] == 4 and queue["has_more"]
    assert [t["scope"] for t in queue["items"]] == ["segment", "session"]
    assert storage.db.execute("SELECT COUNT(*) FROM analysis_jobs").fetchone()[0] == 0
    assert [r["id"] for r in storage.runs(session["id"])] == [review["id"], whole["id"]]
    job = storage.next_job(review, False)
    storage.begin(job)
    queue = storage.queue(job)
    assert queue["waiting_count"] == 3 and all(t["run_id"] != review["id"] for t in queue["items"])
    storage.fail(job, "失败1")
    assert storage.queue(None)["waiting_count"] == 4
    storage.begin(job)
    storage.fail(job, "失败2")
    queue = storage.queue(None)
    assert queue["waiting_count"] == 3 and queue["failed_count"] == 1
    assert queue["failures"][0]["last_error"] == "失败2"
    store.close()


def test_queue_pending_speech_flush_and_waiting_context(settings):
    settings = replace(settings, segment_seconds=4)
    store, session, writer, _ = recording(settings, [(1, 128_000)], close=False)
    storage = AnalysisStorage(store)
    run = storage.request(session["id"], pipeline_config(settings))
    job = storage.next_job(run, True)
    from test_analysis import SampleASR, SampleDiarizer

    storage.begin(job)
    storage.commit(
        job,
        analyze_job(
            json.loads(job["payload_json"]), settings.data_dir, SampleDiarizer(), SampleASR()
        ),
    )
    assert storage.next_job(storage.run(run["id"]), True) is None
    queue = storage.queue(None)
    assert queue["waiting_count"] == 1 and "上下文" in queue["items"][0]["wait_reason"]
    writer.close()
    job = storage.next_job(storage.run(run["id"]), False)
    storage.begin(job)
    result = analyze_job(
        json.loads(job["payload_json"]), settings.data_dir, SampleDiarizer(), SampleASR()
    )
    # Simulate an already committed last core with a deferred trailing utterance.
    result["pending"] = result["utterances"][0] | {"epoch_id": writer.epoch_id}
    result["utterances"] = []
    storage.commit(job, result)
    queue = storage.queue(None)
    assert queue["waiting_count"] == 1 and queue["items"][0]["kind"] == "flush"
    flush = storage.next_job(storage.run(run["id"]), False)
    storage.begin(flush)
    assert storage.queue(flush)["waiting_count"] == 0
    store.close()


def test_ready_validation_summary_and_queue_pagination(settings):
    store = Storage(settings.data_dir)
    session = store.create_session("123", "test", "主播", "")
    storage = AnalysisStorage(store)
    with pytest.raises(KeyError):
        storage.request_segment("missing", pipeline_config(settings))
    first = store.reserve_segment(session["id"], "epoch", utc_now(), 16_000)
    with pytest.raises(ValueError, match="尚未保存完成"):
        storage.request_segment(first["id"], pipeline_config(settings))
    for _ in range(104):
        segment = store.reserve_segment(session["id"], "epoch", utc_now(), 16_000)
        store.complete_segment(segment["id"], 480_000)
    storage.request(session["id"], pipeline_config(settings))
    queue = storage.queue(None, limit=50, offset=100)
    assert queue["waiting_count"] == 104 and len(queue["items"]) == 4 and not queue["has_more"]
    rows = storage.segment_summaries(store.segments(session["id"]))
    assert all(s["analysis"]["session_state"] == "pending" for s in rows)
    assert all(s["analysis"]["review_state"] == "not_requested" for s in rows)
    store.close()


def test_v1_migration_indexes_old_sources_preserves_requests_and_wal(settings):
    settings = replace(settings, segment_seconds=4)
    store, session, _, _ = recording(settings, [(1, 100_000)])
    storage = AnalysisStorage(store)
    run = storage.request(session["id"], pipeline_config(settings))
    old_rows, _, _ = process_all(storage, run, settings)
    unfinished = storage.request(session["id"], pipeline_config(settings))
    with store.db:
        store.db.execute("DROP TABLE utterance_sources")
        store.db.execute("DROP INDEX analysis_scope")
        for column in ("scope", "segment_id", "revision"):
            store.db.execute(f"ALTER TABLE analysis_runs DROP COLUMN {column}")
        store.db.execute("PRAGMA user_version=1")
    store.close()
    store = Storage(settings.data_dir)
    storage = AnalysisStorage(store)
    assert storage.latest(session["id"])["id"] == unfinished["id"]
    assert storage.utterances(run["id"]) == old_rows
    for segment in store.segments(session["id"]):
        assert storage.segment_utterances(segment["id"], "session")[1] == old_rows
    backups = list((settings.data_dir / "backups").glob("before-analysis-v1-*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM utterances").fetchone()[0] == 1
    assert store.db.execute("PRAGMA foreign_key_check").fetchall() == []
    store.close()


async def test_single_recovery_and_priority_after_active_window(settings):
    settings = replace(settings, segment_seconds=4)
    store, session, _, _ = recording(settings, [(1, 192_000)])
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    entered, release = asyncio.Event(), asyncio.Event()

    class BlockingWorker(FixtureWorker):
        async def execute(self, payload):
            if not entered.is_set():
                entered.set()
                await release.wait()
            return await super().execute(payload)

    worker = BlockingWorker(settings.data_dir)
    service = AnalysisService(monitor, settings, worker, lambda: {"ready": True})
    try:
        await service.start()
        whole = service.request(session["id"])
        await eventually(entered.is_set)
        segment = store.segments(session["id"])[2]
        review = service.request_segment(segment["id"])
        queue = service.queue()
        assert queue["active"]["scope"] == "session" and queue["active"]["sequence"] == 1
        assert queue["waiting_count"] == 3 and queue["items"][0]["scope"] == "segment"
        release.set()
        await eventually(
            lambda: service.storage.segment_status(segment["id"])["state"] == "complete"
        )
        await eventually(lambda: service.storage.status(session["id"])["state"] == "complete")
        assert worker.sequences == [1, 3, 2, 3]
        assert service.storage.latest(session["id"])["id"] == whole["id"]
        new = service.storage.request_segment(segment["id"], pipeline_config(settings), True)
        job = service.storage.next_job(new, False)
        service.storage.begin(job)
        assert (
            service.storage.segment_status(segment["id"])["available_run_id"]
            == review["requested_run_id"]
        )
    finally:
        await service.shutdown()
        store.close()
    store = Storage(settings.data_dir)
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    worker = FixtureWorker(settings.data_dir)
    service = AnalysisService(monitor, settings, worker, lambda: {"ready": True})
    try:
        await service.start()
        await eventually(
            lambda: service.storage.segment_status(segment["id"])["available_revision"] == 2
        )
        assert worker.sequences == [3]
    finally:
        await service.shutdown()
        store.close()


def test_api_clip_versions_sorted_cursor_exports_and_origin(settings):
    settings = replace(settings, segment_seconds=4)
    store, session, _, _ = recording(settings, [(1, 64_000), (2, 64_000), (1, 64_000)])
    storage = AnalysisStorage(store)
    run = storage.request(session["id"], pipeline_config(settings))
    rows, _, _ = process_all(storage, run, settings)
    # Commit order can differ from receive time; the cursor must remain the maximum ordinal.
    with store.db:
        store.db.execute(
            "UPDATE utterances SET start_at=?,end_at=? WHERE id=?",
            ("2026-09-01T00:00:00+00:00", "2026-09-01T00:00:04+00:00", rows[1]["id"]),
        )
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    service = AnalysisService(
        monitor, settings, FixtureWorker(settings.data_dir), lambda: {"ready": True}
    )
    segment = store.segments(session["id"])[1]
    with TestClient(create_app(settings, monitor, service), base_url="http://127.0.0.1") as client:
        page = client.get(f"/api/sessions/{session['id']}/utterances?limit=2").json()
        assert [r["id"] for r in page["items"]] == [rows[1]["id"], rows[0]["id"]]
        assert page["next_cursor"] == rows[1]["ordinal"]
        next_page = client.get(
            f"/api/sessions/{session['id']}/utterances?after={page['next_cursor']}"
        ).json()
        assert [r["id"] for r in next_page["items"]] == [rows[2]["id"]]
        for format in ("txt", "md", "jsonl"):
            doc = client.get(f"/api/sessions/{session['id']}/transcript?format={format}").text
            if format == "jsonl":
                assert [json.loads(line)["id"] for line in doc.splitlines()] == [
                    rows[1]["id"],
                    rows[0]["id"],
                    rows[2]["id"],
                ]
            else:
                assert doc.index("2026-09-01") < doc.index("人物 1")
        assert client.get("/api/analysis/queue?limit=101").status_code == 422
        assert client.post("/api/segments/missing/analysis").status_code == 404
        pending = store.reserve_segment(session["id"], "new", utc_now(), 16_000)
        assert client.post(f"/api/segments/{pending['id']}/analysis").status_code == 409
        assert (
            client.post(
                f"/api/segments/{segment['id']}/analysis", headers={"Origin": "https://evil.test"}
            ).status_code
            == 403
        )
        first = client.post(f"/api/segments/{segment['id']}/analysis").json()
        deadline = time.monotonic() + 3
        while client.get(f"/api/segments/{segment['id']}/analysis").json()["state"] != "complete":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        review = client.get(f"/api/segments/{segment['id']}/utterances?scope=segment").json()
        whole = client.get(f"/api/segments/{segment['id']}/utterances?scope=session").json()
        assert review["run_id"] == first["requested_run_id"] and whole["run_id"] == run["id"]
        assert len(review["items"]) == len(whole["items"]) == 1
        assert client.get(f"/api/segments/{segment['id']}/transcript").status_code == 200
        assert client.get(f"/api/sessions/{session['id']}").json()["segment_count"] == 3
        segments = client.get(f"/api/sessions/{session['id']}/segments?around=2&limit=1").json()
        assert segments[0]["id"] == segment["id"] and segments[0]["page_offset"] == 1
    store.close()


async def test_review_during_realtime_does_not_follow_new_audio(settings):
    from array import array

    settings = replace(settings, segment_seconds=4)
    store, session, writer, _ = recording(settings, [(1, 64_000)], close=False)
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    monitor.enabled, monitor.session = True, session
    store.set_realtime(True)
    ready = {"ready": False, "reason": "等待模型"}
    worker = FixtureWorker(settings.data_dir)
    service = AnalysisService(monitor, settings, worker, lambda: ready)
    try:
        await service.start()
        segment = store.segments(session["id"])[0]
        review = service.request_segment(segment["id"])
        await eventually(
            lambda: service.storage.segment_status(segment["id"])["state"] == "needs_setup"
        )
        queue = service.queue()
        assert queue["waiting_count"] == 2
        assert all("模型" in t["wait_reason"] for t in queue["items"])
        writer.feed(array("h", [2]).tobytes() * 64_000, utc_now())
        ready.update(ready=True, reason="")
        service.wake.set()
        await eventually(
            lambda: service.storage.segment_status(segment["id"])["state"] == "complete"
        )
        run = service.storage.run(review["requested_run_id"])
        assert run["target_sequence"] == run["processed_sequence"] == 1
        rows = service.storage.utterances(run["id"])
        assert {s["segment_id"] for row in rows for s in row["sources"]} == {segment["id"]}
        assert service.storage.latest(session["id"])["scope"] == "session"
    finally:
        writer.close()
        await service.shutdown()
        store.close()
