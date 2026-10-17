import pytest
from conftest import FakeCapture, FakeSource

from douyin_live_sense.monitor import Monitor, MonitorConflict
from douyin_live_sense.storage import Storage


def make_monitor(settings):
    store = Storage(settings.data_dir)
    return store, Monitor(store, settings, FakeSource(), FakeCapture())


async def test_start_local_conflicts_with_running_guard(settings):
    store, monitor = make_monitor(settings)
    await monitor.start("123")
    with pytest.raises(MonitorConflict):
        await monitor.start_local("microphone")
    await monitor.stop()
    store.close()


async def test_guard_start_conflicts_with_local(settings):
    store, monitor = make_monitor(settings)
    await monitor.start_local("microphone")
    with pytest.raises(MonitorConflict):
        await monitor.start("123")
    await monitor.stop_local()
    store.close()


async def test_feed_local_creates_segments_and_finishes(settings):
    store, monitor = make_monitor(settings)
    status = await monitor.start_local("system", realtime_analysis=False)
    assert status["mode"] == "local"
    assert status["source"] == "system"
    session_id = status["session_id"]
    for _ in range(5):
        monitor.feed_local(b"\x00\x01" * 800)
    stopped = await monitor.stop_local("stopped")
    assert stopped["state"] == "stopped"
    assert stopped["mode"] is None
    assert store.session(session_id)["ended_at"] is not None
    count, seconds = store.totals(session_id)
    assert count >= 1 and seconds > 0
    store.close()


async def test_start_local_writes_realtime_without_autorestore(settings):
    store, monitor = make_monitor(settings)
    await monitor.start_local("microphone", realtime_analysis=True)
    assert store.guard()["enabled"] == 0
    assert store.guard()["realtime_analysis"] == 1
    await monitor.stop_local()
    assert store.guard()["realtime_analysis"] == 0
    store.close()


async def test_restore_finishes_stale_local_sessions(settings):
    store = Storage(settings.data_dir)
    session = store.create_session("local", "microphone", "本机录音", "麦克风输入")
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    await monitor.restore()
    assert store.session(session["id"])["ended_at"] is not None
    store.close()
