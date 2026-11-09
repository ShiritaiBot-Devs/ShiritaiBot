from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shiritaibot.core import Context, PublicError, Tool, canonical, now, uid
from shiritaibot.core.logs import brief

if TYPE_CHECKING:
    from shiritaibot.auth import Permissions

WINDOW = 2.0
REASON_BUDGET = 2.0
QUICK_SECONDS = 300
PAGE_SIZE = 5
RISK_LABELS = {"safe": "安全", "normal": "一般", "sensitive": "敏感", "dangerous": "危险"}
RISK_ORDER = {"dangerous": 0, "sensitive": 1, "normal": 2, "safe": 3}
# Credential-bearing files are never auto-approved: the prompt must always be one-shot.
SENSITIVE_FILE = re.compile(
    r"config\.toml|master\.key|web\.token|\.credentials|state\.sqlite3|profiles[\\/]", re.I
)
REASON_SYSTEM = (
    "你在为主人撰写工具调用的审批说明。只输出两行纯文本，不要解释：\n"
    "要做：一句话说明这次调用打算做什么\n"
    "为什么：一句话说明为什么需要做（结合对话）\n"
    "每行不超过40字。"
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
    shown: tuple[str, ...] = ()


class ApprovalMenu:
    """Collects pending human approvals into one numbered menu per chat."""

    target: str = "web:owner"

    def __init__(self, permissions: Permissions):
        self.permissions = permissions
        self.batch: Batch | None = None
        self.queue: list[Request] = []
        self.window: list[Request] = []
        self.expires: dict[str, float] = {}
        self.tasks: set[asyncio.Task] = set()
        self.window_task: asyncio.Task | None = None
        self.refresh_lock = asyncio.Lock()

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def _send(self, text: str) -> None:
        if self.permissions.notify:
            await self.permissions.notify(text)

    async def submit(self, ctx: Context, tool: Tool, args: dict, key: str, expires: float) -> None:
        req = Request(key=key, ctx=ctx, tool=tool, args=args)
        req.reason_task = self._spawn(self._describe(req))
        self.expires[key] = expires
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

    def accepts(self, session: str, text: str) -> bool:
        return session == self.target and self._valid(self._option(text))

    async def reply(self, session: str, text: str) -> bool:
        number = self._option(text)
        if session != self.target or number is None:
            return False
        async with self.refresh_lock:
            if self.batch is None or not self._valid(number):
                return False
            remaining = await self._sync()
            if not remaining:
                await self._finish()
                return True
            if self.batch.stage == "single":
                return await self._reply_single(remaining[0], number)
            if self.batch.stage == "list":
                if number in {1, 2}:
                    await self._settle_all(remaining, number == 1)
                    return True
                return await self._open_detail(remaining)
            # 过期条目会让分组重新编号；旧编号不能落到没展示过的分组上。
            if self.batch.shown != self._signature(self._groups(remaining)):
                await self._show_detail(remaining)
                return True
            return await self._reply_detail(remaining, number)

    async def _reply_single(self, req: Request, number: int) -> bool:
        if number in {3, 4} and not self._can_grant(req):
            return False
        if number == 4:
            text = await self.permissions.grant_permanent(
                req.ctx.session, req.tool.name, req.tool.risk, req.reason
            )
            await self._send(text)
            await self._refresh_locked()
            return True
        if number == 3:
            await self.permissions.grant_quick(req.ctx.session, req.tool.name, req.args)
            await self._send(f"五分钟内 {req.tool.name} 的审批将自动同意")
            await self._refresh_locked()
            return True
        if number not in {1, 2}:
            return False
        await self._resolve(req, number == 1)
        verb = "同意" if number == 1 else "拒绝"
        await self._send(f"已{verb}：{req.tool.name} {req.key}")
        await self._refresh_locked()
        return True

    async def _settle_all(self, requests: list[Request], allow: bool) -> None:
        for req in requests:
            await self._resolve(req, allow)
        verb = "同意" if allow else "拒绝"
        await self._send(f"已全部{verb}：{len(requests)} 条")
        await self._finish()

    async def _open_detail(self, requests: list[Request]) -> bool:
        groups = self._groups(requests)
        if not groups:
            return False
        await self._send("已打开详情。")
        if len(groups) == 1:
            self.batch.stage = "single"
            await self._show_single(requests)
        else:
            await self._show_detail(requests)
        return True

    async def _reply_detail(self, remaining: list[Request], number: int) -> bool:
        groups = self._groups(remaining)
        if number == 2 * len(groups) + 1:
            self.batch.stage = "single"
            await self._show_single(remaining)
            return True
        index = (number - 1) // 2
        if index >= len(groups):
            return False
        _, name, group = groups[index]
        allow = number % 2 == 1
        for req in group:
            await self._resolve(req, allow)
        verb = "同意" if allow else "拒绝"
        await self._send(f"{name}：已全部{verb} {len(group)} 条")
        await self._refresh_locked()
        return True

    async def _show_detail(self, requests: list[Request]) -> None:
        groups = self._groups(requests)
        if not groups:
            return
        self.batch.stage = "detail"
        self.batch.shown = self._signature(groups)
        await self._send(self._detail_text(groups))

    def _signature(self, groups: list[tuple[str, str, list[Request]]]) -> tuple:
        return tuple((name, len(group)) for _, name, group in groups)

    async def close(self) -> None:
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
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
            if self.batch is None:
                await self._open(live[:PAGE_SIZE])
                self.queue.extend(live[PAGE_SIZE:])
            else:
                self.queue.extend(live)

    async def _open(self, requests: list[Request]) -> None:
        self.batch = Batch(uid()[:10], requests)
        for req in requests:
            row = await self.permissions.store.get("approval", req.key)
            if row and row.get("status") == "pending":
                row["batch"] = self.batch.id
                await self.permissions.store.put("approval", req.key, row, req.ctx.session)
        for req in requests:
            await self._await_reason(req)
        if len(requests) == 1:
            self.batch.stage = "single"
            await self._show_single(requests)
        else:
            self.batch.stage = "list"
            self.batch.shown = tuple(req.key for req in requests)
            await self._send(self._list_text(requests))

    async def _show_single(self, requests: list[Request]) -> None:
        req = requests[0]
        await self._await_reason(req)
        if self.batch:
            self.batch.shown = (req.key,)
        await self._send(self._single_text(req))

    async def _await_reason(self, req: Request) -> None:
        if req.reason or req.reason_task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(req.reason_task), REASON_BUDGET)
        except (TimeoutError, asyncio.CancelledError):
            pass

    async def _is_pending(self, req: Request) -> bool:
        row = await self.permissions.store.get("approval", req.key)
        return bool(
            row
            and row.get("status") == "pending"
            and row.get("expires", 0) >= now().timestamp()
        )

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
        while self.queue:
            live = []
            while self.queue and len(live) < PAGE_SIZE:
                req = self.queue.pop(0)
                if await self._is_pending(req):
                    live.append(req)
            if live:
                await self._open(live)
                return

    async def _resolve(self, req: Request, allow: bool) -> None:
        try:
            await self.permissions.resolve(req.key, allow)
        except PublicError:
            return
        if self.batch:
            self.batch.settled.add(req.key)

    async def _describe(self, req: Request) -> None:
        req.reason = await self._reason(req)

    async def _reason(self, req: Request) -> str:
        llm = self.permissions.llm
        if llm is None:
            return self._fallback(req)
        try:
            history = await self.permissions.store.history(
                req.ctx.session, limit=6, include_commands=False
            )
            request = canonical(
                {
                    "tool": req.tool.name,
                    "description": req.tool.description,
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
                    REASON_SYSTEM,
                    [{"role": "user", "content": request}],
                    [],
                    settings=self.permissions.config.value,
                )
            lines = [line.strip() for line in reply.text.splitlines() if line.strip()]
            if lines and lines[0].startswith("要做"):
                return "\n".join(lines[:2])
        except Exception:
            pass
        return self._fallback(req)

    def _fallback(self, req: Request) -> str:
        return (
            f"要做：{req.tool.name} {brief(canonical(req.args), 160)}\n"
            "为什么：（说明生成失败，请按上面的参数判断）"
        )

    def _can_grant(self, req: Request) -> bool:
        if req.ctx.background:
            return False
        return not (
            req.tool.name == "shell"
            and SENSITIVE_FILE.search(str(req.args.get("command", "")))
        )

    def _single_text(self, req: Request) -> str:
        lines = [
            f"需要确认：{req.tool.name}",
            *(req.reason or self._fallback(req)).splitlines(),
            f"参数：{brief(canonical(req.args), 300)}",
            "回数字：",
            "1 同意",
            "2 拒绝",
        ]
        if self._can_grant(req):
            lines.append(f"3 五分钟内 {req.tool.name} 都同意")
            warning = "（⚠ 高危，慎重）" if req.tool.risk in {"sensitive", "dangerous"} else ""
            lines.append(f"4 以后 {req.tool.name} 都同意，不再问{warning}")
        else:
            lines.append("（本次命令涉及密钥文件或来自后台会话，只提供 1/2）")
        return "\n".join(lines)

    def _list_text(self, requests: list[Request]) -> str:
        ordered = sorted(
            requests,
            key=lambda req: (RISK_ORDER.get(req.tool.risk, 9), req.tool.name, req.key),
        )
        lines = [f"有 {len(ordered)} 条待确认："]
        for index, req in enumerate(ordered, 1):
            summary = (req.reason or self._fallback(req)).splitlines()[0]
            summary = summary.removeprefix("要做：").strip()
            lines.append(
                f"{index}. {req.tool.name}（{RISK_LABELS.get(req.tool.risk, req.tool.risk)}）— {brief(summary, 30)}"
            )
        lines += ["回数字：", "1 全部同意", "2 全部拒绝", "3 看详情"]
        return "\n".join(lines)

    def _detail_text(self, groups: list[tuple[str, str, list[Request]]]) -> str:
        lines = ["详情，按类型分："]
        for risk, name, group in groups:
            lines.append(f"[{RISK_LABELS.get(risk, risk)} · {name}] {len(group)} 条")
        lines.append("回数字：")
        for index, (_, name, _) in enumerate(groups):
            lines.append(f"{2 * index + 1} {name} 全部同意")
            lines.append(f"{2 * index + 2} {name} 全部拒绝")
        lines.append(f"{2 * len(groups) + 1} 逐条处理")
        return "\n".join(lines)
