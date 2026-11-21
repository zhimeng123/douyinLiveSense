import time

from conftest import FakeCapture, FakeSource, login
from fastapi.testclient import TestClient

from douyin_live_sense.app import create_app
from douyin_live_sense.audio import SegmentWriter
from douyin_live_sense.monitor import Monitor
from douyin_live_sense.storage import Storage, utc_now


def test_api_start_stop_conflict_and_validation(settings, auth):
    store = Storage(settings.data_dir)
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    app = create_app(settings, monitor, auth=auth)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        assert client.get("/").status_code == 200
        assert "直播监听守卫" in client.get("/").text
        assert client.get("/style.css").status_code == 200
        assert client.get("/app.js").status_code == 200
        assert (
            client.post("/api/monitor/start", json={"room": "https://evil.test/123"}).status_code
            == 400
        )
        assert client.post("/api/monitor/start", json={"room": ""}).status_code == 422
        assert client.post("/api/monitor/start", json={"room": "123"}).status_code == 200
        assert client.post("/api/monitor/start", json={"room": "123"}).status_code == 200
        assert client.post("/api/monitor/start", json={"room": "456"}).status_code == 409
        deadline = time.monotonic() + 2
        while client.get("/api/monitor").json()["state"] != "recording":
            assert time.monotonic() < deadline
            time.sleep(0.005)
        assert "cdn.test" not in client.get("/api/monitor").text
        assert client.post("/api/monitor/stop").json()["state"] == "stopped"
        sessions = client.get("/api/sessions").json()
        assert sessions[0]["segment_count"] == 1
        session_id = sessions[0]["id"]
        segment = client.get(f"/api/sessions/{session_id}/segments").json()[0]
        audio = client.get(f"/api/segments/{segment['id']}/audio")
        assert audio.status_code == 200 and audio.content[:4] == b"RIFF"
        assert audio.headers["content-type"] == "audio/wav"
        partial = client.get(
            f"/api/segments/{segment['id']}/audio", headers={"Range": "bytes=0-43"}
        )
        assert partial.status_code == 206 and len(partial.content) == 44
        download = client.get(f"/api/segments/{segment['id']}/audio?download=true")
        assert "attachment" in download.headers["content-disposition"]
        assert client.get(f"/api/sessions/{session_id}/events").status_code == 200
        assert client.get("/api/sessions/missing/segments").status_code == 404
        assert client.get("/api/segments/missing/audio").status_code == 404
        assert client.get("/api/sessions?limit=1000").status_code == 422
    store.close()


def test_pending_audio_is_not_public_and_invalid_host_origin_rejected(settings, auth):
    store = Storage(settings.data_dir)
    session = store.create_session("123", "id", "a", "t")
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    app = create_app(settings, monitor, auth=auth)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        writer = SegmentWriter(store, settings, session["id"])
        writer.feed(b"\x01\x02" * 8, utc_now())
        segment_id = writer.segment["id"]
        assert client.get(f"/api/segments/{segment_id}/audio").status_code == 404
        assert (
            client.post(
                "/api/monitor/start", json={"room": "123"}, headers={"Origin": "https://evil.test"}
            ).status_code
            == 403
        )
        assert client.get("/api/monitor", headers={"Host": "evil.test"}).status_code == 400
        assert not monitor.enabled
        writer.close()
        assert client.get(f"/api/segments/{segment_id}/audio").status_code == 200
        path = store.segment_path(store.segment(segment_id))
        path.unlink()
        assert client.get(f"/api/segments/{segment_id}/audio").status_code == 404
    store.close()


def test_delete_session_removes_audio_records_and_files(settings, auth):
    store = Storage(settings.data_dir)
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    app = create_app(settings, monitor, auth=auth)
    session = store.create_session("123", "live", "主播", "标题")
    writer = SegmentWriter(store, settings, session["id"])
    writer.feed(b"\x01\x02" * 8, utc_now())
    writer.close()
    audio_dir = settings.data_dir / "audio" / "123" / session["id"]
    assert audio_dir.is_dir()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        assert client.delete("/api/sessions/missing").status_code == 404
        response = client.delete(f"/api/sessions/{session['id']}")
        assert response.status_code == 200
        assert response.json() == {"deleted": session["id"]}
        assert client.get(f"/api/sessions/{session['id']}").status_code == 404
        assert store.session(session["id"]) is None
        assert not audio_dir.exists()
        assert client.get("/api/sessions").json() == []
    store.close()


def test_delete_session_blocked_while_recording(settings, auth):
    store = Storage(settings.data_dir)
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    app = create_app(settings, monitor, auth=auth)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        assert client.post("/api/monitor/start", json={"room": "123"}).status_code == 200
        deadline = time.monotonic() + 2
        while not monitor.session:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        session_id = monitor.session["id"]
        assert client.delete(f"/api/sessions/{session_id}").status_code == 409
        assert client.post("/api/monitor/stop").status_code == 200
    store.close()


def test_delete_session_blocked_while_analysing(settings, auth):
    store = Storage(settings.data_dir)
    session = store.create_session("123", "live", "主播", "标题")
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    app = create_app(settings, monitor, auth=auth)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        # 停掉调度循环，才能稳定构造“存在未完成分析”的状态。
        app.state.analysis.task.cancel()
        time.sleep(0.05)
        with store.db:
            store.db.execute(
                "INSERT INTO analysis_runs(id,session_id,config_key,config_json,created_at,state) "
                "VALUES (?,?,?,?,?,'pending')",
                ("run-1", session["id"], "k", "{}", utc_now()),
            )
        assert client.delete(f"/api/sessions/{session['id']}").status_code == 409
        assert store.session(session["id"]) is not None
    store.close()
