import json
from dataclasses import replace

import pytest
from conftest import FakeCapture, FakeSource
from fastapi.testclient import TestClient
from test_analysis import FixtureWorker, SampleASR, SampleDiarizer, recording

from douyin_live_sense.analysis import AnalysisService
from douyin_live_sense.analysis_storage import AnalysisStorage
from douyin_live_sense.analysis_worker import analyze_job
from douyin_live_sense.app import create_app
from douyin_live_sense.models import pipeline_config
from douyin_live_sense.monitor import Monitor
from douyin_live_sense.storage import Storage, utc_now


def complete_job(storage, job, settings):
    storage.begin(job)
    storage.commit(
        job,
        analyze_job(
            json.loads(job["payload_json"]), settings.data_dir, SampleDiarizer(), SampleASR()
        ),
    )


def test_request_progress_counts_committed_clips_and_single_scope(settings):
    settings = replace(settings, segment_seconds=4)
    store, session, _, _ = recording(settings, [(0, 192_000)])
    storage = AnalysisStorage(store)
    whole = storage.request(session["id"], pipeline_config(settings))
    assert storage.request(session["id"], pipeline_config(settings))["id"] == whole["id"]
    selected = store.segments(session["id"])[2]
    review = storage.request_segment(selected["id"], pipeline_config(settings))
    records = {r["run_id"]: r for r in storage.requests(None)["items"]}
    assert len(records) == 2
    assert records[whole["id"]]["total_segments"] == 3
    assert records[review["id"]]["total_segments"] == 1
    assert records[review["id"]]["completed_segments"] == 0
    job = storage.next_job(whole, False)
    storage.begin(job)
    assert storage.requests(job)["items"][0]["run_id"] == whole["id"]
    assert storage.request_segments(whole["id"], job)["items"][0]["state"] == "running"
    assert storage.requests(job)["items"][0]["progress_percent"] == 0
    storage.commit(
        job,
        analyze_job(
            json.loads(job["payload_json"]), settings.data_dir, SampleDiarizer(), SampleASR()
        ),
    )
    records = {r["run_id"]: r for r in storage.requests(None)["items"]}
    assert records[whole["id"]]["progress_percent"] == 33.3
    assert records[whole["id"]]["completed_segments"] == 1
    assert records[whole["id"]]["processed_seconds"] == 4
    assert records[whole["id"]]["target_seconds"] == 12
    assert records[review["id"]]["progress_percent"] == 0
    assert storage.request_segments(whole["id"], None)["items"][0]["state"] == "complete"
    complete_job(storage, storage.next_job(review, False), settings)
    assert storage.next_job(storage.run(review["id"]), False) is None
    records = {r["run_id"]: r for r in storage.requests(None)["items"]}
    assert records[review["id"]]["progress_percent"] == 100
    assert records[review["id"]]["processed_seconds"] == 4
    assert records[whole["id"]]["progress_percent"] == 33.3
    assert len(storage.request_segments(review["id"], None)["items"]) == 1
    store.close()


def test_progress_requires_trailing_speech_and_survives_recovery(settings):
    settings = replace(settings, segment_seconds=4)
    store, session, _, _ = recording(settings, [(1, 64_000)])
    storage = AnalysisStorage(store)
    run = storage.request(session["id"], pipeline_config(settings))
    job = storage.next_job(run, False)
    storage.begin(job)
    result = analyze_job(
        json.loads(job["payload_json"]), settings.data_dir, SampleDiarizer(), SampleASR()
    )
    result["pending"] = result["utterances"][0] | {
        "epoch_id": store.segments(session["id"])[0]["epoch_id"]
    }
    result["utterances"] = []
    storage.commit(job, result)
    record = storage.requests(None)["items"][0]
    assert record["completed_segments"] == record["total_segments"] == 1
    assert record["pending_speech"] and record["progress_percent"] == 99
    flush = storage.next_job(storage.run(run["id"]), False)
    storage.begin(flush)
    assert storage.requests(flush)["items"][0]["progress_percent"] == 99
    store.close()
    store = Storage(settings.data_dir)
    storage = AnalysisStorage(store)
    storage.recover()
    assert storage.requests(None)["items"][0]["progress_percent"] == 99
    assert storage.next_job(storage.run(run["id"]), False)["id"] == flush["id"]
    complete_job(storage, storage.next_job(storage.run(run["id"]), False), settings)
    assert storage.next_job(storage.run(run["id"]), False) is None
    record = storage.requests(None)["items"][0]
    assert record["progress_percent"] == 100 and not record["pending_speech"]
    store.close()


