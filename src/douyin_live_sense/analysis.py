import asyncio
import json
import logging
import multiprocessing
import os
import shutil
import sys
import time
from pathlib import Path

from .analysis_storage import AnalysisStorage
from .analysis_worker import worker_main
from .config import Settings
from .models import model_status, pipeline_config
from .monitor import Monitor, MonitorConflict
from .security import redact
from .transcripts import export_run

logger = logging.getLogger(__name__)


class ProcessWorker:
    def __init__(self, settings: Settings, target=worker_main):
        self.settings = settings
        self.target = target
        self.process = None
        self.connection = None
        ffmpeg = shutil.which(settings.ffmpeg)
        self.library_dir = (
            str(Path(ffmpeg).resolve().parent.parent / "lib")
            if sys.platform == "darwin" and ffmpeg
            else None
        )

    async def execute(self, payload: dict) -> dict:
        if self.process is None:
            context = multiprocessing.get_context("spawn")
            parent, child = context.Pipe()
            self.process = context.Process(
                target=self.target, args=(child, self.settings), name="local-audio-analysis"
            )
            # macOS dyld reads this at interpreter startup, before SDK imports.
            library_path = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH")
            if self.library_dir:
                os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = self.library_dir + (
                    ":" + library_path if library_path else ""
                )
            try:
                self.process.start()
            finally:
                if library_path is None:
                    os.environ.pop("DYLD_FALLBACK_LIBRARY_PATH", None)
                else:
                    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = library_path
            child.close()
            self.connection = parent
        self.connection.send(payload)
        async with asyncio.timeout(300):
            while not self.connection.poll():  # noqa: ASYNC110
                if not self.process.is_alive():
                    raise RuntimeError(f"分析进程异常退出（{self.process.exitcode}）。")
                await asyncio.sleep(0.05)
            try:
                response = self.connection.recv()
            except EOFError as error:
                raise RuntimeError(
                    f"分析进程连接已关闭（退出码 {self.process.exitcode}）。"
                ) from error
        if "error" in response:
            raise RuntimeError(response["error"])
        return response["result"]

    async def close(self) -> None:
        if self.process is None:
            return
        if self.process.is_alive():
            self.process.terminate()
            await asyncio.to_thread(self.process.join, 5)
            if self.process.is_alive():
                self.process.kill()
                await asyncio.to_thread(self.process.join, 5)
        else:
            self.process.join()
        self.connection.close()
        self.process.close()
        self.connection = self.process = None


