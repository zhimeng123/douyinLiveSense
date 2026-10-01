import asyncio
import contextlib
import io
import logging
import re
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

from streamget import DouyinLiveStream

from .security import redact

logger = logging.getLogger(__name__)


class SourceError(Exception):
    """可重试的直播解析错误。"""

    def __init__(self, message: str, diagnostic: str = ""):
        super().__init__(message)
        self.diagnostic = diagnostic


class NeedsAttention(SourceError):
    """需要用户处理的登录、风控或配置错误。"""


def normalize_room(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"[0-9]{1,20}", value):
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.netloc != "live.douyin.com":
        raise ValueError("请输入数字直播间 ID 或 live.douyin.com 直播链接。")
    query_ids = parse_qs(parsed.query).get("live_web_rid", [])
    if query_ids:
        if len(query_ids) != 1:
            raise ValueError("链接中只能包含一个 live_web_rid。")
        room_id = query_ids[0]
    else:
        room_id = parsed.path.strip("/")
    if not re.fullmatch(r"[0-9]{1,20}", room_id):
        raise ValueError("直播链接缺少有效的数字直播间 ID。")
    return room_id


@dataclass(frozen=True)
class RoomInfo:
    room_id: str
    is_live: bool
    anchor_name: str = ""
    title: str = ""
    source_live_id: str = ""
    stream_url: str = field(default="", repr=False)
    user_agent: str = field(default="", repr=False)


class LiveSource(Protocol):
    async def resolve(self, room_id: str) -> RoomInfo: ...


class DouyinSource:
    def __init__(self, cookie: str = "", timeout: float = 40):
        self.cookie = cookie
        self.timeout = timeout
        self.sdk = DouyinLiveStream(cookies=cookie or None)

    async def resolve(self, room_id: str) -> RoomInfo:
        # StreamGet's HEAD helper prints transport errors. Capture those diagnostics so
        # signed URLs never reach stdout; this application uses logging for all output.
        diagnostics = io.StringIO()
        try:
            with contextlib.redirect_stdout(diagnostics):
                async with asyncio.timeout(self.timeout):
                    data = await self.sdk.fetch_web_stream_data(
                        f"https://live.douyin.com/{room_id}"
                    )
                    status = data.get("status")
                    if status not in {2, 4}:
                        raise SourceError(f"抖音返回未知直播状态：{status!r}，将重新检查。")
                    common = {
                        "room_id": room_id,
                        "anchor_name": data.get("anchor_name") or "",
                        "title": data.get("title") or "",
                        "source_live_id": str(data.get("id_str") or data.get("id") or ""),
                    }
                    if status == 4:
                        return RoomInfo(is_live=False, **common)
                    stream = await self.sdk.fetch_stream_url(data, "SD")
                    url = stream.flv_url or stream.m3u8_url
                    if not stream.is_live or not url:
                        raise SourceError("直播已开播，但未取得可用播放地址。")
                    return RoomInfo(
                        is_live=True,
                        stream_url=url,
                        user_agent=self.sdk.pc_headers["user-agent"],
                        **common,
                    )
        except SourceError:
            raise
        except Exception as error:
            # Translate the SDK boundary into actionable errors without misclassifying
            # malformed responses or network failures as a login requirement.
            message = redact(str(error), self.cookie)
            markers = ("risk control", "风控", "登录", "login required", "captcha", "验证码")
            if any(marker in message.lower() for marker in markers):
                raise NeedsAttention(
                    "抖音要求登录或触发风控。请配置/更新 DOUYIN_COOKIE 后重新启动守卫。"
                    f" 详情：{message}"
                ) from error
            raise SourceError(f"直播解析失败（{type(error).__name__}）：{message}") from error
        finally:
            if diagnostics.getvalue():
                logger.warning("StreamGet: %s", redact(diagnostics.getvalue(), self.cookie))
