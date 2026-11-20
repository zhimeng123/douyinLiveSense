import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

COOKIE_NAME = "dls_session"
SESSION_COOKIE_PATH = "/"
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789"

_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


def generate_password(length: int = 20) -> str:
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(length))


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = encoded.split("$")
        if scheme != "scrypt":
            return False
        expected = _b64decode(digest)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_b64decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _write_secret_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


class AuthStore:
    """Single-admin credential file: scrypt hash plus session signing secret."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.is_file()

    def _read(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _create(self, password: str) -> str:
        payload = {
            "version": 1,
            "username": "admin",
            "password_hash": hash_password(password),
            "session_secret": _b64encode(os.urandom(32)),
            "created_at": int(time.time()),
        }
        _write_secret_file(self.path, payload)
        return password

    def create(self, password: str | None = None) -> str:
        if self.exists():
            raise FileExistsError(f"{self.path} 已存在。")
        return self._create(password or generate_password())

    def reset(self, password: str | None = None) -> str:
        return self._create(password or generate_password())

    def set_password(self, password: str) -> None:
        payload = self._read()
        payload["password_hash"] = hash_password(password)
        _write_secret_file(self.path, payload)

    def verify(self, password: str) -> bool:
        if not self.exists():
            return False
        return verify_password(password, self._read()["password_hash"])

    @property
    def secret(self) -> bytes:
        return _b64decode(self._read()["session_secret"])

    @property
    def username(self) -> str:
        return self._read().get("username", "admin")


def sign_session(secret: bytes, username: str, expires_at: int) -> str:
    payload = _b64encode(
        json.dumps({"u": username, "exp": int(expires_at)}, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest()
    return f"{payload}.{_b64encode(signature)}"


def verify_session(secret: bytes, token: str, now: int | None = None) -> dict | None:
    try:
        payload, signature = token.rsplit(".", 1)
        expected = hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64decode(signature), expected):
            return None
        data = json.loads(_b64decode(payload))
        username = data.get("u")
        expires_at = data.get("exp")
        if not isinstance(username, str) or not isinstance(expires_at, int):
            return None
    except (ValueError, TypeError):
        return None
    current = int(time.time()) if now is None else int(now)
    if expires_at <= current:
        return None
    return {"u": username, "exp": expires_at}


class LoginLimiter:
    """In-memory per-IP failure counter with a fixed lock window."""

    def __init__(self, max_failures: int = 5, lock_seconds: int = 300):
        self.max_failures = max_failures
        self.lock_seconds = lock_seconds
        self._failures: dict[str, int] = {}
        self._locked_until: dict[str, float] = {}

    def retry_after(self, ip: str, now: float | None = None) -> int:
        current = time.monotonic() if now is None else now
        until = self._locked_until.get(ip, 0.0)
        if until <= current:
            return 0
        return int(until - current) + 1

    def fail(self, ip: str, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        if self.retry_after(ip, now=current):
            return
        count = self._failures.get(ip, 0) + 1
        self._failures[ip] = count
        if count >= self.max_failures:
            self._locked_until[ip] = current + self.lock_seconds
            self._failures[ip] = 0

    def reset(self, ip: str) -> None:
        self._failures.pop(ip, None)
        self._locked_until.pop(ip, None)