def test_progress_snapshot_excludes_new_audio_pending_files_and_failed_clips(settings):
    store = Storage(settings.data_dir)
    session = store.create_session("123", "test", "主播", "")
    storage = AnalysisStorage(store)
    pending = store.reserve_segment(session["id"], "epoch", utc_now(), 16_000)
    first = store.reserve_segment(session["id"], "epoch", utc_now(), 16_000)
    store.complete_segment(first["id"], 64_000)
    run = storage.request(session["id"], pipeline_config(settings))
    extra = store.reserve_segment(session["id"], "epoch", utc_now(), 16_000)
    store.complete_segment(extra["id"], 32_000)
    job = storage.next_job(run, False)
    for _ in range(2):
        storage.begin(job)
        storage.fail(job, "测试失败")
    record = storage.requests(None)["items"][0]
    assert record["total_segments"] == 1 and record["progress_percent"] == 0
    assert record["target_seconds"] == 4 and record["state"] == "needs_attention"
    page = storage.request_segments(run["id"], None)
    assert page["total_count"] == 1 and page["items"][0]["state"] == "failed"
    assert page["items"][0]["segment_id"] == first["id"]
    assert pending["id"] not in {s["segment_id"] for s in page["items"]}
    storage.request(session["id"], pipeline_config(settings), retry=True)
    assert storage.requests(None)["items"][0]["total_segments"] == 2
    assert storage.request_segments(run["id"], None)["items"][0]["state"] == "pending"
    store.close()


def test_progress_record_and_clip_pagination(settings):
    store = Storage(settings.data_dir)
    session = store.create_session("123", "test", "主播", "")
    storage = AnalysisStorage(store)
    for _ in range(105):
        segment = store.reserve_segment(session["id"], "epoch", utc_now(), 16_000)
        store.complete_segment(segment["id"], 480_000)
        storage.request_segment(segment["id"], pipeline_config(settings))
    whole = storage.request(session["id"], pipeline_config(settings))
    page = storage.requests(None, limit=50, offset=100)
    assert page["total_count"] == 106 and len(page["items"]) == 6 and not page["has_more"]
    page = storage.request_segments(whole["id"], None, limit=50, offset=100)
    assert page["total_count"] == 105 and len(page["items"]) == 5 and not page["has_more"]
    assert [s["sequence"] for s in page["items"]] == list(range(101, 106))
    with pytest.raises(KeyError):
        storage.request_segments("missing", None)
    store.close()


def test_progress_api_and_validation(settings):
    settings = replace(settings, segment_seconds=4)
    store, session, _, _ = recording(settings, [(0, 128_000)])
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    service = AnalysisService(
        monitor,
        settings,
        FixtureWorker(settings.data_dir),
        lambda: {"ready": False, "reason": "测试等待模型"},
    )
    run = service.storage.request(session["id"], pipeline_config(settings))
    with TestClient(create_app(settings, monitor, service), base_url="http://127.0.0.1") as client:
        records = client.get("/api/analysis/requests").json()
        assert records["total_count"] == 1
        assert records["items"][0]["total_segments"] == 2
        assert records["items"][0]["progress_percent"] == 0
        page = client.get(f"/api/analysis/requests/{run['id']}/segments?limit=1").json()
        assert page["total_count"] == 2 and page["has_more"]
        assert client.get("/api/analysis/requests/missing/segments").status_code == 404
        assert client.get("/api/analysis/requests?limit=101").status_code == 422
        assert (
            client.get(f"/api/analysis/requests/{run['id']}/segments?offset=-1").status_code == 422
        )
    store.close()
