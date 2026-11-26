"""Shared execution limits and context projection for tool-driven turns."""

from __future__ import annotations

import asyncio
import json
from enum import StrEnum

from shiritaibot.agent.context import context_chars
from shiritaibot.core import PublicError, canonical, fingerprint
from shiritaibot.i18n import t


class TurnState(StrEnum):
    PREPARING = "preparing"
    RUNNING = "running"
    PRUNING = "pruning"
    COMPACTING = "compacting"
    WRAPPING_UP = "wrapping_up"
    COMPLETED = "completed"
    WAITING_INPUT = "waiting_input"
    BUDGET_STOPPED = "budget_stopped"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TurnLimit(Exception):
    pass


class TurnExecution:
    def __init__(self, cfg, llm, session, *, seconds=None, steps=None, exhausted=None):
        self.cfg, self.llm, self.session = cfg, llm, session
        self.state = TurnState.PREPARING
        self.steps = 0
        self.max_steps = min(cfg.policy.max_steps, steps) if steps is not None else cfg.policy.max_steps
        seconds = min(cfg.policy.max_turn_seconds, seconds) if seconds is not None else cfg.policy.max_turn_seconds
        self.deadline = asyncio.get_running_loop().time() + seconds
        self.soft_deadline = self.deadline - min(60, seconds / 5)
        self.exhausted = exhausted
        self.compactions = 0
        self.summary = ""
        self.through = 0
        self.last_repeat = ""
        self.repeat_count = 0

    def check(self):
        if self.exhausted and self.exhausted():
            raise TurnLimit("goal")
        if asyncio.get_running_loop().time() >= self.soft_deadline:
            raise TurnLimit("time")

    async def work(self, action, *args, **kwargs):
        self.check()
        timer = asyncio.timeout_at(self.soft_deadline)
        try:
            async with timer:
                return await action(*args, **kwargs)
        except TimeoutError:
            if timer.expired():
                raise TurnLimit("time") from None
            raise

    def reserve_step(self, *, tool_free=False):
        self.check()
        if self.steps >= self.max_steps - (0 if tool_free else 1):
            raise TurnLimit("steps")
        self.steps += 1

    def observe_result(self, function, output):
        signature = fingerprint([function, output])
        self.repeat_count = self.repeat_count + 1 if signature == self.last_repeat else 1
        self.last_repeat = signature
        if self.repeat_count == 3:
            try:
                value = json.loads(output)
            except ValueError:
                value = output
            if not isinstance(value, dict):
                value = {"output": value}
            return canonical({**value, "warning": t("agent.execution.repeat")})
        return output

    async def reduce(self, history, start, anchors, checkpoint):
        self.state = TurnState.RUNNING
        base = self.cfg.policy.max_context_chars
        if context_chars(history) + len(anchors) < base * 1.2:
            return
        self.state = TurnState.PRUNING
        steps = [i for i, m in enumerate(history) if m.get("tool_calls")]
        cutoff = steps[-3] if len(steps) >= 3 else (steps[0] if steps else len(history))
        for i, message in enumerate(history[:cutoff]):
            if message["role"] != "tool":
                continue
            output = message["content"]
            if not isinstance(output, str) or len(output) <= 1000:
                continue
            try:
                value = json.loads(output)
            except ValueError:
                value = None
            artifact = value.get("artifact") if isinstance(value, dict) else None
            history[i] = {
                **message,
                "content": canonical({
                    "preview": output[:500] + " … " + output[-250:],
                    **({"artifact": artifact, "read_with": "artifact_read"} if artifact else {}),
                }),
            }
        size = context_chars(history) + len(anchors)
        if size < base * 1.6:
            self.state = TurnState.RUNNING
            return
        if self.compactions >= 3:
            raise TurnLimit("context")
        older = history[start:cutoff]
        if not any(m["role"] != "user" for m in older):
            if size >= base * 2:
                raise TurnLimit("context")
            self.state = TurnState.RUNNING
            return
        self.state = TurnState.COMPACTING
        self.compactions += 1
        cfg = self.cfg.model_copy(deep=True)
        cfg.routes["light"] = list(dict.fromkeys(filter(None, [
            cfg.context.compression_model, *cfg.routes.get("light", [])
        ])))
        material = [
            {k: v for k, v in m.items() if not k.startswith("_")}
            for m in older if m["role"] != "user" and not m.get("_work_summary")
        ]
        try:
            reply = await self.work(
                self.llm.complete, "light", t("agent.execution.compact"),
                [{"role": "user", "content": canonical({"previous": self.summary, "steps": material})}],
                [], settings=cfg, scope=self.session, tool_free=True,
            )
            if not reply.text.strip() or reply.calls:
                raise PublicError("agent.context.compressFailed")
        except PublicError:
            if cfg.context.compression_failure == "error":
                raise
            if size >= base * 2 or self.compactions >= 3:
                raise TurnLimit("context") from None
            self.state = TurnState.RUNNING
            return
        summary = reply.text.strip()[: max(1000, min(6000, base // 3))]
        through = max((m.get("_seq", 0) for m in older if m["role"] != "user"), default=0)
        projected = [
            *history[:start],
            *(m for m in older if m["role"] == "user" and not m.get("_work_summary")),
            summary_message(summary),
            *history[cutoff:],
        ]
        await checkpoint(projected, summary, max(through, self.through))
        history[:] = projected
        self.summary, self.through = summary, max(through, self.through)
        if context_chars(history) + len(anchors) >= base * 2:
            raise TurnLimit("context")
        self.state = TurnState.RUNNING

    async def wrap(self, reason, route, system, history, repair):
        self.state = TurnState.WRAPPING_UP
        if self.steps >= self.max_steps or (self.exhausted and self.exhausted()):
            self.state = TurnState.BUDGET_STOPPED
            return t("agent.execution.localHandoff")
        self.steps += 1
        limit = self.cfg.policy.max_context_chars
        # Retain the instructions and the tail of the evidence even when the
        # ordinary provider history no longer fits.
        evidence = []
        remaining = limit
        for item in reversed(repair(history, None)):
            preview = str(item.get("_original_content", item.get("content", "")))
            preview = preview[: min(remaining, max(1000, limit // 4))]
            evidence.append({"role": item["role"], "content": preview})
            remaining -= len(preview) + 40
            if remaining <= 0:
                break
        original = next((m for m in reversed(history) if m.get("_turn_input")), {})
        material = {
            "reason": reason,
            "instruction": str(original.get("_original_content", original.get("content", "")))[:limit],
            "evidence": list(reversed(evidence)),
        }
        async with asyncio.timeout_at(self.deadline):
            reply = await self.llm.complete(
                route, system + "\n\n" + t("agent.execution.wrap"),
                [{"role": "user", "content": canonical(material)}], [],
                settings=self.cfg, scope=self.session, tool_free=True,
            )
        if reply.calls or not reply.text.strip():
            raise PublicError("agent.execution.wrapFailed")
        self.state = TurnState.BUDGET_STOPPED
        return reply.text


def summary_message(summary):
    return {
        "role": "user",
        "content": t("agent.execution.summary", summary=summary),
        "_work_summary": True,
    }
