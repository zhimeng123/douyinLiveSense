import asyncio
import json
from types import SimpleNamespace

import pytest

from douyin_live_sense.security import redact
from douyin_live_sense.source import DouyinSource, NeedsAttention, SourceError, normalize_room


@pytest.mark.parametrize(
    "value",
    [
        "123456789",
        " 123456789 ",
        "https://live.douyin.com/123456789/",
        "https://live.douyin.com/?live_web_rid=123456789",
        "https://live.douyin.com/?foo=1&live_web_rid=123456789",
    ],
)
def test_normalize_room(value):
    assert normalize_room(value) == "123456789"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "直播间",
        "１２３",
        "123abc",
        "https://evil.test/123456789",
        "https://live.douyin.com.evil.test/123",
        "https://live.douyin.com@evil.test/123",
        "https://live.douyin.com/",
        "https://live.douyin.com/123/456",
        "file:///123",
        "https://live.douyin.com/?live_web_rid=123&live_web_rid=456",
        "1" * 21,
    ],
)
def test_invalid_room(value):
    with pytest.raises(ValueError):
        normalize_room(value)


class SDK:
    pc_headers = {"user-agent": "test-agent"}

    def __init__(self, data, flv="https://flv.test/live", hls="https://hls.test/live"):
        self.data, self.flv, self.hls = data, flv, hls
        self.calls = []

    async def fetch_web_stream_data(self, url):
        self.calls.append(url)
        if isinstance(self.data, Exception):
            raise self.data
        return self.data

    async def fetch_stream_url(self, data, quality):
        self.calls.append(quality)
        return SimpleNamespace(is_live=True, flv_url=self.flv, m3u8_url=self.hls)


@pytest.mark.parametrize(
    "flv,hls,expected",
    [
        ("https://flv.test/live", "https://hls.test/live", "https://flv.test/live"),
        ("", "https://hls.test/live", "https://hls.test/live"),
    ],
)
async def test_sdk_mapping_and_protocol_choice(flv, hls, expected):
    source = DouyinSource()
    source.sdk = SDK(
        {"status": 2, "anchor_name": "主播", "title": "标题", "id_str": "live-1"}, flv, hls
    )
    room = await source.resolve("123")
    assert room.stream_url == expected
    assert room.anchor_name == "主播" and room.source_live_id == "live-1"
    assert source.sdk.calls == ["https://live.douyin.com/123", "SD"]
    assert "https://" not in repr(room)


async def test_offline_is_explicit_and_does_not_fetch_stream():
    source = DouyinSource()
    source.sdk = SDK({"status": 4, "anchor_name": "主播"})
    assert not (await source.resolve("123")).is_live
    assert len(source.sdk.calls) == 1
    source.sdk = SDK({"status": None})
    with pytest.raises(SourceError, match="未知直播状态"):
        await source.resolve("123")


@pytest.mark.parametrize(
    "error,expected",
    [
        (Exception("it triggered risk control"), NeedsAttention),
        (Exception("需要登录"), NeedsAttention),
        (json.JSONDecodeError("Expecting value", "network failure", 0), SourceError),
        (OSError("Network unreachable"), SourceError),
    ],
)
async def test_sdk_error_classification(error, expected):
    source = DouyinSource()
    source.sdk = SDK(error)
    with pytest.raises(expected) as caught:
        await source.resolve("123")
    if expected is SourceError:
        assert not isinstance(caught.value, NeedsAttention)


async def test_sdk_timeout():
    source = DouyinSource(timeout=0.01)

    class SlowSDK(SDK):
        async def fetch_web_stream_data(self, url):
            await asyncio.sleep(1)

    source.sdk = SlowSDK({})
    with pytest.raises(SourceError, match="TimeoutError"):
        await source.resolve("123")


def test_redact_urls_credentials_and_exception_context():
    cookie = "sessionid=private-secret; ttwid=visitor-secret"
    text = (
        "fetch https://cdn.test/file?sign=abc failed; "
        "sessionid=private-secret; ttwid=visitor-secret"
    )
    sanitized = redact(text, cookie)
    assert "abc" not in sanitized and "private-secret" not in sanitized
    assert "visitor-secret" not in sanitized
    assert "cdn.test" in sanitized


def test_malformed_url_cannot_break_error_reporting():
    assert "PRIVATE_SIGNATURE" not in redact("failed https://[broken?sign=PRIVATE_SIGNATURE")


async def test_sdk_printed_diagnostics_are_redacted(caplog):
    class PrintingSDK(SDK):
        async def fetch_stream_url(self, data, quality):
            print("failed https://cdn.test/live?sign=secret")
            return await super().fetch_stream_url(data, quality)

    source = DouyinSource()
    source.sdk = PrintingSDK({"status": 2})
    await source.resolve("123")
    assert "secret" not in caplog.text and "cdn.test" in caplog.text
