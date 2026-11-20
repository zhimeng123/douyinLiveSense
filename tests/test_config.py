import pytest

from douyin_live_sense.config import Settings


def test_defaults_are_loopback_and_seven_days(monkeypatch):
    for key in [
        "DLS_HOST",
        "DLS_PORT",
        "DLS_ALLOWED_HOSTS",
        "DLS_TRUST_PROXY",
        "DLS_SECURE_COOKIES",
        "DLS_SESSION_DAYS",
    ]:
        monkeypatch.delenv(key, raising=False)
    settings = Settings.from_env()
    assert settings.host == "127.0.0.1"
    assert settings.port == 8787
    assert settings.allowed_hosts == ("127.0.0.1", "localhost")
    assert settings.trust_proxy is False
    assert settings.secure_cookies == "auto"
    assert settings.session_days == 7
    assert settings.auth_path == settings.data_dir / "auth.json"


def test_proxy_settings_parse(monkeypatch):
    monkeypatch.setenv("DLS_ALLOWED_HOSTS", "live.example.com, 10.0.0.5")
    monkeypatch.setenv("DLS_TRUST_PROXY", "1")
    monkeypatch.setenv("DLS_SECURE_COOKIES", "always")
    monkeypatch.setenv("DLS_SESSION_DAYS", "3")
    settings = Settings.from_env()
    assert settings.allowed_hosts == ("live.example.com", "10.0.0.5")
    assert settings.trust_proxy is True
    assert settings.secure_cookies == "always"
    assert settings.session_days == 3


@pytest.mark.parametrize(
    "key,value",
    [
        ("DLS_SECURE_COOKIES", "sometimes"),
        ("DLS_SESSION_DAYS", "0"),
        ("DLS_PORT", "70000"),
    ],
)
def test_invalid_settings_fail_loudly(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        Settings.from_env()
