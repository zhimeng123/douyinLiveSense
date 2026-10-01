import asyncio
import time
from dataclasses import replace
from datetime import datetime

import pytest
from conftest import DISCONNECT, FakeCapture, FakeSource, eventually, live_room

from douyin_live_sense.monitor import Monitor, MonitorConflict
from douyin_live_sense.source import NeedsAttention, RoomInfo, SourceError
from douyin_live_sense.storage import Storage


async def test_start_idempotence_conflict_and_concurrency(settings):
    store = Storage(settings.data_dir)
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    try:
        results = await asyncio.gather(
            monitor.start("123"), monitor.start("123"), monitor.start("456"), return_exceptions=True
        )
        assert results[0]["room_id"] == "123" and results[1]["room_id"] == "123"
        assert isinstance(results[2], MonitorConflict)
        task = monitor.task
        await monitor.start("123")
        assert monitor.task is task
        await eventually(lambda: monitor.state == "recording")
        session_id = monitor.session["id"]
        await monitor.stop()
        assert monitor.state == "stopped" and not store.guard()["enabled"]
        assert store.session(session_id)["end_reason"] == "stopped"
        assert store.totals(session_id)[1] == 8 / 16000
    finally:
        await monitor.shutdown()
        store.close()


async def test_wait_start_live_then_offline_keeps_guard_enabled(settings):
    store = Storage(settings.data_dir)
    offline = RoomInfo("123", False, anchor_name="主播")
    source = FakeSource(offline, live_room(), offline)
    capture = FakeCapture((b"\x01\x02" * 8, DISCONNECT))
    monitor = Monitor(store, settings, source, capture)
    try:
        await monitor.start("123")
        await eventually(lambda: len(store.sessions()) == 1 and monitor.state == "waiting")
        session = store.sessions()[0]
        assert session["end_reason"] == "offline" and session["segment_count"] == 1
        assert monitor.enabled and store.guard()["enabled"]
        before = time.monotonic()
        await monitor.stop()
        assert time.monotonic() - before < 0.1
        calls = len(source.calls)
        await asyncio.sleep(0.02)
        assert len(source.calls) == calls
    finally:
        await monitor.shutdown()
        store.close()


async def test_reconnect_preserves_session_new_epoch_and_gap(settings):
    store = Storage(settings.data_dir)
    capture = FakeCapture((b"\x01\x02" * 8, DISCONNECT), (b"\x03\x04" * 8, None))
    source = FakeSource()
    monitor = Monitor(store, settings, source, capture)
    try:
        await monitor.start("123")
        await eventually(lambda: capture.calls == 2 and monitor.state == "recording")
        session_id = monitor.session["id"]
        assert len(store.sessions()) == 1 and len(source.calls) == 2
        assert monitor.last_error is None and monitor.retry_index == 0
        await monitor.stop()
        segments = store.segments(session_id)
        assert segments[0]["epoch_id"] != segments[1]["epoch_id"]
        assert datetime.fromisoformat(segments[1]["start_at"]) > datetime.fromisoformat(
            segments[0]["end_at"]
        )
        gaps = [event for event in store.events(session_id) if event["kind"] == "gap"]
        assert len(gaps) == 1 and gaps[0]["ended_at"] is not None
    finally:
        await monitor.shutdown()
        store.close()


async def test_network_resolution_errors_retry_and_clear(settings):
    store = Storage(settings.data_dir)
    source = FakeSource(SourceError("DNS failed"), SourceError("timeout"), live_room())
    monitor = Monitor(store, settings, source, FakeCapture())
    try:
        await monitor.start("123")
        await eventually(lambda: monitor.state == "recording")
        assert len(source.calls) == 3 and monitor.enabled
        assert monitor.last_error is None
    finally:
        await monitor.stop()
        store.close()


