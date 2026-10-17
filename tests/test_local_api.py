import time
from dataclasses import replace

import pytest
from conftest import FakeCapture, FakeSource
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from douyin_live_sense.app import create_app
from douyin_live_sense.monitor import Monitor
from douyin_live_sense.storage import Storage


def build(settings):
    store = Storage(settings.data_dir)
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    return store, create_app(settings, monitor), monitor


def wait_session_end(client, session_id, deadline=2.0):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        session = client.get(f"/api/sessions/{session_id}").json()
        if session.get("ended_at"):
            return session
        time.sleep(0.01)
    raise AssertionError("场次未结束")


def test_local_websocket_records_audio(settings):
    settings = replace(settings, idle_seconds=30)
    store, app, monitor = build(settings)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        with client.websocket_connect(
            "/api/local/audio?source=microphone&realtime=false",
            headers={"host": "127.0.0.1"},
        ) as ws:
            hello = ws.receive_json()
            assert hello["session_id"]
            session_id = hello["session_id"]
            for _ in range(4):
                ws.send_bytes(b"\x00\x02" * 400)
        wait_session_end(client, session_id)
        sessions = client.get("/api/sessions").json()
        assert sessions[0]["id"] == session_id
        assert sessions[0]["room_id"] == "local"
        assert sessions[0]["segment_count"] >= 1
        assert client.get("/api/monitor").json()["mode"] is None
    store.close()


def test_local_websocket_conflicts_with_guard(settings):
    settings = replace(settings, idle_seconds=30)
    store, app, monitor = build(settings)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/monitor/start", json={"room": "123"}).status_code == 200
        with client.websocket_connect(
            "/api/local/audio?source=microphone", headers={"host": "127.0.0.1"}
        ) as ws:
            assert "error" in ws.receive_json()
        assert client.get("/api/monitor").json()["mode"] == "douyin"
        client.post("/api/monitor/stop")
    store.close()


def test_local_websocket_rejects_foreign_origin(settings):
    store, app, monitor = build(settings)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/api/local/audio?source=microphone",
                headers={"host": "127.0.0.1", "origin": "https://evil.test"},
            ) as ws:
                ws.send_bytes(b"\x00")
                ws.receive_json()
    store.close()
