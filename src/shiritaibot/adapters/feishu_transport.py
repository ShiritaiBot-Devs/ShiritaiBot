"""Feishu application bot transport using the official Channel SDK."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import httpx
import lark_channel
from lark_channel import (
    ChannelConfig,
    FeishuChannel,
    FeishuChannelError,
    InboundConfig,
    LogLevel,
    MediaSource,
    NameCacheConfig,
    OutboundConfig,
    PolicyConfig,
    RetryConfig,
    SecurityConfig,
    TransportConfig,
)
from lark_channel.ws import client as sdk_ws

from shiritaibot.core.logs import get_logger

DOMAINS = {
    "feishu": ("https://open.feishu.cn", "https://accounts.feishu.cn"),
    "lark": ("https://open.larksuite.com", "https://accounts.larksuite.com"),
}
MESSAGE_EVENT = "im.message.receive_v1"
GROUP_SCOPE = "im:message.group_msg"
FILE_SCOPE = "im:resource"
TEXT_CHUNK_LIMIT = 3500
MENTION = re.compile(r"@_user_\d+|@_all")
TOKEN_FAILURES = {
    20002: "应用密钥无效",
    20009: "应用尚未安装到租户",
    20048: "应用不存在",
    20069: "应用尚未启用",
    20130: "应用认证失败",
}

# The SDK logger can contain credential-bearing WS URLs and server error bodies.
# Leave other users of the named logger alone, and report safe diagnostics below.
_SDK_DIRECTORY = Path(lark_channel.__file__ or "").parent.as_posix() + "/"
logging.getLogger("Lark").addFilter(
    lambda record: not record.pathname.replace("\\", "/").startswith(_SDK_DIRECTORY)
)

log = get_logger("飞书")


@dataclass(frozen=True, slots=True)
class FeishuInbound:
    app_id: str
    tenant_key: str
    message_id: str
    chat_id: str
    chat_type: str
    sender_id: str
    sender_type: str
    message_type: str
    text: str
    mentions: tuple[str, ...]
    raw: dict


class FeishuSendError(RuntimeError):
    """Safe to log. An uncertain delivery may already have reached the recipient."""

    def __init__(self, code: int | str, *, uncertain: bool):
        super().__init__(f"飞书消息发送失败（{code}）")
        self.code = code
        self.uncertain = uncertain


def _object(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


async def _close_sdk_loop(loop: asyncio.AbstractEventLoop) -> None:
    # The pinned SDK stops its global WebSocket loop without draining the
    # heartbeat and keepalive tasks. Only drain the loop owned by this adapter.
    if loop.is_running():
        loop.call_soon_threadsafe(loop.stop)
        try:
            async with asyncio.timeout(12):
                # The SDK exposes no signal for its WebSocket loop stopping.
                while loop.is_running():  # noqa: ASYNC110
                    await asyncio.sleep(0.05)
        except TimeoutError:
            log.warning("飞书长连接未能及时停止")
            return

    async def drain() -> None:
        tasks = asyncio.all_tasks() - {asyncio.current_task()}
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    await asyncio.to_thread(loop.run_until_complete, drain())
    loop.close()


def _parse_message(payload: dict, app_id: str, bot_open_id: str) -> FeishuInbound | None:
    header = _object(payload.get("header"))
    if header.get("event_type") != MESSAGE_EVENT or header.get("app_id") != app_id:
        return None
    event = _object(payload.get("event"))
    message = _object(event.get("message"))
    sender = _object(event.get("sender"))
    sender_id = _string(_object(sender.get("sender_id")).get("open_id"))
    message_id = _string(message.get("message_id"))
    chat_id = _string(message.get("chat_id"))
    chat_type = _string(message.get("chat_type"))
    if not (sender_id and message_id and chat_id and chat_type in {"p2p", "group"}):
        return None

    mentions = []
    mention_text: dict[str, str] = {}
    raw_mentions = message.get("mentions")
    if isinstance(raw_mentions, list):
        for value in raw_mentions:
            mention = _object(value)
            open_id = _string(_object(mention.get("id")).get("open_id"))
            if open_id:
                mentions.append(open_id)
            key = _string(mention.get("key"))
            name = _string(mention.get("name"))
            if key and open_id and open_id == bot_open_id:
                mention_text[key] = ""
            elif key and name:
                mention_text[key] = f"@{name}"

    message_type = _string(message.get("message_type"))
    content = ""
    if message_type == "text":
        try:
            content = _string(_object(json.loads(_string(message.get("content")))).get("text"))
        except (ValueError, RecursionError):
            return None
    text = MENTION.sub(lambda match: mention_text.get(match[0], match[0]), content).strip()
    # Event header token fields are unnecessary for the runtime or plugin hooks.
    event_header = {
        key: header[key]
        for key in ("app_id", "tenant_key", "event_id", "event_type", "create_time")
        if key in header
    }
    return FeishuInbound(
        app_id=app_id,
        tenant_key=_string(header.get("tenant_key")),
        message_id=message_id,
        chat_id=chat_id,
        chat_type=chat_type,
        sender_id=sender_id,
        sender_type=_string(sender.get("sender_type")),
        message_type=message_type,
        text=text,
        mentions=tuple(mentions),
        raw={"header": event_header, "event": event},
    )


class FeishuTransport:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        domain: Literal["feishu", "lark"],
        receive: Callable[[FeishuInbound], Coroutine[Any, Any, None]],
    ):
        self.app_id = app_id
        self._app_secret = app_secret
        self._api_domain, self._accounts_domain = DOMAINS[domain]
        self._receive = receive
        self._channel: FeishuChannel | None = None
        self.group_message_scope: bool | None = None
        self.credential_error = ""
        self.connection_error = ""

    @property
    def bot_open_id(self) -> str:
        identity = self._channel.bot_identity if self._channel else None
        return identity.open_id if identity else ""

    @property
    def status(self) -> str:
        if self._channel is None:
            return "disconnected"
        state = self._channel.connection_snapshot()
        return "connected" if state.ready and state.state == "connected" else "connecting"

    async def _probe_permissions(self) -> None:
        # Omit scope to request the list of permissions active on this app.
        # Discard the returned token and keep unknown when this optional API fails.
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                response = await client.post(
                    f"{self._accounts_domain}/oauth/v3/token",
                    auth=(self.app_id, self._app_secret),
                    data={"grant_type": "client_credentials"},
                )
            result = _object(response.json())
        except (httpx.HTTPError, ValueError, RecursionError):
            return
        code = result.get("code")
        if type(code) is not int:
            return
        if code in TOKEN_FAILURES:
            self.credential_error = TOKEN_FAILURES[code]
        if response.is_success and code == 0 and _string(result.get("access_token")):
            scopes = result.get("scope", "")
            if isinstance(scopes, str):
                self.group_message_scope = GROUP_SCOPE in scopes.split()

    async def run(self) -> None:
        app_loop = asyncio.get_running_loop()
        previous_ws_loop = sdk_ws.loop
        if previous_ws_loop.is_running() and previous_ws_loop is not app_loop:
            self.connection_error = "当前进程已有飞书长连接"
            log.warning(self.connection_error)
            return
        channel = FeishuChannel(
            app_id=self.app_id,
            app_secret=self._app_secret,
            domain=self._api_domain,
            log_level=LogLevel.CRITICAL,
            config=ChannelConfig(
                transport=TransportConfig(handshake_timeout_seconds=10, http_timeout_seconds=10)
            ),
            inbound=InboundConfig(
                emit_raw_events=True,
                expand_merge_forward=False,
                fetch_interactive_card=False,
                name_cache=NameCacheConfig(enabled=False),
            ),
            # Admission, mention handling, dedup and batching belong to the Hub.
            policy=PolicyConfig(
                dm_policy="disabled", group_policy="disabled", require_mention=False
            ),
            outbound=OutboundConfig(
                text_chunk_limit=TEXT_CHUNK_LIMIT, retry=RetryConfig(max_attempts=1)
            ),
            security=SecurityConfig(mode="strict", allow_local_insecure_ws=False),
        )

        async def receive_raw(payload: dict) -> None:
            message = _parse_message(payload, self.app_id, self.bot_open_id)
            if message is None:
                return
            # Raw callbacks run on the SDK's background loop. The runtime and
            # store belong to the app loop, so never use them directly here.
            if asyncio.get_running_loop() is app_loop:
                await self._receive(message)
                return
            coro = self._receive(message)
            try:
                pending = asyncio.run_coroutine_threadsafe(coro, app_loop)
            except RuntimeError:
                coro.close()
                return
            await asyncio.wrap_future(pending)

        channel.on("raw", receive_raw)
        self._channel = channel
        self.connection_error = self.credential_error = ""
        self.group_message_scope = None
        permissions = asyncio.create_task(self._probe_permissions())
        # lark-channel-sdk 1.4.0 binds WS tasks to a module-global loop.
        # V1 uses a single Feishu transport and keeps its loop separate from
        # Uvicorn even when the SDK was first imported inside a running loop.
        ws_loop = asyncio.new_event_loop()
        ws_loop.set_exception_handler(lambda _, __: log.warning("飞书长连接内部任务异常"))
        sdk_ws.loop = ws_loop
        try:
            await channel.connect_until_ready(timeout=None)
            log.log("飞书长连接已建立")
            # The SDK keeps the transport alive and handles reconnects in the
            # background. The Hub owns cancellation of this task.
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.connection_error = "飞书长连接未建立，请检查应用配置和网络"
            log.warning(self.connection_error)
            await permissions
        finally:
            self._channel = None
            permissions.cancel()
            await asyncio.gather(permissions, return_exceptions=True)
            try:
                await channel.disconnect()
            except Exception:
                log.warning("飞书长连接关闭异常")
            await _close_sdk_loop(ws_loop)
            if ws_loop.is_closed():
                sdk_ws.loop = previous_ws_loop

    async def send_text(
        self, text: str, *, chat_id: str = "", open_id: str = "", request_id: str = ""
    ) -> dict:
        if bool(chat_id) == bool(open_id) or not text.strip():
            raise ValueError("飞书发送需要一个目标和非空文本")
        if len(text) > TEXT_CHUNK_LIMIT:
            raise FeishuSendError("text_too_long", uncertain=False)
        return await self._send(
            self._connected_channel(),
            chat_id or open_id,
            "chat_id" if chat_id else "open_id",
            {"text": text},
            request_id,
        )

    async def send_file(self, name: str, data: bytes, *, open_id: str) -> dict:
        channel = self._connected_channel()
        try:
            file_key = await channel.upload_media(
                MediaSource(kind="buffer", buffer=data),
                kind="file",
                file_name=name,
                file_type="stream",
            )
        except FeishuChannelError as exc:
            code = exc.context.get("raw_code")
            raise FeishuSendError(
                code if type(code) is int and code != 0 else "upload_failed", uncertain=False
            ) from None
        if self._connected_channel() is not channel:
            raise FeishuSendError("not_connected", uncertain=False)
        file = {"file": {"source": MediaSource(kind="key", key=file_key)}}
        return await self._send(channel, open_id, "open_id", file)

    def _connected_channel(self) -> FeishuChannel:
        channel = self._channel
        if channel is None or self.status != "connected":
            raise FeishuSendError("not_connected", uncertain=False)
        return channel

    @staticmethod
    async def _send(
        channel: FeishuChannel,
        recipient: str,
        receive_id_type: Literal["chat_id", "open_id"],
        message: dict,
        request_id: str = "",
    ) -> dict:
        try:
            result = await channel.send(
                recipient,
                message,
                {"receive_id_type": receive_id_type, "uuid": request_id or uuid4().hex},
            )
        except Exception:
            raise FeishuSendError("unconfirmed", uncertain=True) from None
        message_id = _string(result.message_id)
        if result.success and message_id:
            return {"message_id": message_id}
        error = result.error
        code = error.raw_code if error else None
        if type(code) is int and code != 0:
            uncertain = 500 <= code < 600 or 50000 <= code < 60000
            raise FeishuSendError(code, uncertain=uncertain)
        raise FeishuSendError("unconfirmed", uncertain=True)
