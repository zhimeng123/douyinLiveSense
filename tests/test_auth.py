import stat

import pytest

from douyin_live_sense.auth import (
    AuthStore,
    LoginLimiter,
    generate_password,
    sign_session,
    verify_session,
)


def test_create_stores_only_hash_with_0600(tmp_path):
    store = AuthStore(tmp_path / "auth.json")
    password = store.create("correct-horse-1234")
    assert password == "correct-horse-1234"
    assert store.verify("correct-horse-1234") is True
    assert store.verify("wrong") is False
    raw = (tmp_path / "auth.json").read_text()
    assert "correct-horse-1234" not in raw
    assert stat.S_IMODE((tmp_path / "auth.json").stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        store.create("another-password")


def test_generated_password_is_twenty_chars():
    password = generate_password()
    assert len(password) == 20
    assert password.isalnum()


def test_session_round_trip_and_expiry(tmp_path):
    store = AuthStore(tmp_path / "auth.json")
    store.create("correct-horse-1234")
    token = sign_session(store.secret, "admin", expires_at=2_000_000_000)
    assert verify_session(store.secret, token, now=1_900_000_000)["u"] == "admin"
    assert verify_session(store.secret, token, now=2_000_000_001) is None
    assert verify_session(store.secret, token + "x", now=1_900_000_000) is None


def test_reset_rotates_password_and_invalidates_sessions(tmp_path):
    store = AuthStore(tmp_path / "auth.json")
    store.create("correct-horse-1234")
    old = sign_session(store.secret, "admin", expires_at=2_000_000_000)
    new_password = store.reset("new-password-5678")
    assert new_password == "new-password-5678"
    assert store.verify("new-password-5678") is True
    assert verify_session(store.secret, old, now=1_900_000_000) is None


def test_set_password_keeps_existing_sessions(tmp_path):
    store = AuthStore(tmp_path / "auth.json")
    store.create("correct-horse-1234")
    token = sign_session(store.secret, "admin", expires_at=2_000_000_000)
    store.set_password("new-password-5678")
    assert store.verify("new-password-5678") is True
    assert store.verify("correct-horse-1234") is False
    assert verify_session(store.secret, token, now=1_900_000_000)["u"] == "admin"


def test_login_limiter_locks_after_failures():
    limiter = LoginLimiter(max_failures=2, lock_seconds=60)
    assert limiter.retry_after("1.2.3.4", now=100) == 0
    limiter.fail("1.2.3.4", now=100)
    assert limiter.retry_after("1.2.3.4", now=101) == 0
    limiter.fail("1.2.3.4", now=102)
    assert limiter.retry_after("1.2.3.4", now=103) == 60
    assert limiter.retry_after("1.2.3.4", now=163) == 0
    limiter.reset("1.2.3.4")
    assert limiter.retry_after("1.2.3.4", now=104) == 0
