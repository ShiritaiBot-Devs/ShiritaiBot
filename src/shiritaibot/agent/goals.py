from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field, replace

from shiritaibot.agent.execution import TurnState
from shiritaibot.agent.plan import clear_plan, read_plan
from shiritaibot.core import Context, PublicError, canonical, now, uid
from shiritaibot.i18n import t

TERMINAL = {"completed", "cancelled"}
SUBAGENT_RESULTS = {"completed", "failed", "cancelled", "interrupted", "needs_input"}
BUSY = {"active", "evaluating", "continuing"}
GOAL_USAGE: ContextVar[Callable[[int], Awaitable[None]] | None] = ContextVar("goal_usage", default=None)
BUDGETS = {
    "steps": ("step_budget", "total_steps_used"),
    "seconds": ("time_budget", "total_seconds_used"),
    "tokens": ("token_budget", "tokens_used"),
    "continuations": ("max_continuations", "continuations_used"),
}


def remaining(goal, unit):
    budget, used = BUDGETS[unit]
    return max(0, goal[budget] - goal[used])


def available(goal, *, automatic=False):
    return all(remaining(goal, unit) > 0 for unit in BUDGETS if automatic or unit != "continuations")


def goal_context(ctx):
    # Re-resolve permissions in the actor. Keep the original restrictions, but
    # discard fields that belong to the triggering message or turn.
    return asdict(replace(
        ctx, goal_id="", turn="", tool_call_id="", web_message_ids=(),
        reply_anchor=None, group_wake="",
    ))


@dataclass
class GoalAttempt:
    controller: Goals
    scope: str
    goal: dict
    started: float
    seen: dict = field(default_factory=dict)
    synthetic: bool = False

    async def charge(self, tokens):
        if tokens:
            self.goal["tokens_used"] += tokens
            await self.controller.charge(self.scope, self.goal["goal_id"], tokens)


