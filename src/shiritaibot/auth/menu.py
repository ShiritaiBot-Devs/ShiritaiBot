from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shiritaibot.config import is_feishu_session
from shiritaibot.core import Context, PublicError, Tool, canonical, uid
from shiritaibot.core.logs import brief
from shiritaibot.i18n import t
from shiritaibot.prompts import prompt as prompt_text

if TYPE_CHECKING:
    from shiritaibot.auth import Permissions

WINDOW = 2.0
REASON_BUDGET = 2.0
QUICK_SECONDS = 300
PAGE_SIZE = 5
RISK_LABELS = {level: f"approvals.risk.{level}" for level in ("safe", "normal", "sensitive", "dangerous")}
RISK_ORDER = {"dangerous": 0, "sensitive": 1, "normal": 2, "safe": 3}
# Credential-bearing files are never auto-approved: the prompt must always be one-shot.
SENSITIVE_FILE = re.compile(
    r"config\.toml|master\.key|web\.token|\.credentials|state\.sqlite3|profiles[\\/]", re.I
)


@dataclass
class Request:
    key: str
    ctx: Context
    tool: Tool
    args: dict
    reason: str = ""
    reason_task: asyncio.Task | None = None


@dataclass
class Batch:
    id: str
    requests: list[Request]
    stage: str = "list"
    settled: set[str] = field(default_factory=set)
    shown: tuple[str | tuple[str, int], ...] = ()


class ApprovalMenu:
    """Routes approvals to separate numbered menus for each owner chat."""

    def __init__(self, permissions: Permissions):
        self.permissions = permissions
        self.chats: dict[str, _ChatMenu] = {}

    async def submit(self, ctx: Context, tool: Tool, args: dict, key: str) -> None:
        target = self.permissions.approval_target(ctx)
        if target:
            chat = self.chats.get(target)
            if chat is None:
                chat = self.chats[target] = _ChatMenu(self.permissions, target)
            chat.submit(ctx, tool, args, key)

    def settled(self, key: str) -> None:
        for chat in self.chats.values():
            chat.settled(key)

    def accepts(self, session: str, text: str) -> bool:
        chat = self.chats.get(session)
        return chat is not None and chat.accepts(text)

    def batch_active(self, session: str, batch_id: str) -> bool:
        chat = self.chats.get(session)
        return bool(
            chat and chat._current() and chat.batch and chat.batch.id == batch_id
            and len(chat.batch.settled) < len(chat.batch.requests)
        )

    async def reply(
        self, session: str, text: str, *, approver: Context | None = None
    ) -> bool:
        chat = self.chats.get(session)
        if chat is None or (is_feishu_session(session) and (
            approver is None
            or approver.session != session
            or not self.permissions.can_review(session, approver)
        )):
            return False
        return await chat.reply(text, approver)

    async def reconfigured(self) -> None:
        stale = [target for target in self.chats if not self.permissions.approval_target_current(target)]
        await self._close(stale)

    async def close(self) -> None:
        await self._close(list(self.chats))

    async def _close(self, targets: list[str]) -> None:
        chats = [self.chats.pop(target) for target in targets]
        for chat in chats:
            chat.closed = True
        await asyncio.gather(*(chat.close() for chat in chats))


