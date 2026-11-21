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
