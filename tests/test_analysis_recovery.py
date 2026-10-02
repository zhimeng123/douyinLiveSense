import asyncio
import json
import os
import sqlite3
from array import array
from dataclasses import replace

import pytest
from conftest import FakeCapture, FakeSource, eventually, live_room
from test_analysis import FixtureWorker, SampleASR, SampleDiarizer, process_all, recording

from douyin_live_sense.analysis import AnalysisService, ProcessWorker
from douyin_live_sense.analysis_storage import AnalysisStorage
from douyin_live_sense.analysis_worker import analyze_job
from douyin_live_sense.models import pipeline_config
from douyin_live_sense.monitor import Monitor
from douyin_live_sense.storage import Storage


def test_v0_migration_backup_preserves_recordings_and_guard(settings):
    settings.data_dir.mkdir(exist_ok=True)
    database = settings.data_dir / "sense.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE guard(id INTEGER PRIMARY KEY,room_id TEXT,enabled INTEGER,last_error TEXT);
            INSERT INTO guard VALUES (1,'123',1,NULL);
            CREATE TABLE sessions(id TEXT PRIMARY KEY,room_id TEXT,source_live_id TEXT,
                anchor_name TEXT,title TEXT,started_at TEXT,ended_at TEXT,end_reason TEXT);
            INSERT INTO sessions VALUES ('old','123','live','主播','标题','2026-10-01',NULL,NULL);
            CREATE TABLE segments(id TEXT PRIMARY KEY,session_id TEXT,sequence INTEGER,
                epoch_id TEXT,start_at TEXT,end_at TEXT,sample_rate INTEGER,sample_count INTEGER,
                relative_path TEXT,state TEXT);
            INSERT INTO segments VALUES ('seg','old',1,'epoch','2026-10-01','2026-10-01',
                16000,480000,'audio/old.wav','ready');
        """)
    store = Storage(settings.data_dir)
    assert store.guard()["enabled"] == 1 and store.guard()["realtime_analysis"] == 0
    assert store.totals("old") == (1, 30)
    assert store.epoch_closed("epoch")
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 2
    backups = list((settings.data_dir / "backups").glob("*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("SELECT sample_count FROM segments").fetchone()[0] == 480000
        assert "realtime_analysis" not in {row[1] for row in db.execute("PRAGMA table_info(guard)")}
    store.close()
    Storage(settings.data_dir).close()
    assert list((settings.data_dir / "backups").glob("*.sqlite3")) == backups


async def test_manual_snapshot_does_not_expand_while_model_setup_or_worker_busy(settings):
    settings = replace(settings, segment_seconds=4)
    store, session, writer, _ = recording(settings, [(1, 64_000)], close=False)
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    ready = {"ready": False, "reason": "需要模型"}
    worker = FixtureWorker(settings.data_dir)
    service = AnalysisService(monitor, settings, worker, lambda: ready)
    try:
        await service.start()
        service.request(session["id"])
        writer.feed(array("h", [2]).tobytes() * 64_000, "2026-10-01T00:00:00+00:00")
        assert service.storage.latest(session["id"])["target_sequence"] == 1
        ready.update(ready=True, reason="")
        service.wake.set()
        await eventually(lambda: service.storage.status(session["id"])["state"] == "complete")
        status = service.storage.status(session["id"])
        assert status["processed_seconds"] == 4 and status["unrequested_seconds"] == 4
        assert worker.sequences == [1]
    finally:
        writer.close()
        await service.shutdown()
        store.close()


async def test_live_follows_new_session_and_manual_stop_resets_default(settings):
    settings = replace(settings, segment_seconds=0.002)
    source = FakeSource(live_room(live_id="first"), live_room(live_id="second"))
    from douyin_live_sense.source import SourceError

    capture = FakeCapture((b"\x01\x00" * 33, SourceError("断线")), (b"\x01\x00" * 33, None))
    store = Storage(settings.data_dir)
    monitor = Monitor(store, settings, source, capture)
    service = AnalysisService(
        monitor, settings, FixtureWorker(settings.data_dir), lambda: {"ready": True, "reason": ""}
    )
    try:
        await service.start()
        await monitor.start("123", realtime_analysis=True)
        await eventually(lambda: capture.calls == 2)
        service.sync_live()
        second = monitor.session["id"]
        assert service.storage.latest(second) is not None
        await monitor.stop()
        service.sync_live()
        assert not store.guard()["realtime_analysis"]
        await monitor.start("123")
        assert not monitor.status()["realtime_analysis"]
    finally:
        await monitor.stop()
        await service.shutdown()
        store.close()


async def test_live_toggle_and_duplicate_start_are_serialized(settings):
    settings = replace(settings, segment_seconds=4)
    store = Storage(settings.data_dir)
    capture = FakeCapture((array("h", [1]).tobytes() * 100_000, None))
    monitor = Monitor(store, settings, FakeSource(), capture)
    service = AnalysisService(
        monitor, settings, FixtureWorker(settings.data_dir), lambda: {"ready": True, "reason": ""}
    )
    try:
        await service.start()
        await monitor.start("123", True)
        await eventually(lambda: monitor.state == "recording")
        await asyncio.gather(monitor.start("123", False), service.set_realtime(True))
        assert store.guard()["realtime_analysis"] == 1
        session_id = monitor.session["id"]

        async def request():
            await asyncio.sleep(0)
            return service.request(session_id)

        requests = await asyncio.gather(*[request() for _ in range(3)])
        assert len({request["id"] for request in requests}) == 1
        await monitor.stop()
        service.sync_live()
        await eventually(lambda: service.storage.status(session_id)["state"] == "complete")
        assert service.storage.status(session_id)["target_seconds"] == 6.25
    finally:
        await monitor.shutdown()
        await service.shutdown()
        store.close()


async def test_restart_resumes_guard_realtime_and_unfinished_analysis(settings):
    settings = replace(settings, segment_seconds=4)
    store = Storage(settings.data_dir)
    capture = FakeCapture((array("h", [1]).tobytes() * 80_000, None))
    first = Monitor(store, settings, FakeSource(), capture)
    service = AnalysisService(
        first,
        settings,
        FixtureWorker(settings.data_dir),
        lambda: {"ready": False, "reason": "缺少模型"},
    )
    await service.start()
    await first.start("123", True)
    await eventually(lambda: first.state == "recording")
    session_id = first.session["id"]
    service.sync_live()
    await first.shutdown()
    await service.shutdown()
    second = Monitor(store, settings, FakeSource(), FakeCapture((b"\x01\x00" * 8, None)))
    resumed = AnalysisService(
        second, settings, FixtureWorker(settings.data_dir), lambda: {"ready": True, "reason": ""}
    )
    try:
        await second.restore()
        await resumed.start()
        assert second.session["id"] == session_id and second.status()["realtime_analysis"]
        await eventually(lambda: resumed.storage.status(session_id)["processed_sequence"] >= 2)
        await second.stop()
        resumed.sync_live()
        await eventually(lambda: resumed.storage.status(session_id)["state"] == "complete")
        assert store.guard()["realtime_analysis"] == 0
    finally:
        await second.shutdown()
        await resumed.shutdown()
        store.close()


def test_speaker_namespace_and_short_unknown_changes(settings):
    settings = replace(settings, segment_seconds=4)
    store, session, _, _ = recording(settings, [(1, 8_000), (2, 8_000)])
    storage = AnalysisStorage(store)
    run = storage.request(session["id"], pipeline_config(settings))
    rows, _, _ = process_all(storage, run, settings)
    assert len(rows) == 2 and all("speaker_uncertain" in row["flags"] for row in rows)
    store.close()
    store, session, _, _ = recording(settings, [(1, 64_000)])
    storage = AnalysisStorage(store)
    run = storage.request(session["id"], pipeline_config(settings))
    job = storage.next_job(run, False)
    payload = json.loads(job["payload_json"])
    payload["profiles"] = [
        {
            "id": "old",
            "number": 1,
            "namespace": "other-model",
            "embedding": [1, 0],
            "clean_samples": 32_000,
        }
    ]
    with pytest.raises(ValueError, match="不能混用"):
        analyze_job(payload, settings.data_dir, SampleDiarizer(), SampleASR())
    store.close()


def test_split_local_labels_do_not_create_duplicate_voice_profiles(settings):
    settings = replace(settings, segment_seconds=30)
    store, session, _, _ = recording(settings, [(1, 128_000)])
    storage = AnalysisStorage(store)
    run = storage.request(session["id"], pipeline_config(settings))

    class SplitLabels:
        def diarize(self, pcm, sample_rate):
            from douyin_live_sense.analysis_types import Diarization, Turn

            return Diarization(
                [Turn(0, 64_000, "first"), Turn(64_000, 128_000, "second")],
                {"first": [1, 0], "second": [1, 0]},
                "fixture-v1",
            )

    rows, _, _ = process_all(storage, run, settings, diarizer=SplitLabels())
    assert len(storage.profiles(run["id"])) == 1
    assert rows[0]["speaker_ids"] and "speaker_uncertain" in rows[1]["flags"]
    store.close()


def test_future_context_does_not_allocate_ids_early_and_max_turn_is_bounded(settings):
    settings = replace(settings, segment_seconds=30)
    store, session, _, pcm = recording(settings, [(1, 16_000 * 31)])
    storage = AnalysisStorage(store)
    run = storage.request(session["id"], pipeline_config(settings))
    rows, _, asr = process_all(storage, run, settings)
    assert b"".join(asr.calls) == pcm
    assert [len(call) // 2 for call in asr.calls] == [480_000, 16_000]
    assert rows[0]["speaker_ids"] == rows[1]["speaker_ids"]
    assert len(storage.profiles(run["id"])) == 1
    store.close()


def child_echo(connection, settings):
    while connection.recv() is not None:
        connection.send({"result": {"pid": os.getpid()}})


async def test_real_spawn_process_is_reused_and_reaped(settings):
    worker = ProcessWorker(settings, target=child_echo)
    result = await worker.execute({})
    pid = result["pid"]
    assert pid != os.getpid() and worker.process.is_alive()
    assert (await worker.execute({}))["pid"] == pid
    await worker.close()
    assert worker.process is None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def child_closes_pipe(connection, settings):
    connection.close()


async def test_abrupt_worker_close_has_error_context(settings):
    worker = ProcessWorker(settings, target=child_closes_pipe)
    try:
        with pytest.raises(RuntimeError, match="分析进程"):
            await worker.execute({})
    finally:
        await worker.close()
    assert worker.process is None


async def test_export_failure_keeps_db_and_rebuild_never_reruns_models(settings, monkeypatch):
    settings = replace(settings, segment_seconds=4)
    store, session, _, _ = recording(settings, [(1, 64_000)])
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    worker = FixtureWorker(settings.data_dir)
    service = AnalysisService(monitor, settings, worker, lambda: {"ready": True, "reason": ""})
    from douyin_live_sense import analysis

    original = analysis.export_run

    def fail_export(storage, run):
        raise OSError("磁盘只读")

    monkeypatch.setattr(analysis, "export_run", fail_export)
    try:
        await service.start()
        service.request(session["id"])
        await eventually(lambda: service.storage.status(session["id"])["state"] == "complete")
        status = service.storage.status(session["id"])
        assert status["utterance_count"] == 1 and "磁盘只读" in status["export_error"]
        calls = worker.calls
        monkeypatch.setattr(analysis, "export_run", original)
        service.export(service.storage.latest(session["id"]))
        assert service.storage.status(session["id"])["export_error"] is None
        assert worker.calls == calls
    finally:
        await service.shutdown()
        store.close()


async def test_restart_exports_committed_rows_without_loading_models(settings):
    settings = replace(settings, segment_seconds=4)
    store, session, _, _ = recording(settings, [(1, 64_000)])
    storage = AnalysisStorage(store)
    run = storage.request(session["id"], pipeline_config(settings))
    rows, _, _ = process_all(storage, run, settings)
    assert storage.run(run["id"])["export_error"] == "文档待更新"
    store.close()
    store = Storage(settings.data_dir)
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    worker = FixtureWorker(settings.data_dir)
    service = AnalysisService(monitor, settings, worker, lambda: {"ready": False, "reason": "离线"})
    path = settings.data_dir / "analysis" / session["id"] / run["id"] / "transcript.jsonl"
    try:
        await service.start()
        await eventually(lambda: service.storage.run(run["id"])["export_error"] is None)
        exported = [json.loads(line) for line in path.read_text().splitlines()]
        assert [row["id"] for row in exported] == [row["id"] for row in rows]
        assert worker.calls == 0
    finally:
        await service.shutdown()
        store.close()
