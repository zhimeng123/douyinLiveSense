import asyncio
import os
import struct
import sys
import wave
from dataclasses import replace
from datetime import datetime

import pytest
from conftest import eventually

from douyin_live_sense.audio import (
    DiskFull,
    FFmpegCapture,
    SegmentWriter,
    reauthentication_required,
)
from douyin_live_sense.source import RoomInfo, SourceError
from douyin_live_sense.storage import InstanceLock, Storage, utc_now


def create_writer(store, settings):
    session = store.create_session("123", "live-1", "主播", "标题")
    return session, SegmentWriter(store, settings, session["id"])


def read_all(store, session_id):
    contents = []
    for segment in store.segments(session_id):
        with wave.open(str(store.segment_path(segment)), "rb") as wav:
            assert wav.getnframes() == segment["sample_count"]
            assert wav.getframerate() == 16000 and wav.getnchannels() == 1
            contents.append(wav.readframes(wav.getnframes()))
    return b"".join(contents)


def test_sample_exact_segments_odd_reads_and_tail(settings):
    store = Storage(settings.data_dir)
    session, writer = create_writer(store, settings)
    pcm = b"".join(struct.pack("<h", sample) for sample in range(101))
    start = utc_now()
    for index in range(0, len(pcm), 7):
        writer.feed(pcm[index : index + 7], start)
    assert store.totals(session["id"])[0] == 3
    assert (
        store.db.execute("SELECT COUNT(*) FROM segments WHERE state='pending'").fetchone()[0] == 1
    )
    writer.close()
    segments = store.segments(session["id"])
    assert [s["sample_count"] for s in segments] == [32, 32, 32, 5]
    assert read_all(store, session["id"]) == pcm
    for left, right in zip(segments, segments[1:], strict=False):
        assert left["end_at"] == right["start_at"]
    assert (
        datetime.fromisoformat(segments[-1]["end_at"]) - datetime.fromisoformat(start)
    ).total_seconds() == pytest.approx(101 / 16000, abs=1e-6)
    store.close()


def test_crash_recovery_repairs_stale_header_and_preserves_id(settings):
    store = Storage(settings.data_dir)
    session, writer = create_writer(store, settings)
    writer.feed(b"\x01\x02" * 5, utc_now())
    segment_id = writer.segment["id"]
    # Mimic a crash after further raw PCM was flushed but the header was not patched.
    writer.wav.writeframesraw(b"\x03\x04" * 7)
    writer.file.flush()
    path = store.segment_path(writer.segment).with_suffix(".wav.part")
    on_disk = path.read_bytes()
    writer.close()
    store.db.execute("UPDATE segments SET state='pending',sample_count=0 WHERE id=?", (segment_id,))
    store.db.commit()
    final = store.segment_path(store.segment(segment_id))
    final.unlink()
    path.write_bytes(on_disk + b"\xff")
    assert store.recover() == []
    segment = store.segment(segment_id)
    assert segment["state"] == "ready" and segment["sample_count"] == 12
    assert final.is_file() and not path.exists()
    assert read_all(store, session["id"]) == b"\x01\x02" * 5 + b"\x03\x04" * 7
    assert store.recover() == []
    store.close()


def test_recover_final_file_before_database_commit(settings):
    store = Storage(settings.data_dir)
    session, writer = create_writer(store, settings)
    writer.feed(b"\x01\x02" * 5, utc_now())
    segment_id = writer.segment["id"]
    writer.close()
    with store.db:
        store.db.execute("UPDATE segments SET state='pending' WHERE id=?", (segment_id,))
    assert store.recover() == []
    assert store.segment(segment_id)["sample_count"] == 5
    store.close()


@pytest.mark.parametrize("contents", [None, b"broken", b"RIFF" + b"\x00" * 40])
def test_unrecoverable_files_are_recorded_and_preserved(settings, contents):
    store = Storage(settings.data_dir)
    session = store.create_session("123", "id", "a", "t")
    segment = store.reserve_segment(session["id"], "epoch", utc_now(), 16000)
    path = store.segment_path(segment).with_suffix(".wav.part")
    if contents is not None:
        path.parent.mkdir(parents=True)
        path.write_bytes(contents)
    assert len(store.recover()) == 1
    assert store.segment(segment["id"])["state"] == "failed"
    assert store.events(session["id"])[0]["kind"] == "recovery_failed"
    if contents is not None:
        assert path.read_bytes() == contents
    store.close()


def test_disk_check_on_every_segment_keeps_completed_audio(settings, monkeypatch):
    store = Storage(settings.data_dir)
    session, writer = create_writer(store, replace(settings, min_free_bytes=100))
    free = iter([1000, 0])
    monkeypatch.setattr(
        "douyin_live_sense.audio.shutil.disk_usage",
        lambda path: type("Usage", (), {"free": next(free)})(),
    )
    with pytest.raises(DiskFull):
        writer.feed(b"\x01\x02" * 64, utc_now())
    writer.close()
    assert read_all(store, session["id"]) == b"\x01\x02" * 32
    store.close()


def test_instance_lock(settings):
    settings.data_dir.mkdir(exist_ok=True)
    lock = InstanceLock(settings.data_dir)
    with pytest.raises(RuntimeError, match="已有服务"):
        InstanceLock(settings.data_dir)
    lock.close()
    InstanceLock(settings.data_dir).close()


@pytest.mark.parametrize(
    "detail,attention",
    [("HTTP error 403 Forbidden", False), ("401 Unauthorized", True), ("Connection reset", False)],
)
def test_expired_stream_is_retried_before_requiring_login(detail, attention):
    assert reauthentication_required(detail) is attention


async def test_idle_timeout_saves_tail_and_reaps_process(settings, tmp_path):
    fake = tmp_path / "fake-ffmpeg"
    fake.write_text(
        f"#!{sys.executable}\nimport sys,time\n"
        "sys.stdout.buffer.write(b'\\x01\\x02'*8)\n"
        "sys.stdout.buffer.flush()\ntime.sleep(10)\n"
    )
    fake.chmod(0o755)
    store = Storage(settings.data_dir)
    session, writer = create_writer(store, settings)
    capture = FFmpegCapture(replace(settings, ffmpeg=str(fake), idle_seconds=2))
    pids = []

    def on_audio(now):
        pids.append(capture.process.pid)
        capture.settings = replace(capture.settings, idle_seconds=0.05)

    try:
        with pytest.raises(SourceError, match="未收到音频"):
            await capture.run(
                RoomInfo("123", True, stream_url="https://fake.test"), writer, on_audio
            )
    finally:
        writer.close()
    assert capture.process is None and len(pids) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(pids[0], 0)
    assert read_all(store, session["id"]) == b"\x01\x02" * 8
    store.close()


async def test_cancellation_reaps_ffmpeg(settings, tmp_path):
    fake = tmp_path / "fake-ffmpeg"
    fake.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(10)\n")
    fake.chmod(0o755)
    store = Storage(settings.data_dir)
    _, writer = create_writer(store, settings)
    capture = FFmpegCapture(replace(settings, ffmpeg=str(fake)))
    task = asyncio.create_task(
        capture.run(RoomInfo("123", True, stream_url="https://fake.test"), writer, lambda _: None)
    )
    await eventually(lambda: capture.process is not None)
    pid = capture.process.pid
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    writer.close()
    assert capture.process is None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    store.close()
