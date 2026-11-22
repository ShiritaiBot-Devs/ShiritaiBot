from __future__ import annotations

import asyncio
import base64
import html
import json
import random
import re
import time
from collections import deque
from time import monotonic
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from shiritaibot.adapters.feishu import Feishu
from shiritaibot.adapters.feishu_transport import FeishuSendError
from shiritaibot.adapters.qq_official import QQMessage, QQOfficial, QQOfficialSendError
from shiritaibot.agent.commands import builtin_slash
from shiritaibot.api.event import MessageEvent
from shiritaibot.config import is_feishu_session, is_qq_official_session
from shiritaibot.core import (
    Actor,
    ApprovalPort,
    Context,
    ExternalEvent,
    PublicError,
    ReplyAnchor,
    RuntimePort,
    canonical,
    current_reply,
    fingerprint,
    now,
    uid,
)
from shiritaibot.core.logs import brief, get_logger
from shiritaibot.core.text import clean_outbound, clip_image_text
from shiritaibot.meme.inject import MemeInjector

if TYPE_CHECKING:
    from shiritaibot.heartflow import Heartflow, Observation


def segments(value) -> list[dict]:
    if isinstance(value, list):
        return value
    result, end = [], 0
    for match in re.finditer(r"\[CQ:([a-z_]+)((?:,[^\]]*)?)\]", str(value)):
        if match.start() > end:
            result.append(
                {"type": "text", "data": {"text": html.unescape(value[end : match.start()])}}
            )
        data = dict(part.split("=", 1) for part in match[2].lstrip(",").split(",") if "=" in part)
        result.append({"type": match[1], "data": {k: html.unescape(v) for k, v in data.items()}})
        end = match.end()
    if end < len(value):
        result.append({"type": "text", "data": {"text": html.unescape(value[end:])}})
    return result


def describe(parts: list[dict], images: dict[int, str] | None = None) -> str:
    """Readable plain text for group context, keeping placeholders for non-text segments."""
    labels = {"image": "[图片]", "face": "[表情]", "record": "[语音]", "video": "[视频]"}
    out = []
    for index, part in enumerate(parts):
        data = part.get("data", {})
        kind = part.get("type")
        if kind == "text":
            out.append(str(data.get("text", "")))
        elif kind == "at":
            out.append(f"@{data.get('name') or data.get('qq', '')} ")
        elif kind == "file":
            out.append(f"[文件 {data.get('name', '')}]")
        elif kind == "forward":
            out.append(f"[合并转发 {data.get('id', '')}]")
        elif images and index in images:
            out.append(images[index])
        elif kind in labels:
            out.append(labels[kind])
    return clip_image_text("".join(out).strip(), 2000)


def forward_node(message: list[dict], user_id: str, nickname: str) -> dict:
    return {
        "type": "node",
        "data": {"user_id": user_id, "nickname": nickname, "content": message},
    }


