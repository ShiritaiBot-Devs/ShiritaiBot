"""Emotion indicator: rule gate, decision-model judgment, state, and prompt fragment.

A turn snapshots the state into its `<context>`, then schedules judgment in the
background, so a judgment only shows up from the next turn and never delays a reply.
Failures are swallowed: the bot answers exactly as it would without emotion.
"""

from __future__ import annotations

import asyncio
import json

from shiritaibot.core import Context, ExternalEvent, now
from shiritaibot.core.logs import brief, get_logger
from shiritaibot.emotion import rules, state
from shiritaibot.emotion.judge import SITUATION_FLOOR, EmotionJudge
from shiritaibot.emotion.prompt import render
from shiritaibot.i18n import Text
from shiritaibot.prompts import prompt as prompt_text

JUDGE_TIMEOUT = 30
CARE_KIND = "emotion_care"

log = get_logger("emotion.log.source")


class EmotionManager:
    def __init__(self, store, llm, config, emit=None):
        self.store, self.llm, self.config, self.emit = store, llm, config, emit
        self.tasks: set[asyncio.Task] = set()

    async def _signals(self, ctx: Context) -> tuple[list[str], bool]:
        history = await self.store.history(ctx.session, limit=12, include_commands=False)
        recent: list[str] = []
        if not ctx.group:
            recent = [
                message.get("_original_content") or message.get("content", "")
                for message in history
                if message.get("role") == "user"
            ][-3:]
        failed = False
        for message in history:
            if message.get("role") != "tool":
                continue
            try:
                body = json.loads(message.get("content", ""))
            except (TypeError, ValueError):
                continue
            if isinstance(body, dict) and set(body) == {"error"}:
                failed = True
        return recent, failed

    async def observe(self, ctx: Context, text: str) -> None:
        if not self.config.value.emotion.enabled or ctx.background:
            return
        try:
            recent, failed = await self._signals(ctx)
        except Exception as exc:
            log.debug("emotion.log.signalFailed", exc=exc)
            recent, failed = [], False
        task = asyncio.create_task(self.process(ctx, text, recent, failed))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def process(
        self,
        ctx: Context,
        text: str,
        recent: list[str] | None = None,
        failed: bool | None = None,
    ) -> None:
        try:
            cfg = self.config.value.with_session_routes(ctx.session, deep=True)
            if not cfg.emotion.enabled:
                return
            if recent is None:
                recent, failed = await self._signals(ctx)
            hit = await rules.screen(text, recent, bool(failed))
            if not hit.hit:
                return
            judge = EmotionJudge(self.llm, self.config)
            judgment = None
            if judge.usable(cfg):
                try:
                    async with asyncio.timeout(JUDGE_TIMEOUT):
                        judgment = await judge.judge(cfg, text, recent, hit)
                except Exception as exc:
                    log.debug("emotion.log.judgeFailed", exc=exc)
                    return
                if judgment is None:
                    return
            if judgment is not None:
                semantic = judgment.semantic
                affect, impact = judgment.affect, judgment.impact
                situation, source = judgment.situation, judgment.source
            else:
                semantic, affect, impact, situation, source = (
                    None,
                    rules.expected_affect(hit),
                    0.0,
                    "",
                    "rules",
                )
            raw = state.combine(semantic, hit.level, hit.negativity, cfg.emotion)
            at = now().timestamp()
            keys = state.scope_keys(ctx)
            previous_by_key = {key: await self.store.get(state.KIND, key) for key in keys}
            # Only summarize situation when the merged state will actually keep it.
            if (
                judgment is not None
                and judgment.semantic >= SITUATION_FLOOR
                and raw >= state.threshold(cfg.emotion)
                and any(
                    raw
                    >= (
                        state.decay(
                            float(prev.get("value", 0.0)),
                            float(prev.get("updated", 0.0)),
                            cfg.emotion.persistence,
                            at,
                        )
                        if prev
                        else 0.0
                    )
                    for prev in previous_by_key.values()
                )
            ):
                try:
                    async with asyncio.timeout(JUDGE_TIMEOUT):
                        situation = await judge.situation(cfg, text, recent)
                except Exception as exc:
                    log.debug("emotion.log.situationFailed", exc=exc)
                    situation = ""
            for key in keys:
                previous = previous_by_key[key]
                updated = state.merge(
                    previous,
                    raw,
                    affect=affect,
                    impact=impact,
                    situation=situation,
                    source=source,
                    cfg=cfg.emotion,
                    at=at,
                )
                if updated is None:
                    continue
                await self.store.put(state.KIND, key, updated, ctx.session)
                if key == "owner":
                    await self._care(cfg, updated, at)
                if cfg.emotion.developer:
                    log.log(
                        Text("emotion.log.judged", key=key, value=updated['value'], source=updated['source'], affect=updated['affect'], brief=brief(list(hit.categories)))
                    )
        except Exception as exc:
            log.debug("emotion.log.failed", exc=exc)

    async def _care(self, cfg, updated: dict, at: float) -> None:
        if self.emit is None or cfg.emotion.initiative < 0.8 or updated["value"] < 0.75:
            return
        last = await self.store.get(CARE_KIND, "owner")
        if last and at - float(last.get("at", 0)) < cfg.proactive.throttle_seconds:
            return
        await self.emit(
            ExternalEvent(
                source="情绪",  # i18n: ok
                kind="care",
                dedup_key=f"emotion:{int(at // 600)}",
                title=prompt_text("EMOTION_PROACTIVE", value=updated['value']),
                payload={"scope": "owner"},
                urgency="low",
            )
        )
        await self.store.put(CARE_KIND, "owner", {"at": at})

    async def context(self, ctx: Context, cfg) -> str:
        if not cfg.emotion.enabled:
            return ""
        try:
            row = await self.store.get(state.KIND, state.read_key(ctx))
        except Exception as exc:
            log.debug("emotion.log.stateFailed", exc=exc)
            return ""
        if not row:
            return ""
        at = now().timestamp()
        value = state.decay(
            float(row.get("value", 0.0)),
            float(row.get("updated", 0.0)),
            cfg.emotion.persistence,
            at,
        )
        return render(
            group=ctx.group,
            value=value,
            affect=row.get("affect", ""),
            situation=row.get("situation", ""),
            updated=float(row.get("updated", 0.0)),
            tone=cfg.emotion.tone,
            at=at,
        )

    async def close(self) -> None:
        tasks, self.tasks = list(self.tasks), set()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
