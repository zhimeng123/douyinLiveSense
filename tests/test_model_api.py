import pytest
from conftest import login
from fastapi.testclient import TestClient

from douyin_live_sense.model_manager import ModelManager, TokenRequired
from douyin_live_sense.models import write_token


@pytest.fixture
def build_model_app(build_app):
    return build_app


def test_models_require_login(build_model_app):
    client = TestClient(build_model_app(), base_url="http://127.0.0.1")
    with client:
        assert client.get("/api/models").status_code == 401


def test_token_is_masked_and_not_returned(build_model_app, monkeypatch):
    client = TestClient(build_model_app(), base_url="http://127.0.0.1")
    with client:
        login(client)

        def save_token(self, token):
            write_token(self.settings, token)
            return {"gated": False, "warning": ""}

        monkeypatch.setattr(ModelManager, "set_token", save_token)
        response = client.put("/api/models/token", json={"token": "hf_secret_value_1234"})
        assert response.status_code == 200
        assert "hf_secret_value_1234" not in response.text
        assert response.json()["token"]["configured"] is True
        assert response.json()["token"]["masked"] == "hf_s...1234"


def test_download_without_token_reports_needs_token(build_model_app, monkeypatch):
    def missing_token(self, parts):
        raise TokenRequired()

    monkeypatch.setattr(ModelManager, "start_download", missing_token)
    client = TestClient(build_model_app(), base_url="http://127.0.0.1")
    with client:
        login(client)
        response = client.post("/api/models/download", json={"parts": ["diarization"]})
        assert response.status_code == 400
        assert response.json()["needs_token"] is True


def test_preferences_enable_after_models_ready(build_model_app, monkeypatch):
    monkeypatch.setattr(
        "douyin_live_sense.app.model_status",
        lambda settings: {"ready": True, "reason": "", "components": {}},
    )
    client = TestClient(build_model_app(), base_url="http://127.0.0.1")
    with client:
        login(client)
        assert client.put("/api/preferences", json={"recognition": True}).status_code == 200
        assert client.get("/api/preferences").json()["recognition"] is True