def qq_download_url(url: str) -> bool:
    """Only QQ's own HTTPS CDNs; never protocol-side local paths or arbitrary hosts."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and any(
            host == d or host.endswith("." + d)
            for d in ("qq.com", "qq.com.cn", "qpic.cn", "gtimg.cn")
        )
    )


def approval_reply(text: str) -> re.Match | None:
    """「同意」/「拒绝」单独发即可；带 10 位 id 时指名道姓地处理某一条。"""
    return re.fullmatch(r"(同意|拒绝)\s*([a-f0-9]{10})?", text)


async def file_ref(store, adapter: str, group_id: str, data: dict, session: str) -> str:
    """A session-bound handle for qq_file_download; platform IDs never leave the store."""
    ref = uid()
    await store.put(
        "qq_file", ref, {"adapter": adapter, "group_id": group_id, "data": data}, session
    )
    return ref


class OneBotNotConnected(PublicError):
    pass


class OneBotActionError(PublicError):
    def __init__(self, action: str, result: dict):
        retcode = result.get("retcode")
        detail = f"OneBot {action} 失败（retcode={retcode if type(retcode) is int else '未知'}）"
        reason = " ".join(str(result[key]) for key in ("wording", "message") if result.get(key))
        if "onMsgInfoListUpdate" in reason:
            detail += "：协议端等待 QQ 发送回执超时"
        elif "timeout" in reason.lower() or "超时" in reason:
            detail += "：协议端操作超时"
        elif reason:
            detail += "：协议端返回错误"
        super().__init__(detail)


class OneBot:
    def __init__(self, config, vault, store, sink):
        self.config, self.vault, self.store, self.sink = config, vault, store, sink
        self.ws = None
        self.pending: dict[str, asyncio.Future] = {}
        self.status = "disconnected"
        self.self_id = config.self_id
        self.events: asyncio.Queue = asyncio.Queue(maxsize=256)
        self.log = get_logger(config.id)
        self._friends: tuple[float, frozenset[str]] = (0.0, frozenset())
        # Auto-start bookkeeping: when the failure streak began and the next allowed attempt.
        self.failed_since: float | None = None
        self.next_auto_start = 0.0
        self.auto_process: asyncio.subprocess.Process | None = None

    async def call(self, action: str, params: dict) -> Any:
        if not self.ws:
            raise OneBotNotConnected("OneBot 未连接")
        key = uid()
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        try:
            await self.ws.send(canonical({"action": action, "params": params, "echo": key}))
            async with asyncio.timeout(30):
                result = await future
            return self.action_data(action, result)
        finally:
            self.pending.pop(key, None)

    @staticmethod
    def action_data(action: str, result: dict) -> Any:
        if result.get("status") != "ok" or result.get("retcode", 0) != 0:
            raise OneBotActionError(action, result)
        return result.get("data") or {}

    async def get_file(self, file_id: str, max_bytes: int) -> dict:
        # NapCat may return the whole file as base64 in one response. A separate
        # connection keeps large replies away from the main 1 MiB event socket.
        token = await self.vault.resolve(self.config.token)
        key = uid()
        message = {"action": "get_file", "params": {"file_id": file_id}, "echo": key}
        try:
            async with asyncio.timeout(60):
                async with connect(
                    self.config.url,
                    additional_headers={"Authorization": f"Bearer {token}"} if token else {},
                    max_size=4 * ((max_bytes + 2) // 3) + 65536,
                    max_queue=1,
                ) as ws:
                    await ws.send(canonical(message))
                    while True:
                        result = json.loads(await ws.recv())
                        if result.get("echo") == key:
                            data = self.action_data("get_file", result)
                            if isinstance(data, dict):
                                return data
                            raise PublicError("OneBot 文件响应格式无效")
        except TimeoutError:
            raise PublicError("OneBot 下载文件超时") from None
        except ConnectionClosed as exc:
            if exc.sent and exc.sent.code == 1009:
                raise PublicError("OneBot 文件响应超过大小上限") from None
            raise PublicError("OneBot 文件连接已断开") from None
        except (WebSocketException, OSError, ValueError):
            raise PublicError("OneBot 文件接口不可用") from None

    async def friend_ids(self) -> frozenset[str]:
        """协议端好友列表，缓存 300 秒；拉取失败时沿用旧缓存。"""
        ts, ids = self._friends
        if monotonic() - ts >= 300:
            try:
                data = await self.call("get_friend_list", {})
                ids = frozenset(str(row.get("user_id")) for row in data or () if row.get("user_id"))
                self._friends = (monotonic(), ids)
            except Exception as exc:
                self.log.warning("获取好友列表失败，沿用缓存判断", exc=exc)
        return ids

    def invalidate_friends(self) -> None:
        self._friends = (0.0, frozenset())

    async def consume(self) -> None:
        while True:
            data = await self.events.get()
            try:
                await self.sink(self, data)
            except Exception as exc:
                self.log.error(f"处理 OneBot 事件失败（{type(exc).__name__}）", exc=exc)
                await self.store.activity(
                    "adapter.event_failed", f"{self.config.id}: {type(exc).__name__}"
                )

    async def identify(self) -> None:
        result = await self.call("get_login_info", {})
        actual = str(result.get("user_id", ""))
        if self.config.self_id and actual != self.config.self_id:
            self.log.error(
                f"登录账号 {actual} 与配置的 self_id {self.config.self_id} 不一致，已断开"
            )
            if self.ws:
                await self.ws.close(code=1008, reason="self_id mismatch")
            return
        self.self_id = actual
        self.status = "connected"
        self.failed_since = None
        self.log.log(f"已连接 OneBot，登录账号 {result.get('nickname') or actual}（{actual}）")

    async def run(self) -> None:
        delay = 1
        warned = False
        while True:
            try:
                self.log.debug(f"正在连接 {self.config.url}")
                token = await self.vault.resolve(self.config.token)
                async with connect(
                    self.config.url,
                    additional_headers={"Authorization": f"Bearer {token}"} if token else {},
                    max_size=2**20,
                    ping_interval=20,
                    ping_timeout=20,
                ) as ws:
                    self.ws = ws
                    self.status = "connecting"
                    delay, warned = 1, False
                    async with asyncio.TaskGroup() as group:
                        consumer = group.create_task(self.consume())
                        identity = group.create_task(self.identify())
                        try:
                            async for raw in ws:
                                data = json.loads(raw)
                                key = str(data.get("echo", ""))
                                if key in self.pending:
                                    future = self.pending[key]
                                    if not future.done():
                                        future.set_result(data)
                                elif data.get("post_type"):
                                    try:
                                        self.events.put_nowait(data)
                                    except asyncio.QueueFull:
                                        self.log.warning("事件队列已满，丢弃一条 OneBot 事件")
                                        await self.store.activity(
                                            "adapter.overflow", self.config.id
                                        )
                        finally:
                            consumer.cancel()
                            identity.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Only the first failure of a reconnect streak is a warning; retries stay quiet.
                message = f"连接断开或无法连接（{type(exc).__name__}），约 {delay} 秒后重连"
                (self.log.debug if warned else self.log.warning)(message)
                warned = True
                await self.store.activity(
                    "adapter.disconnected", f"{self.config.id}: {type(exc).__name__}"
                )
                await self.try_auto_start()
            finally:
                self.ws, self.status = None, "disconnected"
                for future in self.pending.values():
                    if not future.done():
                        future.set_exception(PublicError("OneBot 连接断开"))
            await asyncio.sleep(delay + random.random())
            delay = min(60, delay * 2)

    async def try_auto_start(self) -> None:
        """Launch the backend command once the endpoint has been unreachable long enough."""
        command = self.config.auto_start
        if not command:
            return
        clock = time.monotonic()
        if self.failed_since is None:
            self.failed_since = clock
        if clock - self.failed_since < self.config.auto_start_after:
            return
        # At most one launch per five minutes: startup tools may take a while themselves.
        if clock < self.next_auto_start:
            return
        self.next_auto_start = clock + 300
        self.log.log(f"协议端已连续不可达，尝试自动启动：{command[0]}")
        try:
            self.auto_process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await self.store.activity("adapter.autostart", f"{self.config.id}: {command[0]}")
        except Exception as exc:
            # Usually a missing path or a launcher that needs elevation and cannot ask for it.
            self.log.warning(f"自动启动失败（{type(exc).__name__}）：{exc}")
            await self.store.activity(
                "adapter.autostart_failed", f"{self.config.id}: {type(exc).__name__}"
            )


class AdapterHub:
    def __init__(self, config, vault, store):
        self.config, self.vault, self.store = config, vault, store
        self.instances: dict[str, OneBot | Feishu | QQOfficial] = {}
        self.tasks: list[asyncio.Task] = []
        self.runtime: RuntimePort | None = None
        self.permissions: ApprovalPort | None = None
        # Friend / group requests become events so PE decides whether to tell the owner.
        self.emit: Callable[[ExternalEvent], Awaitable[str]] | None = None
        # shiritaibot.meme.MemeManager: collects group stickers, turns markers into stickers.
        self.memes: Any = None
        # shiritaibot.plugin.core.hooks.HookBus: plugin commands, listeners and send hooks.
        self.hooks: Any = None
        # Passive participation: last interject per session and a one-hour history.
        self.interject_at: dict[str, float] = {}
        self.interject_log: dict[str, deque[float]] = {}
        self.heartflow: Heartflow | None = None

    async def start(self) -> None:
        for cfg in self.config.value.adapters:
            if not cfg.enabled:
                continue
            bot: OneBot | Feishu | QQOfficial
            if cfg.type == "feishu":
                assert self.runtime is not None and self.permissions is not None
                get_logger(cfg.id).log("启动飞书应用机器人长连接")
                bot = Feishu(
                    cfg,
                    self.vault,
                    self.store,
                    self.runtime,
                    self.permissions,
                    self.enqueue,
                    self.hooks,
                    settings=self.config,
                    active_context=lambda ctx: self.config.value.feishu_context_valid(ctx),
                )
            elif cfg.type == "qq_official":
                logger = get_logger(cfg.id)
                logger.log("启动 QQ 官方机器人 Webhook")
                bot = QQOfficial(cfg, self.vault, self.receive_qq_official, logger)
            else:
                get_logger(cfg.id).log(f"启动协议端（OneBot 11 → {cfg.url}）")
                bot = OneBot(cfg, self.vault, self.store, self.receive)
            self.instances[cfg.id] = bot
            self.tasks.append(asyncio.create_task(bot.run()))
        self.tasks.append(asyncio.create_task(self.flush()))

    def diagnostics(self) -> dict[str, str]:
        return {
            key: bot.diagnostic
            for key, bot in self.instances.items()
            if isinstance(bot, (Feishu, QQOfficial)) and bot.diagnostic
        }

    async def close(self) -> None:
        if self.heartflow:
            await self.heartflow.reset()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        self.instances.clear()

    async def receive_qq_official(self, bot: QQOfficial, incoming: QQMessage) -> None:
        assert self.runtime is not None and self.permissions is not None
        cfg, settings = bot.config, self.config.value
        group, target, user = incoming.group, incoming.target, incoming.user
        session = cfg.session(target, group=group)
        if not settings.active_qq_official_session(session):
            field = "群 OpenID" if group else "用户 OpenID"
            bot.log.debug(f"忽略未配置的 QQ 官方会话（{field} {target}）")
            return
        if user in cfg.ignore_users:
            return
        dedup = f"qqofficial:{cfg.app_id}:{incoming.message_id}"
        if not await self.store.execute(
            "INSERT OR IGNORE INTO seen_messages VALUES(?,?)", (dedup, now().timestamp())
        ):
            return
        text = incoming.text
        owner = not group and user == cfg.owner_id
        keyword = settings.matches_wake_keyword(text)
        wake = incoming.mentioned or keyword
        profile = settings.session_memory(session)
        if group and settings.group_context.enabled and text:
            await self.store.record_group(
                session, dedup, user, user, text, wake, memory_profile=profile
            )
        if not text:
            return
        rest = settings.strip_wake_prefix(text)
        if rest != text and (
            rest.startswith("/")
            or rest in {"停", "取消"}
            or approval_reply(rest)
            or self.permissions.menu.accepts(session, rest)
        ):
            text = rest
        direct_command = group and not settings.group_command_require_wake and text.startswith("/")
        if group and not (wake or direct_command):
            return
        anchor = ReplyAnchor(session, incoming.message_id, incoming.at + incoming.reply_limit[0])
        ctx = Context(
            session, user, owner=owner, group=group, memory_profile=profile, reply_anchor=anchor
        )
        token = current_reply.set(anchor)
        try:
            if owner and (reply := approval_reply(text)):
                try:
                    if reply[2]:
                        await self.permissions.resolve(reply[2], reply[1] == "同意", approver=ctx)
                    else:
                        await self.permissions.resolve_latest(ctx, reply[1] == "同意")
                except PublicError as exc:
                    await self.enqueue(session, str(exc), decorate=False)
                else:
                    await self.enqueue(session, "已处理审批。", decorate=False)
                return
            if owner and await self.permissions.menu.reply(session, text, approver=ctx):
                return
            if (
                not group
                and settings.private_require_keyword
                and not keyword
                and not (text.startswith("/") or text in {"停", "取消"})
            ):
                return
            event = MessageEvent(
                session,
                text,
                user,
                platform="qq_official",
                adapter=cfg.id,
                group_id=target if group else "",
                message_id=incoming.message_id,
                is_owner=owner,
                is_wake=not group or wake or direct_command,
                raw=incoming.raw,
            )
            if group and not wake:
                if self.hooks is not None and await self.hooks.dispatch_command(event):
                    return
                if not builtin_slash(text):
                    return
            elif self.hooks is not None and await self.hooks.dispatch(event):
                return
            await self.runtime.submit(ctx, text)
        finally:
            current_reply.reset(token)

    async def receive(self, bot: OneBot, event: dict) -> None:
        assert self.runtime is not None and self.permissions is not None
        cfg = bot.config
        if event.get("post_type") == "request":
            await self.request(bot, event)
            return
        if event.get("post_type") != "message" or not bot.self_id:
            return
        user = str(event.get("user_id", ""))
        if user == bot.self_id or user in cfg.ignore_users:
            return
        group = event.get("message_type") == "group"
        target = str(event.get("group_id")) if group else user
        sender = event.get("sender") or {}
        speaker = f"{sender.get('card') or sender.get('nickname') or user}({user})"
        if group and target not in cfg.groups:
            bot.log.debug(f"忽略未启用群 {target} 的消息", speaker)
            return
        if (
            not group
            and user != cfg.owner_id
            and user not in cfg.friends
            and user not in await bot.friend_ids()
        ):
            bot.log.debug("忽略非好友私聊（不在协议端好友列表和 friends 白名单中）", speaker)
            return
        session = f"{'group' if group else 'private'}:{cfg.id}:{target}"
        parts = segments(event.get("message", []))
        text = "".join(
            str(s.get("data", {}).get("text", "")) for s in parts if s.get("type") == "text"
        ).strip()
        mentioned = any(
            s.get("type") == "at" and str(s.get("data", {}).get("qq")) == bot.self_id for s in parts
        )
        settings = self.config.value
        keyword = settings.matches_wake_keyword(text)
        flow = (
            self.heartflow
            if group
            and settings.group_context.enabled
            and settings.group_context.interject_mode == "heartflow"
            else None
        )
        message_id = f"{cfg.id}:{event.get('message_id')}"
        arrival = flow.arrival(session, wake=mentioned or keyword) if flow else None
        where = f"群 {target}" if group else "私聊"
        # Untriggered group chatter is only context, so it stays at debug level.
        (bot.log.log if not group or mentioned or keyword else bot.log.debug)(
            brief(describe(parts), 200) or "[空消息]", where, speaker
        )
        if group and self.memes is not None:
            self.memes.hooker.observe(session, parts)
        images: dict[int, str] = {}

        async def image_reference(index: int, data: dict) -> str:
            if index not in images:
                if not qq_download_url(str(data.get("url") or "")):
                    return "[QQ 图片，协议端未提供可用的下载地址]"
                ref = await file_ref(self.store, cfg.id, target if group else "", data, session)
                images[index] = f"[QQ 图片引用 {ref}: 图片]"
            return images[index]

        if group and settings.group_context.enabled:
            for index, part in enumerate(parts):
                if part.get("type") == "image":
                    await image_reference(index, part.get("data", {}))
            await self.store.record_group(
                session,
                message_id,
                user,
                str(sender.get("card") or sender.get("nickname") or user),
                describe(parts, images),
                mentioned or keyword,
                memory_profile=settings.session_memory(session),
            )

        def message(wake: bool) -> MessageEvent:
            return MessageEvent(
                session=session,
                text=text,
                sender_id=user,
                sender_name=str(sender.get("card") or sender.get("nickname") or ""),
                platform="qq",
                adapter=cfg.id,
                group_id=target if group else "",
                message_id=str(event.get("message_id", "")),
                is_owner=user == cfg.owner_id,
                is_wake=wake,
                segments=parts,
                raw=event,
            )

        if not group and settings.private_require_keyword and not keyword:
            owner_reply = user == cfg.owner_id and (
                approval_reply(text) or self.permissions.menu.accepts(session, text)
            )
            if not (text.startswith("/") or text in {"停", "取消"} or owner_reply):
                bot.log.debug("私聊未包含唤醒词，不处理", speaker)
                return
        passive_group = group and not (mentioned or keyword)
        direct_command = (
            passive_group and not settings.group_command_require_wake and text.startswith("/")
        )
        if (not passive_group or direct_command) and not await self.store.execute(
            "INSERT OR IGNORE INTO seen_messages VALUES(?,?)", (message_id, now().timestamp())
        ):
            bot.log.debug("重复消息，已跳过", speaker)
            return
        wake = flow.wake(session) if flow and not passive_group else ""
        if passive_group:
            if text.startswith("/"):
                if direct_command:
                    if self.hooks is not None and await self.hooks.dispatch_command(message(True)):
                        return
                    if builtin_slash(text):
                        await self.runtime.submit(
                            Context(session, user, owner=user == cfg.owner_id, group=True), text
                        )
                return
            if self.hooks is not None and await self.hooks.dispatch(message(False)):
                return
            if flow and arrival is not None:
                flow.message(session, arrival, user, speaker, describe(parts, images))
            else:
                await self.maybe_interject(bot, session, user, speaker, describe(parts, images))
            return
        # Keep the original text for the model, but allow "小知，/stop" and approval replies.
        rest = settings.strip_wake_prefix(text)
        if rest != text and (
            rest.startswith("/")
            or rest in {"停", "取消"}
            or approval_reply(rest)
            or self.permissions.menu.accepts(session, rest)
        ):
            text = rest
        if not group and user == cfg.owner_id:
            approver = Context(session, user, owner=True)
            match = approval_reply(text)
            if match:
                allow = match[1] == "同意"
                try:
                    if match[2]:
                        await self.permissions.resolve(match[2], allow, approver=approver)
                        key, label = match[2], ""
                    else:
                        # 不带 id：作用于最新一条待审批，回复里说明处理的是哪条。
                        row = await self.permissions.resolve_latest(approver, allow)
                        key, label = row["id"], f"{row.get('tool')} "
                    bot.log.log(f"主人{match[1]}了审批 {key}", where)
                    await self.enqueue(session, f"审批已处理：{label}{match[1]} {key}")
                except PublicError as exc:
                    bot.log.warning(f"审批处理失败：{exc}", where)
                    await self.enqueue(session, str(exc))
                return
            if await self.permissions.menu.reply(session, text, approver=approver):
                return
        if self.hooks is not None and await self.hooks.dispatch(message(True)):
            return
        for index, part in enumerate(parts):
            data = part.get("data", {})
            if part.get("type") == "file":
                ref = await file_ref(self.store, cfg.id, target if group else "", data, session)
                text += f"\n[QQ 文件引用 {ref}: {data.get('name') or data.get('file') or 'file'}]"
            elif part.get("type") == "image":
                text += "\n" + await image_reference(index, data)
            elif part.get("type") == "video":
                if data.get("url"):
                    ref = await file_ref(self.store, cfg.id, target if group else "", data, session)
                    text += f"\n[QQ 视频引用 {ref}: {data.get('file') or '视频'}]"
                else:
                    text += "\n[QQ 视频，协议端未提供下载地址]"
            elif part.get("type") == "forward" and data.get("id"):
                text += f"\n[QQ 合并转发 {data['id']}]"
            elif part.get("type") == "reply" and data.get("id"):
                text = f"[引用 QQ 消息 {data['id']}] " + text
        text = text.strip()
        if text:
            owner = user == cfg.owner_id
            await self.runtime.submit(
                Context(
                    session,
                    user,
                    "agent" if owner and not group else "chat",
                    owner,
                    group,
                    group_wake=wake,
                ),
                text,
            )

    async def maybe_interject(
        self, bot: OneBot, session: str, user: str, speaker: str, text: str
    ) -> None:
        """Occasionally let the Bot read untriggered group chatter and chime in."""
        # Imported lazily: proactive -> memory -> agent would cycle at module load.
        from shiritaibot.proactive import quiet_hours

        settings = self.config.value
        group = settings.group_context
        if (
            not group.enabled
            or group.interject_mode != "simple"
            or not text
            or self.runtime is None
        ):
            return
        busy = getattr(self.runtime, "busy", None)
        if busy is not None and busy(session):
            return
        proactive = settings.proactive
        try:
            local = now().astimezone(ZoneInfo(proactive.timezone))
        except Exception:
            local = now().astimezone()
        if quiet_hours(proactive.quiet_start, proactive.quiet_end, local):
            return
        clock = asyncio.get_running_loop().time()
        if clock - self.interject_at.get(session, float("-inf")) < group.interject_cooldown:
            return
        recent = self.interject_log.setdefault(session, deque())
        while recent and clock - recent[0] > 3600:
            recent.popleft()
        if len(recent) >= group.interject_max_per_hour:
            return
        if random.random() >= group.interject_probability:
            return
        self.interject_at[session] = clock
        recent.append(clock)
        prompt = (
            "[群聊观察] 群里刚有人发言，这条没有 @ 你。\n"
            f"最新一条来自 {speaker}：{brief(text, 300)}\n"
            "能顺着话题自然接一句就写要发到群里的内容，否则只输出 <pass>。"
        )
        bot.log.debug(f"群聊观察：可能插话（{speaker}）")
        asyncio.create_task(self._interject(session, user, prompt))

    async def _interject(self, session: str, user: str, prompt: str) -> None:
        try:
            assert self.runtime is not None
            await self.runtime.submit(
                Context(session, user, "chat", group=True, interject=True), prompt
            )
        except Exception as exc:
            get_logger("群聊").debug(f"插话失败（{type(exc).__name__}）")

    async def request(self, bot: OneBot, event: dict) -> None:
        kind, flag = event.get("request_type"), str(event.get("flag", ""))
        if kind not in {"friend", "group"} or not flag:
            return
        key = fingerprint([bot.config.id, flag])[:16]
        user, group = str(event.get("user_id", "")), str(event.get("group_id") or "")
        sub_type = str(event.get("sub_type", "")) if kind == "group" else ""
        title = (
            f"QQ 好友申请：{user}"
            if kind == "friend"
            else f"QQ 群邀请：{user} 邀请加入群 {group}"
            if sub_type == "invite"
            else f"QQ 入群申请：{user} 申请加入群 {group}"
        )
        record = {
            "adapter": bot.config.id,
            "kind": kind,
            "sub_type": sub_type,
            "user_id": user,
            "group_id": group,
            "comment": str(event.get("comment", ""))[:500],
            "flag": flag,
            "status": "pending",
            "at": now().isoformat(),
        }
        if not await self.store.insert("qq_request", key, record):
            return
        bot.log.log(f"{title}（{brief(record['comment'], 80) or '无附言'}）")
        if self.emit is not None:
            await self.emit(
                ExternalEvent(
                    source="qq",
                    kind=f"qq.request.{kind}",
                    dedup_key=key,
                    title=title,
                    payload={
                        "request": key,
                        "comment": record["comment"],
                        "group_id": group,
                        "how_to_handle": "用 qq_request_handle 处理该 request",
                    },
                    actors=[Actor(identity=f"qq:{user}", role="requester")] if user else [],
                )
            )

    async def deliver_group_reply(self, session: str, text: str, wake: str) -> None:
        await self.enqueue(session, text, group_wake=wake)

    async def enqueue(
        self,
        session: str,
        text: str,
        *,
        decorate: bool = True,
        approval_batch: str = "",
        observation: Observation | None = None,
        group_wake: str = "",
        image: bytes | None = None,
    ) -> None:
        if decorate and self.hooks is not None:
            decorated = await self.hooks.decorate(session, text)
            if decorated is None:
                return
            text = decorated
        if observation and not (self.heartflow and self.heartflow.valid(session, observation.id)):
            return
        await self._enqueue(
            session,
            text,
            approval_batch=approval_batch,
            observation=observation,
            group_wake=group_wake,
            image=image,
        )
        if self.hooks is not None:
            self.hooks.sent(session, text)

    async def _enqueue(
        self,
        session: str,
        text: str,
        *,
        approval_batch: str = "",
        observation: Observation | None = None,
        group_wake: str = "",
        image: bytes | None = None,
    ) -> None:
        injector = self.memes.injector if self.memes is not None else None
        if session.startswith(("web:", "background:")):
            if injector is not None:
                text = injector.plain(text)
            await self.store.put("reply", uid(), {"text": text, "at": now().isoformat()}, session)
            return
        text = clean_outbound(text)
        official = is_qq_official_session(session)
        feishu = is_feishu_session(session)
        if (official or feishu) and injector is not None:
            text = injector.plain(text)
            injector = None
        # <meme> markers become their own sticker messages, splitting the text around them.
        cfg = self.config.value
        expires = now().timestamp() + 86400
        delivery = {
            "status": "pending",
            "expires": expires,
            "memory_profile": self.config.value.session_memory(session),
        }
        if official:
            anchor = current_reply.get()
            if anchor is None or anchor.session != session:
                get_logger(session.split(":")[1]).warning("QQ 官方回复缺少对应的入站消息，已跳过")
                return
            delivery.update(qq_reply_to=anchor.message_id, expires=anchor.expires)
        if approval_batch:
            delivery["approval_batch"] = approval_batch
        if group_wake:
            delivery["group_wake"] = group_wake
        if observation:
            delivery["heartflow"] = observation.id
            delivery["memory_profile"] = observation.profile

        async def put(piece: dict):
            key = uid()
            if observation:
                observation.rows.add(key)
            try:
                await self.store.put("outbox", key, {**piece, **delivery}, session)
            except Exception:
                if observation:
                    observation.rows.discard(key)
                raise

        if image is not None:
            await put({"text": text, "image": base64.b64encode(image).decode("ascii")})
            return
        pieces = injector.split(text) if injector is not None else [text]
        fold = (
            not official
            and not feishu
            and cfg.onebot_forward_long_messages
            and sum(len(piece) for piece in pieces if isinstance(piece, str))
            > cfg.onebot_forward_threshold
        )
        rows = []
        chunk = 600 if official else 3500
        for piece in pieces:
            if not isinstance(piece, str):
                rows.append({"text": MemeInjector.label(piece), "meme": piece["id"]})
                continue
            rows.extend(
                {"text": piece[index : index + chunk]} for index in range(0, len(piece), chunk)
            )
        if fold:
            await put({"text": "".join(row["text"] for row in rows), "forward": rows})
        else:
            for row in rows:
                await put(row)

    async def outgoing(self, row: dict) -> list[dict] | None:
        """OneBot message segments for an outbox row; None when its sticker is gone."""
        if image := row.get("image"):
            return [{"type": "image", "data": {"file": "base64://" + image}}]
        if not row.get("meme"):
            return [{"type": "text", "data": {"text": row["text"]}}]
        data = await self.memes.core.image(row["meme"]) if self.memes is not None else None
        if data is None:
            return None
        return [
            {
                "type": "image",
                "data": {
                    "file": "base64://" + base64.b64encode(data).decode(),
                    "sub_type": 1,
                    "summary": "[动画表情]",
                },
            }
        ]

    async def notify_owner(self, text: str, *, handoff: bool = True) -> None:
        target = next(
            (
                a
                for a in self.config.value.adapters
                if a.type == "onebot11" and a.enabled and a.owner_id
            ),
            None,
        )
        session = target.owner_session() if target else "web:owner"
        if not handoff:
            # Approval prompts must be visible while the requesting turn waits for approval;
            # plugins cannot rewrite or cancel them.
            await self.enqueue(session, text, decorate=False)
        elif self.runtime is not None:
            await self.runtime.handoff(session, text, self.enqueue)
        else:
            await self.enqueue(session, text)
            await self.store.append(
                session,
                "handoff",
                {"role": "assistant", "content": text, "_proactive": True},
                memory_profile=self.config.value.session_memory(session),
            )

    async def flush(self) -> None:
        while True:
            for row in await self.store.with_status("outbox", ("pending",)):
                if row["status"] != "pending":
                    continue
                kind, adapter_id, target = row["scope"].split(":", 2)
                bot = self.instances.get(adapter_id)
                if row.get("heartflow") and not (
                    self.heartflow and self.heartflow.valid(row["scope"], row["heartflow"])
                ):
                    row["status"] = "expired"
                elif row["expires"] < now().timestamp():
                    row["status"] = "expired"
                    get_logger(adapter_id).warning("消息超过发送有效期，已放弃")
                elif kind == "group" and row.get(
                    "memory_profile", "default"
                ) != self.config.value.session_memory(row["scope"]):
                    row["status"] = "failed"
                elif is_qq_official_session(row["scope"]) and (
                    not self.config.value.active_qq_official_session(row["scope"])
                    or bot is not None
                    and not isinstance(bot, QQOfficial)
                ):
                    row["status"] = "failed"
                elif is_feishu_session(row["scope"]) and (
                    not self.current_feishu_delivery(row)
                    or not isinstance(bot, Feishu)
                    and bot is not None
                ):
                    row["status"] = "failed"
                elif not bot or bot.status != "connected":
                    continue
                elif isinstance(bot, QQOfficial):
                    await self.flush_qq_official(bot, row, kind, target.rpartition("/")[2])
                    continue
                elif isinstance(bot, Feishu):
                    await self.flush_feishu(bot, row, kind, target)
                    continue
                elif row.get("forward"):
                    nodes = [
                        forward_node(content, bot.self_id, self.config.value.assistant_name)
                        for piece in row["forward"]
                        if (content := await self.outgoing(piece)) is not None
                    ]
                    await self.flush_onebot(bot, row, kind, target, nodes)
                    continue
                elif (message := await self.outgoing(row)) is None:
                    row["status"] = "failed"
                    get_logger(adapter_id).warning(f"表情 {row['meme']} 已被删除，跳过发送")
                else:
                    await self.flush_onebot(bot, row, kind, target, message)
                    continue
                await self.store.put("outbox", row["id"], row, row["scope"])
                if row.get("heartflow") and self.heartflow:
                    self.heartflow.settled(row["scope"], row)
            await asyncio.sleep(1)

    async def flush_onebot(
        self, bot: OneBot, row: dict, kind: str, target: str, message: list[dict]
    ) -> None:
        group = kind == "group"
        where = f"→ {'群' if group else '私聊'} {target}"
        if not target.isascii() or not target.isdecimal():
            row["status"] = "failed"
            bot.log.warning("无效的 OneBot 目标，已跳过", where)
            await self.store.put("outbox", row["id"], row, row["scope"])
            if row.get("heartflow") and self.heartflow:
                self.heartflow.settled(row["scope"], row)
            return
        row["status"] = "uncertain"
        await self.store.put("outbox", row["id"], row, row["scope"])
        if row.get("heartflow") and not (
            self.heartflow and self.heartflow.valid(row["scope"], row["heartflow"])
        ):
            row["status"] = "expired"
            await self.store.put("outbox", row["id"], row, row["scope"])
            if self.heartflow:
                self.heartflow.settled(row["scope"], row)
            return
        try:
            params: dict[str, Any] = {"group_id" if group else "user_id": int(target)}
            if row.get("forward"):
                action = "send_group_forward_msg" if group else "send_private_forward_msg"
                params["messages"] = message
            else:
                action = "send_group_msg" if group else "send_private_msg"
                params["message"] = message
            receipt = await bot.call(action, params)
            if not isinstance(receipt, dict) or receipt.get("message_id") is None:
                raise PublicError("OneBot 回包未包含 message_id")
        except OneBotNotConnected:
            # call() did not submit anything, so the queue can wait for a reconnect.
            row["status"] = "pending"
            await self.store.put("outbox", row["id"], row, row["scope"])
        except Exception as exc:
            reason = str(exc) if isinstance(exc, PublicError) else type(exc).__name__
            bot.log.warning(f"发送未确认（{reason}），为避免重复不再重试", where)
        else:
            row.update(status="sent", receipt=receipt)
            row.pop("image", None)
            await self.store.put("outbox", row["id"], row, row["scope"])
            if row.get("group_wake") and self.heartflow:
                self.heartflow.passive_sent(row["scope"], row["group_wake"], row["text"])
            if row.get("heartflow"):
                try:
                    await self.store.append(
                        row["scope"],
                        row["heartflow"],
                        {
                            "role": "assistant",
                            "content": row["text"],
                            "_speaker": self.config.value.assistant_name,
                        },
                        memory_profile=row["memory_profile"],
                    )
                except Exception as exc:
                    bot.log.warning(f"插话已发送，记录会话历史失败（{type(exc).__name__}）", where)
            bot.log.debug(f"已发送：{brief(row['text'], 80)}", where)
            await self.record_sent_group(
                bot.self_id, row, kind, enabled=self.config.value.group_context.enabled
            )
        if row.get("heartflow") and self.heartflow and row["status"] != "pending":
            self.heartflow.settled(row["scope"], row)
        await asyncio.sleep(0.7)

    async def flush_qq_official(self, bot: QQOfficial, row: dict, kind: str, target: str) -> None:
        message_id = row.get("qq_reply_to")
        # Reserve a sequence before POST. QQ deduplicates on (msg_id, msg_seq).
        key = fingerprint([bot.config.app_id, message_id])
        seq = await self.store.get("qq_official_reply", key) or {"sequence": 0}
        limit = 5 if kind == "group" else 4
        if not message_id or seq["sequence"] >= limit:
            row["status"] = "failed"
            bot.log.warning("QQ 官方回复缺少消息 ID 或已达到单条消息回复上限", row["scope"])
            await self.store.put("outbox", row["id"], row, row["scope"])
            return
        seq.update(sequence=seq["sequence"] + 1, expires=row["expires"])
        await self.store.put("qq_official_reply", key, seq)
        row["status"] = "uncertain"
        await self.store.put("outbox", row["id"], row, row["scope"])
        try:
            if image := row.get("image"):
                receipt = await bot.send_image(kind, target, image, message_id, seq["sequence"])
            else:
                receipt = await bot.send_text(kind, target, row["text"], message_id, seq["sequence"])
        except PublicError as exc:
            if not isinstance(exc, QQOfficialSendError) or not exc.uncertain:
                row["status"] = "failed"
                await self.store.put("outbox", row["id"], row, row["scope"])
            bot.log.warning(f"QQ 官方回复未成功（{exc}）", row["scope"])
        except Exception as exc:
            bot.log.warning(f"QQ 官方回复未确认（{type(exc).__name__}）", row["scope"])
        else:
            row.update(status="sent", receipt=receipt)
            row.pop("image", None)
            await self.store.put("outbox", row["id"], row, row["scope"])
            await self.record_sent_group(
                bot.self_id, row, kind, enabled=self.config.value.group_context.enabled
            )
        await asyncio.sleep(0.7)

    async def record_sent_group(self, sender: str, row: dict, kind: str, *, enabled: bool) -> None:
        if kind != "group" or not enabled:
            return
        try:
            await self.store.record_group(
                row["scope"],
                f"outbox:self:{row['id']}",
                sender,
                self.config.value.assistant_name,
                row["text"],
                False,
                memory_profile=row.get("memory_profile", "default"),
            )
        except Exception as exc:
            get_logger(row["scope"].split(":", 2)[1]).warning(
                f"消息已发送，记录群聊上下文失败（{type(exc).__name__}）"
            )

    def current_feishu_delivery(self, row: dict) -> bool:
        batch_id = row.get("approval_batch")
        return self.config.value.active_feishu_session(row["scope"]) is not None and (
            not batch_id
            or (
                self.permissions is not None
                and self.permissions.menu.batch_active(row["scope"], batch_id)
            )
        )

    async def flush_feishu(self, bot: Feishu, row: dict, kind: str, target: str) -> None:
        if not bot.can_deliver(kind, target) or row.get("meme"):
            row["status"] = "failed"
            await self.store.put("outbox", row["id"], row, row["scope"])
            return
        # Persist before submitting. A crash during a request leaves a visible uncertain
        # receipt rather than repeating the message after Feishu's deduplication window.
        row["status"] = "uncertain"
        await self.store.put("outbox", row["id"], row, row["scope"])
        if not self.current_feishu_delivery(row):
            row["status"] = "failed"
            await self.store.put("outbox", row["id"], row, row["scope"])
            return
        try:
            if image := row.get("image"):
                receipt = await bot.send_image(kind, target, base64.b64decode(image), row["id"])
            else:
                receipt = await bot.send_text(kind, target, row["text"], row["id"])
        except FeishuSendError as exc:
            if not exc.uncertain:
                row["status"] = "pending" if exc.code == "not_connected" else "failed"
                await self.store.put("outbox", row["id"], row, row["scope"])
            bot.log.warning(str(exc))
        except Exception as exc:
            bot.log.warning(f"飞书发送未确认（{type(exc).__name__}）。")
        else:
            row.update(status="sent", receipt=receipt)
            row.pop("image", None)
            await self.store.put("outbox", row["id"], row, row["scope"])
            await self.record_sent_group(
                bot.self_id, row, kind, enabled=bot.config.feishu_group_context
            )
        await asyncio.sleep(0.7)
