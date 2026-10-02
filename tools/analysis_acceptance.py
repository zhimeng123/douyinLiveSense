"""Real SDK + models, manual/live transitions, isolated outage, and durable results."""

import argparse
import asyncio
import json
import logging
import math
import os
import subprocess
import time
import wave
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import uvicorn
from live_acceptance import Relay, RelayedSource

from douyin_live_sense.analysis import AnalysisService
from douyin_live_sense.analysis_audio import read_sources
from douyin_live_sense.app import create_app
from douyin_live_sense.config import Settings
from douyin_live_sense.models import model_status
from douyin_live_sense.monitor import Monitor
from douyin_live_sense.security import RedactingFormatter, redact
from douyin_live_sense.source import DouyinSource
from douyin_live_sense.storage import Storage, utc_now


async def rss_kib(pid):
    text = await asyncio.to_thread(
        subprocess.check_output, ["ps", "-o", "rss=", "-p", str(pid)], text=True
    )
    return int(text.strip())


async def run(args):
    original = Settings.from_env()
    if not model_status(original)["ready"]:
        raise RuntimeError(model_status(original)["reason"])
    directory = original.data_dir / "acceptance" / uuid4().hex
    directory.mkdir(parents=True)
    (directory / "models").symlink_to(original.data_dir / "models", target_is_directory=True)
    settings = replace(original, data_dir=directory)
    store = Storage(directory)
    relay = Relay()
    relay_server = await asyncio.start_server(relay.handle, "127.0.0.1", 0)
    relay_port = relay_server.sockets[0].getsockname()[1]
    monitor = Monitor(
        store, settings, RelayedSource(DouyinSource(settings.cookie), relay, relay_port)
    )
    analysis = AnalysisService(monitor, settings)
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings, monitor, analysis),
            host="127.0.0.1",
            port=args.port,
            log_config=None,
            access_log=False,
        )
    )
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        if server_task.done():
            await server_task
            raise RuntimeError("验收服务启动失败。")
        await asyncio.sleep(0.1)
    start = time.monotonic()
    report = {
        "started_at": utc_now(),
        "room": args.room,
        "target_audio_seconds": args.minutes * 60,
        "data_dir": str(directory),
        "arguments": vars(args),
        "diarization_device_selection": settings.diarization_device,
        "analysis_threads": settings.analysis_threads,
        "validation": "real_live_and_real_local_models",
        "accuracy_metrics": "not_measured_without_human_annotation",
    }
    live_enabled = live_disabled = interrupted = recovered = False
    first_audio = None
    epoch_before_outage = None
    measurements, latencies = [], []
    cursors = {}
    catch_up_at = None
    last_measure = -60
    await monitor.start(args.room)
    try:
        async with asyncio.timeout(args.minutes * 60 + 900):
            while True:
                await asyncio.sleep(1)
                if monitor.state == "needs_attention":
                    raise RuntimeError(monitor.last_error)
                sessions = store.sessions(100)
                seconds = sum(session["duration_seconds"] for session in sessions)
                if monitor.writer:
                    seconds += monitor.writer.open_samples / 16_000
                    if monitor.writer.total_samples and first_audio is None:
                        first_audio = time.monotonic()
                elapsed_audio = time.monotonic() - first_audio if first_audio else 0
                if elapsed_audio >= args.manual_seconds and not live_enabled:
                    assert not analysis.storage.runs(None), "默认手动时意外创建了自动任务"
                    await analysis.set_realtime(True)
                    live_enabled = True
                    report["live_enabled_at"] = utc_now()
                    report["backfill_audio_seconds"] = seconds
                    print("LIVE_ENABLED_WITH_BACKFILL", round(seconds, 2), flush=True)
                if elapsed_audio >= args.interrupt_after and not interrupted and monitor.writer:
                    epoch_before_outage = monitor.writer.epoch_id
                    relay.disconnect(args.outage_seconds)
                    interrupted = True
                    print("ISOLATED_OUTAGE", utc_now(), flush=True)
                if (
                    interrupted
                    and monitor.writer
                    and monitor.writer.epoch_id != epoch_before_outage
                    and monitor.writer.total_samples
                ):
                    recovered = True
                if elapsed_audio >= args.disable_after and live_enabled and not live_disabled:
                    await analysis.set_realtime(False)
                    live_disabled = True
                    report["live_disabled_at"] = utc_now()
                    print("LIVE_DISABLED_DRAIN_QUEUE", flush=True)
                for session in sessions:
                    status = analysis.storage.status(session["id"])
                    if status["state"] == "needs_attention":
                        raise RuntimeError(status["last_error"])
                    if (
                        not catch_up_at
                        and live_enabled
                        and not live_disabled
                        and status.get("backlog_seconds", 0) <= 30
                        and status.get("processed_seconds", 0) > 0
                    ):
                        catch_up_at = datetime.now(UTC)
                        report["caught_up_at"] = catch_up_at.isoformat()
                    run_id = status.get("id")
                    if run_id:
                        rows = analysis.storage.utterances(run_id, cursors.get(run_id, 0), 500)
                        for row in rows:
                            end = datetime.fromisoformat(row["end_at"])
                            if catch_up_at and not live_disabled and end >= catch_up_at:
                                latencies.append((datetime.now(UTC) - end).total_seconds())
                        if rows:
                            cursors[run_id] = rows[-1]["ordinal"]
                elapsed = time.monotonic() - start
                if elapsed - last_measure >= 60:
                    last_measure = elapsed
                    worker = analysis.worker.process
                    measurement = {
                        "elapsed_seconds": round(elapsed),
                        "audio_seconds": round(seconds, 3),
                        "monitor": monitor.state,
                        "main_rss_kib": await rss_kib(os.getpid()),
                        "worker_rss_kib": await rss_kib(worker.pid)
                        if worker and worker.is_alive()
                        else 0,
                        "worker_pid": worker.pid if worker and worker.is_alive() else None,
                        "outage_recovered": recovered,
                        "analysis": [analysis.storage.status(s["id"]) for s in sessions],
                    }
                    measurements.append(measurement)
                    print(
                        json.dumps(
                            {k: v for k, v in measurement.items() if k != "analysis"},
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                if seconds >= args.minutes * 60 and recovered and live_disabled:
                    break
        await monitor.stop()
        analysis.sync_live()
        session_ids = [session["id"] for session in store.sessions(100)]
        for session_id in session_ids:
            analysis.request(session_id)
        async with asyncio.timeout(900):
            while any(analysis.storage.status(s)["state"] != "complete" for s in session_ids):
                if analysis.last_error:
                    raise RuntimeError(analysis.last_error)
                await asyncio.sleep(1)
        identities = [
            row[0] for row in store.db.execute("SELECT id FROM utterances ORDER BY ordinal")
        ]
        for session_id in session_ids:
            analysis.request(session_id)
        await asyncio.sleep(1)
        assert identities == [
            row[0] for row in store.db.execute("SELECT id FROM utterances ORDER BY ordinal")
        ]
        samples = checked = 0
        for row in store.db.execute("SELECT * FROM segments WHERE state='ready'"):
            with wave.open(str(store.segment_path(dict(row))), "rb") as audio:
                assert audio.getparams()[:3] == (1, 2, 16000)
                assert audio.getnframes() == row["sample_count"]
                samples += audio.getnframes()
                checked += 1
        spans = {}
        for row in store.db.execute("SELECT * FROM utterances"):
            sources = json.loads(row["sources_json"])
            assert 0 < len(read_sources(directory, sources)) <= 30 * 32_000
            assert sources[0]["start_at"] == row["start_at"]
            assert sources[-1]["end_at"] == row["end_at"]
            for source in sources:
                spans.setdefault(source["segment_id"], []).append(
                    (source["start_sample"], source["end_sample"])
                )
        for intervals in spans.values():
            ordered = sorted(intervals)
            assert all(
                left[1] <= right[0] for left, right in zip(ordered, ordered[1:], strict=False)
            )
        report.update(
            {
                "passed": True,
                "captured_seconds": samples / 16_000,
                "verified_segments": checked,
                "utterance_count": len(identities),
                "utterance_sources_verified": True,
                "outage_recovered": recovered,
                "duplicates_after_repeat": False,
                "session_analysis": [analysis.storage.status(s) for s in session_ids],
                "warm_latency_samples": len(latencies),
                "warm_latency_p95_seconds": sorted(latencies)[math.ceil(len(latencies) * 0.95) - 1]
                if latencies
                else None,
                "ffmpeg_reaped": monitor.capture.process is None,
            }
        )
    except Exception as error:
        report.update({"passed": False, "error": redact(str(error), settings.cookie)})
        raise
    finally:
        server.should_exit = True
        await server_task
        relay_server.close()
        await relay_server.wait_closed()
        for task in tuple(relay.tasks):
            task.cancel()
        await asyncio.gather(*relay.tasks, return_exceptions=True)
        report.update(
            {
                "ended_at": utc_now(),
                "wall_seconds": round(time.monotonic() - start),
                "measurements": measurements,
                "analysis_process_reaped": analysis.worker.process is None,
            }
        )
        (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        store.close()
        print("REPORT_PATH", directory / "report.json", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--room", default="123456789")
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--manual-seconds", type=float, default=600)
    parser.add_argument("--disable-after", type=float, default=1500)
    parser.add_argument("--interrupt-after", type=float, default=720)
    parser.add_argument("--outage-seconds", type=float, default=12)
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter(os.environ.get("DOUYIN_COOKIE", "")))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(args))
