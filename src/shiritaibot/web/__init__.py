from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
import re
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field, field_validator
from starlette.background import BackgroundTask
from starlette.middleware.trustedhost import TrustedHostMiddleware

from shiritaibot import __version__ as BOT_VERSION
from shiritaibot.adapters.qq_official import MAX_CALLBACK_BYTES, CallbackError, QQOfficial
from shiritaibot.agent.commands import help_section_titles, visible_builtin_commands
from shiritaibot.api.event import MessageEvent
from shiritaibot.app import Application
from shiritaibot.config import (
    BUILTIN_MEMORY_PROFILES,
    MAIN_SESSION,
    ConfigRevisionConflict,
    ModelConfig,
    ProviderConfig,
    Settings,
    is_feishu_session,
    is_qq_official_session,
)
from shiritaibot.core import (
    Context,
    ExternalEvent,
    Model,
    PublicError,
    canonical,
    logs,
    session_dir,
)
from shiritaibot.core.process import run_process
from shiritaibot.i18n import Text, localize, normalize, t, use_locale
from shiritaibot.llm.subscription import LOGIN_SECONDS
from shiritaibot.llm.subscription import PROTOCOL as SUBSCRIPTION_PROTOCOL
from shiritaibot.meme.core import MAX_MEME_BYTES
from shiritaibot.plugin.core.packages import MAX_ARCHIVE, download_github, validate_id
from shiritaibot.routine import RoutineItem
from shiritaibot.skill import MAX_ARCHIVE as MAX_SKILL_ARCHIVE
from shiritaibot.store.statistics import (
    MAX_STATS_HOURS,
    statistics,
    statistics_sessions,
    subscription_usage,
)
from shiritaibot.tools.builtin import register_builtin
from shiritaibot.updater import Updater
from shiritaibot.web.chat_files import upload_attachment, workspace_for, workspace_suggestions
from shiritaibot.web.exchange_rates import ExchangeRates
from shiritaibot.web.feishu_qr import FeishuQrApply, FeishuQrRegistration, FeishuQrStart


class SecretInput(Model):
    value: str = Field(min_length=1, max_length=10000)


class LogLevelInput(Model):
    level: str = Field(pattern=r"^(debug|info|warning|error)$")


class MessageInput(Model):
    client_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    text: str = Field(max_length=32000)
    session: str = Field(default="web:owner", pattern=r"^web:[a-zA-Z0-9_-]{1,80}$")
    attachments: list[Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]] = Field(
        default_factory=list, max_length=20
    )


WebChatId = Annotated[str, Field(pattern=r"^web:[a-zA-Z0-9_-]{1,80}$")]


class WebChatUpdate(Model):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    pinned: bool | None = None


class WebWorkspaceInput(Model):
    path: str = Field(max_length=4096)
    create: bool = False


class RoutineInput(RoutineItem):
    session: str = "web:owner"

    @field_validator("session")
    @classmethod
    def validate_session(cls, value: str) -> str:
        if not MAIN_SESSION.fullmatch(value):
            raise ValueError(t("web.invalidSession"))
        return value


class ApprovalInput(Model):
    allow: bool


class ModelTestInput(Model):
    provider: ProviderConfig
    model: ModelConfig


class SubscriptionCallback(Model):
    callback: str = Field(min_length=1, max_length=16000)


class MemeInput(Model):
    category: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=300)


class UpdateInput(Model):
    kind: str = Field(pattern=r"^(release|commit)$")
    ref: str = Field(min_length=1, max_length=100)


class PluginRepositoryInput(Model):
    id: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    url: str = Field(max_length=300)


def ensure_token(home: Path) -> str:
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = home / "web.token"
    if not path.exists():
        with path.open("x", encoding="utf-8") as stream:
            os.chmod(path, 0o600)
            stream.write(secrets.token_urlsafe(32))
    return path.read_text(encoding="utf-8").strip()


class LocaleMiddleware:
    """Renders API text in the WebUI's language for the duration of each request.

    The WebUI sends its interface language in ``X-ShiritaiBot-Locale``. Without it, the
    instance's bot language applies; Accept-Language is ignored so the default never
    depends on a client's environment.
    """

    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", ())
        }
        locale = normalize(headers.get("x-shiritaibot-locale")) or self.settings().locale
        with use_locale(locale):
            await self.app(scope, receive, send)


