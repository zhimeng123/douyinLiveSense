import asyncio
import logging
import os
import shutil
import wave
from collections import deque
from datetime import UTC, datetime
from uuid import uuid4

from .config import Settings
from .security import redact
from .source import NeedsAttention, RoomInfo, SourceError
from .storage import Storage, add_seconds

logger = logging.getLogger(__name__)


class DiskFull(NeedsAttention):
    pass


class SegmentWriter:
    def __init__(self, store: Storage, settings: Settings, session_id: str):
        self.store = store
        self.settings = settings
        self.session_id = session_id
        self.epoch_id = uuid4().hex
        self.epoch_start: str | None = None
        self.total_samples = 0
        self.open_samples = 0
        self.segment: dict | None = None
        self.wav = None
        self.file = None
        self.remainder = b""
        self.max_samples = round(settings.segment_seconds * settings.sample_rate)

    def _open(self) -> None:
        if shutil.disk_usage(self.store.data_dir).free < self.settings.min_free_bytes:
            raise DiskFull("剩余磁盘空间不足 1 GB，已停止采集。请释放空间后重新启动守卫。")
        self.segment = self.store.reserve_segment(
            self.session_id,
            self.epoch_id,
            add_seconds(self.epoch_start, self.total_samples / self.settings.sample_rate),
            self.settings.sample_rate,
        )
        path = self.store.segment_path(self.segment).with_suffix(".wav.part")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("wb")
        self.wav = wave.open(self.file, "wb")
        self.wav.setparams((1, 2, self.settings.sample_rate, 0, "NONE", "not compressed"))

    def feed(self, pcm: bytes, received_at: str) -> None:
        if self.epoch_start is None:
            self.epoch_start = received_at
        pcm = self.remainder + pcm
        usable = len(pcm) // 2 * 2
        self.remainder = pcm[usable:]
        offset = 0
        while offset < usable:
            if self.wav is None:
                self._open()
            count = min((usable - offset) // 2, self.max_samples - self.open_samples)
            self.wav.writeframesraw(pcm[offset : offset + count * 2])
            self.file.flush()
            self.open_samples += count
            self.total_samples += count
            offset += count * 2
            if self.open_samples == self.max_samples:
                self._finish()

    def _finish(self) -> None:
        if self.wav is None:
            return
        segment = self.segment
        samples = self.open_samples
        self.wav.close()
        self.wav = None
        self.file.flush()
        os.fsync(self.file.fileno())
        self.file.close()
        self.file = None
        final = self.store.segment_path(segment)
        os.replace(final.with_suffix(".wav.part"), final)
        self.store.complete_segment(segment["id"], samples)
        self.segment = None
        self.open_samples = 0

    def close(self) -> None:
        try:
            self._finish()
        finally:
            if self.file is not None:
                self.file.close()
                self.file = None
            if self.remainder:
                self.store.event(
                    self.session_id, "incomplete_sample", "连接尾部有 1 字节不完整采样"
                )
                self.remainder = b""


class FFmpegCapture:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.process: asyncio.subprocess.Process | None = None

    async def run(self, room: RoomInfo, writer: SegmentWriter, on_audio) -> None:
        args = [
            self.settings.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-nostdin",
            "-rw_timeout",
            "15000000",
            "-user_agent",
            room.user_agent or "Mozilla/5.0",
            "-referer",
            "https://live.douyin.com/",
            "-i",
            room.stream_url,
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(self.settings.sample_rate),
            "-acodec",
            "pcm_s16le",
            "-f",
            "s16le",
            "pipe:1",
        ]
        self.process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        process = self.process
        diagnostics: deque[str] = deque(maxlen=30)

        async def drain_stderr():
            while data := await process.stderr.read(4096):
                diagnostics.append(data.decode(errors="replace"))

        drain = asyncio.create_task(drain_stderr())
        last_received: datetime | None = None
        try:
            while True:
                try:
                    pcm = await asyncio.wait_for(
                        process.stdout.read(65_536), self.settings.idle_seconds
                    )
                except TimeoutError as error:
                    raise SourceError("连续 20 秒未收到音频，正在重新连接。") from error
                if not pcm:
                    code = await process.wait()
                    await drain
                    detail = redact("".join(diagnostics), self.settings.cookie)[-3000:].strip()
                    if reauthentication_required(detail):
                        raise NeedsAttention("播放流拒绝访问，请检查登录 Cookie。", detail)
                    raise SourceError(
                        "音频连接结束，正在重新检查直播状态。", f"FFmpeg {code}: {detail}"
                    )
                now = datetime.now(UTC)
                if (
                    last_received
                    and (now - last_received).total_seconds() > self.settings.idle_seconds
                ):
                    raise SourceError("检测到休眠或音频接收中断，正在建立新的采集时间基准。")
                last_received = now
                writer.feed(pcm, now.isoformat())
                on_audio(now.isoformat())
        finally:

            async def drain_stdout():
                while await process.stdout.read(65_536):
                    pass

            discard = asyncio.create_task(drain_stdout())
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass  # The process exited between the returncode check and signal.
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    if process.returncode is None:
                        process.kill()
                    await process.wait()
            await discard
            await drain
            self.process = None


def reauthentication_required(message: str) -> bool:
    # A signed CDN URL can expire with 403. Re-resolve that URL rather than
    # treating every forbidden response as an account login requirement.
    return any(
        word in message.lower() for word in ("401 unauthorized", "login required", "captcha")
    )
