from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable

from shiritaibot.adapters.feishu_transport import FeishuInbound, FeishuTransport
from shiritaibot.api.event import MessageEvent
from shiritaibot.config import AdapterConfig
from shiritaibot.core import ApprovalPort, Context, PublicError, RuntimePort, now
from shiritaibot.core.logs import get_logger


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
    ) -> None:
        self.config, self.vault, self.store = config, vault, store
        self.runtime, self.permissions, self.reply, self.hooks = runtime, permissions, reply, hooks
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
                parts.append(issue)
            if self.config.groups and self.transport.group_message_scope is not True:
                state = "缺少" if self.transport.group_message_scope is False else "尚未确认"
                parts.append(f"{state}群消息权限 im:message.group_msg，未 @ 的关键词唤醒待验证。")
        if not self.config.owner_id:
            parts.append(
                f"最近私聊发送者 open_id 为 {self.owner_candidate}，核对后填入主人 ID。"
                if self.owner_candidate
                else "请向机器人发一条私聊，然后在这里查看发送者 open_id。"
            )
        return " ".join(parts)

    async def run(self) -> None:
        try:
            secret = await self.vault.resolve(self.config.app_secret)
        except Exception:
            self.connection_issue = "无法读取飞书 App Secret，请重新填写。"
            return
        if not self.config.app_id or not secret:
            self.connection_issue = "请填写飞书 App ID 和 App Secret。"
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
            if not self.config.feishu_group_context and not self.wake_prefix(message.text):
                return
        else:
            return
        try:
            self.events.put_nowait(message)
        except asyncio.QueueFull:
            self.log.warning("飞书事件队列已满，未处理一条消息。")

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
                self.log.warning(f"处理飞书消息失败（{type(exc).__name__}）。")

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
        event_id = f"feishu:{cfg.feishu_binding}:{message.message_id}"
        if not await self.store.execute(
            "INSERT OR IGNORE INTO seen_messages VALUES(?,?)", (event_id, now().timestamp())
        ):
            return
        if group and cfg.feishu_group_context:
            await self.store.record_group(
                session, event_id, message.sender_id, "", text, bool(prefix)
            )
        if group and not prefix:
            return
        if prefix:
            text = text[len(prefix) :].lstrip(" ，,：:！!、\t\n") or prefix
        if not group and (match := re.fullmatch(r"(同意|拒绝)\s+([a-f0-9]{10})", text)):
            try:
                await self.permissions.resolve(match[2], match[1] == "同意")
                await self.reply(session, "审批已处理。")
            except PublicError as exc:
                await self.reply(session, str(exc))
            return
        if self.hooks is not None and await self.hooks.dispatch(
            MessageEvent(
                session,
                text,
                message.sender_id,
                platform="feishu",
                adapter=cfg.id,
                group_id=message.chat_id if group else "",
                message_id=message.message_id,
                is_owner=not group,
            )
        ):
            return
        await self.runtime.submit(
            Context(
                session,
                message.sender_id,
                "chat" if group else "agent",
                not group,
                group,
                memory_profile="default" if group else cfg.memory_profile,
            ),
            text,
        )

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