class _ChatMenu:

    def __init__(self, permissions: Permissions, target: str):
        self.permissions = permissions
        self.target = target
        self.closed = False
        self.batch: Batch | None = None
        self.queue: list[Request] = []
        self.window: list[Request] = []
        self.tasks: set[asyncio.Task] = set()
        self.window_task: asyncio.Task | None = None
        self.refresh_lock = asyncio.Lock()

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def _send(self, text: str, *, prompt: bool = False) -> None:
        if self._current() and self.permissions.notify:
            batch_id = self.batch.id if prompt and self.batch and is_feishu_session(self.target) else ""
            await self.permissions.notify(text, session=self.target, batch_id=batch_id)

    def submit(self, ctx: Context, tool: Tool, args: dict, key: str) -> None:
        req = Request(key=key, ctx=ctx, tool=tool, args=args)
        req.reason_task = self._spawn(self._describe(req))
        if self.batch is not None:
            self.queue.append(req)
            return
        self.window.append(req)
        if self.window_task is None:
            self.window_task = self._spawn(self._wait_window())

    def settled(self, key: str) -> None:
        if self.batch and any(req.key == key for req in self.batch.requests):
            self.batch.settled.add(key)
            self._spawn(self._refresh())

    def _option(self, text: str) -> int | None:
        match = re.fullmatch(r"[1-9]\d?", text.strip())
        return int(match.group()) if match else None

    def _groups(self, requests: list[Request]) -> list[tuple[str, str, list[Request]]]:
        buckets: dict[tuple[str, str], list[Request]] = {}
        for req in requests:
            buckets.setdefault((req.tool.risk, req.tool.name), []).append(req)
        return [
            (risk, name, buckets[(risk, name)])
            for risk, name in sorted(
                buckets, key=lambda item: (RISK_ORDER.get(item[0], 9), item[1])
            )
        ]

    def _valid(self, number: int | None) -> bool:
        if number is None or self.batch is None:
            return False
        if self.batch.stage == "single":
            requests = [req for req in self.batch.requests if req.key not in self.batch.settled]
            limit = 4 if requests and self._can_grant(requests[0]) else 2
            return 1 <= number <= limit
        if self.batch.stage == "detail":
            groups = self._groups(
                [req for req in self.batch.requests if req.key not in self.batch.settled]
            )
            return 1 <= number <= 2 * len(groups) + 1
        return 1 <= number <= 3

    def _current(self) -> bool:
        return not self.closed and self.permissions.approval_target_current(self.target)

    def accepts(self, text: str) -> bool:
        return bool(self.batch and self.batch.shown and self._current() and self._valid(self._option(text)))

    async def reply(self, text: str, approver: Context | None) -> bool:
        number = self._option(text)
        batch = self.batch
        if number is None or batch is None or not batch.shown:
            return False
        async with self.refresh_lock:
            if self.batch is not batch or not self._current() or not self._valid(number):
                return False
            remaining = await self._sync()
            if not self._current():
                return False
            if not remaining:
                await self._finish()
                return True
            if batch.stage == "single":
                if batch.shown != (remaining[0].key,):
                    await self._show_single(remaining)
                    return True
                return await self._reply_single(remaining[0], number, approver)
            if batch.stage == "list":
                if number in {1, 2}:
                    await self._settle_all(remaining, number == 1, approver)
                    return True
                return await self._open_detail(batch, remaining)
            # 过期条目会让分组重新编号；旧编号不能落到没展示过的分组上。
            if batch.shown != self._signature(self._groups(remaining)):
                await self._show_detail(remaining)
                return True
            return await self._reply_detail(batch, remaining, number, approver)

    async def _reply_single(self, req: Request, number: int, approver: Context | None) -> bool:
        if number in {3, 4} and not self._can_grant(req):
            return False
        if number == 4:
            try:
                text = await self.permissions.grant_permanent(
                    req.ctx.session, req.tool.name, req.tool.risk, req.reason, menu_target=self.target
                )
            except PublicError:
                return False
            await self._send(text)
            await self._refresh_locked()
            return True
        if number == 3:
            try:
                await self.permissions.grant_quick(
                    req.ctx.session, req.tool.name, menu_target=self.target
                )
            except PublicError:
                return False
            await self._send(t("approvals.menu.quickGranted", name=req.tool.name))
            await self._refresh_locked()
            return True
        if number not in {1, 2}:
            return False
        if await self._resolve(req, number == 1, approver):
            verb = t("approvals.menu.verb.allow") if number == 1 else t("approvals.menu.verb.deny")
            await self._send(t("approvals.menu.settled", verb=verb, name=req.tool.name, key=req.key))
        await self._refresh_locked()
        return True

    async def _settle_all(
        self, requests: list[Request], allow: bool, approver: Context | None
    ) -> None:
        count = 0
        for req in requests:
            count += await self._resolve(req, allow, approver)
        verb = t("approvals.menu.verb.allow") if allow else t("approvals.menu.verb.deny")
        if count:
            await self._send(t("approvals.menu.settledAll", verb=verb, count=count))
        await self._finish()

    async def _open_detail(self, batch: Batch, requests: list[Request]) -> bool:
        groups = self._groups(requests)
        if not groups:
            return False
        await self._send(t("approvals.menu.detailOpened"))
        if len(groups) == 1:
            batch.stage = "single"
            await self._show_single(requests)
        else:
            await self._show_detail(requests)
        return True

    async def _reply_detail(
        self, batch: Batch, remaining: list[Request], number: int, approver: Context | None
    ) -> bool:
        groups = self._groups(remaining)
        if number == 2 * len(groups) + 1:
            batch.stage = "single"
            await self._show_single(remaining)
            return True
        index = (number - 1) // 2
        if index >= len(groups):
            return False
        _, name, group = groups[index]
        allow = number % 2 == 1
        count = 0
        for req in group:
            count += await self._resolve(req, allow, approver)
        verb = t("approvals.menu.verb.allow") if allow else t("approvals.menu.verb.deny")
        if count:
            await self._send(t("approvals.menu.settledGroup", name=name, verb=verb, count=count))
        await self._refresh_locked()
        return True

    async def _show_detail(self, requests: list[Request]) -> None:
        groups = self._groups(requests)
        batch = self.batch
        if not groups or batch is None:
            return
        batch.stage = "detail"
        batch.shown = self._signature(groups)
        await self._send(self._detail_text(groups), prompt=True)

    def _signature(
        self, groups: list[tuple[str, str, list[Request]]]
    ) -> tuple[tuple[str, int], ...]:
        return tuple((name, len(group)) for _, name, group in groups)

    async def close(self) -> None:
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        async with self.refresh_lock:
            self.batch = None
            self.queue.clear()
            self.window.clear()

    async def _wait_window(self) -> None:
        try:
            await asyncio.sleep(WINDOW)
        finally:
            self.window_task = None
        entries, self.window = self.window, []
        live = [req for req in entries if await self._is_pending(req)]
        if not live:
            return
        async with self.refresh_lock:
            if not self._current():
                return
            if self.batch is None:
                self.queue.extend(live)
                await self._finish()
            else:
                self.queue.extend(live)

    async def _open(self, requests: list[Request]) -> None:
        batch = Batch(uid()[:10], [])
        async with self.permissions.resolve_lock:
            for req in requests:
                row = await self.permissions.store.get("approval", req.key)
                if self._current() and self.permissions.is_waiting(row):
                    row["batch"] = batch.id
                    await self.permissions.store.put("approval", req.key, row, req.ctx.session)
                    batch.requests.append(req)
            if not batch.requests:
                return
            self.batch = batch
        requests = batch.requests
        for req in requests:
            await self._await_reason(req)
        requests = await self._sync()
        if not requests:
            self.batch = None
            return
        if len(requests) == 1:
            batch.stage = "single"
            await self._show_single(requests)
        else:
            batch.stage = "list"
            batch.shown = tuple(req.key for req in requests)
            await self._send(self._list_text(requests), prompt=True)

    async def _show_single(self, requests: list[Request]) -> None:
        req = requests[0]
        await self._await_reason(req)
        if self.batch:
            self.batch.shown = (req.key,)
        await self._send(self._single_text(req), prompt=True)

    async def _await_reason(self, req: Request) -> None:
        if req.reason or req.reason_task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(req.reason_task), REASON_BUDGET)
        except TimeoutError:
            pass

    async def _is_pending(self, req: Request) -> bool:
        row = await self.permissions.store.get("approval", req.key)
        return self.permissions.is_waiting(row)

    async def _sync(self) -> list[Request]:
        if self.batch is None:
            return []
        for req in self.batch.requests:
            if req.key in self.batch.settled:
                continue
            if not await self._is_pending(req):
                self.batch.settled.add(req.key)
        return [req for req in self.batch.requests if req.key not in self.batch.settled]

    async def _refresh(self) -> None:
        async with self.refresh_lock:
            await self._refresh_locked()

    async def _refresh_locked(self) -> None:
        remaining = await self._sync()
        if self.batch is None:
            return
        if not remaining:
            await self._finish()
        elif self.batch.stage == "single":
            if self.batch.shown != (remaining[0].key,):
                await self._show_single(remaining)
        elif self.batch.stage == "detail":
            if self.batch.shown != self._signature(self._groups(remaining)):
                await self._show_detail(remaining)

    async def _finish(self) -> None:
        self.batch = None
        while self.queue and self._current():
            live = []
            while self.queue and len(live) < PAGE_SIZE:
                req = self.queue.pop(0)
                if await self._is_pending(req):
                    live.append(req)
            if live:
                await self._open(live)
                if self.batch:
                    return

    async def _resolve(self, req: Request, allow: bool, approver: Context | None) -> bool:
        if not self._current():
            return False
        try:
            await self.permissions.resolve(
                req.key, allow, approver=approver, menu_target=self.target
            )
        except PublicError:
            return False
        if self.batch:
            self.batch.settled.add(req.key)
        return True

    async def _describe(self, req: Request) -> None:
        req.reason = await self._reason(req)

    async def _reason(self, req: Request) -> str:
        llm = self.permissions.llm
        if llm is None:
            return self._fallback(req)
        try:
            history = await self.permissions.store.history(
                req.ctx.session, limit=6, include_commands=False, memory_profile=req.ctx.memory_profile
            )
            request = canonical(
                {
                    "tool": req.tool.name,
                    "description": t(req.tool.description),
                    "risk": req.tool.risk,
                    "arguments": req.args,
                    "conversation": [
                        {
                            "role": message["role"],
                            "content": brief(
                                str(
                                    message.get("_original_content")
                                    or message.get("content")
                                    or ""
                                ),
                                800,
                            ),
                        }
                        for message in history
                    ],
                }
            )
            async with asyncio.timeout(REASON_BUDGET):
                reply = await llm.complete(
                    "light",
                    prompt_text("APPROVAL_REASON"),
                    [{"role": "user", "content": request}],
                    [],
                    settings=self.permissions.config.value,
                )
            lines = [line.strip() for line in reply.text.splitlines() if line.strip()]
            # The first line starts with "To do" in the active language.
            if lines and lines[0].startswith(("要做", "To do")):  # i18n: ok
                return "\n".join(lines[:2])
        except Exception:
            pass
        return self._fallback(req)

    def _fallback(self, req: Request) -> str:
        return (
            t("approvals.menu.fallback", name=req.tool.name, brief=brief(canonical(req.args), 160))
        )

    def _can_grant(self, req: Request) -> bool:
        if (
            req.ctx.background
            or req.ctx.require_manual_approval
            or is_feishu_session(req.ctx.session)
        ):
            return False
        return not (
            req.tool.name == "shell"
            and SENSITIVE_FILE.search(str(req.args.get("command", "")))
        )

    def _single_text(self, req: Request) -> str:
        lines = [
            t("approvals.menu.needsConfirm", name=req.tool.name),
            *(req.reason or self._fallback(req)).splitlines(),
            t("approvals.menu.args", brief=brief(canonical(req.args), 300)),
            t("approvals.menu.replyNumber"),
            t("approvals.menu.option.allow"),
            t("approvals.menu.option.deny"),
        ]
        if self._can_grant(req):
            lines.append(t("approvals.menu.option.quick", name=req.tool.name))
            warning = t("approvals.menu.option.dangerous") if req.tool.risk in {"sensitive", "dangerous"} else ""
            lines.append(t("approvals.menu.option.always", name=req.tool.name, warning=warning))
        elif req.ctx.require_manual_approval:
            lines.append(t("approvals.menu.option.perCall"))
        elif not is_feishu_session(req.ctx.session):
            lines.append(t("approvals.menu.option.limited"))
        return "\n".join(lines)

    def _list_text(self, requests: list[Request]) -> str:
        ordered = sorted(
            requests,
            key=lambda req: (RISK_ORDER.get(req.tool.risk, 9), req.tool.name, req.key),
        )
        lines = [t("approvals.menu.batch.title", count=len(ordered))]
        for index, req in enumerate(ordered, 1):
            summary = (req.reason or self._fallback(req)).splitlines()[0]
            summary = summary.removeprefix("要做：").removeprefix("To do:").strip()  # i18n: ok
            lines.append(
                f"{index}. {req.tool.name}（{t(RISK_LABELS.get(req.tool.risk, req.tool.risk))}）— {brief(summary, 30)}"
            )
        lines += [t("approvals.menu.replyNumber"), t("approvals.menu.batch.allowAll"), t("approvals.menu.batch.denyAll"), t("approvals.menu.batch.details")]
        return "\n".join(lines)

    def _detail_text(self, groups: list[tuple[str, str, list[Request]]]) -> str:
        lines = [t("approvals.menu.detail.title")]
        for risk, name, group in groups:
            lines.append(t("approvals.menu.detail.group", risk=t(RISK_LABELS.get(risk, risk)), name=name, count=len(group)))
        lines.append(t("approvals.menu.replyNumber"))
        for index, (_, name, _) in enumerate(groups):
            lines.append(t("approvals.menu.detail.allowGroup", value=2 * index + 1, name=name))
            lines.append(t("approvals.menu.detail.denyGroup", value=2 * index + 2, name=name))
        lines.append(t("approvals.menu.detail.oneByOne", value=2 * len(groups) + 1))
        return "\n".join(lines)
