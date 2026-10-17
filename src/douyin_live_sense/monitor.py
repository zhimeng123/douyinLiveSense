import asyncio
import logging
import shutil
import time

from .audio import FFmpegCapture, SegmentWriter
from .config import Settings
from .security import redact
from .source import DouyinSource, LiveSource, NeedsAttention, SourceError, normalize_room
from .storage import Storage, utc_now

logger = logging.getLogger(__name__)


class MonitorConflict(Exception):
    pass


class Monitor:
    def __init__(
        self,
        store: Storage,
        settings: Settings,
        source: LiveSource | None = None,
        capture: FFmpegCapture | None = None,
    ):
        self.store = store
        self.settings = settings
        self.source = source or DouyinSource(settings.cookie, settings.resolve_seconds)
        self.capture = capture or FFmpegCapture(settings)
        self.lock = asyncio.Lock()
        self.task: asyncio.Task | None = None
        guard = store.guard()
        self.room_id = guard["room_id"]
        self.enabled = False
        self.state = "needs_attention" if guard["last_error"] else "stopped"
        self.last_error = guard["last_error"]
        self.session: dict | None = None
        self.anchor_name = ""
        self.title = ""
        self.last_audio_at: str | None = None
        self.retry_deadline: float | None = None
        self.writer: SegmentWriter | None = None
        self.retry_index = 0
        self.mode: str | None = None
        self.local_source: str | None = None

    async def restore(self) -> None:
        for failure in self.store.recover():
            logger.error("音频恢复失败: %s", failure)
        self.store.close_stale_epochs()
        self.store.finish_local_sessions("service_restart")
        guard = self.store.guard()
        if guard["enabled"] and guard["room_id"]:
            await self.start(guard["room_id"], bool(guard["realtime_analysis"]))

    async def start(self, value: str, realtime_analysis: bool = False) -> dict:
        room_id = normalize_room(value)
        async with self.lock:
            if self.enabled:
                if self.room_id != room_id:
                    raise MonitorConflict("正在守卫另一个直播间，请先停止当前守卫。")
                return self.status()
            if not shutil.which(self.settings.ffmpeg):
                raise NeedsAttention("未找到 FFmpeg。请先安装 FFmpeg 并加入 PATH。")
            if self.session and self.session["room_id"] != room_id:
                self.store.finish_session(self.session["id"], "room_changed")
            self.room_id = room_id
            self.session = self.store.open_session(room_id)
            self.anchor_name = self.session["anchor_name"] if self.session else ""
            self.title = self.session["title"] if self.session else ""
            self.last_audio_at = None
            self.last_error = None
            self.retry_index = 0
            self.mode = "douyin"
            self.local_source = None
            self.enabled = True
            self.state = "checking"
            self.store.set_guard(room_id, True)
            self.store.set_realtime(realtime_analysis)
            if self.session:
                self.store.open_gap(
                    self.session["id"],
                    "服务重新启动或重新启用守卫",
                    self.store.last_segment_end(self.session["id"]),
                )
            self.task = asyncio.create_task(self._run(), name="douyin-monitor")
            return self.status()

    async def stop(self) -> dict:
        async with self.lock:
            if self.mode == "local":
                return self._finish_local("stopped")
            self.enabled = False
            self.store.set_guard(self.room_id, False)
            await self._cancel_task()
            self._finish_session("stopped")
            self.store.set_realtime(False)
            self.state = "stopped"
            self.last_error = None
            return self.status()

    async def start_local(self, source: str, realtime_analysis: bool = False) -> dict:
        if source not in {"system", "microphone"}:
            raise ValueError("未知的录音来源。")
        async with self.lock:
            if self.enabled:
                raise MonitorConflict("正在监听或录音，请先停止当前任务。")
            self.room_id = "local"
            self.local_source = source
            self.mode = "local"
            self.session = self.store.create_session(
                "local", source, "本机录音", "系统声音" if source == "system" else "麦克风输入"
            )
            self.anchor_name = self.session["anchor_name"]
            self.title = self.session["title"]
            self.last_audio_at = None
            self.last_error = None
            self.retry_index = 0
            self.enabled = True
            self.state = "recording"
            self.store.set_realtime(realtime_analysis)
            self.store.set_guard("local", False)
            self.writer = SegmentWriter(self.store, self.settings, self.session["id"])
            return self.status()

    def feed_local(self, pcm: bytes) -> None:
        if self.mode != "local" or not self.enabled or self.writer is None:
            raise MonitorConflict("本机录音未在运行。")
        if self.state != "recording":
            self.store.close_gap(self.session["id"], utc_now())
        now = utc_now()
        self.state = "recording"
        self.writer.feed(pcm, now)
        self.last_audio_at = now
        self.last_error = None

    async def stop_local(self, reason: str = "stopped") -> dict:
        async with self.lock:
            if self.mode != "local":
                return self.status()
            return self._finish_local(reason)

    def _finish_local(self, reason: str) -> dict:
        if self.writer:
            self.writer.close()
            self.writer = None
        if self.session:
            self.store.finish_session(self.session["id"], reason)
            self.session = None
        self.enabled = False
        self.mode = None
        self.local_source = None
        self.state = "stopped"
        self.last_error = None
        self.store.set_realtime(False)
        return self.status()

    async def shutdown(self) -> None:
        async with self.lock:
            if self.mode == "local":
                self._finish_local("stopped")
                return
            await self._cancel_task()
            if self.enabled and self.session:
                self.store.open_gap(
                    self.session["id"],
                    "服务关闭，等待下次启动恢复",
                    self.store.last_segment_end(self.session["id"]),
                )

    async def _cancel_task(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        elif self.task:
            # Retrieve an unexpected task failure instead of losing its context.
            self.task.result()
        self.task = None

    def _finish_session(self, reason: str) -> None:
        if self.session:
            self.store.finish_session(self.session["id"], reason)
            self.session = None

    def _on_audio(self, received_at: str) -> None:
        if self.state != "recording":
            self.store.close_gap(self.session["id"], received_at)
            logger.info("开始采集直播间 %s，场次 %s", self.room_id, self.session["id"])
        self.state = "recording"
        self.last_audio_at = received_at
        self.last_error = None
        self.retry_index = 0

    async def _sleep(self, seconds: float) -> None:
        self.retry_deadline = time.monotonic() + seconds
        try:
            await asyncio.sleep(seconds)
        finally:
            self.retry_deadline = None

    async def _run(self) -> None:
        try:
            while self.enabled:
                try:
                    self.state = "reconnecting" if self.session else "checking"
                    room = await self.source.resolve(self.room_id)
                    self.anchor_name, self.title = room.anchor_name, room.title
                    if not room.is_live:
                        self._finish_session("offline")
                        self.state = "waiting"
                        self.last_error = None
                        self.retry_index = 0
                        await self._sleep(self.settings.poll_seconds)
                        continue
                    if (
                        self.session
                        and room.source_live_id
                        and self.session["source_live_id"]
                        and room.source_live_id != self.session["source_live_id"]
                    ):
                        self._finish_session("new_live")
                    if not self.session:
                        self.session = self.store.create_session(
                            self.room_id, room.source_live_id, room.anchor_name, room.title
                        )
                    self.writer = SegmentWriter(self.store, self.settings, self.session["id"])
                    try:
                        await self.capture.run(room, self.writer, self._on_audio)
                        raise SourceError("采集连接已关闭，重新检查直播状态。")
                    finally:
                        self.writer.close()
                        self.writer = None
                except NeedsAttention:
                    raise
                except SourceError as error:
                    self.last_error = redact(str(error), self.settings.cookie)
                    logger.warning(
                        "直播间 %s: %s %s",
                        self.room_id,
                        self.last_error,
                        redact(error.diagnostic, self.settings.cookie),
                    )
                    self.state = "reconnecting"
                    if self.session:
                        self.store.open_gap(
                            self.session["id"],
                            self.last_error,
                            self.store.last_segment_end(self.session["id"]),
                        )
                    delays = self.settings.retry_delays
                    delay = delays[min(self.retry_index, len(delays) - 1)]
                    self.retry_index += 1
                    await self._sleep(delay)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # A failed worker must leave a visible state, stop automatic restoration,
            # and preserve the exception's useful context without exposing credentials.
            self.last_error = redact(f"{type(error).__name__}: {error}", self.settings.cookie)
            logger.error("守卫需要处理: %s", self.last_error)
            if isinstance(error, SourceError) and error.diagnostic:
                logger.error("详情: %s", redact(error.diagnostic, self.settings.cookie))
            self.enabled = False
            self.state = "needs_attention"
            self.store.set_guard(self.room_id, False, self.last_error)
            if self.session:
                self.store.event(self.session["id"], "needs_attention", self.last_error)
                self.store.open_gap(
                    self.session["id"],
                    self.last_error,
                    self.store.last_segment_end(self.session["id"]),
                )

    def status(self) -> dict:
        session_id = self.session["id"] if self.session else None
        count, seconds = self.store.totals(session_id)
        if self.writer:
            seconds += self.writer.open_samples / self.settings.sample_rate
        return {
            "state": self.state,
            "enabled": self.enabled,
            "mode": self.mode,
            "source": self.local_source,
            "realtime_analysis": bool(self.store.guard()["realtime_analysis"]),
            "room_id": self.room_id,
            "anchor_name": self.anchor_name,
            "title": self.title,
            "session_id": session_id,
            "started_at": self.session["started_at"] if self.session else None,
            "last_audio_at": self.last_audio_at,
            "last_error": self.last_error,
            "segment_count": count,
            "captured_seconds": seconds,
            "retry_in_seconds": max(0, self.retry_deadline - time.monotonic())
            if self.retry_deadline
            else None,
            "data_dir": str(self.store.data_dir),
            "time_basis": "音频接收时间（北京时间）",
        }