def create_app(
    home: Path,
    token: str | None = None,
    allowed_hosts: list[str] | None = None,
    log_level: str | None = None,
    request_restart: Callable[[], None] | None = None,
    updater: Updater | None = None,
    *,
    desktop: bool = False,
) -> FastAPI:
    runtime = Application(home, desktop=desktop)
    feishu_qr = FeishuQrRegistration(runtime)
    manager = logs.LogManager(home, log_level)
    web_log = logs.get_logger("WebUI")
    exchange_rates = ExchangeRates()
    instance_id = secrets.token_hex(16)
    restarting = False

    def restart_once() -> bool:
        nonlocal restarting
        if restarting or request_restart is None:
            return False
        restarting = True
        request_restart()
        return True

    updater = updater or Updater(
        Path(__file__).resolve().parents[3],
        restart_once if request_restart is not None else None,
        home=home,
    )

    @asynccontextmanager
    async def lifespan(api):
        api.state.token = token or await asyncio.to_thread(ensure_token, home)
        await asyncio.to_thread(manager.install)
        try:
            await runtime.start()
            try:
                yield
            finally:
                await feishu_qr.close()
                await runtime.close()
        finally:
            manager.uninstall()

    api = FastAPI(title="ShiritaiBot", version=BOT_VERSION, lifespan=lifespan)
    api.state.runtime = runtime
    api.add_middleware(LocaleMiddleware, settings=lambda: runtime.config.value)
    api.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver", *(allowed_hosts or [])],
    )

    @api.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    async def auth(request: Request):
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
        if not supplied or not hmac.compare_digest(supplied, api.state.token):
            raise HTTPException(401, t("web.token_required"))

    protected = [Depends(auth)]

    async def source_deployment():
        if desktop:
            raise HTTPException(404)

    source_update = [*protected, Depends(source_deployment)]

    @api.exception_handler(PublicError)
    async def public_error(request, exc):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @api.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's default response includes rejected inputs, possibly credentials.
        detail = "\n".join(
            ".".join(map(str, error["loc"][1:])) + ": " + error["msg"] for error in exc.errors()
        )
        return JSONResponse(status_code=422, content={"detail": detail})

    @api.get("/api/session-locales", dependencies=protected)
    async def session_locales():
        """Sessions whose language was set with /lang (docs/I18N.md)."""
        rows = await runtime.store.list("session_locale")
        return [
            {"session": row["id"], "locale": locale}
            for row in rows
            if (locale := normalize(row.get("locale")))
        ]

    @api.delete("/api/session-locales", dependencies=protected)
    async def clear_session_locale(session: str = Query(min_length=1, max_length=400)):
        await runtime.store.delete("session_locale", session)
        return {"status": "ok"}

    @api.post("/api/providers/discover", dependencies=protected)
    async def discover(body: ProviderConfig):
        return await runtime.llm.discover(body)

    def subscription_provider(provider_id: str) -> ProviderConfig | None:
        return next(
            (
                provider
                for provider in runtime.config.value.providers
                if provider.id == provider_id and provider.protocol == SUBSCRIPTION_PROTOCOL
            ),
            None,
        )

    def saved_subscription(provider_id: str) -> ProviderConfig:
        provider = subscription_provider(provider_id)
        if provider is None:
            raise PublicError("web.subscriptionUnsaved")
        return provider

    @api.get("/api/providers/subscriptions/{provider_id}", dependencies=protected)
    async def subscription_status(provider_id: str):
        if subscription_provider(provider_id) is None:
            return {"saved": False}
        return {
            "saved": True,
            **await runtime.llm.subscriptions.status(provider_id),
            "usage": await subscription_usage(
                runtime.store, provider_id, runtime.config.value.proactive.timezone
            ),
        }

    @api.post("/api/providers/subscriptions/{provider_id}/start", dependencies=protected)
    async def subscription_start(provider_id: str):
        async with runtime.configure_lock:
            url = await runtime.llm.subscriptions.start(saved_subscription(provider_id))
        helper = Path(__file__).with_name("chatgpt_login_helper.py")
        source = await asyncio.to_thread(helper.read_text, encoding="utf-8")
        text = {
            key: t(f"web.chatgptHelper.{key}")
            for key in ("lang", "title", "intro", "label", "copy", "copied", "manual")
        }
        source = re.sub(
            r"^TEXT = .*$",
            lambda _: "TEXT = " + json.dumps(text, ensure_ascii=False),
            source,
            count=1,
            flags=re.M,
        )
        return {
            "script": source + f"\nif __name__ == '__main__':\n    main({url!r})\n",
            "expires_in": LOGIN_SECONDS,
        }

    @api.post("/api/providers/subscriptions/{provider_id}/finish", dependencies=protected)
    async def subscription_finish(provider_id: str, body: SubscriptionCallback):
        async with runtime.configure_lock:
            await runtime.llm.subscriptions.finish(saved_subscription(provider_id), body.callback)
        return {"ok": True}

    @api.delete("/api/providers/subscriptions/{provider_id}", dependencies=protected)
    async def subscription_disconnect(provider_id: str):
        async with runtime.configure_lock:
            revoked = await runtime.llm.subscriptions.disconnect(saved_subscription(provider_id))
        return {"revoked": revoked}

    @api.get("/api/stats", dependencies=protected)
    async def stats(
        hours: int = Query(default=24, ge=1, le=MAX_STATS_HOURS),
        session: str | None = Query(default=None, max_length=400),
    ):
        return await statistics(
            runtime.store,
            hours,
            models=runtime.config.value.models,
            session=session,
            zone=runtime.config.value.proactive.timezone,
        )

    @api.get("/api/stats/sessions", dependencies=protected)
    async def stats_sessions():
        return await statistics_sessions(runtime.store)

    @api.get("/api/exchange-rate", dependencies=protected)
    async def exchange_rate():
        return await exchange_rates.get()

    @api.get("/api/commands", dependencies=protected)
    async def commands(session: WebChatId = "web:owner"):
        ctx = Context(session, "owner", owner=True, memory_profile=runtime.config.value.session_memory(session))
        return [
            {"name": name, "description": description}
            for name, description in visible_builtin_commands(ctx, runtime.registry).items()
        ] + [
            {"name": handler.command[1:], "description": t(handler.description) or handler.plugin}
            for handler in runtime.plugins.hooks.commands_for_context(ctx)
        ]

    @api.get("/api/help-menu/sections", dependencies=protected)
    async def help_menu_sections():
        return [
            {"id": key, "title": title}
            for key, title in help_section_titles(runtime.plugins.hooks.of("command")).items()
        ]

    @api.get("/api/health", dependencies=protected)
    async def health():
        return {
            "status": "ok",
            "version": BOT_VERSION,
            "instance_id": instance_id,
            "restart_available": request_restart is not None,
            "config_version": runtime.config.version,
            "adapters": {k: x.status for k, x in runtime.adapters.instances.items()},
            "adapter_diagnostics": runtime.adapters.diagnostics(),
            "plugins": list(runtime.plugins.loaded),
            "mcp": {k: x.status for k, x in runtime.mcp.servers.items()},
            "tools": sorted(runtime.registry.tools),
            "actors": len(runtime.runtime.actors),
            "subagents": len(runtime.subagents.tasks),
        }

    @api.post("/api/restart", dependencies=protected, status_code=202)
    async def restart():
        if request_restart is None:
            raise HTTPException(503, t("web.restartUnsupported"))
        background = None
        if not restarting:
            web_log.log("web.log.restart")
            background = BackgroundTask(restart_once)
        return JSONResponse(
            {"status": "restarting", "instance_id": instance_id},
            status_code=202,
            background=background,
        )

    @api.get("/api/update/status", dependencies=source_update)
    def update_status():
        return updater.status()

    @api.get("/api/update/releases", dependencies=source_update)
    async def update_releases(
        page: int = Query(1, ge=1, le=1000),
        size: int = Query(8, ge=1, le=50),
        prerelease: bool = False,
        refresh: bool = False,
    ):
        return await updater.releases(page, size, prerelease, refresh)

    @api.get("/api/update/preview", dependencies=source_update)
    async def update_preview(refresh: bool = False):
        return {"commit": await updater.preview(refresh)}

    @api.post("/api/update/inspect", dependencies=source_update)
    async def update_inspect(body: UpdateInput):
        return await updater.inspect(body.kind, body.ref)

    @api.post("/api/update/apply", dependencies=source_update, status_code=202)
    async def update_apply(body: UpdateInput):
        web_log.log(Text("web.log.update", kind=body.kind, ref=body.ref))
        return await updater.apply(body.kind, body.ref)

    def config_etag() -> str:
        return f'"{instance_id}-{runtime.config.version}"'

    @api.get("/api/config", dependencies=protected)
    async def config(response: Response):
        # Keep the application revision independent of proxy ETag transformations.
        response.headers["X-Config-Revision"] = config_etag()
        response.headers["ETag"] = config_etag()
        data = runtime.config.value.model_dump()
        for adapter in data["adapters"]:
            configured = bool(adapter.pop("app_secret"))
            if adapter["type"] in {"feishu", "qq_official"}:
                adapter.update(app_secret="", app_secret_configured=configured)
        return data

    @api.put("/api/config", dependencies=protected)
    async def save_config(body: Settings, request: Request):
        expected_version = None
        # Cloudflare may weaken ETags when compressing JSON. Prefer the opaque
        # application revision; retain If-Match for older WebUI clients.
        revision = request.headers.get("x-config-revision", request.headers.get("if-match"))
        if revision is not None:
            if revision != config_etag():
                raise HTTPException(412, t("web.configChanged"))
            expected_version = runtime.config.version
        try:
            await runtime.reconfigure(body, expected_version=expected_version)
        except ConfigRevisionConflict:
            raise HTTPException(412, t("web.configChanged")) from None
        return {
            "ok": True,
            "version": runtime.config.version,
            "warnings": await runtime.diagnostics(),
        }

    @api.get("/api/diagnostics", dependencies=protected)
    async def diagnostics():
        return {"warnings": await runtime.diagnostics()}

    @api.get("/api/config/schema", dependencies=protected)
    async def config_schema():
        return Settings.model_json_schema()

    @api.post("/api/adapters/qq-official/{adapter_id}/callback")
    async def qq_official_callback(adapter_id: str, request: Request):
        bot = runtime.adapters.instances.get(adapter_id)
        if not isinstance(bot, QQOfficial):
            raise HTTPException(404, t("web.qqOfficialMissing"))
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_CALLBACK_BYTES:
                raise HTTPException(413, t("web.callbackTooLarge"))
        try:
            return await bot.callback(bytes(body), request.headers)
        except CallbackError as exc:
            raise HTTPException(exc.status, t("web.qqCallbackRejected")) from None

    @api.post("/api/adapters/feishu/qr", dependencies=protected)
    async def feishu_qr_start(body: FeishuQrStart):
        return await feishu_qr.start(body.domain)

    @api.get("/api/adapters/feishu/qr/{session_id}", dependencies=protected)
    async def feishu_qr_status(session_id: str):
        return feishu_qr.status(session_id)

    @api.delete("/api/adapters/feishu/qr/{session_id}", dependencies=protected, status_code=204)
    async def feishu_qr_cancel(session_id: str):
        await feishu_qr.cancel(session_id)

    @api.post("/api/adapters/feishu/qr/{session_id}/apply", dependencies=protected)
    async def feishu_qr_apply(session_id: str, body: FeishuQrApply):
        return await feishu_qr.apply(session_id, body)

    @api.get("/api/tools", dependencies=protected)
    async def tools(response: Response, session: str = "web:owner"):
        if not session.startswith(("web:", "private:", "group:")):
            raise HTTPException(422, t("web.mainOnly"))
        row = await runtime.store.get("session", session) or {}
        settings = runtime.config.value
        group = session.startswith("group:")
        feishu = is_feishu_session(session)
        actor = row.get("actor", "")
        memory_profile = settings.session_memory(session)
        official = is_qq_official_session(session)
        active = True
        if feishu:
            adapter = settings.active_feishu_session(session)
            active = adapter is not None
            owner = active and not group
            if adapter is not None:
                if group:
                    actor = actor or "ou_preview"
                else:
                    actor = adapter.owner_id
        elif official:
            adapter = settings.active_qq_official_session(session)
            active = adapter is not None
            owner = adapter is not None and session == adapter.owner_session()
            actor = actor if group else session.rpartition("/")[2]
        else:
            owner = row.get("owner", session.startswith("web:"))
            if session.startswith("private:"):
                owner = owner or any(session == a.owner_session() for a in settings.adapters)
        ctx = Context(session, actor, owner=owner, group=group, memory_profile=memory_profile)
        ctx = runtime.permissions.context(ctx)
        # Keep the application revision independent of proxy ETag transformations.
        response.headers["X-Config-Revision"] = config_etag()
        response.headers["ETag"] = config_etag()
        return {
            "session": session,
            "platform": "feishu"
            if feishu
            else "qq_official"
            if official
            else "web"
            if session.startswith("web:")
            else "qq",
            "active": active,
            "config_version": runtime.config.version,
            "policy": runtime.permissions.session_policy(ctx).model_dump(),
            "source": "session" if session in settings.policy.sessions else "default",
            "tools": [
                {
                    "name": tool.name,
                    "description": t(tool.description),
                    "plugin": tool.plugin,
                    "risk": tool.risk,
                    "safe": tool.safe,
                    "chat": tool.chat,
                    "permission": runtime.permissions.configured(ctx, tool),
                    "decision": runtime.permissions.decision(ctx, tool),
                }
                for tool in sorted(runtime.registry.tools.values(), key=lambda tool: tool.name)
            ],
        }

    @api.get("/api/secrets", dependencies=protected)
    async def secret_names():
        return [r["id"] for r in await runtime.store.list("secret")]

    @api.put("/api/secrets/{name}", dependencies=protected)
    async def secret_set(name: str, body: SecretInput):
        await runtime.vault.set(name, body.value)
        await runtime.refresh_secret_list()
        await register_builtin(runtime)
        return {"ok": True}

    @api.get("/api/records/{kind}", dependencies=protected)
    async def records(
        kind: str,
        page: int | None = Query(default=None, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        approval_filter: str = Query(default="", pattern=r"^(pending|history)?$"),
    ):
        if kind not in {
            "activity",
            "turn",
            "session",
            "approval",
            "routine",
            "event",
            "notification",
            "short_term",
            "proactive_trace",
            "memory_summary",
            "memory_judgment",
            "memory_extract",
            "subagent_run",
            "outbox",
            "reply",
            "usage",
        }:
            raise HTTPException(404)
        if page is not None:
            return await runtime.store.page(kind, page, page_size, approval_filter)
        return await runtime.store.list(kind)

    @api.get("/api/logs/raw", dependencies=protected)
    async def raw_logs(lines: int = 500, q: str = ""):
        lines = max(1, min(lines, 5000))
        return {"lines": await asyncio.to_thread(manager.tail, lines, q[:200])}

    @api.get("/api/logs", dependencies=protected)
    async def structured_logs(limit: int = 500, level: str = "", source: str = "", q: str = ""):
        limit = max(1, min(limit, 5000))
        return await asyncio.to_thread(manager.entries, limit, level[:10], source[:200], q[:200])

    @api.get("/api/logs/level", dependencies=protected)
    async def log_level_get():
        return {"level": manager.level_name}

    @api.put("/api/logs/level", dependencies=protected)
    async def log_level_set(body: LogLevelInput):
        before = manager.level_name
        # Announce at whichever of the two levels still lets a LOG line through.
        if body.level in {"warning", "error"}:
            web_log.log(Text("web.log.level", old=before.upper(), new=body.level.upper()))
        level = await asyncio.to_thread(manager.set_level, body.level)
        if body.level not in {"warning", "error"}:
            web_log.log(Text("web.log.level", old=before.upper(), new=level.upper()))
        return {"level": level}

    @api.get("/api/history", dependencies=protected)
    async def history(
        session: str = "web:owner", profile: str | None = None, timeline: bool = False
    ):
        return await runtime.store.history(
            session,
            memory_profile=profile or runtime.config.value.session_memory(session),
            timeline=timeline,
        )

    @api.get("/api/web-chats", dependencies=protected)
    async def web_chats():
        return await runtime.store.web_chats()

    @api.get("/api/web-chats/{session}/workspace", dependencies=protected)
    async def get_workspace(session: WebChatId):
        path = await workspace_for(runtime.store, runtime.home, session)
        return {"path": str(path), "custom": path != session_dir(runtime.home, "workspaces", session)}

    @api.put("/api/web-chats/{session}/workspace", dependencies=protected)
    async def set_workspace(session: WebChatId, body: WebWorkspaceInput):
        value = body.path.strip()
        path = Path(value)
        if value:
            if not path.is_absolute() or "\x00" in value:
                raise HTTPException(422, t("web.workspaceUnavailable"))
            try:
                if not await asyncio.to_thread(path.is_dir):
                    if await asyncio.to_thread(path.exists):
                        raise HTTPException(422, t("web.workspaceUnavailable"))
                    if not body.create:
                        return {"missing": True, "path": value,
                                "suggestions": await asyncio.to_thread(workspace_suggestions, path)}
            except OSError as exc:
                raise HTTPException(422, t("web.workspaceUnavailable")) from exc
        async with runtime.runtime.submit_locks.setdefault(session, asyncio.Lock()):
            if runtime.runtime.busy(session):
                raise HTTPException(409, t("web.chatBusy"))
            if value:
                try:
                    if body.create:
                        await asyncio.to_thread(path.mkdir, parents=True, exist_ok=True)
                    path = await asyncio.to_thread(path.resolve, strict=True)
                    if not await asyncio.to_thread(path.is_dir):
                        raise HTTPException(422, t("web.workspaceUnavailable"))
                except (OSError, RuntimeError) as exc:
                    raise HTTPException(422, t("web.workspaceUnavailable")) from exc
            await runtime.store.update_web_chat(session, None, None)
            if value:
                await runtime.store.put("web_workspace", session, {"path": str(path)}, session)
            else:
                await runtime.store.delete("web_workspace", session)
        return await get_workspace(session)

    @api.post("/api/web-chats/{session}/attachments", dependencies=protected)
    async def add_chat_attachment(session: WebChatId, request: Request, name: str = Query(max_length=250)):
        workspace = await workspace_for(runtime.store, runtime.home, session)
        document = await upload_attachment(request, workspace, name)
        return await runtime.store.put("web_attachment", document["id"], document, session)

    @api.get("/api/web-chats/{session}/context", dependencies=protected)
    async def web_chat_context(session: WebChatId):
        return await runtime.store.get("web_context", session)

    @api.get("/api/help-images/{key}", dependencies=protected)
    async def help_image(key: str):
        row = await runtime.store.get("help_image", key)
        if row is None:
            raise HTTPException(404)
        return Response(base64.b64decode(row["png"]), media_type="image/png")

    @api.put("/api/web-chats/{session}", dependencies=protected)
    async def update_web_chat(session: WebChatId, body: WebChatUpdate):
        title = body.title.strip() if body.title is not None else None
        if title == "":
            raise HTTPException(422, t("web.chatNameEmpty"))
        await runtime.store.update_web_chat(session, title, body.pinned)
        return {"ok": True}

    @api.delete("/api/web-chats/{session}", dependencies=protected)
    async def delete_web_chat(session: WebChatId):
        async with runtime.runtime.submit_locks.setdefault(session, asyncio.Lock()):
            if runtime.runtime.busy(session):
                raise HTTPException(409, t("web.chatBusy"))
            await runtime.store.delete_web_chat(session)
        return {"ok": True}

    @api.post("/api/messages", dependencies=protected)
    async def message(body: MessageInput):
        if not body.text.strip() and not body.attachments:
            raise HTTPException(422, t("web.chatEmptyMessage"))
        text = body.text
        if body.attachments:
            workspace = await workspace_for(runtime.store, runtime.home, body.session)
            attachments = []
            for key in dict.fromkeys(body.attachments):
                row = await runtime.store.get("web_attachment", key)
                if not row or row["scope"] != body.session or row["workspace"] != str(workspace):
                    raise HTTPException(422, t("web.chatAttachmentUnavailable"))
                attachments.append(str(Path(row["path"]).relative_to(workspace).as_posix()))
            text = "\n\n".join(filter(None, [text.strip(), t("web.chatAttachedFiles") + "\n" + "\n".join(
                f"- {canonical(path)}" for path in attachments
            )]))
        where = "" if body.session == "web:owner" else body.session
        web_log.log(logs.brief(body.text, 200), where, "web.log.owner")
        event = MessageEvent(
            session=body.session,
            text=text.strip(),
            sender_id="owner",
            sender_name=runtime.config.value.owner_name,
            platform="web",
            message_id=body.client_id or "",
            is_owner=True,
        )
        # Plugin replies use the chat's language, not the browser's.
        locale, _ = await runtime.runtime.session_locale(body.session)
        with use_locale(locale):
            if await runtime.plugins.hooks.dispatch(event):
                return {"status": "plugin"}
        return {
            "status": await runtime.runtime.submit(
                Context(
                    body.session,
                    "owner",
                    "agent",
                    True,
                    web_message_ids=(body.client_id,) if body.client_id else (),
                ),
                text,
            )
        }

    @api.post("/api/approvals/{key}", dependencies=protected)
    async def approval(key: str, body: ApprovalInput):
        await runtime.permissions.resolve(key, body.allow)
        return {"ok": True}

    # Read-only: the Agent maintains memory through tools (DESIGN.md §1.1).
    @api.get("/api/memory/profiles", dependencies=protected)
    async def memory_profiles():
        return [
            {
                "id": profile_id,
                **profile.model_dump(),
                # Built-in stores are not saved in config, so their names follow the language.
                **(
                    {"name": t(f"config.memoryProfiles.{profile_id}")}
                    if profile is BUILTIN_MEMORY_PROFILES.get(profile_id)
                    else {}
                ),
            }
            for profile_id in runtime.memories
            if (profile := runtime.config.value.memory.profile(profile_id)) is not None
        ]

    def configured_memory(profile: str):
        if runtime.config.value.memory.profile(profile) is None or profile not in runtime.memories:
            raise HTTPException(404, t("web.memoryProfileMissing"))
        return runtime.memory_for(profile)

    @api.get("/api/memory", dependencies=protected)
    async def memories(
        kind: str = "",
        q: str = "",
        page: int | None = Query(default=None, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        profile: str = "default",
    ):
        if kind not in {"", "entity", "fact", "event", "conflict"}:
            raise HTTPException(422, t("web.memoryKindInvalid"))
        memory = configured_memory(profile)
        if page is not None:
            return await memory.browse_page(kind, q[:200], page, page_size)
        return await memory.browse(kind[:20], q[:200])

    @api.get("/api/memory/status", dependencies=protected)
    async def memory_status(profile: str = "default"):
        memory = configured_memory(profile)
        return await memory.vectors.status(await memory.documents())

    @api.get("/api/memory/{key}", dependencies=protected)
    async def memory_detail(key: str, profile: str = "default"):
        return await configured_memory(profile).get(key)

    @api.get("/api/memes", dependencies=protected)
    async def memes(
        category: str = "",
        q: str = "",
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=24, ge=1, le=100),
    ):
        core = runtime.memes.core
        return {
            **core.listing(category[:32], q[:200].strip(), page, page_size),
            "count": core.count,
            "capacity": runtime.config.value.meme.capacity,
            "counts": core.counts(),
            "index": await core.vectors.status(core.documents()),
        }

    @api.post("/api/memes", dependencies=protected)
    async def meme_upload(
        request: Request,
        category: str = Query(default="", max_length=32),
        description: str = Query(default="", max_length=300),
    ):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_MEME_BYTES:
                raise HTTPException(413, t("web.memeTooLarge"))
        # Uploads skip the persona check and capacity: the owner chose them.
        return await runtime.memes.core.ingest(
            bytes(data), source="manual", category=category, description=description
        )

    @api.get("/api/memes/{key}/image", dependencies=protected)
    async def meme_image(key: str):
        core = runtime.memes.core
        row = core.get(key) or core.resolve(key)
        data = await core.image(row["id"]) if row else None
        if row is None or data is None:
            raise HTTPException(404, t("web.memeMissing"))
        return Response(data, media_type=row.get("mime") or "image/png")

    @api.put("/api/memes/{key}", dependencies=protected)
    async def meme_update(key: str, body: MemeInput):
        return await runtime.memes.core.update(key, body.category, body.description)

    @api.post("/api/memes/{key}/describe", dependencies=protected)
    async def meme_describe(key: str):
        core = runtime.memes.core
        data = await core.image(key)
        if data is None:
            raise HTTPException(404, t("web.memeMissing"))
        description = await core.describe(data, runtime.config.value)
        return await core.update(key, None, description)

    @api.delete("/api/memes/{key}", dependencies=protected)
    async def meme_delete(key: str):
        await runtime.memes.core.delete(key)
        return {"ok": True}

    @api.get("/api/knowledge", dependencies=protected)
    async def knowledge(
        q: str = "",
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        library: str = "default",
    ):
        kb = runtime.knowledge_for(library)
        return {**await kb.listing(q[:200].strip(), page, page_size), **await kb.stats()}

    @api.get("/api/knowledge/libraries", dependencies=protected)
    async def knowledge_libraries():
        return [
            {"id": "default", "name": t("web.defaultLibrary")},
            *(
                {"id": library, "name": config.name}
                for library, config in runtime.config.value.knowledge.libraries.items()
            ),
        ]

    @api.post("/api/knowledge", dependencies=protected)
    async def knowledge_upload(
        request: Request,
        filename: str = Query(min_length=1, max_length=255),
        title: str = Query(default="", max_length=200),
        library: str = "default",
    ):
        limit = runtime.config.value.knowledge.max_file_mb
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > limit * 1024 * 1024:
                raise HTTPException(413, t("web.fileTooLarge", limit=limit))
        return await runtime.knowledge_for(library).add(
            bytes(data), filename, title=title, source="web"
        )

    @api.get("/api/knowledge/search", dependencies=protected)
    async def knowledge_search(
        q: str = Query(min_length=1, max_length=500),
        limit: int = Query(default=10, ge=1, le=20),
        library: str = "default",
    ):
        return await runtime.knowledge_for(library).search(q, limit)

    @api.get("/api/knowledge/{key}", dependencies=protected)
    async def knowledge_detail(
        key: str,
        start: int = Query(default=0, ge=0),
        count: int = Query(default=20, ge=1, le=100),
        library: str = "default",
    ):
        kb = runtime.knowledge_for(library)
        document = await kb.get(key)
        if document is None:
            raise HTTPException(404, t("web.documentMissing"))
        return {**document, "items": await kb.chunks(key, start, count)}

    @api.get("/api/knowledge/{key}/file", dependencies=protected)
    async def knowledge_file(key: str, library: str = "default"):
        found = await runtime.knowledge_for(library).file(key)
        if found is None:
            raise HTTPException(404, t("web.documentMissing"))
        document, path = found
        if not await asyncio.to_thread(path.is_file):
            raise HTTPException(404, t("web.originalMissing"))
        # Attachment and a neutral type: uploaded content never renders on this origin.
        return FileResponse(
            path, media_type="application/octet-stream", filename=document["filename"]
        )

    @api.delete("/api/knowledge/{key}", dependencies=protected)
    async def knowledge_delete(key: str, library: str = "default"):
        await runtime.knowledge_for(library).delete(key)
        return {"ok": True}

    @api.get("/api/routines", dependencies=protected)
    async def routines():
        return await runtime.store.list("routine", limit=-1)

    @api.post("/api/routines", dependencies=protected)
    async def routine(body: RoutineInput):
        item = RoutineItem.model_validate(body.model_dump(exclude={"session"}))
        return await runtime.routine.create(item, session=body.session)

    @api.post("/api/routines/{key}/{status}", dependencies=protected)
    async def routine_status(key: str, status: str):
        return await runtime.routine.update_status(key, status)

    @api.post("/api/events", dependencies=protected)
    async def event(body: ExternalEvent):
        return {"id": await runtime.proactive.emit(body)}

    @api.post("/api/events/batch", dependencies=protected)
    async def event_batch(body: list[ExternalEvent]):
        return {"ids": await runtime.proactive.emit_many(body)}

    @api.get("/api/events/{key}", dependencies=protected)
    async def event_status(key: str):
        row = await runtime.proactive.get_event(key)
        if row is None:
            raise HTTPException(404, t("web.eventMissing"))
        return row

    @api.get("/api/plugins", dependencies=protected)
    async def plugins():
        return localize(await runtime.plugins.listing())

    @api.post("/api/plugins/reload", dependencies=protected)
    async def reload_plugins():
        async with runtime.configure_lock:
            return await runtime.plugins.reload_all()

    @api.post("/api/plugins/install/github", dependencies=protected)
    async def install_github_plugin(body: PluginRepositoryInput):
        validate_id(body.id)
        archive = await download_github(body.url)
        async with runtime.configure_lock:
            return await runtime.plugins.install(body.id, archive)

    @api.post("/api/plugins/install/zip/{key}", dependencies=protected)
    async def install_zip_plugin(key: str, request: Request):
        validate_id(key)
        archive = bytearray()
        async for chunk in request.stream():
            archive.extend(chunk)
            if len(archive) > MAX_ARCHIVE:
                raise HTTPException(413, t("web.pluginZipTooLarge"))
        async with runtime.configure_lock:
            return await runtime.plugins.install(key, bytes(archive))

    @api.delete("/api/plugins/{key}", dependencies=protected)
    async def remove_plugin(key: str):
        validate_id(key)
        async with runtime.configure_lock:
            return await runtime.plugins.remove(key)

    @api.post("/api/plugins/{key}/reload", dependencies=protected)
    async def reload(key: str):
        async with runtime.configure_lock, runtime.plugins.manage_lock:
            await runtime.plugins.sync_config()
            try:
                await runtime.plugins.reload(key)
            except Exception:
                raise PublicError(runtime.plugins.errors.get(key, t("web.pluginLoadFailed"))) from None
            return localize(await runtime.plugins.listing())

    @api.get("/api/skills", dependencies=protected)
    async def skills():
        return await runtime.skills.listing()

    @api.get("/api/skills/{name}", dependencies=protected)
    async def skill_detail(name: str):
        return await runtime.skills.detail(name)

    @api.post("/api/skills/install", dependencies=protected)
    async def install_skill(request: Request, replace: bool = False):
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > MAX_SKILL_ARCHIVE:
                raise HTTPException(413, t("web.skillTooLarge"))
        await runtime.skills.install_bytes(bytes(data), replace=replace)
        return await runtime.skills.listing()

    @api.delete("/api/skills/{name}", dependencies=protected)
    async def remove_skill(name: str):
        await runtime.skills.remove(name)
        return await runtime.skills.listing()

    @api.get("/api/mcp", dependencies=protected)
    async def mcp_servers():
        return runtime.mcp.listing()

    @api.post("/api/mcp/{key}/reconnect", dependencies=protected)
    async def reconnect_mcp(key: str):
        async with runtime.configure_lock:
            return await runtime.mcp.reconnect(key)

    @api.post("/api/subagents/{key}/cancel", dependencies=protected)
    async def cancel(key: str):
        await runtime.subagents.cancel(key)
        return {"ok": True}

    @api.post("/api/subagents/{key}/detect", dependencies=protected)
    async def detect(key: str):
        cfg = next((x for x in runtime.config.value.subagents if x.id == key), None)
        if not cfg:
            raise HTTPException(404)
        if cfg.kind == "shiritaibot":
            raise HTTPException(400, t("web.noCliCheck"))
        return await run_process(
            [*cfg.command, "--version"], home, timeout=10, env=runtime.subagents.child_env(cfg)
        )

    async def run_model_test(provider: ProviderConfig, model: ModelConfig):
        if model.provider != provider.id:
            raise PublicError("web.providerMismatch")
        try:
            cfg = Settings(
                providers=[provider.model_copy(update={"enabled": True})],
                models=[model.model_copy(update={"enabled": True})],
                routes={"test": [model.id]},
            )
        except ValueError:
            raise PublicError(
                "web.providerUrl"
            ) from None
        if model.kind == "embedding":
            _, vectors = await runtime.llm.embed(["Connection test"], settings=cfg, route="test")
            return {"text": t("web.dimensions", count=len(vectors[0])), "usage": {}}
        if provider.protocol == "systemone":
            answers = await runtime.llm.decide(
                "test",
                "Connection test",
                {"ok": {"type": "noul", "instructions": "Is this a connection test?"}},
                settings=cfg,
            )
            if answers is None:
                raise PublicError("web.decisionFailed")
            return {"text": canonical(answers)[:1000], "usage": {}}
        reply = await runtime.llm.complete(
            "test",
            "Reply with OK.",
            [{"role": "user", "content": "Connection test"}],
            [],
            settings=cfg,
        )
        return {"text": reply.text[:1000], "usage": reply.usage}

    @api.post("/api/models/test", dependencies=protected)
    async def test_draft_model(body: ModelTestInput):
        return await run_model_test(body.provider, body.model)

    @api.post("/api/models/{key}/test", dependencies=protected)
    async def test_model(key: str):
        cfg = runtime.config.value
        model = next((m for m in cfg.models if m.id == key), None)
        if model is None:
            raise HTTPException(
                404, t("web.modelNotSaved")
            )
        provider = next((p for p in cfg.providers if p.id == model.provider), None)
        if provider is None:
            raise PublicError("web.providerMissing")
        return await run_model_test(provider, model)

    @api.websocket("/api/ws")
    async def websocket(ws: WebSocket):
        await ws.accept()
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        async def send_events():
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20)
                except TimeoutError:
                    event = {"kind": "heartbeat"}
                await ws.send_json(event)

        async def wait_for_disconnect():
            while (await ws.receive())["type"] != "websocket.disconnect":
                continue

        try:
            async with asyncio.timeout(5):
                supplied = await ws.receive_text()
            if not hmac.compare_digest(supplied, api.state.token):
                await ws.close(1008)
                return
            runtime.bus.subscribers.add(queue)
            tasks = (asyncio.create_task(send_events()), asyncio.create_task(wait_for_disconnect()))
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except (WebSocketDisconnect, TimeoutError, RuntimeError):
            pass
        finally:
            runtime.bus.subscribers.discard(queue)

    dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    if dist.is_dir():
        # Unauthenticated visits render the sign-in card, so these are the same page.
        @api.get("/token", include_in_schema=False)
        async def login():
            return FileResponse(dist / "index.html")

        @api.get("/login", include_in_schema=False)
        async def sign_in_page():
            return FileResponse(dist / "index.html")

        api.mount("/", StaticFiles(directory=dist, html=True), name="webui")
    return api