class Goals:
    def __init__(self, runtime):
        self.runtime = runtime
        self.store = runtime.store
        self.locks: dict[str, asyncio.Lock] = {}
        self.stopping = False

    def lock(self, session):
        return self.locks.setdefault(session, asyncio.Lock())

    async def get(self, session):
        return await self.store.get("session_goals", session)

    async def save(self, session, goal):
        goal["updated_at"] = now().isoformat()
        await self.store.put("session_goals", session, goal, session)

    @staticmethod
    def authorized(goal, ctx):
        return goal["origin"]["actor"] == ctx.actor and goal["origin"]["memory_profile"] == ctx.memory_profile

    async def control(self, ctx, argument):
        session = ctx.session
        action, _, extra = argument.partition(" ")
        action = action.casefold()
        if ctx.background or ctx.interject or ctx.routine_event:
            raise PublicError("commands.goal.unavailable")
        async with self.lock(session):
            goal = await self.get(session)
            if goal and goal["status"] not in TERMINAL and not self.authorized(goal, ctx):
                raise PublicError("commands.goal.unavailable")
            if not action or action == "status":
                if not goal or not self.authorized(goal, ctx):
                    return t("commands.goal.empty")
                return t(
                    "commands.goal.status", objective=goal["objective"],
                    status=t(f"commands.goal.states.{goal['status']}"),
                    steps=remaining(goal, "steps"), seconds=int(remaining(goal, "seconds")),
                    tokens=remaining(goal, "tokens"), continuations=remaining(goal, "continuations"),
                )
            if action in {"pause", "clear", "resume"}:
                if not goal or goal["status"] in TERMINAL:
                    return t("commands.goal.empty")
                if action == "resume":
                    if goal["status"] not in {"paused", "budget_limited"}:
                        raise PublicError("commands.goal.alreadyRunning")
                    for option in extra.split():
                        unit, _, value = option.partition("=")
                        if unit not in BUDGETS or not value.isascii() or not value.isdecimal() or len(value) > 9:
                            raise PublicError("commands.goal.budgetSyntax")
                        goal[BUDGETS[unit][0]] += int(value)
                    if not available(goal):
                        raise PublicError("commands.goal.budgetSyntax")
                    goal["status"] = "continuing"
                    goal["manual"] = True
                    goal["feedback"] = ""
                    await self.save(session, goal)
                    self.wake(goal, session)
                    return t("commands.goal.resumed")
                if extra.strip():
                    raise PublicError("commands.goal.usage")
                goal["status"] = "paused" if action == "pause" else "cancelled"
                await self.save(session, goal)
            else:
                if ctx.mode != "agent":
                    raise PublicError("commands.goal.agentMode")
                if goal and goal["status"] not in TERMINAL:
                    raise PublicError("commands.goal.exists")
                policy = self.runtime.config.value.policy
                goal = {
                    "goal_id": uid(), "objective": argument, "status": "continuing",
                    "origin": goal_context(ctx), "manual": True, "feedback": "",
                    "pending_subagents": {}, "subagent_inbox": {},
                    "max_continuations": 8, "continuations_used": 0,
                    "step_budget": policy.max_steps * 9, "total_steps_used": 0,
                    "time_budget": policy.max_turn_seconds * 9, "total_seconds_used": 0,
                    "token_budget": 1_000_000, "tokens_used": 0,
                }
                await clear_plan(self.store, session, ctx.memory_profile)
                await self.save(session, goal)
                self.wake(goal, session)
                return t("commands.goal.created", objective=argument)
        assert goal is not None
        await self.stop_actor(session, goal["goal_id"])
        if action == "clear":
            await self.archive(session, goal)
        return t("commands.goal.paused" if action == "pause" else "commands.goal.cleared")

    async def stop_actor(self, session, goal_id):
        actor = self.runtime.actors.get(session)
        if actor is None:
            return
        if actor.goal_wake and actor.goal_wake[0].goal_id == goal_id:
            actor.goal_wake = None
        if actor.context and actor.context.goal_id == goal_id and actor.current:
            actor.current.cancel()
            await asyncio.gather(actor.current, return_exceptions=True)

    async def cancel(self, ctx, *, owner=False):
        async with self.lock(ctx.session):
            goal = await self.get(ctx.session)
            if not goal or goal["status"] in TERMINAL or not (owner or self.authorized(goal, ctx)):
                return
            goal["status"] = "cancelled"
            await self.save(ctx.session, goal)
        await self.stop_actor(ctx.session, goal["goal_id"])
        await self.archive(ctx.session, goal)

    async def archive(self, session, goal):
        async with self.lock(session):
            current = await self.get(session)
            if current and current["goal_id"] == goal["goal_id"] and current["status"] in TERMINAL:
                await clear_plan(
                    self.store, session, goal["origin"]["memory_profile"], archive=goal["goal_id"]
                )
                await self.store.put("goal_archive", goal["goal_id"], current, session)

    def wake(self, goal, session):
        if self.stopping:
            return
        ctx = Context(**{**goal["origin"], "mode": "agent", "goal_id": goal["goal_id"]})
        actor = self.runtime.actor_for(session)
        actor.goal_wake = (ctx, "")
        actor.wake.set()

    async def begin(self, actor, ctx, text):
        if ctx.background or ctx.interject or ctx.routine_event:
            return ctx, text, None
        async with self.lock(ctx.session):
            goal = await self.get(ctx.session)
            allowed = (
                goal and self.authorized(goal, ctx) and (
                    (ctx.goal_id == goal["goal_id"] and goal["status"] == "continuing")
                    or (not ctx.goal_id and goal["status"] in {"continuing", "waiting_subagent"})
                )
            )
            if not allowed:
                return ctx, text, None
            assert goal is not None
            settings = self.runtime.config.value
            if (
                ctx.mode != "agent" or not settings.adapter_context_valid(ctx)
                or settings.session_memory(ctx.session) != ctx.memory_profile
            ):
                goal["status"] = "paused"
                await self.save(ctx.session, goal)
                return ctx, text, None
            automatic = bool(ctx.goal_id and not goal.pop("manual", False))
            if not available(goal, automatic=automatic):
                goal["status"] = "budget_limited"
                await self.save(ctx.session, goal)
                return ctx, text, None
            if automatic:
                goal["continuations_used"] += 1
            goal["status"] = "active"
            await self.save(ctx.session, goal)
            actor.goal_wake = None
            ctx = replace(ctx, mode="agent", goal_id=goal["goal_id"])
            actor.context = ctx
            synthetic = not text
            if synthetic:
                text = t("agent.goal.continue", feedback=goal.get("feedback", ""))
            return ctx, text, GoalAttempt(self, ctx.session, goal, time.monotonic(), synthetic=synthetic)

    async def abandon_attempt(self, attempt):
        async with self.lock(attempt.scope):
            goal = await self.get(attempt.scope)
            if goal and goal["goal_id"] == attempt.goal["goal_id"] and goal["status"] in {"active", "evaluating"}:
                goal["status"] = "paused"
                await self.save(attempt.scope, goal)

    async def charge(self, session, goal_id, tokens):
        async with self.lock(session):
            goal = await self.get(session)
            if goal and goal["goal_id"] == goal_id and goal["status"] not in TERMINAL:
                goal["tokens_used"] += tokens
                await self.save(session, goal)

    async def checkpoint_steps(self, attempt, steps):
        async with self.lock(attempt.scope):
            goal = await self.get(attempt.scope)
            if goal and goal["goal_id"] == attempt.goal["goal_id"]:
                goal["total_steps_used"] = max(
                    goal["total_steps_used"], attempt.goal["total_steps_used"] + steps
                )
                await self.save(attempt.scope, goal)

    async def results(self, attempt):
        async with self.lock(attempt.scope):
            goal = await self.get(attempt.scope)
            if not goal or goal["goal_id"] != attempt.goal["goal_id"]:
                return ""
            attempt.goal["tokens_used"] = goal["tokens_used"]
            inbox = goal["subagent_inbox"]
            attempt.seen = {key: item["round"] for key, item in inbox.items() if item["fresh"]}
            if not inbox and not goal["pending_subagents"]:
                return ""
            return t("agent.goal.subagents", data=canonical({
                "pending": list(goal["pending_subagents"]), "results": list(inbox.values()),
            }))

    async def ack_results(self, attempt):
        if not attempt.seen:
            return
        async with self.lock(attempt.scope):
            goal = await self.get(attempt.scope)
            if goal and goal["goal_id"] == attempt.goal["goal_id"]:
                for key, round_number in attempt.seen.items():
                    item = goal["subagent_inbox"].get(key)
                    if item and item["round"] == round_number:
                        item["fresh"] = False
                await self.save(attempt.scope, goal)

    async def judge(self, ctx, cfg, goal, history, final):
        steps = await read_plan(self.store, ctx.session, ctx.memory_profile)
        evidence = [
            {"role": item["role"], "content": str(item.get("content", ""))[:3000]}
            for item in history[-18:]
        ]
        material = canonical({"objective": goal["objective"], "plan": steps, "evidence": evidence, "reply": final})
        routes = cfg.model_copy(deep=True)
        routes.routes["light"] = list(dict.fromkeys(
            [*cfg.routes.get("light", []), *cfg.routes.get("router", [])]
        ))
        async with asyncio.timeout(min(60, remaining(goal, "seconds"))):
            reply = await self.runtime.llm.complete(
                "light", t("agent.goal.judge"), [{"role": "user", "content": material}],
                [], settings=routes, scope=ctx.session, tool_free=True,
            )
        data = json.loads(reply.text.strip().removeprefix("```json").removesuffix("```").strip())
        if reply.calls or not isinstance(data, dict) or data.get("verdict") not in ("MET", "NOT_MET", "BLOCKED"):
            raise ValueError("invalid goal verdict")
        return data["verdict"], str(data.get("feedback", ""))[:2000]

    async def finish(self, actor, ctx, cfg, attempt, state, steps, history, final):
        session = ctx.session
        await self.checkpoint_steps(attempt, steps)
        elapsed = time.monotonic() - attempt.started
        async with self.lock(session):
            goal = await self.get(session)
            if not goal or goal["goal_id"] != ctx.goal_id:
                return
            goal["total_seconds_used"] += elapsed
            if goal["status"] != "active":
                await self.save(session, goal)
                return
            report = ""
            if state in {TurnState.CANCELLED, TurnState.FAILED, TurnState.WAITING_INPUT}:
                goal["status"] = "paused"
            elif not available(goal):
                goal["status"] = "budget_limited"
                report = t("agent.goal.budget")
            elif state == TurnState.COMPLETED and goal["pending_subagents"] and not self.ready(goal):
                goal["status"] = "waiting_subagent"
            elif state == TurnState.BUDGET_STOPPED or self.ready(goal) or actor.steering:
                if self.continue_goal(goal, automatic=not actor.steering, feedback=final):
                    self.wake(goal, session)
                else:
                    report = t("agent.goal.budget")
            else:
                goal["status"] = "evaluating"
            await self.save(session, goal)
        if goal["status"] != "evaluating":
            return report
        judging = time.monotonic()
        task = actor.goal_judge = asyncio.create_task(self.judge(ctx, cfg, goal, history, final))
        try:
            verdict, feedback = await task
        except asyncio.CancelledError:
            if (current := asyncio.current_task()) and current.cancelling():
                raise
            verdict, feedback = "NOT_MET", ""
        except Exception:
            verdict, feedback = "BLOCKED", t("agent.goal.judgeFailed")
        finally:
            actor.goal_judge = None
        async with self.lock(session):
            goal = await self.get(session)
            if not goal or goal["goal_id"] != ctx.goal_id:
                return
            goal["total_seconds_used"] += time.monotonic() - judging
            if goal["status"] != "evaluating":
                await self.save(session, goal)
                return
            if actor.steering:
                self.continue_goal(goal, automatic=False, feedback="")
            elif verdict == "MET":
                goal["status"] = "completed"
                report = t("agent.goal.completed")
            elif verdict == "BLOCKED":
                goal["status"] = "paused"
                report = t("agent.goal.paused", feedback=feedback)
            elif self.continue_goal(goal, feedback=feedback):
                self.wake(goal, session)
            else:
                report = t("agent.goal.budget")
            await self.save(session, goal)
        if goal["status"] == "completed":
            await self.archive(session, goal)
        return report

    @staticmethod
    def continue_goal(goal, *, automatic=True, feedback=""):
        if not available(goal, automatic=automatic):
            goal["status"] = "budget_limited"
            return False
        goal.update(status="continuing", feedback=feedback, manual=not automatic)
        return True

    @staticmethod
    def ready(goal):
        inbox = goal["subagent_inbox"]
        return any(
            item["fresh"] and item["status"] == "needs_input" for item in inbox.values()
        ) or (not goal["pending_subagents"] and any(item["fresh"] for item in inbox.values()))

    async def bind(self, ctx, row):
        if not ctx.goal_id:
            return
        async with self.lock(ctx.session):
            goal = await self.get(ctx.session)
            if goal and goal["goal_id"] == ctx.goal_id and goal["status"] == "active":
                goal["pending_subagents"][row["id"]] = row.get("round", 0)
                goal["subagent_inbox"].pop(row["id"], None)
                await self.save(ctx.session, goal)

    async def subagent_result(self, session, row):
        async with self.lock(session):
            goal = await self.get(session)
            key = row["id"]
            if not goal or goal["status"] in TERMINAL:
                return False
            round_number = row.get("round", 0)
            if goal["subagent_inbox"].get(key, {}).get("round") == round_number:
                return True
            if goal["pending_subagents"].get(key) != round_number:
                return False
            if self.stopping or row["status"] not in SUBAGENT_RESULTS:
                return True
            goal["pending_subagents"].pop(key)
            goal["subagent_inbox"][key] = {
                "run_id": key, "status": row["status"], "round": round_number,
                "result": row.get("result", "")[-1600:], "question": row.get("question", ""),
                "fresh": True,
            }
            wake = goal["status"] == "waiting_subagent" and self.ready(goal)
            if wake:
                wake = self.continue_goal(goal)
            await self.save(session, goal)
            if wake:
                self.wake(goal, session)
            return True

    async def recover(self):
        while rows := await self.store.with_status("session_goals", tuple(BUSY)):
            for goal in rows:
                goal["status"] = "paused"
                await self.save(goal["scope"], goal)

    async def watch(self):
        while True:
            rows = await self.store.query(
                "SELECT data FROM records WHERE kind='session_goals' "
                "AND json_extract(data,'$.status') IN ('waiting_subagent','active','paused','budget_limited')"
            )
            for row in rows:
                goal = json.loads(row["data"])
                for key, round_number in goal["pending_subagents"].items():
                    run = await self.store.get("subagent_run", key)
                    if run is None:
                        run = {"id": key, "round": round_number, "status": "interrupted"}
                    await self.subagent_result(goal["scope"], run)
            await asyncio.sleep(20)
