from __future__ import annotations

import asyncio
import math
import re
import secrets
import time
from dataclasses import dataclass, field
from json import JSONDecodeError
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from pydantic import Field

from shiritaibot.adapters.feishu_transport import DOMAINS, FILE_SCOPE, MESSAGE_EVENT
from shiritaibot.app import Application, FeishuAdapterConflict
from shiritaibot.config import AdapterConfig
from shiritaibot.core import Model
from shiritaibot.core.logs import get_logger

FeishuDomain = Literal["feishu", "lark"]
RegistrationStatus = Literal["waiting", "authorized", "denied", "expired", "error", "applied"]

START_TIMEOUT = 20
MAX_QR_LIFETIME = 600
APPLY_LIFETIME = 300
MAX_SESSIONS = 8

log = get_logger("飞书")
RECOVERY_MESSAGE = (
    "未能获取飞书应用凭据。若已在手机上创建，请在飞书开放平台查看 "
    "App ID 和 App Secret，再手动配置。"
)


async def _register_app(**options):
    # lark-oapi 1.7.5 uses a private flow without a transport hook. Keep its
    # device code, domain switching and timing, and only normalize poll results.
    from lark_oapi.scene.registration import _AsyncFlow

    class RegistrationFlow(_AsyncFlow):
        async def _post(self, data):
            polling = data.get("action") == "poll"
            try:
                result = await super()._post(data)
            except (httpx.RequestError, JSONDecodeError):
                if not polling:
                    raise
                return {"error": "authorization_pending"}
            if polling:
                if not isinstance(result, dict):
                    return {"error": "authorization_pending"}
                if not result.get("error") and not (
                    result.get("client_id") and result.get("client_secret")
                ):
                    return {**result, "error": "authorization_pending"}
            return result

    return await RegistrationFlow(**options).run()


class FeishuQrStart(Model):
    domain: FeishuDomain = "feishu"


class FeishuQrApply(Model):
    adapter_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    enabled: bool = True
    memory_profile: Literal["feishu", "default"] = "feishu"


