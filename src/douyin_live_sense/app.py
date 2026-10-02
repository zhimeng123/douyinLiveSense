from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .analysis import AnalysisService
from .analysis_audio import read_sources, wav_bytes
from .config import Settings
from .monitor import Monitor, MonitorConflict
from .source import NeedsAttention
from .storage import InstanceLock, Storage
from .transcripts import chronological, speaker_label


class StartRequest(BaseModel):
    room: str = Field(min_length=1, max_length=2048)
    realtime_analysis: bool = False


class RealtimeRequest(BaseModel):
    enabled: bool


class AnalysisRequest(BaseModel):
    retry: bool = False


class SegmentAnalysisRequest(BaseModel):
    reanalyse: bool = False
    retry: bool = False


def create_app(
    settings: Settings | None = None,
    monitor: Monitor | None = None,
    analysis: AnalysisService | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = monitor.store if monitor else None
        instance_lock = None
        try:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            instance_lock = InstanceLock(store.data_dir if store else settings.data_dir)
            store = store or Storage(settings.data_dir)
            app.state.monitor = monitor or Monitor(store, settings)
            app.state.store = store
            await app.state.monitor.restore()
            app.state.analysis = analysis or AnalysisService(app.state.monitor, settings)
            await app.state.analysis.start()
            try:
                yield
            finally:
                await app.state.monitor.shutdown()
                await app.state.analysis.shutdown()
        finally:
            if instance_lock:
                instance_lock.close()
            if not monitor and store:
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
            result = await app.state.monitor.start(body.room, body.realtime_analysis)
            app.state.analysis.sync_live()
            return result
        except MonitorConflict as error:
            raise HTTPException(409, str(error)) from error
        except (ValueError, NeedsAttention) as error:
            raise HTTPException(400, str(error)) from error

    @app.post("/api/monitor/stop")
    async def stop_monitor():
        previous = app.state.monitor.status()
        result = await app.state.monitor.stop()
        if previous["realtime_analysis"] and previous["session_id"]:
            app.state.analysis.request(previous["session_id"])
        app.state.analysis.sync_live()
        return result

    @app.get("/api/analysis/status")
    async def get_analysis_status():
        return app.state.analysis.status()

    @app.get("/api/analysis/queue")
    async def get_queue(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        return app.state.analysis.queue(limit, offset)

    @app.put("/api/analysis/realtime")
    async def set_realtime(body: RealtimeRequest):
        try:
            return await app.state.analysis.set_realtime(body.enabled)
        except MonitorConflict as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/api/analysis/requests")
    async def get_analysis_requests(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        service = app.state.analysis
        return service.storage.requests(service.current_job, limit, offset)

    @app.get("/api/analysis/requests/{run_id}/segments")
    async def get_request_segments(
        run_id: str,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        service = app.state.analysis
        try:
            return service.storage.request_segments(run_id, service.current_job, limit, offset)
        except KeyError as error:
            raise HTTPException(404, "分析请求不存在。") from error

    @app.post("/api/sessions/{session_id}/analysis")
    async def start_analysis(session_id: str, body: AnalysisRequest | None = None):
        try:
            return app.state.analysis.request(session_id, body.retry if body else False)
        except KeyError as error:
            raise HTTPException(404, "场次不存在。") from error

    @app.get("/api/sessions/{session_id}/analysis")
    async def get_analysis(session_id: str):
        if not app.state.store.session(session_id):
            raise HTTPException(404, "场次不存在。")
        return app.state.analysis.storage.status(session_id)

    @app.get("/api/sessions/{session_id}/utterances")
    async def get_utterances(
        session_id: str,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ):
        if not app.state.store.session(session_id):
            raise HTTPException(404, "场次不存在。")
        storage = app.state.analysis.storage
        run = storage.latest(session_id)
        rows = storage.utterances(run["id"], after, limit) if run else []
        numbers = {p["id"]: p["number"] for p in storage.profiles(run["id"])} if run else {}
        return utterance_page(run, rows, numbers, after)

    def utterance_page(run, rows, numbers, after):
        cursor = max((row["ordinal"] for row in rows), default=after)
        for row in rows:
            row["speaker_label"] = speaker_label(row, numbers)
            row["cross_segment"] = len({source["segment_id"] for source in row["sources"]}) > 1
        return {
            "run_id": run["id"] if run else None,
            "items": sorted(rows, key=chronological),
            "next_cursor": cursor,
        }

    def require_segment(segment_id: str):
        segment = app.state.store.segment(segment_id)
        if not segment:
            raise HTTPException(404, "切片不存在。")
        if segment["state"] != "ready":
            raise HTTPException(409, "切片尚未保存完成。")
        return segment

    @app.post("/api/segments/{segment_id}/analysis")
    async def start_segment_analysis(segment_id: str, body: SegmentAnalysisRequest | None = None):
        require_segment(segment_id)
        body = body or SegmentAnalysisRequest()
        return app.state.analysis.request_segment(segment_id, body.reanalyse, body.retry)

    @app.get("/api/segments/{segment_id}/analysis")
    async def get_segment_analysis(segment_id: str):
        require_segment(segment_id)
        return app.state.analysis.storage.segment_status(segment_id)

    @app.get("/api/segments/{segment_id}/utterances")
    async def get_segment_utterances(
        segment_id: str,
        scope: Annotated[str, Query(pattern="^(session|segment)$")] = "session",
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ):
        require_segment(segment_id)
        storage = app.state.analysis.storage
        run, rows = storage.segment_utterances(segment_id, scope, after, limit)
        numbers = {p["id"]: p["number"] for p in storage.profiles(run["id"])} if run else {}
        return utterance_page(run, rows, numbers, after)

    @app.get("/api/segments/{segment_id}/transcript")
    async def get_segment_transcript(
        segment_id: str,
        format: Annotated[str, Query(pattern="^(txt|md|jsonl)$")] = "txt",
    ):
        require_segment(segment_id)
        run = app.state.analysis.storage.latest_segment(segment_id, completed=True)
        if not run:
            raise HTTPException(404, "此切片尚无完成的单片复核结果。")
        return transcript_response(run, format, f"{segment_id}-review-v{run['revision']}")

    @app.get("/api/utterances/{utterance_id}/audio")
    async def get_utterance_audio(utterance_id: str):
        row = app.state.analysis.storage.utterance(utterance_id)
        if not row:
            raise HTTPException(404, "发言不存在。")
        try:
            pcm = read_sources(app.state.store.data_dir, row["sources"])
        except FileNotFoundError as error:
            raise HTTPException(404, "原始音频文件已不存在。") from error
        return Response(wav_bytes(pcm), media_type="audio/wav")

    @app.get("/api/sessions/{session_id}/transcript")
    async def get_transcript(
        session_id: str,
        format: Annotated[str, Query(pattern="^(txt|md|jsonl)$")] = "txt",
    ):
        run = app.state.analysis.storage.latest(session_id)
        if not run:
            raise HTTPException(404, "此场次尚未请求分析。")
        return transcript_response(run, format, session_id)

    def transcript_response(run: dict, format: str, filename: str):
        try:
            paths = app.state.analysis.export(run)
        except OSError as error:
            raise HTTPException(
                503, "文档暂时无法保存，请检查磁盘；分析记录仍在数据库中。"
            ) from error
        return FileResponse(
            paths[format], media_type="text/plain; charset=utf-8", filename=f"{filename}.{format}"
        )

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
        around: Annotated[int | None, Query(ge=1)] = None,
    ):
        if not app.state.store.session(session_id):
            raise HTTPException(404, "场次不存在。")
        if around is not None:
            count = app.state.store.db.execute(
                "SELECT COUNT(*) FROM segments WHERE session_id=? AND state='ready' AND sequence<?",
                (session_id, around),
            ).fetchone()[0]
            offset = count // limit * limit
        segments = app.state.analysis.storage.segment_summaries(
            app.state.store.segments(session_id, limit, offset)
        )
        for segment in segments:
            segment["page_offset"] = offset
        return segments

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str):
        session = app.state.store.session(session_id)
        if not session:
            raise HTTPException(404, "场次不存在。")
        session["segment_count"], session["duration_seconds"] = app.state.store.totals(session_id)
        return session

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
