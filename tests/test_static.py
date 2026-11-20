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


def test_index_places_model_setup_in_menu_view():
    html = (STATIC / "index.html").read_text()
    assert 'id="nav-models"' in html
    models_start = html.index('<section id="models-view"')
    models_end = html.index('</section>', models_start)
    home_start = html.index('<div id="home-view">')
    home_end = html.index('</div>\n      <section id="queue-view"', home_start)
    models_view = html[models_start:models_end]
    home_view = html[home_start:home_end]
    assert 'id="models-card"' in models_view
    assert 'id="models-card"' not in home_view


def test_app_routes_model_settings_view():
    js = (STATIC / "app.js").read_text()
    for value in ['"#/models"', '"models-view"', '"nav-models"']:
        assert value in js


def test_app_js_calls_model_endpoints():
    js = (STATIC / "app.js").read_text()
    for endpoint in [
        "/api/models",
        "/api/models/token",
        "/api/models/download",
        "/api/models/job",
    ]:
        assert endpoint in js
