import asyncio
from dataclasses import replace

import pytest

from douyin_live_sense.config import Settings
from douyin_live_sense.source import RoomInfo, SourceError


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path,
        segment_seconds=0.002,
        min_free_bytes=0,
        retry_delays=(0.01, 0.02, 0.03, 0.04),
        poll_seconds=0.01,
        idle_seconds=0.1,
    )


def live_room(room_id="123456789", live_id="live-1"):
    return RoomInfo(room_id, True, "主播", "测试直播", live_id, "https://cdn.test/live?sign=secret")


class FakeSource:
    def __init__(self, *items):
        self.items = list(items or [live_room()])
        self.calls = []

    async def resolve(self, room_id):
        self.calls.append(room_id)
        await asyncio.sleep(0)
        item = self.items.pop(0) if len(self.items) > 1 else self.items[0]
        if isinstance(item, Exception):
            raise item
        return replace(item, room_id=room_id)


class FakeCapture:
    def __init__(self, *plans):
        self.plans = list(plans or [(b"\x01\x02" * 8, None)])
        self.calls = 0
        self.closed = 0
        self.process = None

    async def run(self, room, writer, on_audio):
        from douyin_live_sense.storage import utc_now

        self.calls += 1
        pcm, error = self.plans.pop(0) if len(self.plans) > 1 else self.plans[0]
        try:
            if pcm:
                now = utc_now()
                writer.feed(pcm, now)
                on_audio(now)
            if error:
                raise error
            await asyncio.Event().wait()
        finally:
            self.closed += 1


async def eventually(predicate, deadline_seconds=2):
    async with asyncio.timeout(deadline_seconds):
        # The public monitor state has no event interface; poll it under a deadline.
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.002)


DISCONNECT = SourceError("连接中断")
