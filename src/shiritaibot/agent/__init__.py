from __future__ import annotations

import asyncio
import json
import re
import time
from collections import Counter, deque
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from functools import partial
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from shiritaibot.agent.commands import command
from shiritaibot.agent.context import ContextManager, context_chars, dialogue_message
from shiritaibot.api.event import AgentResult, LLMRequest, LLMResponse, Turn
from shiritaibot.config import is_feishu_session
from shiritaibot.core import (
    Context,
    InteractionRequired,
    PublicError,
    canonical,
    current_reply,
    fingerprint,
    now,
    record_usage_for,
    uid,
)
from shiritaibot.core.logs import Logger, brief, describe_session, get_logger
from shiritaibot.core.text import clip_image_text
from shiritaibot.i18n import Locale, Text, normalize, t, use_locale
from shiritaibot.meme.inject import meme_prompt
from shiritaibot.memory.judge import recall_question
from shiritaibot.memory.recall import MemoryRecall
from shiritaibot.proactive import (
    PREPARE_SESSION,
    REPORT_TOOL,
    LoopResult,
    parse_loop_result,
    report_spec,
)
from shiritaibot.prompts import prompt as prompt_text
from shiritaibot.store import owner_memory_scope

# Minimum Jev probability for Chat; the LLM router is likewise told to prefer Work.
ROUTER_CHAT_THRESHOLD = 0.6
ROUTE_NAMES = {"chat": "Chat", "agent": "Agent Work", "agent_chat": "Agent Chat"}
WORK_HANDOFF = "<handoff_to_work/>"


def loop_logger(ctx: Context) -> Logger:
    if ctx.routine_event or ctx.session.startswith(PREPARE_SESSION):
        source = "Proactive Agent"
    else:
        source = t("agent.log.background") if ctx.background else t("agent.log.mainLoop")
    return get_logger(source, describe_session(ctx.session))


def runtime_session(ctx: Context) -> str:
    return PREPARE_SESSION + ctx.routine_event if ctx.routine_event else ctx.session


def tool_outcome(output: str) -> tuple[bool, str]:
    """(ok, readable summary) of a registry result string."""
    try:
        value = json.loads(output)
    except ValueError:
        return True, brief(output, 100)
    if isinstance(value, dict) and set(value) == {"error"}:
        return False, brief(value["error"], 160)
    if isinstance(value, dict) and isinstance(value.get("ok"), bool):
        if not value["ok"]:
            reason = value.get("message") or value.get("error") or value.get("status") or t("agent.businessFailure")
            return False, brief(reason, 160)
    if isinstance(value, dict) and value.get("truncated") and value.get("artifact"):
        return (
            True,
            prompt_text("RESULT_SAVED", value=value['artifact'][:8], brief=brief(value.get('preview', ''), 80)),
        )
    return True, brief(value, 100) or prompt_text("EMPTY_RESULT")


def repair_history(messages: list[dict], max_chars: int | None) -> list[dict]:
    """Keep complete request/result pairs, including after interrupted turns."""
    result, pending = [], {}
    for message in messages:
        if message.get("_command"):
            continue
        if message["role"] != "tool" and pending:
            result.extend(
                {
                    "role": "tool",
                    "tool_call_id": key,
                    "content": prompt_text("INTERRUPTED_SIDE_EFFECTS"),
                }
                for key in pending
            )
            pending.clear()
        if message["role"] == "tool":
            # 损坏记录（缺 tool_call_id，如旧版审批备注）直接跳过，避免 KeyError 卡死会话。
            call_id = message.get("tool_call_id")
            if call_id is None or call_id not in pending:
                continue
            pending.pop(call_id)
        result.append(message)
        for call in message.get("tool_calls", []):
            pending[call["id"]] = True
    result.extend(
        {"role": "tool", "tool_call_id": key, "content": prompt_text("INTERRUPTED")}
        for key in pending
    )
    # Trim at user boundaries; never send an orphan tool result to a provider.
    while max_chars is not None and len(canonical(result)) > max_chars:
        index = next((i for i, m in enumerate(result[1:], 1) if m["role"] == "user"), None)
        if index is None:
            break
        result = result[index:]
    while result and result[0]["role"] != "user" and not result[0].get("_proactive"):
        result.pop(0)
    if result and result[0].get("_proactive"):
        result.insert(0, {"role": "user", "content": prompt_text("CONTEXT_SENT_NOTICE")})
    return result


def localize(rows: list[dict], zone: ZoneInfo) -> list[dict]:
    """Render stored UTC timestamps as owner-local wall time so the model never reads UTC."""
    result = []
    for row in rows:
        row = dict(row)
        for key in ("created", "updated"):
            value = row.get(key)
            try:
                if isinstance(value, (int, float)):
                    stamp = datetime.fromtimestamp(value, zone)
                elif isinstance(value, str):
                    stamp = datetime.fromisoformat(value).astimezone(zone)
                else:
                    continue
            except (ValueError, OverflowError, OSError):
                continue
            row[key] = stamp.strftime("%Y-%m-%d %H:%M")
        result.append(row)
    return result


