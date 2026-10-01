from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings
from .monitor import Monitor, MonitorConflict
from .source import NeedsAttention
from .storage import InstanceLock, Storage


class StartRequest(BaseModel):
    room: str = Field(min_length=1, max_length=2048)


def create_app(settings: Settings | None = None, monitor: Monitor | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = monitor.store if monitor else Storage(settings.data_dir)
        instance_lock = None
        try:
            instance_lock = InstanceLock(store.data_dir)
            app.state.monitor = monitor or Monitor(store, settings)
            app.state.store = store
            await app.state.monitor.restore()
            try:
                yield
            finally:
                await app.state.monitor.shutdown()
        finally:
            if instance_lock:
                instance_lock.close()
            if not monitor:
                store.close()

    app = FastAPI(title="抖音直播监听守卫", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.middleware("http")
    async def local_only(request, call_next):
        # Bind to loopback and reject cross-origin mutations from unrelated websites.
        # Local browser requests have a matching Origin or no Origin.
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            if origin != f"{request.url.scheme}://{request.headers.get('host', '')}":
                from fastapi.responses import JSONResponse

                return JSONResponse({"detail": "不允许来自其他网站的操作。"}, status_code=403)
        return await call_next(request)

    @app.get("/api/monitor")
    async def get_monitor():
        return app.state.monitor.status()

    @app.post("/api/monitor/start")
    async def start_monitor(body: StartRequest):
        try:
            return await app.state.monitor.start(body.room)
        except MonitorConflict as error:
            raise HTTPException(409, str(error)) from error
        except (ValueError, NeedsAttention) as error:
            raise HTTPException(400, str(error)) from error

    @app.post("/api/monitor/stop")
    async def stop_monitor():
        return await app.state.monitor.stop()

    @app.get("/api/sessions")
    async def get_sessions(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        return app.state.store.sessions(limit, offset)

    @app.get("/api/sessions/{session_id}/segments")
    async def get_segments(
        session_id: str,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        if not app.state.store.session(session_id):
            raise HTTPException(404, "场次不存在。")
        return app.state.store.segments(session_id, limit, offset)

    @app.get("/api/sessions/{session_id}/events")
    async def get_events(session_id: str):
        if not app.state.store.session(session_id):
            raise HTTPException(404, "场次不存在。")
        return app.state.store.events(session_id)

    @app.get("/api/segments/{segment_id}/audio")
    async def get_audio(segment_id: str, download: bool = False):
        segment = app.state.store.segment(segment_id)
        if not segment or segment["state"] != "ready":
            raise HTTPException(404, "音频不存在或尚未保存完成。")
        path = app.state.store.segment_path(segment)
        if not path.is_file():
            raise HTTPException(404, "音频文件已不存在。")
        return FileResponse(
            path,
            media_type="audio/wav",
            filename=path.name if download else None,
        )

    static = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=static, html=True), name="web")
    return app
