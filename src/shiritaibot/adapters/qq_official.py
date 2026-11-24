from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from shiritaibot.core import PublicError, now
from shiritaibot.i18n import Text, t

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
MAX_CALLBACK_BYTES = 1024 * 1024
OPEN_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
GROUP_EVENTS = {"GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"}


class CallbackError(Exception):
    def __init__(self, status: int):
        self.status = status


class QQOfficialSendError(PublicError):
    def __init__(self, detail: str, *, uncertain: bool = False):
        super().__init__(detail)
        self.uncertain = uncertain


def callback_payload(secret: str, body: bytes, signature: str, timestamp: str) -> tuple[dict, bool]:
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        raise CallbackError(400) from None
    if not isinstance(payload, dict) or not isinstance(payload.get("d"), dict):
        raise CallbackError(400)
    seed = secret.encode()
    key = Ed25519PrivateKey.from_private_bytes((seed * 32)[:32])
    if payload.get("op") == 13:
        data = payload["d"]
        token, stamp = data.get("plain_token"), data.get("event_ts")
        # The signing challenge is unsigned. Accept only the platform's token
        # alphabet, so this endpoint cannot sign a JSON event for another caller.
        if not (
            isinstance(token, str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1,256}", token)
            and isinstance(stamp, str)
            and re.fullmatch(r"[0-9]{1,16}", stamp)
        ):
            raise CallbackError(400)
        return {"plain_token": token, "signature": key.sign((stamp + token).encode()).hex()}, True
    try:
        if not re.fullmatch(r"[0-9]{1,16}", timestamp) or len(signature) != 128:
            raise ValueError
        key.public_key().verify(bytes.fromhex(signature), timestamp.encode() + body)
    except (InvalidSignature, ValueError):
        raise CallbackError(401) from None
    return payload, False


@dataclass(frozen=True)
class QQMessage:
    message_id: str
    user: str
    target: str
    group: bool
    mentioned: bool
    text: str
    at: float
    raw: dict

    @property
    def reply_limit(self) -> tuple[int, int]:
        return (300, 5) if self.group else (3600, 4)

    @classmethod
    def parse(cls, payload: dict, received: float) -> QQMessage | None:
        kind = payload.get("t")
        if (
            payload.get("op") != 0
            or not isinstance(kind, str)
            or kind not in {*GROUP_EVENTS, "C2C_MESSAGE_CREATE"}
        ):
            return None
        data = payload["d"]
        author = data.get("author")
        message_id = data.get("id")
        if (
            not isinstance(author, dict)
            or author.get("bot") is True
            or not isinstance(message_id, str)
            or not 0 < len(message_id) <= 512
        ):
            return None
        group = kind in GROUP_EVENTS
        user = author.get("member_openid" if group else "user_openid")
        target = data.get("group_openid") if group else user
        if not (
            isinstance(user, str)
            and OPEN_ID.fullmatch(user)
            and isinstance(target, str)
            and OPEN_ID.fullmatch(target)
        ):
            return None
        content = data.get("content", "")
        text = content.strip() if isinstance(content, str) else ""
        if kind == "GROUP_AT_MESSAGE_CREATE":
            text = re.sub(r"^(?:<@!?[A-Za-z0-9_-]+>\s*)+", "", text)
        if data.get("attachments"):
            text = (text + " " + t("markers.attachment")).strip()
        try:
            stamp = datetime.fromisoformat(data.get("timestamp", ""))
            at = min(received, stamp.timestamp()) if stamp.tzinfo is not None else received
        except (TypeError, ValueError, OverflowError, OSError):
            at = received
        return cls(
            message_id, user, target, group, kind == "GROUP_AT_MESSAGE_CREATE", text, at, data
        )


