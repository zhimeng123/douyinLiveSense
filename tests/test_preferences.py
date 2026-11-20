from conftest import FakeCapture, FakeSource, login
from fastapi.testclient import TestClient

from douyin_live_sense.analysis import AnalysisService
from douyin_live_sense.app import create_app
from douyin_live_sense.monitor import Monitor
from douyin_live_sense.preferences import Preferences
from douyin_live_sense.storage import Storage


def test_defaults_to_record_only(tmp_path):
    assert Preferences(tmp_path / "preferences.json").enabled() is False


def test_toggle_persists(tmp_path):
    prefs = Preferences(tmp_path / "preferences.json")
    prefs.set_enabled(True)
    assert Preferences(tmp_path / "preferences.json").enabled() is True


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
        response = client.put("/api/preferences", json={"recognition": True})
        assert response.status_code == 409
        assert "asr" in response.text


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