async def test_backoff_schedule_and_stop_interrupts_wait(settings):
    store = Storage(settings.data_dir)
    monitor = Monitor(store, settings, FakeSource(SourceError("network failed")), FakeCapture())
    waits = []

    async def record_sleep(seconds):
        waits.append(seconds)
        if len(waits) >= 6:
            await asyncio.Event().wait()

    monitor._sleep = record_sleep
    try:
        await monitor.start("123")
        await eventually(lambda: len(waits) >= 6)
        assert waits == [0.01, 0.02, 0.03, 0.04, 0.04, 0.04]
        await monitor.stop()
    finally:
        await monitor.shutdown()
        store.close()


async def test_attention_visible_redacted_and_not_restored(settings):
    store = Storage(settings.data_dir)
    settings = replace(settings, cookie="sessionid=private-secret")
    source = FakeSource(
        NeedsAttention("login required https://cdn.test/live?sign=secret sessionid=private-secret")
    )
    monitor = Monitor(store, settings, source, FakeCapture())
    try:
        await monitor.start("123")
        await eventually(lambda: monitor.state == "needs_attention")
        assert not monitor.enabled and not store.guard()["enabled"]
        assert (
            "private-secret" not in monitor.last_error and "sign=secret" not in monitor.last_error
        )
        restored = Monitor(store, settings, source, FakeCapture())
        await restored.restore()
        assert restored.state == "needs_attention" and restored.task is None
    finally:
        await monitor.shutdown()
        store.close()


async def test_shutdown_restore_keeps_session_and_manual_stop_disables(settings):
    store = Storage(settings.data_dir)
    first = Monitor(store, settings, FakeSource(), FakeCapture())
    second = None
    try:
        await first.start("123")
        await eventually(lambda: first.state == "recording")
        session_id = first.session["id"]
        await first.shutdown()
        assert store.guard()["enabled"] and store.session(session_id)["ended_at"] is None
        second = Monitor(store, settings, FakeSource(), FakeCapture())
        await second.restore()
        await eventually(lambda: second.state == "recording")
        assert second.session["id"] == session_id
        await second.stop()
        third = Monitor(store, settings, FakeSource(), FakeCapture())
        await third.restore()
        assert third.task is None and third.state == "stopped"
    finally:
        await first.shutdown()
        if second:
            await second.shutdown()
        store.close()


async def test_new_broadcast_closes_old_session(settings):
    store = Storage(settings.data_dir)
    source = FakeSource(live_room(live_id="old"), live_room(live_id="new"))
    capture = FakeCapture((b"\x01\x02" * 8, DISCONNECT), (b"\x03\x04" * 8, None))
    monitor = Monitor(store, settings, source, capture)
    try:
        await monitor.start("123")
        await eventually(lambda: capture.calls == 2 and monitor.state == "recording")
        sessions = store.sessions()
        assert len(sessions) == 2
        assert sessions[0]["source_live_id"] == "new"
        assert sessions[1]["end_reason"] == "new_live"
    finally:
        await monitor.stop()
        store.close()


async def test_disk_failure_keeps_tail_and_requests_action(settings, monkeypatch):
    store = Storage(settings.data_dir)
    settings = replace(settings, min_free_bytes=100)
    free = iter([1000, 0])
    monkeypatch.setattr(
        "douyin_live_sense.audio.shutil.disk_usage",
        lambda path: type("Usage", (), {"free": next(free)})(),
    )
    monitor = Monitor(store, settings, FakeSource(), FakeCapture((b"\x01\x02" * 64, None)))
    try:
        await monitor.start("123")
        await eventually(lambda: monitor.state == "needs_attention")
        assert store.totals(monitor.session["id"])[1] == 32 / 16000
        assert not monitor.enabled
    finally:
        await monitor.stop()
        store.close()


async def test_missing_ffmpeg_is_actionable(settings):
    store = Storage(settings.data_dir)
    monitor = Monitor(
        store, replace(settings, ffmpeg="nonexistent-ffmpeg"), FakeSource(), FakeCapture()
    )
    with pytest.raises(NeedsAttention, match="FFmpeg"):
        await monitor.start("123")
    assert not monitor.enabled and monitor.task is None
    store.close()
