import json

import pytest
from conftest import FakeCapture, FakeSource, login
from fastapi.testclient import TestClient

from douyin_live_sense.analysis import AnalysisService
from douyin_live_sense.app import create_app
from douyin_live_sense.monitor import Monitor
from douyin_live_sense.preferences import Preferences
from douyin_live_sense.storage import Storage


def test_defaults_to_record_and_default_seconds(tmp_path):
    prefs = Preferences(tmp_path / "preferences.json", default_segment_seconds=30)
    assert prefs.mode() == "record"
    assert prefs.segment_seconds() == 30
    assert prefs.enabled() is False


def test_legacy_v1_recognition_true_migrates_to_models(tmp_path):
    path = tmp_path / "preferences.json"
    path.write_text(json.dumps({"version": 1, "recognition": True}))
    assert Preferences(path).mode() == "models"
    assert Preferences(path).enabled() is True


def test_update_persists_mode_and_seconds(tmp_path):
    prefs = Preferences(tmp_path / "preferences.json")
    prefs.update(mode="models", segment_seconds=600)
    reloaded = Preferences(tmp_path / "preferences.json")
    assert reloaded.mode() == "models"
    assert reloaded.segment_seconds() == 600
    assert reloaded.snapshot() == {"mode": "models", "segment_seconds": 600}


@pytest.mark.parametrize("seconds", [0, -1, 14_401])
def test_update_rejects_seconds_out_of_range(tmp_path, seconds):
    with pytest.raises(ValueError):
        Preferences(tmp_path / "preferences.json").update(segment_seconds=seconds)


def test_update_rejects_unknown_mode(tmp_path):
    with pytest.raises(ValueError):
        Preferences(tmp_path / "preferences.json").update(mode="auto")


@pytest.mark.parametrize("payload", [{"version": "2"}, {"version": None}, {"version": []}])
def test_unreadable_version_uses_defaults(tmp_path, payload):
    path = tmp_path / "preferences.json"
    path.write_text(json.dumps(payload))
    prefs = Preferences(path, default_segment_seconds=30)
    assert prefs.mode() == "record"
    assert prefs.segment_seconds() == 30


def test_api_refuses_enabling_without_models(settings, auth, monkeypatch):
    monkeypatch.setattr(
        "douyin_live_sense.app.model_status",
        lambda settings: {
            "ready": False,
            "reason": "未准备模型：asr",
            "components": {
                "transcription": {"missing": ["asr"]},
                "diarization": {"missing": ["diarization"]},
            },
        },
    )
    store = Storage(settings.data_dir)
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    with TestClient(
        create_app(settings, monitor, auth=auth), base_url="http://127.0.0.1"
    ) as client:
        login(client)
        response = client.put("/api/preferences", json={"mode": "models"})
        assert response.status_code == 409
        assert "asr" in response.text


def test_preferences_api_reports_mode_and_duration(build_app):
    client = TestClient(build_app(), base_url="http://127.0.0.1")
    with client:
        login(client)
        body = client.get("/api/preferences").json()
        assert body["mode"] == "record"
        assert body["recognition"] is False
        assert body["segment_seconds"] == 0.002
        assert body["locked"] is False
        assert body["segment_min_seconds"] == 1
        assert body["segment_max_seconds"] == 14_400


def test_preferences_api_sets_duration(build_app):
    client = TestClient(build_app(), base_url="http://127.0.0.1")
    with client:
        login(client)
        response = client.put("/api/preferences", json={"segment_seconds": 5})
        assert response.status_code == 200
        assert response.json()["segment_seconds"] == 5
        assert client.get("/api/preferences").json()["segment_seconds"] == 5


@pytest.mark.parametrize("seconds", [0, 14_401])
def test_preferences_api_rejects_bad_duration(build_app, seconds):
    client = TestClient(build_app(), base_url="http://127.0.0.1")
    with client:
        login(client)
        response = client.put("/api/preferences", json={"segment_seconds": seconds})
        assert response.status_code == 422


def test_feishu_mode_is_not_available_yet(build_app):
    client = TestClient(build_app(), base_url="http://127.0.0.1")
    with client:
        login(client)
        response = client.put("/api/preferences", json={"mode": "feishu"})
        assert response.status_code == 409
        assert "飞书" in response.text
        assert client.get("/api/preferences").json()["mode"] == "record"


def test_mode_cannot_change_while_task_running(build_app, monkeypatch):
    monkeypatch.setattr(
        "douyin_live_sense.app.model_status",
        lambda settings: {"ready": True, "reason": "", "components": {}},
    )
    client = TestClient(build_app(), base_url="http://127.0.0.1")
    with client:
        login(client)
        client.app.state.monitor.enabled = True
        response = client.put("/api/preferences", json={"mode": "models"})
        assert response.status_code == 409
        assert client.get("/api/preferences").json()["locked"] is True
        assert client.put("/api/preferences", json={"segment_seconds": 8}).status_code == 200


def test_rejected_update_leaves_stored_preferences_untouched(build_app):
    client = TestClient(build_app(), base_url="http://127.0.0.1")
    with client:
        login(client)
        assert client.put("/api/preferences", json={"segment_seconds": 7}).status_code == 200
        assert client.put("/api/preferences", json={"segment_seconds": 14_401}).status_code == 422
        assert client.put("/api/preferences", json={"mode": "feishu"}).status_code == 409
        body = client.get("/api/preferences").json()
        assert body["mode"] == "record"
        assert body["segment_seconds"] == 7


def test_injected_monitor_uses_saved_duration(build_app):
    client = TestClient(build_app(), base_url="http://127.0.0.1")
    with client:
        login(client)
        assert client.put("/api/preferences", json={"segment_seconds": 9}).status_code == 200
        assert client.app.state.monitor.segment_seconds() == 9


def test_legacy_preferences_file_is_migrated_on_read(build_app, settings):
    (settings.data_dir / "preferences.json").write_text(
        json.dumps({"version": 1, "recognition": True})
    )
    client = TestClient(build_app(), base_url="http://127.0.0.1")
    with client:
        login(client)
        body = client.get("/api/preferences").json()
        assert body["mode"] == "models"
        assert body["recognition"] is True


def test_analysis_request_is_noop_when_disabled(settings):
    store = Storage(settings.data_dir)
    monitor = Monitor(store, settings, FakeSource(), FakeCapture())
    prefs = Preferences(settings.data_dir / "preferences.json")
    service = AnalysisService(monitor, settings, worker=FixtureWorker(), preferences=prefs)
    session = store.create_session("123", "live", "a", "t")
    assert service.request(session["id"])["state"] == "not_requested"


class FixtureWorker:
    async def execute(self, payload):
        return {}