def recent_lines(rows: list[dict], zone: ZoneInfo) -> str:
    """Recent per-turn summaries as readable lines with absolute local times."""
    return "\n".join(
        prompt_text("CONTEXT_RECENT_SUMMARY", created=row['created'], summary=row['summary']) for row in localize(rows, zone)
    )


def local_clock(zone: ZoneInfo) -> str:
    local = now().astimezone(zone)
    # The weekday in the session's language (周一 / Monday).
    return local.strftime("%Y-%m-%d %H:%M ") + t(f"tools.weekdays.{local.weekday()}")


def _plain(message: dict) -> str:
    content = message.get("_original_content", message.get("content"))
    return content.split("\n<context")[0].strip() if isinstance(content, str) else ""


_PASS = re.compile(r"^[\s\"“”'‘’]*(?:<pass>|</pass>|/pass|pass)[\s.。!！\"“”'‘’]*$", re.I)  # i18n: ok


def _is_pass(text: str) -> bool:
    return bool(_PASS.match(text.strip()))


def _is_loop_result(text: str) -> bool:
    try:
        parse_loop_result(text)
    except (ValueError, TypeError):
        return False
    return True


def _arguments(value) -> dict:
    """Tool-call arguments as a fresh dict for plugin hooks (malformed JSON → {})."""
    return dict(value) if isinstance(value, dict) else {}


def _report(arguments: str | dict) -> tuple[LoopResult | None, str]:
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
        return LoopResult.model_validate(args), canonical({"ok": True})
    except (ValueError, TypeError):
        return None, canonical({"error": t("agent.reportArgsInvalid")})


class SessionActor:
    def __init__(self, runtime, session: str):
        self.runtime, self.session = runtime, session
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=20)
        self.steering: deque[tuple[Context, str]] = deque()
        self.buffered: list[tuple[Context, str]] = []
        self.ready: deque[list[tuple[Context, str]]] = deque()
        self.first_buffered: float | None = None
        self.deadline = 0.0
        self.wake = asyncio.Event()
        self.current: asyncio.Task | None = None
        self.context: Context | None = None
        self.task = asyncio.create_task(self.run(), name=f"session:{session}")

    async def submit(self, ctx: Context, text: str) -> str:
        # Only a whole message counts, so "stop" inside a sentence never cancels.
        if text.strip().casefold() in {"/stop", "/cancel", "停", "取消", "stop", "cancel"}:  # i18n: ok
            if self.current and self.context and (ctx.owner or ctx.actor == self.context.actor):
                loop_logger(ctx).log(Text("agent.log.cancelled", actor=ctx.actor))
                self.current.cancel()
                self.steering.clear()
            self._discard_buffered(ctx)
            # Also discard queued work when no turn has started yet.
            retained = []
            while not self.queue.empty():
                item = self.queue.get_nowait()
                if not ctx.owner and item[0].actor != ctx.actor:
                    retained.append(item)
            for item in retained:
                self.queue.put_nowait(item)
            if retained:
                self.wake.set()
            return "cancelled"
        if (
            self.current
            and not self.current.done()
            and self.context
            and not ctx.background
            and ctx.actor == self.context.actor
            and ctx.memory_profile == self.context.memory_profile
            and ctx.mode == "agent"
        ):
            self.steering.append((ctx, text))
            loop_logger(ctx).log(Text("agent.log.steered", brief=brief(text, 80)))
            return "steering"
        policy = self.runtime.config.value.policy
        if (
            self.current is None
            and not ctx.background
            and not ctx.routine_event
            and not ctx.interject
            and not ctx.session.startswith("web:")
            and self.queue.empty()
            and policy.debounce_enabled
            and policy.debounce_seconds > 0
        ):
            at = asyncio.get_running_loop().time()
            if self.buffered and (
                self.buffered[-1][0].actor != ctx.actor
                or self.buffered[-1][0].memory_profile != ctx.memory_profile
            ):
                self._finish_batch()
            if not self.buffered:
                self.first_buffered = at
            first = self.first_buffered if self.first_buffered is not None else at
            self.buffered.append((ctx, text))
            # A finished sentence ends the window early; the cap bounds total wait.
            immediate = bool(policy.debounce_suffixes) and text.rstrip().endswith(
                tuple(policy.debounce_suffixes)
            )
            wait = 0 if immediate else policy.debounce_seconds
            self.deadline = min(at + wait, first + policy.debounce_max_seconds)
            loop_logger(ctx).debug(
                Text("agent.log.mergeWindow", count=len(self.buffered), max=max(0.0, self.deadline - at))
            )
            self.wake.set()
            return "buffering"
        try:
            self.queue.put_nowait((ctx, text))
        except asyncio.QueueFull:
            loop_logger(ctx).warning("agent.log.queueFull")
            raise PublicError("agent.queueFull") from None
        self.wake.set()
        return "queued"

    def _finish_batch(self) -> None:
        if not self.buffered:
            return
        self.ready.append(self.buffered)
        self.buffered = []
        self.first_buffered = None
        self.wake.set()

    @staticmethod
    def _merge_batch(batch: list[tuple[Context, str]]) -> tuple[Context, str]:
        return batch[-1][0], "\n".join(text for _, text in batch)

    def _discard_buffered(self, ctx: Context) -> None:
        if ctx.owner:
            self.buffered, self.ready = [], deque()
        else:
            self.buffered = [item for item in self.buffered if item[0].actor != ctx.actor]
            self.ready = deque(batch for batch in self.ready if batch[0][0].actor != ctx.actor)
        if not self.buffered:
            self.first_buffered = None
            self.deadline = 0.0

    async def _next_input(self) -> tuple[Context, str] | None:
        while True:
            if self.ready:
                return self._merge_batch(self.ready.popleft())
            if self.buffered:
                remaining = self.deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    batch, self.buffered = self.buffered, []
                    self.first_buffered = None
                    return self._merge_batch(batch)
                try:
                    await asyncio.wait_for(self.wake.wait(), remaining)
                    self.wake.clear()
                except TimeoutError:
                    pass
                continue
            if not self.queue.empty():
                return self.queue.get_nowait()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=1800)
                self.wake.clear()
            except TimeoutError:
                return None

    async def run(self) -> None:
        try:
            while True:
                item = await self._next_input()
                if item is None:
                    return
                ctx, text = item
                self.context = ctx
                self.current = asyncio.create_task(self.runtime.turn(self, ctx, text))
                try:
                    await self.current
                except asyncio.CancelledError:
                    task = asyncio.current_task()
                    if task and task.cancelling():
                        raise
                except Exception as exc:
                    loop_logger(ctx).error(Text("agent.log.sessionError", name=type(exc).__name__), exc=exc)
                    await self.runtime.store.activity(
                        "actor.failed", type(exc).__name__, self.session
                    )
                finally:
                    self.current = None
                if self.steering:
                    followup = "\n".join(item for _, item in self.steering)
                    ids = tuple(
                        key for origin, _ in self.steering for key in origin.web_message_ids
                    )
                    self.steering.clear()
                    await self.queue.put((replace(ctx, web_message_ids=ids), followup))
                    self.wake.set()
        finally:
            if self.current:
                self.current.cancel()
                await asyncio.gather(self.current, return_exceptions=True)


