from pathlib import Path

STATIC = Path(__file__).parents[1] / "src" / "douyin_live_sense" / "static"


def test_login_page_has_required_controls():
    html = (STATIC / "login.html").read_text()
    for element in [
        'id="login-form"',
        'id="login-password"',
        'id="login-submit"',
        'id="login-error"',
    ]:
        assert element in html
    assert "/api/login" in (STATIC / "login.js").read_text()


def test_index_has_logout_control():
    assert 'id="logout"' in (STATIC / "index.html").read_text()


def test_index_has_model_setup_controls():
    html = (STATIC / "index.html").read_text()
    for element in [
        "models-card",
        "models-switch",
        "models-token",
        "models-progress",
        "models-transcription",
        "models-diarization",
    ]:
        assert f'id="{element}"' in html


def test_app_js_calls_model_endpoints():
    js = (STATIC / "app.js").read_text()
    for endpoint in [
        "/api/models",
        "/api/models/token",
        "/api/models/download",
        "/api/models/job",
    ]:
        assert endpoint in js