@dataclass(slots=True)
class Registration:
    id: str
    domain: FeishuDomain
    ready: asyncio.Future[dict | None] = field(repr=False)
    task: asyncio.Task | None = field(default=None, repr=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    status: RegistrationStatus = "waiting"
    qr_url: str = field(default="", repr=False)
    deadline: float = 0
    app_id: str = ""
    app_secret: str = field(default="", repr=False)
    message: str = ""
    applied: tuple[FeishuQrApply, int] | None = None

    def public(self) -> dict:
        result = {
            "id": self.id,
            "domain": self.domain,
            "status": self.status,
            "expires_in": max(0, math.ceil(self.deadline - time.monotonic())),
        }
        if self.status == "waiting" and self.qr_url:
            result["qr_url"] = self.qr_url
        if self.app_id:
            result["app_id"] = self.app_id
        if self.applied is not None:
            result["adapter_id"] = self.applied[0].adapter_id
        if self.message:
            result["message"] = self.message
        return result

    def finish(self, status: RegistrationStatus, message: str = "") -> None:
        self.status, self.message, self.deadline = status, message, 0
        self.qr_url = self.app_secret = ""


class FeishuQrRegistration:
    def __init__(self, runtime: Application):
        self.runtime = runtime
        self.sessions: dict[str, Registration] = {}

    async def start(self, domain: FeishuDomain) -> dict:
        # Keep recent results for status and idempotent apply retries, but never evict active sessions.
        if len(self.sessions) >= MAX_SESSIONS:
            for key, session in list(self.sessions.items()):
                if session.task is not None and session.task.done():
                    self.sessions.pop(key)
                    break
            else:
                raise HTTPException(429, "有过多未结束的扫码会话，请取消已有会话后再试。")
        loop = asyncio.get_running_loop()
        session = Registration(secrets.token_urlsafe(24), domain, loop.create_future())
        self.sessions[session.id] = session
        session.task = asyncio.create_task(self._register(session))
        try:
            result = await asyncio.shield(session.ready)
        except asyncio.CancelledError:
            await self.cancel(session.id)
            raise
        if result is None:
            self.sessions.pop(session.id, None)
            raise HTTPException(502, session.message)
        return result

    async def _register(self, session: Registration) -> None:
        def fail(reason: str) -> None:
            waiting = session.ready.done()
            phase = "等待扫码结果" if waiting else "获取二维码"
            log.warning(f"扫码创建失败（{phase}，{reason}）")
            session.finish(
                "error",
                RECOVERY_MESSAGE if waiting else "未能获取飞书二维码，请检查网络后重试。",
            )

        try:
            from lark_oapi.scene.registration.errors import (
                AppAccessDeniedError,
                AppExpiredError,
                RegisterAppError,
            )

            try:
                async with asyncio.timeout(START_TIMEOUT) as timeout:

                    def on_qr_code(info: dict) -> None:
                        url = info.get("url")
                        expiry = info.get("expire_in")
                        if not isinstance(url, str) or not 0 < len(url) <= 8192:
                            raise ValueError("invalid verification URL")
                        parsed = urlsplit(url)
                        if (
                            parsed.scheme != "https"
                            or parsed.hostname
                            not in {
                                "accounts.feishu.cn",
                                "open.feishu.cn",
                                "accounts.larksuite.com",
                                "open.larksuite.com",
                            }
                            or parsed.username is not None
                            or parsed.port not in {None, 443}
                            or type(expiry) is not int
                            or expiry <= 0
                        ):
                            raise ValueError("invalid verification response")
                        lifetime = min(expiry, MAX_QR_LIFETIME)
                        session.qr_url = url
                        session.deadline = time.monotonic() + lifetime
                        timeout.reschedule(asyncio.get_running_loop().time() + lifetime)
                        session.ready.set_result(session.public())

                    def on_status_change(info: dict) -> None:
                        if info.get("status") == "domain_switched":
                            session.domain = "lark"

                    result = await _register_app(
                        on_qr_code=on_qr_code,
                        on_status_change=on_status_change,
                        source="shiritaibot",
                        domain=DOMAINS[session.domain][1],
                        lark_domain=DOMAINS["lark"][1],
                        app_preset={"name": "ShiritaiBot", "desc": "ShiritaiBot 飞书应用机器人"},
                        addons={
                            "preset": False,
                            "scopes": {
                                "tenant": [
                                    "im:message.p2p_msg:readonly",
                                    "im:message:send_as_bot",
                                    FILE_SCOPE,
                                ]
                            },
                            "events": {"items": {"tenant": [MESSAGE_EVENT]}},
                        },
                        create_only=True,
                    )
            except AppAccessDeniedError:
                session.finish("denied", "手机端已拒绝创建飞书应用。")
                return
            except (AppExpiredError, TimeoutError):
                if session.ready.done():
                    session.finish("expired", "二维码已过期，请重新扫码。")
                else:
                    session.finish("error", "获取二维码超时，请检查网络后重试。")
                return
            except RegisterAppError as exc:
                if exc.code == "invalid_grant":
                    session.finish("expired", "二维码已过期，请重新生成。")
                else:
                    fail("飞书返回注册错误")
                return
            except httpx.RequestError:
                fail("飞书网络请求失败")
                return

            if not isinstance(result, dict):
                fail("注册结果格式异常")
                return
            app_id, secret = result.get("client_id"), result.get("client_secret")
            user_info = result.get("user_info")
            brand = user_info.get("tenant_brand") if isinstance(user_info, dict) else None
            if (
                not session.ready.done()
                or not isinstance(app_id, str)
                or not re.fullmatch(r"cli_[A-Za-z0-9]+", app_id)
                or not isinstance(secret, str)
                or not 0 < len(secret) <= 10000
                or secret.startswith(("env:", "secret:"))
            ):
                fail("应用凭据校验失败")
                return
            if isinstance(brand, str) and brand in {"feishu", "lark"} and brand != session.domain:
                fail("注册平台不匹配")
                return
            session.finish("authorized")
            session.app_id, session.app_secret = app_id, secret
            session.deadline = time.monotonic() + APPLY_LIFETIME
            # Avoid retaining the upstream payload (which may include the scanner's identity).
            del result, user_info, secret
        except Exception:
            # SDK exceptions can embed upstream responses and verification URLs.
            fail("注册响应或校验异常")
        finally:
            if not session.ready.done():
                session.ready.set_result(None)

        if session.status == "authorized":
            await asyncio.sleep(APPLY_LIFETIME)
            async with session.lock:
                if session.status == "authorized":
                    session.finish(
                        "expired", "本地保存窗口已过期。若应用已在飞书创建，请用手动配置。"
                    )

    def _get(self, session_id: str) -> Registration:
        if (session := self.sessions.get(session_id)) is None:
            raise HTTPException(404, "扫码会话已结束，请重新获取二维码。")
        return session

    def status(self, session_id: str) -> dict:
        return self._get(session_id).public()

    async def apply(self, session_id: str, body: FeishuQrApply) -> dict:
        session = self._get(session_id)
        async with session.lock:
            if session.applied is not None:
                previous, version = session.applied
                if previous != body:
                    raise HTTPException(409, "此扫码会话已保存。")
            else:
                if session.status != "authorized" or time.monotonic() >= session.deadline:
                    raise HTTPException(409, "扫码会话尚未完成或已过期，请重新扫码。")
                adapter = AdapterConfig(
                    id=body.adapter_id,
                    type="feishu",
                    enabled=body.enabled,
                    domain=session.domain,
                    app_id=session.app_id,
                    app_secret=session.app_secret,
                    memory_profile=body.memory_profile,
                )
                try:
                    version = await self.runtime.add_feishu_adapter(adapter)
                except FeishuAdapterConflict as exc:
                    raise HTTPException(409, str(exc)) from None
                except Exception:
                    raise HTTPException(
                        500, "应用配置未能完成，请在协议连接页检查实例状态。"
                    ) from None
                session.applied = body, version
                session.finish("applied")
                if session.task is not None:
                    session.task.cancel()
            return {"ok": True, "version": version, "adapter_id": body.adapter_id}

    async def cancel(self, session_id: str) -> None:
        if (session := self.sessions.get(session_id)) is not None:
            async with session.lock:
                self.sessions.pop(session_id, None)
                session.finish("expired", "扫码会话已取消。")
                if not session.ready.done():
                    session.ready.set_result(None)
                if session.task is not None:
                    session.task.cancel()
                    await asyncio.gather(session.task, return_exceptions=True)

    async def close(self) -> None:
        for key in list(self.sessions):
            await self.cancel(key)
