"""Real recorded audio, independent review versions and concurrent live capture."""

import argparse
import asyncio
import json
import os
import wave
from dataclasses import replace
from uuid import uuid4

from douyin_live_sense.analysis import AnalysisService
from douyin_live_sense.analysis_audio import read_sources
from douyin_live_sense.audio import SegmentWriter
from douyin_live_sense.config import Settings
from douyin_live_sense.monitor import Monitor
from douyin_live_sense.storage import Storage, utc_now
from douyin_live_sense.transcripts import chronological


async def wait_complete(service, run_id):
    async with asyncio.timeout(300):
        while service.storage.run(run_id)["state"] != "complete":  # noqa: ASYNC110
            run = service.storage.run(run_id)
            if run["state"] in {"needs_attention", "needs_setup"}:
                raise RuntimeError(run["last_error"])
            await asyncio.sleep(0.2)


def check_audio(service, run):
    rows, cursor = [], 0
    while page := service.storage.utterances(run["id"], cursor, 500):
        rows.extend(page)
        cursor = max(row["ordinal"] for row in page)
    for row in rows:
        assert read_sources(service.settings.data_dir, row["sources"])
        if run["scope"] == "segment":
            assert {s["segment_id"] for s in row["sources"]} == {run["segment_id"]}
    paths = service.export(run)
    exported = [json.loads(line) for line in paths["jsonl"].read_text().splitlines()]
    assert [row["id"] for row in exported] == [row["id"] for row in sorted(rows, key=chronological)]
    return rows


async def run(args):
    original = Settings.from_env()
    directory = original.data_dir / "acceptance" / ("archive-" + uuid4().hex)
    directory.mkdir(parents=True)
    (directory / "models").symlink_to(original.data_dir / "models", target_is_directory=True)
    settings = replace(original, data_dir=directory)
    source = Storage(original.data_dir)
    source_segments = source.segments(args.session_id)
    if not source_segments:
        raise ValueError("来源场次没有已完成音频。")
    store = Storage(directory)
    session = store.create_session(args.room, "archive-validation", "历史真实录音", "档案复核验收")
    writer = SegmentWriter(store, settings, session["id"])
    for segment in source_segments:
        with wave.open(str(source.segment_path(segment)), "rb") as wav:
            writer.feed(wav.readframes(wav.getnframes()), source_segments[0]["start_at"])
    writer.close()
    store.finish_session(session["id"], "验收样本")
    source.close()
    monitor = Monitor(store, settings)
    service = AnalysisService(monitor, settings)
    report = {
        "started_at": utc_now(),
        "data_dir": str(directory),
        "validation": "real_recorded_audio_and_real_local_models",
        "accuracy_metrics": "not_measured_without_human_annotation",
        "passed": False,
    }
    worker_pid = None
    try:
        await service.start()
        whole = service.request(session["id"])
        if args.live_seconds:
            await monitor.start(args.room)
        await wait_complete(service, whole["id"])
        whole_rows = check_audio(service, service.storage.run(whole["id"]))
        whole_profiles = service.storage.profiles(whole["id"])
        segment = store.segments(session["id"])[0]
        first = service.request_segment(segment["id"])
        await wait_complete(service, first["requested_run_id"])
        first_rows = check_audio(service, service.storage.run(first["requested_run_id"]))
        second = service.request_segment(segment["id"], reanalyse=True)
        assert service.storage.segment_status(segment["id"])["available_revision"] == 1
        assert (
            service.request_segment(segment["id"], reanalyse=True)["requested_run_id"]
            == second["requested_run_id"]
        )
        await wait_complete(service, second["requested_run_id"])
        second_rows = check_audio(service, service.storage.run(second["requested_run_id"]))
        assert service.storage.utterances(whole["id"], limit=500) == whole_rows
        assert service.storage.profiles(whole["id"]) == whole_profiles
        assert service.storage.latest(session["id"])["id"] == whole["id"]
        report.update(
            history_checks_passed=True,
            history_seconds=store.totals(session["id"])[1],
            whole_utterances=len(whole_rows),
            review_v1_utterances=len(first_rows),
            review_v2_utterances=len(second_rows),
            whole_unchanged=True,
            single_core_only=True,
            exports_chronological=True,
        )
        print("HISTORY_AND_TWO_REVIEWS_COMPLETE", flush=True)
        if args.live_seconds:
            async with asyncio.timeout(args.live_seconds + 180):
                while monitor.status()["captured_seconds"] < args.live_seconds:  # noqa: ASYNC110
                    if monitor.state == "needs_attention":
                        raise RuntimeError(monitor.last_error)
                    await asyncio.sleep(1)
            live_id = monitor.session["id"]
            await monitor.stop()
            live = service.request(live_id)
            await wait_complete(service, live["id"])
            live_rows = check_audio(service, service.storage.run(live["id"]))
            report.update(
                live_seconds=store.totals(live_id)[1],
                live_utterances=len(live_rows),
                concurrent_capture=True,
                ffmpeg_reaped=monitor.capture.process is None,
            )
            print("CONCURRENT_LIVE_AND_WHOLE_COMPLETE", flush=True)
        assert service.queue()["waiting_count"] == 0
        assert not store.db.execute("PRAGMA foreign_key_check").fetchall()
        worker_pid = service.worker.process.pid
        report["passed"] = True
    finally:
        if service.worker.process:
            worker_pid = service.worker.process.pid
        if args.live_seconds and "live_seconds" not in report:
            report["live_capture_incomplete"] = {
                "state": monitor.state,
                "captured_seconds": monitor.status()["captured_seconds"],
                "reason": "等待开播超时" if monitor.state == "waiting" else monitor.last_error,
            }
        await monitor.shutdown()
        await service.shutdown()
        if worker_pid:
            try:
                os.kill(worker_pid, 0)
            except ProcessLookupError:
                report["worker_reaped"] = True
            else:
                report["worker_reaped"] = False
        report["ended_at"] = utc_now()
        (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        store.close()
        print(str(directory / "report.json"), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--room", default="123456789")
    parser.add_argument("--live-seconds", type=float, default=0)
    asyncio.run(run(parser.parse_args()))
