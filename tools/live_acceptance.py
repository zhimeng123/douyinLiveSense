"""Run the real application with a local relay for an isolated network outage test."""

import argparse
import asyncio
import json
import logging
import os
import subprocess
import time
import wave
from dataclasses import replace

import httpx
import uvicorn

from douyin_live_sense.app import create_app
from douyin_live_sense.config import Settings
from douyin_live_sense.monitor import Monitor
from douyin_live_sense.security import RedactingFormatter
from douyin_live_sense.source import DouyinSource
from douyin_live_sense.storage import Storage, utc_now


class Relay:
    def __init__(self):
        self.target = ""
        self.user_agent = "Mozilla/5.0"
        self.blocked_until = 0.0
        self.connections = set()
        self.tasks = set()

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        self.connections.add(writer)
        try:
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            if time.monotonic() < self.blocked_until:
                writer.write(b"HTTP/1.0 503 Service Unavailable\r\n\r\n")
                await writer.drain()
                return
            headers = {"User-Agent": self.user_agent, "Referer": "https://live.douyin.com/"}
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                async with client.stream("GET", self.target, headers=headers) as response:
                    response.raise_for_status()
                    writer.write(b"HTTP/1.0 200 OK\r\nContent-Type: video/x-flv\r\n\r\n")
                    async for data in response.aiter_bytes():
                        if time.monotonic() < self.blocked_until:
                            return
                        writer.write(data)
                        await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError, httpx.HTTPError) as error:
            logging.warning("测试转发连接结束: %s", type(error).__name__)
        finally:
            self.connections.discard(writer)
            self.tasks.discard(task)
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                logging.debug("测试客户端已经断开，转发连接已关闭。")

    def disconnect(self, seconds=12):
        self.blocked_until = time.monotonic() + seconds
        for writer in tuple(self.connections):
            writer.close()


class RelayedSource:
    def __init__(self, source, relay, port):
        self.source, self.relay, self.port = source, relay, port

    async def resolve(self, room_id):
        room = await self.source.resolve(room_id)
        if room.is_live:
            self.relay.target, self.relay.user_agent = room.stream_url, room.user_agent
            return replace(room, stream_url=f"http://127.0.0.1:{self.port}/live.flv")
        return room


async def run(args):
    settings = Settings.from_env()
    store = Storage(settings.data_dir)
    baseline = store.db.execute(
        "SELECT COALESCE(SUM(1.0*sample_count/sample_rate),0) FROM segments WHERE state='ready'"
    ).fetchone()[0]
    relay = Relay()
    relay_server = await asyncio.start_server(relay.handle, "127.0.0.1", 0)
    port = relay_server.sockets[0].getsockname()[1]
    source = RelayedSource(DouyinSource(settings.cookie), relay, port)
    monitor = Monitor(store, settings, source=source)
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings, monitor),
            host="127.0.0.1",
            port=8787,
            log_config=None,
            access_log=False,
        )
    )
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        if server_task.done():
            await server_task
            raise RuntimeError("测试服务未能启动")
        await asyncio.sleep(0.1)
    await monitor.start(args.room)
    start = time.monotonic()
    first_audio = None
    interrupted = False
    recovered = False
    rss = []
    initial_epoch = None
    report = {"started_at": utc_now(), "room": args.room, "target_seconds": args.minutes * 60}
    try:
        for tick in range(int(args.minutes * 60 + 900)):
            await asyncio.sleep(1)
            status = monitor.status()
            if status["state"] == "needs_attention":
                raise RuntimeError(status["last_error"])
            if monitor.writer and monitor.writer.total_samples:
                if first_audio is None:
                    first_audio = time.monotonic()
                    initial_epoch = monitor.writer.epoch_id
                    print("FIRST_AUDIO", utc_now(), flush=True)
                if interrupted and monitor.writer.epoch_id != initial_epoch:
                    recovered = True
            total = (
                store.db.execute(
                    "SELECT COALESCE(SUM(1.0*sample_count/sample_rate),0) "
                    "FROM segments WHERE state='ready'"
                ).fetchone()[0]
                - baseline
            )
            if monitor.writer:
                total += monitor.writer.open_samples / settings.sample_rate
            if (
                first_audio
                and not interrupted
                and time.monotonic() - first_audio > args.interrupt_after
            ):
                interrupted = True
                relay.disconnect(args.outage_seconds)
                print(
                    "ISOLATED_NETWORK_OUTAGE",
                    utc_now(),
                    f"duration={args.outage_seconds}s",
                    flush=True,
                )
            if tick % 60 == 0:
                rss_output = await asyncio.to_thread(
                    subprocess.check_output,
                    ["ps", "-o", "rss=", "-p", str(os.getpid())],
                    text=True,
                )
                current_rss = int(rss_output.strip())
                rss.append({"elapsed": round(time.monotonic() - start), "rss_kib": current_rss})
                print(
                    json.dumps(
                        {
                            "elapsed_seconds": round(time.monotonic() - start),
                            "audio_seconds": round(total, 2),
                            "state": status["state"],
                            "rss_kib": current_rss,
                            "outage_recovered": recovered,
                        }
                    ),
                    flush=True,
                )
            if total >= args.minutes * 60 and recovered:
                break
        else:
            raise RuntimeError("直播采集未在等待时限内达到目标时长")
        await monitor.stop()
        checked = 0
        samples = 0
        for row in store.db.execute("SELECT * FROM segments WHERE state='ready'"):
            segment = dict(row)
            with wave.open(str(store.segment_path(segment)), "rb") as wav:
                assert wav.getnframes() == segment["sample_count"]
                assert wav.getnchannels() == 1 and wav.getsampwidth() == 2
                assert wav.getframerate() == 16000
                samples += wav.getnframes()
                checked += 1
        report.update(
            {
                "passed": True,
                "outage_recovered": recovered,
                "verified_segments": checked,
                "total_samples": samples,
                "captured_seconds": samples / 16000 - baseline,
                "rss_samples": rss,
                "wall_seconds": round(time.monotonic() - start),
                "ended_at": utc_now(),
                "ffmpeg_stopped": monitor.capture.process is None,
            }
        )
    except Exception as error:
        report.update(
            {
                "passed": False,
                "error": str(error),
                "rss_samples": rss,
                "outage_recovered": recovered,
                "ended_at": utc_now(),
            }
        )
        raise
    finally:
        server.should_exit = True
        await server_task
        relay_server.close()
        await relay_server.wait_closed()
        for task in tuple(relay.tasks):
            task.cancel()
        await asyncio.gather(*relay.tasks, return_exceptions=True)
        (settings.data_dir / "live-validation.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        store.close()
        print("REPORT", json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--room", default="123456789")
    parser.add_argument("--minutes", type=float, default=30)
    parser.add_argument("--interrupt-after", type=float, default=180)
    parser.add_argument("--outage-seconds", type=float, default=12)
    arguments = parser.parse_args()
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter(os.environ.get("DOUYIN_COOKIE", "")))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run(arguments))
