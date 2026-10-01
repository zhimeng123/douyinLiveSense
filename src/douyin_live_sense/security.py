import logging
import re
from urllib.parse import urlsplit


def redact(value: str, cookie: str = "") -> str:
    def hide_url(match: re.Match) -> str:
        try:
            hostname = urlsplit(match.group()).hostname
        except ValueError:
            hostname = None
        return f"[URL:{hostname or '已隐藏'}]"

    if cookie:
        value = value.replace(cookie, "[Cookie 已隐藏]")
        for part in cookie.split(";"):
            _, separator, secret = part.strip().partition("=")
            if separator and len(secret) >= 4:
                value = value.replace(secret, "[已隐藏]")
    value = re.sub(
        r"https?://[^\s\"'<>]+",
        hide_url,
        value,
    )
    return re.sub(
        r"(?i)\b(cookie|authorization|sessionid|ttwid|a_bogus|mstoken|token|sign)\s*[:=]\s*[^\n]+",
        r"\1=[已隐藏]",
        value,
    )


class RedactingFormatter(logging.Formatter):
    def __init__(self, cookie: str = ""):
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")
        self.cookie = cookie

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record), self.cookie)
