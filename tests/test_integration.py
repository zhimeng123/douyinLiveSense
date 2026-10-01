import asyncio
import io
import struct
import subprocess
import sys
import threading
import wave
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from douyin_live_sense.audio import FFmpegCapture, SegmentWriter
from douyin_live_sense.source import RoomInfo, SourceError
from douyin_live_sense.storage import Storage


async def test_real_ffmpeg_preserves_pcm_over_http(settings):
    pcm = b"".join(struct.pack("<h", (index % 1000) - 500) for index in range(4000))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(pcm)
    payload = buffer.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    store = Storage(settings.data_dir)
    session = store.create_session("123", "live-1", "a", "t")
    settings = replace(settings, segment_seconds=0.03, idle_seconds=3)
    writer = SegmentWriter(store, settings, session["id"])
    capture = FFmpegCapture(settings)
    try:
        with pytest.raises(SourceError, match="音频连接结束"):
            await capture.run(
                RoomInfo(
                    "123", True, stream_url=f"http://127.0.0.1:{server.server_port}/input.wav"
                ),
                writer,
                lambda _: None,
            )
        writer.close()
        decoded = []
        for segment in store.segments(session["id"]):
            with wave.open(str(store.segment_path(segment)), "rb") as wav:
                decoded.append(wav.readframes(wav.getnframes()))
        assert b"".join(decoded) == pcm
        assert store.totals(session["id"])[0] == 9
        assert capture.process is None
    finally:
        writer.close()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        store.close()


def test_real_process_crash_recovers_actual_written_samples(settings):
    script = """
import os,sys
from pathlib import Path
from douyin_live_sense.storage import Storage,utc_now
from douyin_live_sense.audio import SegmentWriter
from douyin_live_sense.config import Settings
store=Storage(Path(sys.argv[1]))
session=store.create_session('123','live-1','a','t')
store.set_guard('123',True)
writer=SegmentWriter(store,Settings(data_dir=store.data_dir,min_free_bytes=0),session['id'])
writer.feed(b'\\x01\\x02'*50,utc_now())
writer.feed(b'\\x03\\x04'*70,utc_now())
os._exit(17)
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(settings.data_dir)], capture_output=True, timeout=10
    )
    assert process.returncode == 17, process.stderr.decode()
    store = Storage(settings.data_dir)
    session = store.open_session("123")
    assert store.guard()["enabled"]
    assert store.recover() == []
    segment = store.segments(session["id"])[0]
    assert segment["sample_count"] == 120
    with wave.open(str(store.segment_path(segment)), "rb") as wav:
        assert wav.readframes(120) == b"\x01\x02" * 50 + b"\x03\x04" * 70
    assert not list(settings.data_dir.rglob("*.part"))
    store.close()


async def test_stderr_url_split_across_reads_is_redacted(settings, tmp_path):
    script = tmp_path / "stderr-ffmpeg"
    script.write_text(
        f"#!{sys.executable}\nimport sys,time\n"
        "sys.stderr.write('failed https://cdn.test/'+'x'*5000+'?sign=PRIVATE_SIGNATURE\\n')\n"
        "sys.stderr.flush()\n"
    )
    script.chmod(0o755)
    store = Storage(settings.data_dir)
    session = store.create_session("123", "id", "a", "t")
    writer = SegmentWriter(store, settings, session["id"])
    capture = FFmpegCapture(replace(settings, ffmpeg=str(script), idle_seconds=2))
    try:
        with pytest.raises(SourceError) as caught:
            await capture.run(
                RoomInfo("123", True, stream_url="https://fake.test"), writer, lambda _: None
            )
        assert "PRIVATE_SIGNATURE" not in caught.value.diagnostic
        assert "cdn.test" in caught.value.diagnostic
    finally:
        writer.close()
        store.close()