class QQOfficial:
    def __init__(self, config, vault, sink, logger):
        self.config, self.vault, self.sink, self.log = config, vault, sink, logger
        self.self_id = config.app_id
        self.status = "connecting"
        self.diagnostic = ""
        self.client: httpx.AsyncClient | None = None
        self.events: asyncio.Queue[QQMessage] = asyncio.Queue(maxsize=256)
        self._auth_lock = asyncio.Lock()
        self._access_token = ""
        self._renew_at = 0.0

    async def callback(self, body: bytes, headers) -> dict:
        secret = await self.vault.resolve(self.config.app_secret)
        if not secret:
            raise CallbackError(503)
        app_id = headers.get("x-bot-appid")
        if app_id and app_id != self.config.app_id:
            raise CallbackError(403)
        data, challenge = callback_payload(
            secret,
            body,
            headers.get("x-signature-ed25519", ""),
            headers.get("x-signature-timestamp", ""),
        )
        if challenge:
            if app_id != self.config.app_id:
                raise CallbackError(403)
            return data
        message = QQMessage.parse(data, now().timestamp())
        if message:
            try:
                self.events.put_nowait(message)
            except asyncio.QueueFull:
                raise CallbackError(503) from None
        return {"op": 12}

    async def access_token(self) -> str:
        async with self._auth_lock:
            if self._access_token and self._renew_at > monotonic():
                return self._access_token
            secret = await self.vault.resolve(self.config.app_secret)
            if not self.client or not secret:
                raise PublicError("adapters.qqOfficial.notReady")
            response = await self.client.post(
                TOKEN_URL, json={"appId": self.config.app_id, "clientSecret": secret}
            )
            try:
                data = response.json()
                token = data["access_token"]
                lifetime = int(data["expires_in"])
                if response.is_error or not isinstance(token, str) or not token or lifetime <= 0:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise PublicError(
                    "adapters.qqOfficial.tokenFailed", status_code=response.status_code
                ) from None
            self._access_token = token
            self._renew_at = monotonic() + max(1, lifetime - 60)
            return token

    async def credentials(self) -> None:
        while True:
            try:
                await self.access_token()
            except (PublicError, httpx.HTTPError) as exc:
                message = str(exc) if isinstance(exc, PublicError) else type(exc).__name__
                if self.diagnostic != message:
                    self.log.warning(message)
                self.diagnostic, self.status = message, "error"
                await asyncio.sleep(30)
            else:
                if self.status != "connected":
                    self.log.log("adapters.qqOfficial.log.ready")
                self.diagnostic, self.status = "", "connected"
                await asyncio.sleep(max(1, self._renew_at - monotonic()))

    async def run(self) -> None:
        async with httpx.AsyncClient(timeout=15) as client:
            self.client = client
            try:
                async with asyncio.TaskGroup() as group:
                    group.create_task(self.credentials())
                    while True:
                        message = await self.events.get()
                        try:
                            await self.sink(self, message)
                        except Exception as exc:
                            self.log.error(
                                Text("adapters.qqOfficial.log.messageFailed", name=type(exc).__name__), exc=exc
                            )
            finally:
                self.client, self.status = None, "disconnected"

    async def send_text(
        self, kind: str, target: str, text: str, message_id: str, sequence: int
    ) -> dict[str, Any]:
        return await self._send(kind, target, {"content": text, "msg_type": 0}, message_id, sequence)

    async def send_image(
        self, kind: str, target: str, image: str, message_id: str, sequence: int
    ) -> dict[str, Any]:
        return await self._send(kind, target, {"msg_type": 7}, message_id, sequence, image=image)

    async def _send(
        self, kind: str, target: str, payload: dict, message_id: str, sequence: int, *, image: str = ""
    ) -> dict[str, Any]:
        token = await self.access_token()
        domain = "sandbox.api.sgroup.qq.com" if self.config.qq_sandbox else "api.bot.qq.com"
        segment = "groups" if kind == "group" else "users"
        url = f"https://{domain}/v2/{segment}/{target}"
        if image:
            upload = await self._post(
                f"{url}/files", token,
                {"file_type": 1, "file_data": image, "srv_send_msg": False},
                "file_info", sends_message=False,
            )
            payload["media"] = {"file_info": upload["file_info"]}
        response = await self._post(
            f"{url}/messages", token, {**payload, "msg_id": message_id, "msg_seq": sequence},
            "id", sends_message=True,
        )
        return {"message_id": response["id"]}

    async def _post(
        self, url: str, token: str, payload: dict, expected: str, *, sends_message: bool
    ) -> dict:
        assert self.client is not None
        try:
            response = await self.client.post(
                url,
                headers={"Authorization": f"QQBot {token}"},
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise QQOfficialSendError(type(exc).__name__, uncertain=sends_message) from None
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if response.is_success and data.get(expected):
            return data
        code = data.get("code")
        detail = f"HTTP {response.status_code}"
        if type(code) is int:
            detail += t("adapters.qqOfficial.errorCode", code=code)
        if response.status_code == 401:
            async with self._auth_lock:
                if self._access_token == token:
                    self._renew_at = 0
        raise QQOfficialSendError(
            detail, uncertain=sends_message and (response.is_success or response.status_code >= 500)
        )