class AnalysisService:
    def __init__(self, monitor: Monitor, settings: Settings, worker=None, readiness=None):
        self.monitor, self.settings = monitor, settings
        self.storage = AnalysisStorage(monitor.store)
        self.worker = worker or ProcessWorker(settings)
        self.readiness = readiness or (lambda: model_status(settings))
        self.task = None
        self.wake = asyncio.Event()
        self.previous_live_session = None
        self.current_job = None
        self.current_started = None
        self.last_error = None
        self.last_export_check = 0.0

    async def start(self) -> None:
        self.storage.recover()
        self.sync_live()
        self.task = asyncio.create_task(self._run(), name="analysis-dispatcher")

    def sync_live(self) -> None:
        guard = self.monitor.store.guard()
        session = self.monitor.session
        live_id = session["id"] if session and guard["realtime_analysis"] else None
        if self.previous_live_session and self.previous_live_session != live_id:
            self.request(self.previous_live_session)
        if live_id:
            self.request(live_id)
        self.previous_live_session = live_id

    def request(self, session_id: str, retry: bool = False) -> dict:
        self.storage.request(session_id, pipeline_config(self.settings), retry)
        self.wake.set()
        return self.storage.status(session_id)

    def request_segment(
        self, segment_id: str, reanalyse: bool = False, retry: bool = False
    ) -> dict:
        self.storage.request_segment(segment_id, pipeline_config(self.settings), reanalyse, retry)
        self.wake.set()
        return self.storage.segment_status(segment_id)

    def queue(self, limit: int = 50, offset: int = 0) -> dict:
        result = self.storage.queue(self.current_job, limit, offset)
        active = None
        if self.current_job:
            job = self.current_job
            run = self.storage.run(job["run_id"])
            segment = self.storage.db.execute(
                "SELECT * FROM segments WHERE session_id=? AND sequence=?",
                (run["session_id"], job["sequence"]),
            ).fetchone()
            active = {
                "job_id": job["id"],
                "run_id": run["id"],
                "scope": run["scope"],
                "revision": run["revision"],
                "session_id": run["session_id"],
                "segment_id": segment["id"] if segment else None,
                "sequence": job["sequence"],
                "kind": job["kind"],
                "start_at": segment["start_at"] if segment else None,
                "end_at": segment["end_at"] if segment else None,
                "elapsed_seconds": time.monotonic() - self.current_started,
            }
        result["active"] = active
        return result

    async def set_realtime(self, enabled: bool) -> dict:
        async with self.monitor.lock:
            if not self.monitor.enabled:
                raise MonitorConflict("请先开启守卫，再切换实时分析。")
            self.monitor.store.set_realtime(enabled)
            if self.monitor.session:
                self.request(self.monitor.session["id"])
            self.sync_live()
        return self.status()

    def status(self) -> dict:
        return {
            "realtime_analysis": bool(self.monitor.store.guard()["realtime_analysis"]),
            "models": self.readiness(),
            "worker_state": "processing" if self.current_job else "idle",
            "active_run_id": self.current_job["run_id"] if self.current_job else None,
            "last_error": self.last_error,
            "queued_runs": self.storage.db.execute(
                "SELECT COUNT(*) FROM analysis_runs WHERE state NOT IN ('complete','waiting_audio')"
            ).fetchone()[0],
        }

    def export(self, run: dict) -> dict:
        try:
            return export_run(self.storage, run)
        except OSError as error:
            message = f"文档保存失败：{error}"
            logger.error("%s", message)
            with self.storage.db:
                self.storage.db.execute(
                    "UPDATE analysis_runs SET export_error=? WHERE id=?", (message, run["id"])
                )
            raise

    async def _run(self) -> None:
        try:
            while True:
                self.sync_live()
                self.wake.clear()
                ready = self.readiness()
                current_id = self.monitor.session["id"] if self.monitor.session else None
                for run in self.storage.runs(current_id):
                    if run["state"] == "needs_attention":
                        continue
                    if (
                        run["processed_sequence"] >= run["target_sequence"]
                        and not run["pending_json"]
                    ):
                        self.storage.set_state(
                            run["id"], "waiting_audio" if self._is_live(run) else "complete"
                        )
                        continue
                    if not ready["ready"]:
                        self.storage.set_state(run["id"], "needs_setup", ready["reason"])
                        continue
                    if not json.loads(run["config_json"])["models"]:
                        run = self.storage.bind_models(run, pipeline_config(self.settings))
                    job = self.storage.next_job(run, self._is_live(run))
                    if job:
                        await self._execute(job)
                        break
                else:
                    if time.monotonic() - self.last_export_check > 10:
                        self.last_export_check = time.monotonic()
                        for run in self.storage.runs(current_id):
                            if run["export_error"]:
                                try:
                                    self.export(run)
                                except OSError:
                                    # Retry files independently of model inference.
                                    pass
                    try:
                        # Keep cancellation on this task. wait_for creates an inner
                        # task and can lose shutdown cancellation as the wake fires.
                        async with asyncio.timeout(0.5):
                            await self.wake.wait()
                    except TimeoutError:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.last_error = redact(
                f"分析调度停止：{type(error).__name__}: {error}", self.settings.cookie
            )
            logger.exception("%s", self.last_error)

    def _is_live(self, run: dict) -> bool:
        return bool(
            run["scope"] == "session"
            and self.monitor.store.guard()["realtime_analysis"]
            and self.monitor.session
            and self.monitor.session["id"] == run["session_id"]
        )

    async def _execute(self, job: dict) -> None:
        self.current_job = job
        self.current_started = time.monotonic()
        self.storage.begin(job)
        try:
            result = await self.worker.execute(json.loads(job["payload_json"]))
            self.storage.commit(job, result)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            message = redact(f"{type(error).__name__}: {error}", self.settings.cookie)
            logger.error("分析窗口 %s 失败：%s", job["id"], message)
            self.storage.fail(job, message)
            await self.worker.close()
            self.last_error = message
            await asyncio.sleep(0.5)
        else:
            self.last_error = None
            try:
                self.export(self.storage.run(job["run_id"]))
            except OSError:
                pass
        finally:
            self.current_job = None
            self.current_started = None

    async def shutdown(self) -> None:
        self.sync_live()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        await self.worker.close()
        self.storage.recover()
