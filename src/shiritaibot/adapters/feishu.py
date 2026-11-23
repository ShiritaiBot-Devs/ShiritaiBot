from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable

from shiritaibot.adapters.feishu_transport import FeishuInbound, FeishuSendError, FeishuTransport
from shiritaibot.agent.commands import builtin_slash
from shiritaibot.api.event import MessageEvent
from shiritaibot.config import AdapterConfig, ConfigService
from shiritaibot.core import ApprovalPort, Context, PublicError, RuntimePort, now
from shiritaibot.core.logs import get_logger
from shiritaibot.i18n import Text, t

# Control words work in either language.
APPROVAL_REPLY = re.compile(r"(同意|拒绝|approve|deny)\s+([a-f0-9]{10})", re.I)  # i18n: ok
STOP_WORDS = {"停", "取消", "stop", "cancel"}  # i18n: ok
ALLOW_WORDS = {"同意", "approve"}  # i18n: ok


class Feishu:
    """Application bot policy and lifecycle. The transport owns the official APIs."""

    def __init__(
        self,
        config: AdapterConfig,
        vault,
        store,
        runtime: RuntimePort,
        permissions: ApprovalPort,
        reply: Callable[[str, str], Awaitable[None]],
        hooks=None,
        *,
        settings: ConfigService,
        active_context: Callable[[Context], bool] | None = None,
    ) -> None:
        self.config, self.vault, self.store = config, vault, store
        self.runtime, self.permissions, self.reply, self.hooks = runtime, permissions, reply, hooks
        self.settings = settings
        self.active_context = active_context
        self.log = get_logger(config.id)
        self.transport: FeishuTransport | None = None
        self.events: asyncio.Queue[FeishuInbound] = asyncio.Queue(maxsize=256)
        self.connection_issue = ""
        self.owner_candidate = ""

    @property
    def status(self) -> str:
        return self.transport.status if self.transport is not None else "disconnected"

    @property
    def self_id(self) -> str:
        return self.transport.bot_open_id if self.transport is not None else ""

    @property
    def diagnostic(self) -> str:
        parts = [self.connection_issue] if self.connection_issue else []
        if self.transport is not None:
            if issue := self.transport.credential_error or self.transport.connection_error:
                parts.append(t(issue))
            if self.config.groups and self.transport.group_message_scope is not True:
                parts.append(
                    t(
                        "adapters.feishu.scopeMissing"
                        if self.transport.group_message_scope is False
                        else "adapters.feishu.scopeUnconfirmed"
                    )
                )
        if not self.config.owner_id:
            parts.append(
                t("adapters.feishu.ownerCandidate", owner_candidate=self.owner_candidate)
                if self.owner_candidate
                else t("adapters.feishu.ownerHint")
            )
        return " ".join(parts)

    async def run(self) -> None:
        try:
            secret = await self.vault.resolve(self.config.app_secret)
        except Exception:
            self.connection_issue = t("adapters.feishu.secretUnreadable")
            return
        if not self.config.app_id or not secret:
            self.connection_issue = t("adapters.feishu.credentialsMissing")
            return
        self.transport = FeishuTransport(
            self.config.app_id, secret, self.config.domain, self.accept
        )
        consumer = asyncio.create_task(self.consume())
        try:
            await self.transport.run()
        finally:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)

    async def accept(self, message: FeishuInbound) -> None:
        """Keep the SDK callback separate from command handling and model work."""
        if (
            message.app_id != self.config.app_id
            or message.sender_type != "user"
            or message.message_type != "text"
            or not re.fullmatch(r"ou_[A-Za-z0-9]+", message.sender_id)
            or message.sender_id == self.self_id
            or not message.message_id
            or not message.text.strip()
        ):
            return
        if message.chat_type == "p2p":
            if not self.config.owner_id:
                self.owner_candidate = message.sender_id
            if message.sender_id != self.config.owner_id:
                return
        elif message.chat_type == "group":
            if message.chat_id not in self.config.groups:
                return
            if not (
                self.config.feishu_group_context
                or self.wake_prefix(message.text)
                or self.group_command_allowed(message)
            ):
                return
        else:
            return
        try:
            self.events.put_nowait(message)
        except asyncio.QueueFull:
            self.log.warning("adapters.feishu.log.queueFull")

    def group_command_allowed(self, message: FeishuInbound) -> bool:
        return message.text.lstrip().startswith("/") and (
            not self.settings.value.group_command_require_wake or self.self_id in message.mentions
        )

    def wake_prefix(self, text: str) -> str:
        return next(
            (
                word
                for word in sorted(self.config.wake_keywords, key=len, reverse=True)
                if text.lstrip().startswith(word)
            ),
            "",
        )

    async def consume(self) -> None:
        while True:
            message = await self.events.get()
            try:
                await self.receive(message)
            except Exception as exc:
                self.log.warning(Text("adapters.feishu.log.messageFailed", name=type(exc).__name__))

    async def receive(self, message: FeishuInbound) -> None:
        cfg = self.config
        group = message.chat_type == "group"
        session = (
            f"group:{cfg.id}:{cfg.feishu_binding}/{message.chat_id}"
            if group
            else cfg.owner_session()
        )
        text = message.text.strip()
        prefix = self.wake_prefix(text) if group else ""
        mentioned_command = text.startswith("/") and self.self_id in message.mentions
        ctx = Context(
            session,
            message.sender_id,
            "chat" if group else "agent",
            not group,
            group,
            memory_profile=self.settings.value.session_memory(session)
            if self.settings
            else ("default" if group else cfg.memory_profile),
        )
        event_id = f"feishu:{cfg.feishu_binding}:{message.message_id}"
        if not await self.store.execute(
            "INSERT OR IGNORE INTO seen_messages VALUES(?,?)", (event_id, now().timestamp())
        ):
            return
        if self.active_context is not None and not self.active_context(ctx):
            return
        if group and cfg.feishu_group_context:
            await self.store.record_group(
                session,
                event_id,
                message.sender_id,
                "",
                text,
                bool(prefix or mentioned_command),
                memory_profile=ctx.memory_profile,
            )
        if prefix:
            text = text[len(prefix) :].lstrip(" ，,：:！!、\t\n") or prefix
        settings = self.settings.value
        if not group:
            rest = settings.strip_wake_prefix(text)
            if rest != text and (
                rest.startswith("/")
                or rest.lower() in STOP_WORDS
                or APPROVAL_REPLY.fullmatch(rest)
                or self.permissions.menu.accepts(session, rest)
            ):
                text = rest
        if not group and (match := APPROVAL_REPLY.fullmatch(text)):
            try:
                await self.permissions.resolve(match[2], match[1].lower() in ALLOW_WORDS, approver=ctx)
                await self.reply(session, t("adapters.feishu.approvalHandled"))
            except PublicError as exc:
                await self.reply(session, str(exc))
            return
        if not group and await self.permissions.menu.reply(session, text, approver=ctx):
            return
        if (
            not group
            and settings.private_require_keyword
            and not settings.matches_wake_keyword(text)
            and not text.startswith("/")
            and text.lower() not in STOP_WORDS
        ):
            return
        if self.active_context is not None and not self.active_context(ctx):
            return
        event = MessageEvent(
            session,
            text,
            message.sender_id,
            platform="feishu",
            adapter=cfg.id,
            group_id=message.chat_id if group else "",
            message_id=message.message_id,
            is_owner=not group,
        )
        if group and not (prefix or mentioned_command):
            if not self.group_command_allowed(message):
                return
            if self.hooks is not None and await self.hooks.dispatch_command(event):
                return
            if builtin_slash(text):
                await self.runtime.submit(ctx, text)
            return
        if self.hooks is not None and await self.hooks.dispatch(event):
            return
        await self.runtime.submit(ctx, text)

    def can_deliver(self, kind: str, target: str) -> bool:
        binding, _, recipient = target.partition("/")
        return (
            binding == self.config.feishu_binding
            and bool(recipient)
            and (
                recipient in self.config.groups
                if kind == "group"
                else kind == "private" and recipient == self.config.owner_id
            )
        )

    async def send_text(self, kind: str, target: str, text: str, request_id: str) -> dict:
        assert self.transport is not None
        recipient = target.partition("/")[2]
        return await self.transport.send_text(
            text,
            chat_id=recipient if kind == "group" else "",
            open_id=recipient if kind == "private" else "",
            request_id=request_id,
        )

    async def send_image(self, kind: str, target: str, data: bytes, request_id: str) -> dict:
        assert self.transport is not None
        return await self.transport.send_image(
            data,
            target.partition("/")[2],
            "chat_id" if kind == "group" else "open_id",
            request_id,
        )

    async def send_owner_file(self, name: str, data: bytes) -> dict:
        transport = self.transport
        if transport is None or not self.config.owner_id:
            raise FeishuSendError("not_connected", uncertain=False)
        return await transport.send_file(name, data, open_id=self.config.owner_id)