class AgentRuntime:
    def __init__(
        self,
        config,
        store,
        llm,
        registry,
        memory_for,
        deliver,
        short_term=None,
        emotion=None,
        skills=None,
    ):
        self.config, self.store, self.llm = config, store, llm
        self.registry, self.memory_for, self.deliver = registry, memory_for, deliver
        self.wait_for_interaction: (
            Callable[[str, InteractionRequired, float], Awaitable[None]] | None
        ) = None
        # Turn -> P(the message relies on the past), from the router's decision model.
        self.recall_hints: dict[str, float] = {}
        self.short_term = short_term
        self.emotion = emotion
        self.skills = skills
        self.actors: dict[str, SessionActor] = {}
        # shiritaibot.plugin.core.hooks.HookBus: plugin LLM / agent lifecycle hooks.
        self.hooks: Any = None
        self.submit_locks: dict[str, asyncio.Lock] = {}
        self.context_locks: dict[str, asyncio.Lock] = {}
        self.deliver_group_reply: Callable[[str, str, str], Awaitable[None]] | None = None

    async def session_locale(self, session: str) -> tuple[Locale, bool]:
        """The bot language for ``session`` and whether /lang chose it (docs/I18N.md).

        A /lang override is forced: replies use it whatever language the user writes in.
        Without one, the global default applies and replies follow the user.
        """
        if session.startswith("background:"):
            # Background work reports to the owner, so it speaks the owner chat's language.
            session = self.config.value.owner_notification_session()
        row = await self.store.get("session_locale", session)
        if locale := normalize(row.get("locale") if row else None):
            return locale, True
        return self.config.value.locale, False

    async def handoff(self, session: str, text: str, send) -> None:
        # Append between turns, never between an assistant tool call and its result.
        async with self.context_locks.setdefault(session, asyncio.Lock()):
            await send(session, text)
            await self.store.append(
                session,
                "handoff",
                {"role": "assistant", "content": text, "_proactive": True},
                memory_profile=self.config.value.session_memory(session),
            )

    async def submit(self, ctx: Context, text: str) -> str:
        session = runtime_session(ctx)
        async with self.submit_locks.setdefault(session, asyncio.Lock()):
            return await self._submit(ctx, text, actor_session=session)

    async def _submit(self, ctx: Context, text: str, *, actor_session: str) -> str:
        if not self.config.value.adapter_context_valid(ctx):
            return "ignored"

        if not ctx.background:
            ctx = replace(ctx, memory_profile=self.config.value.session_memory(ctx.session))
        ctx = self.registry.permissions.context(ctx)
        if not ctx.interject and not ctx.routine_event:
            # Observation and scheduled runs leave the active session registration alone.
            await self.store.put(
                "session",
                ctx.session,
                {
                    "mode": ctx.mode,
                    "actor": ctx.actor,
                    "owner": ctx.owner,
                    "group": ctx.group,
                    "background": ctx.background,
                    "tool_policy": ctx.tool_policy,
                    "memory_profile": ctx.memory_profile,
                },
            )
            if not self.config.value.adapter_context_valid(ctx):
                return "ignored"
        if not ctx.routine_event and text.strip().startswith("/"):
            loop_logger(ctx).log(Text("agent.log.command", value=prompt_text("ROLE_OWNER") if ctx.owner else ctx.actor, brief=brief(text, 80)))
            locale, _ = await self.session_locale(ctx.session)
            with record_usage_for(ctx.session), use_locale(locale):
                return await command(self, ctx, text)
        if actor_session not in self.actors or self.actors[actor_session].task.done():
            actor = SessionActor(self, actor_session)
            self.actors[actor_session] = actor

            def cleanup(task):
                if self.actors.get(actor_session) is actor:
                    self.actors.pop(actor_session, None)

            actor.task.add_done_callback(cleanup)
        return await self.actors[actor_session].submit(ctx, text)

    def busy(self, session: str) -> bool:
        actor = self.actors.get(session)
        if actor is None:
            return False
        if actor.current is not None and not actor.current.done():
            return True
        return bool(actor.buffered or not actor.queue.empty())

    async def cancel_reconfigured_sessions(self, previous) -> None:
        cfg = self.config.value
        for session, actor in list(self.actors.items()):
            ctx = actor.context
            if (ctx is not None and not cfg.adapter_context_valid(ctx)) or (
                not session.startswith(("background:", PREPARE_SESSION))
                and (
                    cfg.session_memory(session) != previous.session_memory(session)
                    or cfg.knowledge.library_for(session) != previous.knowledge.library_for(session)
                    or (session in cfg.knowledge.sessions)
                    != (session in previous.knowledge.sessions)
                )
            ):
                self.actors.pop(session, None)
                actor.task.cancel()
                await asyncio.gather(actor.task, return_exceptions=True)

    async def close(self) -> None:
        for actor in self.actors.values():
            actor.task.cancel()
        await asyncio.gather(*(a.task for a in self.actors.values()), return_exceptions=True)
        if self.emotion:
            await self.emotion.close()

    async def group_context(self, ctx: Context, cfg, tools: list[dict]) -> str:
        settings = cfg.group_context
        if (
            not cfg.group_context_enabled(ctx.session)
            or not ctx.session.startswith("group:")
            or ctx.background
        ):
            return ""
        rows = []
        if settings.inject_messages:
            rows = await self.store.group_messages(
                ctx.session, settings.inject_messages + 1, memory_profile=ctx.memory_profile
            )
            # The triggering message is the user turn itself.
            if not ctx.interject:
                for index in range(len(rows) - 1, -1, -1):
                    if rows[index]["sender"] == ctx.actor and rows[index]["triggered"]:
                        rows.pop(index)
                        break
            rows = rows[-settings.inject_messages :]
        zone = ZoneInfo(cfg.proactive.timezone)
        # One long message must not crowd out the rest of the recent conversation.
        cap = min(500, settings.inject_chars // 2)
        lines, used = [], 0
        for row in reversed(rows):
            stamp = datetime.fromtimestamp(row["created"], zone).strftime("%m-%d %H:%M")
            text = clip_image_text(row["text"], cap)
            line = f"[{stamp}] {row['name']}({row['sender']}): {text}"
            if used + len(line) > settings.inject_chars:
                break
            lines.append(line)
            used += len(line) + 1
        lines.reverse()
        can_query = any(t["function"]["name"] == "group_context_query" for t in tools)
        if not lines and not can_query:
            return ""
        block = (
            prompt_text("GROUP_CONTEXT_HEAD")
        )
        block += "\n".join(lines) if lines else prompt_text("GROUP_CONTEXT_EMPTY")
        if can_query:
            block += (
                prompt_text("GROUP_CONTEXT_QUERY_HINT")
            )
        return block + "\n</group_context>\n"

    async def route(self, ctx: Context, cfg, text: str) -> str:
        """Pick the model route: Chat, Agent Work, or (when split) Agent Chat."""
        if ctx.mode != "agent":
            return "chat"
        if (
            ctx.background
            or ctx.routine_event
            or not cfg.agent_split
            or not cfg.routes.get("agent_chat")
        ):
            return "agent"
        if not cfg.routes.get("router"):
            return "agent"
        recent = await self.store.history(
            ctx.session, limit=4, include_commands=False, memory_profile=ctx.memory_profile
        )
        lines = [
            t(
                "common.labeled",
                label=prompt_text("ROLE_USER") if m["role"] == "user" else prompt_text("ROLE_ASSISTANT"),
                value=_plain(m)[:300],
            )
            for m in recent
            if m.get("role") in {"user", "assistant"} and _plain(m)
        ]
        material = (
            (prompt_text("ROUTER_RECENT") + "\n".join(lines) + "\n\n" if lines else "") + prompt_text("ROUTER_NEW") + text[:2000]
        )
        questions: dict = {
            "route": {
                "type": "choice",
                "instructions": prompt_text("ROUTER_TASK"),
                "criteria": {
                    "work": prompt_text("ROUTER_WORK"),
                    "chat": prompt_text("ROUTER_CHAT"),
                },
            }
        }
        if ctx.owner and not ctx.group and cfg.memory.inject:
            # Asked in the same request, so memory recall gets its gate for free.
            questions["recall"] = recall_question(prompt_text("ROUTER_RECALL_SUBJECT"))
        answers = await self.llm.decide("router", material, questions, settings=cfg)
        if answers is not None:
            recall = answers.get("recall")
            if isinstance(recall, dict) and isinstance(recall.get("noul"), (int, float)):
                self.recall_hints[ctx.turn] = float(recall["noul"])
            # Calibrated probabilities: only a clear Chat verdict leaves the Work route.
            answer = answers["route"] if isinstance(answers["route"], dict) else {}
            chat = (answer.get("probabilities") or {}).get("chat", 0)
            route = "agent_chat" if chat >= ROUTER_CHAT_THRESHOLD else "agent"
            loop_logger(ctx).log(
                Text("agent.log.routerDecision", value=ROUTE_NAMES[route], float=float(chat))
            )
            return route
        try:
            reply = await self.llm.complete(
                "router",
                prompt_text("ROUTER_SYSTEM"),
                [{"role": "user", "content": material}],
                [],
                settings=cfg,
            )
        except Exception as exc:
            loop_logger(ctx).warning(
                Text("agent.log.routerFailed", name=type(exc).__name__)
            )
            await self.store.activity("router.failed", type(exc).__name__, ctx.session)
            return "agent"
        answer = reply.text.strip().lower()
        route = "agent_chat" if "chat" in answer and "work" not in answer else "agent"
        loop_logger(ctx).log(Text("agent.log.routerAnswer", value=ROUTE_NAMES[route], brief=brief(answer, 20)))
        return route

    async def turn(self, actor: SessionActor, ctx: Context, text: str) -> None:
        async with self.context_locks.setdefault(runtime_session(ctx), asyncio.Lock()):
            token = current_reply.set(ctx.reply_anchor)
            locale, _ = await self.session_locale(ctx.session)
            try:
                with record_usage_for(ctx.session), use_locale(locale):
                    await self._turn(actor, ctx, text)
            finally:
                current_reply.reset(token)

    async def interjection_candidate(self, ctx: Context, text: str) -> str | None:
        """Generate without acquiring the passive actor, writing chat history or delivering."""
        assert ctx.interject and ctx.group and not ctx.owner
        locale, _ = await self.session_locale(ctx.session)
        with record_usage_for(ctx.session), use_locale(locale):
            return await self._turn(None, ctx, text)

    async def _turn(self, actor: SessionActor | None, ctx: Context, text: str) -> str | None:
        turn_started = now()
        settings = self.config.value
        if not settings.adapter_context_valid(ctx) or (
            not ctx.background and ctx.memory_profile != settings.session_memory(ctx.session)
        ):
            return
        ctx = replace(
            self.registry.permissions.context(ctx), turn=ctx.turn if actor is None else uid()
        )
        execution_session = runtime_session(ctx)
        proactive = execution_session.startswith(PREPARE_SESSION)
        append = partial(
            self.store.append, execution_session, ctx.turn, memory_profile=ctx.memory_profile
        )
        if ctx.interject:
            # A passing remark is one cheap chat turn: no tools, no policy upgrade.
            ctx = replace(ctx, mode="chat", allowed_tools=())
        cfg = settings.with_session_routes(ctx.session, deep=True)
        selected_persona = await self.store.get("session_persona", ctx.session)
        locale, forced = await self.session_locale(ctx.session)
        system = cfg.system_prompt(
            selected_persona.get("persona", "") if selected_persona else "",
            owner=ctx.owner,
            locale=locale,
            forced=forced,
        )
        chat_system = system
        if not ctx.background and ctx.session.startswith(("private:", "group:")):
            if not is_feishu_session(ctx.session):
                system += meme_prompt(cfg)
            else:
                system += prompt_text("FEISHU_SESSION")
                chat_system = system
                file_tool = self.registry.tools.get("feishu_file_send")
                if file_tool and self.registry.permissions.decision(ctx, file_tool) != "deny":
                    system += (
                        prompt_text("FEISHU_FILES")
                    )
        if ctx.interject:
            system += (
                prompt_text("INTERJECT")
            )
        emotion_block = ""
        if self.emotion and ctx.memory_profile == "default":
            emotion_block = await self.emotion.context(ctx, cfg)
            if not ctx.interject and not ctx.routine_event:
                await self.emotion.observe(ctx, text)
        route = await self.route(ctx, cfg, text)
        selected = await self.store.get("session_model", ctx.session)
        selected_model = selected.get("model") if selected else None
        if selected_model:
            cfg.routes[route] = [selected_model]
        log = loop_logger(ctx)
        started = time.monotonic()
        log.log(
            Text("agent.log.start", route=ROUTE_NAMES.get(route, route), models=brief(cfg.routes.get(route) or [t("agent.notConfigured")], 60), brief=brief(text, 80))
        )
        # The ambient block only annotates group history, so the triggering message
        # itself must carry the speaker's identity for the model to know who is talking.
        name, prefix = "", ""
        if ctx.group and not proactive and cfg.group_context.identify_sender:
            if ctx.owner:
                name = cfg.owner_name
            else:
                name = await self.store.group_sender_name(
                    ctx.session, ctx.actor, memory_profile=ctx.memory_profile
                )
            prefix = prompt_text("SPEAKER_TAG", value=name or ctx.actor, actor=ctx.actor)
            text = prefix + text
        body = {
            "status": "running",
            "actor": ctx.actor,
            "started": turn_started.isoformat(),
            "steps": 0,
            "summary_eligible": ctx.summary_eligible and not ctx.routine_event,
            "memory_profile": ctx.memory_profile,
            "memory_scope": owner_memory_scope(ctx.memory_profile)
            if ctx.owner and ctx.mode == "agent" and not ctx.group
            else f"shared:{ctx.memory_profile}"
            if cfg.shared_memory(ctx.memory_profile)
            else ctx.session,
            "loop": "proactive" if proactive or ctx.background else "main",
            "route": route,
            "assistant_name": cfg.assistant_name,
            "owner_name": cfg.owner_name,
        }
        await self.store.put("turn", ctx.turn, body, execution_session)
        hooks = self.hooks
        info = Turn(
            ctx.turn,
            execution_session,
            ctx.actor,
            ctx.owner,
            ctx.group,
            ctx.background or bool(ctx.routine_event),
            ctx.mode,
            route,
            text,
            ctx,
        )
        final = ""
        if hooks is not None:
            await hooks.call("agent_begin", info)

        async def collect_steering() -> list[dict]:
            messages = []
            while actor and actor.steering:
                origin, steering = actor.steering.popleft()
                item = {
                    "role": "user",
                    "content": prefix + prompt_text("STEER_TAG") + steering,
                    "_original_content": steering,
                    "_speaker": cfg.owner_name if ctx.owner else name or ctx.actor,
                }
                if origin.web_message_ids:
                    item["_client_ids"] = list(origin.web_message_ids)
                await append(item)
                messages.append(item)
            return messages

        try:
            async with asyncio.timeout(cfg.policy.max_turn_seconds):
                zone = ZoneInfo(cfg.proactive.timezone)
                blocks = [emotion_block] if emotion_block else []
                private_memory = ctx.owner and ctx.mode == "agent" and not ctx.group
                shared_memory = cfg.shared_memory(ctx.memory_profile)
                if cfg.memory.inject and (private_memory or shared_memory):
                    recall = MemoryRecall(self.memory_for(ctx.memory_profile))
                    blocks.append(
                        await recall.render(
                            ctx,
                            text,
                            cfg.memory.inject_chars,
                            self.recall_hints.pop(ctx.turn, None),
                            shared=shared_memory,
                        )
                    )
                if private_memory and self.short_term:
                    summaries = await self.short_term.recent(
                        limit=5, memory_profile=ctx.memory_profile
                    )
                    blocks.append(recent_lines(summaries, zone))
                user = {
                    "role": "user",
                    "_speaker": cfg.owner_name if ctx.owner else name or ctx.actor,
                    "_original_content": text,
                    "content": text
                    + f"\n<context local_time='{local_clock(zone)}' timezone='{cfg.proactive.timezone}'>\n"
                    + "\n".join(block for block in blocks if block)
                    + "\n</context>",
                }
                # Persist the original input even if context compression fails.
                if ctx.web_message_ids:
                    user["_client_ids"] = list(ctx.web_message_ids)
                # Interject turns carry a synthetic observation, not something anyone said.
                if not ctx.interject:
                    await append(user)
                context = ContextManager(self.store, self.llm)
                chat_reply = None
                steering = []
                if (
                    cfg.agent_chat_handoff
                    and route == "agent_chat"
                    and actor is not None
                    and ctx.session.startswith(("private:", "web:"))
                ):
                    steering = await collect_steering()
                    chat_history = repair_history(
                        await context.prepare(
                            ctx.session,
                            cfg,
                            user,
                            owner=ctx.owner,
                            current_turn=ctx.turn,
                            memory_profile=ctx.memory_profile,
                            dialogue_only=True,
                        ),
                        None,
                    )
                    chat_history.extend(dialogue_message(message) for message in [user, *steering])
                    if context_chars(chat_history) > cfg.policy.max_context_chars * 2:
                        raise PublicError("agent.contextLimit")
                    if ctx.session.startswith("private:") and not is_feishu_session(ctx.session):
                        chat_system += meme_prompt(cfg, tool_guidance=False)
                    # The tool-free request excludes plugin request injections as well as tool specs.
                    log.debug(Text("agent.log.agentChat", count=len(chat_history)))
                    chat_reply = await self.llm.complete(
                        route,
                        chat_system + prompt_text("WORK_HANDOFF", marker=WORK_HANDOFF),
                        chat_history,
                        [],
                        settings=cfg,
                        scope=ctx.session,
                        tool_free=True,
                    )
                    if chat_reply.text.strip() == WORK_HANDOFF and not chat_reply.calls:
                        chat_reply = None
                        route = "agent"
                        if selected_model:
                            cfg.routes[route] = [selected_model]
                        info = replace(info, route=route)
                        body.update(route=route, agent_chat_handoff=True)
                        await self.store.put("turn", ctx.turn, body, ctx.session)
                        log.log("agent.log.handoff")
                    elif chat_reply.calls:
                        raise PublicError("agent.chatToolCall")

                tools: list[dict] = []
                history: list[dict] = []
                if chat_reply is None:
                    tools = [] if ctx.interject else self.registry.specs(ctx)
                    # Proactive turns report their conclusion through an internal tool.
                    if proactive:
                        tools = [*tools, report_spec()]
                    if self.skills and any(t["function"]["name"] == "skill_read" for t in tools):
                        system += await self.skills.prompt()
                    # Group chat is supplied per turn and never archived into the session history.
                    ambient = await self.group_context(ctx, cfg, tools)
                    current = {**user, "content": ambient + user["content"]} if ambient else user
                    if actor is None or ctx.routine_event:
                        # Read the origin's history without altering its ongoing conversation.
                        history = repair_history(
                            await self.store.history(
                                ctx.session,
                                limit=20,
                                include_commands=False,
                                memory_profile=ctx.memory_profile,
                            ),
                            cfg.policy.max_context_chars,
                        )
                    else:
                        history = repair_history(
                            await context.prepare(
                                ctx.session,
                                cfg,
                                current,
                                owner=ctx.owner,
                                current_turn=ctx.turn,
                                memory_profile=ctx.memory_profile,
                            ),
                            None,
                        )
                    history.extend([current, *steering])
                nudged = False
                repetitions: Counter = Counter()
                for step in range(cfg.policy.max_steps):
                    if chat_reply is None:
                        history.extend(await collect_steering())
                        if context_chars(history) > cfg.policy.max_context_chars * 2:
                            raise PublicError("agent.contextLimit")
                        log.debug(Text("agent.log.step", value=step + 1, count=len(history)))
                        request = LLMRequest(system, history, tools, route, step)
                        if hooks is not None and hooks.has("llm_request"):
                            # Plugins edit a copy: the stored conversation never changes.
                            request.messages, request.tools = deepcopy(history), deepcopy(tools)
                            await hooks.call("llm_request", info, request)
                        reply = await self.llm.complete(
                            route,
                            request.system_prompt,
                            request.messages,
                            [] if actor is None else request.tools,
                            settings=cfg,
                            scope=ctx.session,
                        )
                    else:
                        reply = chat_reply
                    if hooks is not None and hooks.has("llm_response"):
                        response = LLMResponse(reply.text, tuple(reply.calls))
                        await hooks.call("llm_response", info, response)
                        if isinstance(response.text, str):
                            reply.text = response.text
                    if actor is None:
                        body["status"] = "completed"
                        if reply.calls or not reply.text.strip():
                            return None
                        return "<pass>" if _is_pass(reply.text) else reply.text.strip()
                    if ctx.interject and not reply.calls and _is_pass(reply.text):
                        log.log("agent.log.noInterject")
                        body["status"] = "completed"
                        return
                    message = {
                        "role": "assistant",
                        "content": reply.text,
                        "_opaque": reply.opaque,
                        "_speaker": cfg.assistant_name,
                    }
                    if reply.calls:
                        message["tool_calls"] = reply.calls
                    await append(message)
                    history.append(message)
                    if not reply.calls:
                        if proactive and not nudged and not _is_loop_result(reply.text):
                            nudged = True
                            log.warning("agent.log.noReport")
                            item = {"role": "user", "content": prompt_text("REPORT_REMINDER")}
                            await append(item)
                            history.append(item)
                            continue
                        if not proactive:
                            log.log(Text("agent.log.reply", value=brief(reply.text, 1000) or t("agent.log.empty")))
                        final = reply.text or t("agent.noDisplayable")
                        if ctx.group_wake and self.deliver_group_reply:
                            await self.deliver_group_reply(ctx.session, final, ctx.group_wake)
                        else:
                            await self.deliver(execution_session, final)
                        body["status"] = "completed"
                        return
                    # Main-loop text is visible before tools run; proactive turns
                    # deliver only their final structured notification decision.
                    if not proactive and not ctx.background and reply.text.strip():
                        log.log(Text("agent.log.reply", value=brief(reply.text, 1000)))
                        await self.deliver(ctx.session, reply.text)
                    report = None
                    for call in reply.calls:
                        function = call["function"]
                        sig = fingerprint(function)
                        repetitions[sig] += 1
                        if repetitions[sig] >= 5:
                            log.warning(Text("agent.log.repeatLimit", name=function['name']))
                            raise PublicError("agent.repeatLimit")
                        if proactive and function["name"] == REPORT_TOOL:
                            report, output = _report(function["arguments"])
                            if report is None:
                                log.warning("agent.log.reportInvalid")
                        else:
                            try:
                                shown = json.loads(function["arguments"] or "{}")
                            except (TypeError, ValueError):
                                shown = function["arguments"]
                            log.log(
                                Text("agent.log.toolCall", name=function['name'], value=brief(shown, 120) or t("agent.log.noArgs"))
                            )
                            called = time.monotonic()
                            if hooks is not None and hooks.has("tool_call"):
                                await hooks.call(
                                    "tool_call", info, function["name"], _arguments(shown)
                                )
                            requested_at = now().timestamp()
                            try:
                                output = await self.registry.execute(
                                    replace(ctx, tool_call_id=call["id"]),
                                    function["name"],
                                    function["arguments"],
                                )
                            except InteractionRequired as interaction:
                                if not proactive or self.wait_for_interaction is None:
                                    raise PublicError(interaction.reason) from None
                                await self.wait_for_interaction(
                                    ctx.session, interaction, requested_at
                                )
                                body["status"] = "waiting"
                                await append(
                                    {
                                        "role": "tool",
                                        "tool_call_id": call["id"],
                                        "content": canonical({"waiting": interaction.reason}),
                                    }
                                )
                                return
                            if hooks is not None and hooks.has("tool_result"):
                                await hooks.call(
                                    "tool_result",
                                    info,
                                    function["name"],
                                    _arguments(shown),
                                    output,
                                )
                            ok, summary = tool_outcome(output)
                            spent = time.monotonic() - called
                            if ok:
                                log.log(
                                    Text("agent.log.toolDone", name=function['name'], spent=spent, summary=summary)
                                )
                            else:
                                log.warning(
                                    Text("agent.log.toolFailed", name=function['name'], spent=spent, summary=summary)
                                )
                        result = {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "_tool_name": function["name"],
                            "content": output,
                        }
                        await append(result)
                        history.append(result)
                    if report is not None:
                        log.log(
                            Text("agent.log.notify", value=t("agent.log.send") if report.notify else t("agent.log.silent"), brief=brief(report.reason, 120))
                        )
                        final = canonical(report.model_dump())
                        await self.deliver(execution_session, final)
                        body["status"] = "completed"
                        return
                    body["steps"] = step + 1
                    await self.store.put("turn", ctx.turn, body, execution_session)
                raise PublicError(
                    "agent.stepLimit", steps=body['steps']
                )
        except asyncio.CancelledError:
            body["status"] = "cancelled"
            log.log(Text("agent.log.turnCancelled", steps=body['steps'], value=time.monotonic() - started))
            raise
        except TimeoutError:
            # asyncio.timeout(max_turn_seconds) 到期；工具自身超时已在 tools 层消化，
            # 走到这里的基本都是整轮时间超限。给出与步数超限一致的指引而非裸类型名。
            body["status"] = "failed"
            error = (
                t("agent.timeLimit", max_turn_seconds=cfg.policy.max_turn_seconds, steps=body['steps'])
            )
            log.error(Text("agent.log.turnFailed", error=error))
            if actor is None:
                return None
            await append({"role": "assistant", "content": error})
            final = error
            await self.deliver(execution_session, error)
        except Exception as exc:
            body["status"] = "failed"
            error = (
                str(exc) if isinstance(exc, PublicError) else t("agent.turnFailed", name=type(exc).__name__)
            )
            log.error(
                Text("agent.log.turnFailed", error=error),
                exc=None if isinstance(exc, (PublicError, TimeoutError)) else exc,
            )
            if actor is None:
                return None
            await append({"role": "assistant", "content": error})
            final = error
            await self.deliver(execution_session, error)
        finally:
            if hooks is not None and hooks.has("agent_done"):
                result = AgentResult(body["status"], body["steps"] + 1, final)
                # Shielded so a cancelled turn still reports its end to plugins.
                await asyncio.shield(hooks.call("agent_done", info, result))
            if body["status"] == "completed":
                log.log(
                    Text("agent.log.turnDone", value=body['steps'] + 1, value2=time.monotonic() - started)
                )
            body["finished"] = now().isoformat()
            await self.store.put("turn", ctx.turn, body, execution_session)
            if self.short_term and body["summary_eligible"]:
                await self.short_term.enqueue(ctx, body["status"])
                body["summary_enqueued"] = True
                await self.store.put("turn", ctx.turn, body, ctx.session)
