import pytest
from conftest import TEST_PASSWORD, login
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from douyin_live_sense.auth import COOKIE_NAME, LoginLimiter


def test_unauthenticated_requests_are_blocked(build_app):
    client = TestClient(build_app(), base_url="http://127.0.0.1")
    with client:
        assert client.get("/api/monitor").status_code == 401
        redirect = client.get("/", follow_redirects=False)
        assert redirect.status_code == 302
        assert redirect.headers["location"] == "/login"
        assert client.get("/style.css").status_code == 200
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/local/audio?source=microphone"):
                pass


def test_unauthenticated_api_response_has_matching_content_length(build_app):
    client = TestClient(build_app(), base_url="http://127.0.0.1")
    with client:
        response = client.get("/api/monitor")
        assert response.status_code == 401
        assert response.json() == {"detail": "请先登录。"}
        assert int(response.headers["content-length"]) == len(response.content)


def test_login_sets_seven_day_httponly_cookie(build_app):
    client = TestClient(build_app(), base_url="http://127.0.0.1")
    with client:
        response = client.post("/api/login", json={"password": TEST_PASSWORD})
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert "Max-Age=604800" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie
        assert client.get("/api/monitor").status_code == 200
        assert client.get("/api/auth").json()["username"] == "admin"


def test_login_page_is_public_and_authenticated_users_are_redirected(build_app):
    client = TestClient(build_app(), base_url="http://127.0.0.1")
    with client:
        page = client.get("/login")
        assert page.status_code == 200
        assert 'id="login-form"' in page.text
        login(client)
        redirected = client.get("/login", follow_redirects=False)
        assert redirected.status_code == 307
        assert redirected.headers["location"] == "/"


def test_wrong_password_is_rate_limited(build_app):
    app = build_app(limiter=LoginLimiter(max_failures=2, lock_seconds=60))
    client = TestClient(app, base_url="http://127.0.0.1")
    with client:
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
        locked = client.post("/api/login", json={"password": TEST_PASSWORD})
        assert locked.status_code == 429
        assert int(locked.headers["retry-after"]) > 0


def test_secure_cookie_when_proxy_reports_https(build_app):
    client = TestClient(
        build_app(secure_cookies="auto", trust_proxy=True), base_url="http://127.0.0.1"
    )
    with client:
        response = client.post(
            "/api/login",
            json={"password": TEST_PASSWORD},
            headers={"X-Forwarded-Proto": "https"},
        )
        assert "Secure" in response.headers["set-cookie"]
        # Secure cookies are not replayed over the http test transport; send it explicitly.
        token = response.cookies[COOKIE_NAME]
        started = client.post(
            "/api/monitor/start",
            json={"room": "123"},
            headers={
                "X-Forwarded-Proto": "https",
                "Origin": "https://127.0.0.1",
                "Cookie": f"{COOKIE_NAME}={token}",
            },
        )
        assert started.status_code == 200


def test_logout_and_password_change(build_app):
    client = TestClient(build_app(), base_url="http://127.0.0.1")
    with client:
        login(client)
        wrong = client.post("/api/password", json={"current": "wrong", "new": "new-password-1234"})
        assert wrong.status_code == 400
        short = client.post("/api/password", json={"current": TEST_PASSWORD, "new": "short"})
        assert short.status_code == 422
        changed = client.post(
            "/api/password", json={"current": TEST_PASSWORD, "new": "new-password-1234"}
        )
        assert changed.status_code == 200
        assert client.post("/api/logout").status_code == 200
        assert client.get("/api/monitor").status_code == 401
