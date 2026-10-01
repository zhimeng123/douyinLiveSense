import time

from conftest import FakeCapture, FakeSource
from fastapi.testclient import TestClient

from douyin_live_sense.app import create_app
from douyin_live_sense.audio import SegmentWriter
from douyin_live_sense.monitor import Monitor
from douyin_live_sense.storage import Storage, utc_now


def test_api_start_stop_conflict_and_validation(settings):
    store = Storage(settings.data_dir)
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    with TestClient(create_app(settings, monitor), base_url="http://127.0.0.1") as client:
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


def test_pending_audio_is_not_public_and_invalid_host_origin_rejected(settings):
    store = Storage(settings.data_dir)
    session = store.create_session("123", "id", "a", "t")
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    with TestClient(create_app(settings, monitor), base_url="http://127.0.0.1") as client:
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
