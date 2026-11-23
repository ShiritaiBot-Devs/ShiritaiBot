"""Unified ingress → preprocessing → importance filter → agent loop → notification."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Any, Awaitable, Callable, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from shiritaibot.api.event import ProactiveOutcome
from shiritaibot.config import MAIN_SESSION
from shiritaibot.core import (
    Context,
    ExternalEvent,
    InteractionRequired,
    Model,
    PublicError,
    RuntimePort,
    canonical,
    fingerprint,
    now,
    record_usage_for,
)
from shiritaibot.core.logs import brief, get_logger
from shiritaibot.i18n import Text, TranslatedDict, localize, t
from shiritaibot.memory.judge import remember_question
from shiritaibot.prompts import prompt as prompt_text

PREPARE_SESSION = "background:proactive:"
ROUTINE_KINDS = {"routine.reminder", "routine.job"}
log = get_logger("Proactive")


def event_log(key: str):
    return log.bind(Text("proactive.log.eventTag", value=key[:8]))


def is_routine(event: ExternalEvent) -> bool:
    return event.source == "routine" and event.kind in ROUTINE_KINDS


class Decision(Model):
    importance: int = Field(default=2, ge=0, le=4, strict=True)
    decision: Literal["notify", "digest", "log", "ignore"] = "log"
    message: str = Field(default="", max_length=4000)
    reason: str = Field(min_length=1, max_length=2000)
    urgency: Literal["low", "normal", "high", "critical"] = "normal"
    prepare: str = Field(default="", max_length=2000)
    # Decision model's P(worth remembering), asked alongside triage for memory extraction.
    remember: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="before")
    @classmethod
    def legacy_importance(cls, value):
        if isinstance(value, dict) and "importance" not in value:
            value = dict(value)
            value["importance"] = (
                4
                if value.get("urgency") == "critical"
                else 0
                if value.get("decision") == "ignore"
                else {"low": 1, "normal": 2, "high": 3}.get(value.get("urgency", "normal"), 2)
            )
        return value


class LoopResult(Model):
    notify: bool
    message: str = Field(min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=2000)


_LOOP_KEYS = ("notify", "message", "reason")
_DECODER = json.JSONDecoder(strict=False)


def _loop_fields(data) -> dict | None:
    if not isinstance(data, dict) or not isinstance(data.get("notify"), bool):
        return None
    fields = {k: data[k] for k in _LOOP_KEYS if k in data}
    fields.setdefault("reason", t("proactive.notStated"))
    return fields


def _loose_fields(text: str) -> dict | None:
    """Key-anchored extraction for near-JSON with unescaped quotes inside strings."""
    notify = re.search(r'"notify"\s*:\s*(true|false)', text)
    if not notify:
        return None
    fields: dict = {"notify": notify.group(1) == "true"}
    keys = "|".join(re.escape(k) for k in (*_LOOP_KEYS, "additionalProperties"))
    for key in ("message", "reason"):
        match = re.search(
            rf'"{key}"\s*:\s*"(.*?)"\s*(?=,\s*"(?:{keys})"\s*:|}}\s*(?:```)?\s*$)', text, re.S
        )
        if match:
            raw = match.group(1)
            try:
                fields[key] = _DECODER.decode(f'"{raw}"')
            except ValueError:
                fields[key] = raw.replace("\\n", "\n").replace('\\"', '"')
    fields.setdefault("reason", t("proactive.notStated"))
    return fields


def parse_loop_result(text: str) -> LoopResult:
    """Parse the Loop's final reply, tolerating prose, raw newlines and stray keys."""
    for start in reversed([m.start() for m in re.finditer(r"\{", text)]):
        try:
            data, _ = _DECODER.raw_decode(text, start)
        except ValueError:
            continue
        if (fields := _loop_fields(data)) is not None:
            return LoopResult.model_validate(fields)
    if (fields := _loose_fields(text)) is not None:
        return LoopResult.model_validate(fields)
    raise ValueError("no LoopResult JSON found")


def first_object(text: str) -> dict:
    """First JSON object in a reply, tolerating code fences and surrounding prose."""
    for match in re.finditer(r"\{", text):
        try:
            data, _ = _DECODER.raw_decode(text, match.start())
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    raise ValueError("no JSON object found")


REPORT_TOOL = "proactive_report"
REPORT_SPEC = {
    "type": "function",
    "function": {
        "name": REPORT_TOOL,
        "description": "proactive.report.description",
        "parameters": {
            "type": "object",
            "properties": {
                "notify": {"type": "boolean", "description": "proactive.report.notify"},
                "message": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 4000,
                    "description": "proactive.report.message",
                },
                "reason": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 2000,
                    "description": "proactive.report.reason",
                },
            },
            "required": ["notify", "message", "reason"],
            "additionalProperties": False,
        },
    },
}

# Catalog keys, translated when read.


def report_spec() -> dict:
    """REPORT_SPEC for the model, with its descriptions in the active language."""
    return localize(REPORT_SPEC)


_LEVELS = TranslatedDict(enumerate(f"proactive.importance.{level}" for level in range(5)))
_SUGGESTIONS = TranslatedDict({
    "notify": "proactive.decision.notify",
    "digest": "proactive.decision.digest",
    "log": "proactive.decision.log",
    "ignore": "proactive.decision.ignore",
})
_HIDDEN_FIELDS = {"id", "dedup_key", "uid", "message_id", "uidvalidity"}


def _local(value, zone: ZoneInfo | None):
    """ISO timestamps (stored in UTC) as owner-local wall time."""
    if zone is None or not isinstance(value, str):
        return value
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return value
    if stamp.tzinfo is None:
        return value
    return stamp.astimezone(zone).strftime("%Y-%m-%d %H:%M")


def _readable(value, indent: int = 0, zone: ZoneInfo | None = None) -> list[str]:
    pad = "  " * indent
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            if key in _HIDDEN_FIELDS or item in (None, "", [], {}):
                continue
            if isinstance(item, (dict, list)):
                lines += [f"{pad}{key}:", *_readable(item, indent + 1, zone)]
            else:
                if key.endswith("_at"):
                    item = _local(item, zone)
                text = str(item).replace("\n", "\n" + pad + "  ")
                lines.append(f"{pad}{key}: {text}")
        return lines
    if isinstance(value, list):
        lines = []
        for index, item in enumerate(value, 1):
            if isinstance(item, (dict, list)):
                lines += [prompt_text("EVENT_ITEM", pad=pad, index=index), *_readable(item, indent + 1, zone)]
            else:
                lines.append(f"{pad}- {item}")
        return lines
    return [f"{pad}{value}"]


def describe_event(event: dict, zone: ZoneInfo | None = None) -> str:
    lines = [
        prompt_text("EVENT_SOURCE", source=event['source'], kind=event['kind']),
        prompt_text("EVENT_TITLE", title=event['title']),
    ]
    if event.get("occurred_at"):
        lines.append(prompt_text("EVENT_TIME", local=_local(event['occurred_at'], zone)))
    if event.get("payload"):
        lines += [prompt_text("EVENT_DETAILS"), *_readable(event["payload"], 1, zone)]
    return "\n".join(lines)


def describe_decision(decision: Decision) -> str:
    parts = [
        prompt_text("TRIAGE_SUMMARY", importance=decision.importance, value=_LEVELS[decision.importance], value2=_SUGGESTIONS[decision.decision])
    ]
    if decision.message:
        parts.append(prompt_text("TRIAGE_MESSAGE", message=decision.message))
    parts.append(prompt_text("TRIAGE_REASON", reason=decision.reason))
    if decision.prepare:
        parts.append(prompt_text("TRIAGE_PREPARE", prepare=decision.prepare))
    return "\n".join(parts)


def grade(decision: Decision) -> Literal["drop", "run"]:
    return "drop" if decision.importance == 0 else "run"


def delegated(event: dict) -> bool:
    """A subagent run the owner asked for that finished or stopped for input: never re-triaged."""
    if event["source"] != "subagent":
        return False
    status = event.get("payload", {}).get("status")
    return (event["kind"] == "subagent.completed" and status in {"completed", "failed"}) or (
        event["kind"] == "subagent.needs_input" and status == "needs_input"
    )


def quiet_hours(start: str, end: str, local: datetime) -> bool:
    time = local.strftime("%H:%M")
    if start == end:
        return False
    return start <= time < end if start < end else time >= start or time < end


class ProactiveEngine:
    def __init__(self, config, store, llm, notify, short_term, *, notify_session=None):
        self.config, self.store, self.llm, self.notify = config, store, llm, notify
        self.short_term = short_term
        self.notify_session = notify_session
        self.notify_interaction = notify
        self.interaction_lock = asyncio.Lock()
        self.runtime: RuntimePort | None = None
        # Long-term memory reads each handled event afterwards (memory/extract.py).
        self.memory_sink: Callable[[dict], Awaitable[None]] | None = None
        self.channels: dict[str, list[Callable[[str, str], Awaitable[None]]]] = {}
        # shiritaibot.plugin.core.hooks.HookBus: on_proactive_event / _judged / _done.
        self.hooks: Any = None
        self.wake = asyncio.Event()
        self.lock = asyncio.Lock()

    def preprocess(self, event: ExternalEvent) -> ExternalEvent:
        data = event.model_dump(mode="json")
        for field in ("id", "source", "kind", "dedup_key", "title"):
            data[field] = data[field].strip()
        return ExternalEvent.model_validate(data)

    async def emit(
        self, event: ExternalEvent, *, routine_session: str | None = None, routine_actor: str = ""
    ) -> str:
        event = self.preprocess(event)
        if is_routine(event) and routine_session is None:
            raise PublicError("proactive.routineOnlyScheduler")
        origin = (
            {"routine_session": routine_session, "routine_actor": routine_actor}
            if routine_session is not None
            else {}
        )
        key = fingerprint([event.source, event.dedup_key])
        if await self.store.insert(
            "event",
            key,
            {
                "event": event.model_dump(mode="json"),
                "status": "pending",
                "received_at": now().isoformat(),
                **origin,
            },
        ):
            event_log(key).log(
                Text("proactive.log.received", brief=brief(event.title, 80), source=event.source, kind=event.kind, urgency=event.urgency)
            )
            self.wake.set()
        else:
            event_log(key).debug(Text("proactive.log.duplicate", brief=brief(event.title, 60)))
        return key

    async def emit_many(self, events: list[ExternalEvent]) -> list[str]:
        normalized = [self.preprocess(event) for event in events]
        if any(is_routine(event) for event in normalized):
            raise PublicError("proactive.routineOnlyScheduler")
        return [await self.emit(event) for event in normalized]

    async def get_event(self, key: str) -> dict | None:
        return await self.store.get("event", key)

    async def routine_origin(self, row: dict) -> tuple[str, str]:
        if "routine_session" in row:
            return row["routine_session"], row.get("routine_actor", "")
        # Previously queued occurrences can recover the same origin as their routine.
        routine_id = row["event"]["payload"].get("routine_id")
        item = await self.store.get("routine", routine_id) if isinstance(routine_id, str) else None
        return (item["scope"], item.get("actor", "")) if item else ("", "")

    async def routine_context(self, session: str, actor: str, key: str) -> Context | None:
        cfg = self.config.value
        if not MAIN_SESSION.fullmatch(session):
            return None
        group = session.startswith("group:")
        owner = session.startswith("web:") or any(
            adapter.enabled and session == adapter.owner_session() for adapter in cfg.adapters
        )
        if group and not actor:
            saved = await self.store.get("session", session) or {}
            actor = saved.get("actor", "")
        elif not group:
            actor = (
                "owner" if session.startswith("web:") else session.split(":", 2)[2].split("/")[-1]
            )
        ctx = Context(session, actor, "agent", owner, group, routine_event=key)
        return ctx if cfg.adapter_context_valid(ctx) else None

    async def judge(self, event: ExternalEvent) -> Decision:
        decided = await self.judge_by_decision_model(event)
        if decided is not None:
            return decided
        messages = [{"role": "user", "content": canonical(event.model_dump(mode="json"))}]
        for attempt in range(2):
            try:
                reply = await self.llm.complete(
                    "light",
                    prompt_text("TRIAGE_SYSTEM")
                    + canonical(Decision.model_json_schema()),
                    messages,
                    [],
                )
                decision = Decision.model_validate(first_object(reply.text))
                log.debug(Text("proactive.log.triageText", brief=brief(event.title, 40)))
                return decision
            except Exception as exc:
                log.warning(
                    t("proactive.log.triageInvalid", name=type(exc).__name__)
                    + (t("proactive.log.retrying") if attempt == 0 else t("proactive.log.fallback"))
                )
                messages.append(
                    {"role": "user", "content": prompt_text("TRIAGE_RETRY")}
                )
        return Decision(
            importance=2,
            decision="log",
            message=event.title,
            reason=t("proactive.triageFailed"),
        )

    async def judge_by_decision_model(self, event: ExternalEvent) -> Decision | None:
        """Classify with a System One model such as Jev; it scores but writes no summary."""
        answers = await self.llm.decide(
            "light",
            event.model_dump(mode="json"),
            {
                "importance": {
                    "type": "score",
                    "instructions": prompt_text("JEV_TRIAGE_TASK"),
                    "criteria": [
                        prompt_text("JEV_IMPORTANCE_0"),
                        prompt_text("JEV_IMPORTANCE_1"),
                        prompt_text("JEV_IMPORTANCE_2"),
                        prompt_text("JEV_IMPORTANCE_3"),
                        prompt_text("JEV_IMPORTANCE_4"),
                    ],
                },
                "decision": {
                    "type": "choice",
                    "instructions": prompt_text("JEV_DECISION_TASK"),
                    "criteria": {
                        "notify": prompt_text("JEV_DECISION_NOTIFY"),
                        "digest": prompt_text("JEV_DECISION_DIGEST"),
                        "log": prompt_text("JEV_DECISION_LOG"),
                        "ignore": prompt_text("JEV_DECISION_IGNORE"),
                    },
                },
                # Same state, one more question: whether memory should read this event.
                "remember": remember_question("the event in the state"),
            },
        )
        if answers is None:
            return None
        log.debug(Text("proactive.log.triageJev", brief=brief(event.title, 40)))
        try:
            # The most probable level, not the expected value, so a likely 4 stays a 4.
            levels = answers["importance"]["probabilities"]
            importance = int(max(levels, key=lambda level: levels[level]))
            choice = answers["decision"]
            # Optional: a bad memory answer must not cost the triage itself.
            remember = (answers.get("remember") or {}).get("noul")
            return Decision(
                remember=float(remember) if isinstance(remember, (int, float)) else None,
                importance=importance,
                decision="notify" if importance == 4 else choice["choice"],
                message=event.title,
                reason=t("proactive.jevVerdict", importance=importance, choice=choice['choice'], float=float(choice.get('confidence', 0))),
            )
        except (KeyError, TypeError, ValueError):
            log.warning("proactive.log.jevIncomplete")
            return None

    async def handle(self, row: dict) -> None:
        event = ExternalEvent.model_validate(row["event"])
        key = row["id"]
        await self.short_term.record(
            key, "received", event.title, source=event.source, kind=event.kind, title=event.title
        )
        scheduled = is_routine(event)
        reminder = scheduled and event.kind == "routine.reminder"
        # Memory's own questions for the owner are never discarded, but never urgent either.
        internal = event.source == "memory"
        requested = delegated(row["event"])
        if not self.config.value.proactive.enabled and not scheduled and not requested:
            event_log(key).log("proactive.log.disabled")
            row.update(status="processed", action="record", reason=t("proactive.disabled"))
            await self.store.put("event", key, row)
            await self.short_term.record(key, "logged", row["reason"])
            return
        view = None
        if self.hooks is not None:
            view = await self.hooks.proactive_event(
                self.hooks.view(key, row["event"], scheduled or requested)
            )
            if view.dropped:
                event_log(key).log(Text("proactive.log.pluginDropped", brief=brief(view.drop_reason, 120)))
                row.update(
                    status="discarded",
                    action="drop",
                    reason=view.drop_reason,
                    processed_at=now().isoformat(),
                )
                await self.store.put("event", key, row)
                await self.short_term.record(key, "discarded", view.drop_reason)
                return
            edits = {"title": view.title, "payload": view.payload, "urgency": view.urgency}
            if any(row["event"].get(name) != value for name, value in edits.items()):
                try:
                    event = self.preprocess(ExternalEvent.model_validate({**row["event"], **edits}))
                    row["event"] = event.model_dump(mode="json")
                except ValueError:
                    event_log(key).warning("proactive.log.pluginInvalid")
        if requested:
            # The owner is waiting on this result; Main ended its turn expecting PE to deliver it.
            decision = Decision(
                importance=4,
                decision="notify",
                message=event.title,
                reason=prompt_text("TRIAGE_SUBAGENT_INPUT")
                if event.kind == "subagent.needs_input"
                else prompt_text("TRIAGE_SUBAGENT_DONE"),
            )
        elif scheduled:
            # Scheduled instructions skip the classifier; reminders bypass quiet hours.
            decision = Decision(
                importance=4 if reminder else 3,
                decision="notify",
                message=event.title,
                reason=prompt_text("TRIAGE_REMINDER")
                if reminder
                else prompt_text("TRIAGE_JOB"),
            )
        elif internal:
            decision = Decision(
                importance=1,
                decision="digest",
                message=event.title,
                reason=prompt_text("TRIAGE_MEMORY"),
            )
        else:
            with record_usage_for(PREPARE_SESSION + key):
                decision = await self.judge(event)
        if view is not None and self.hooks is not None:
            decision = await self.hooks.proactive_judged(view, decision)
            if requested:
                # The owner is waiting on a delegated run: plugins cannot silence it.
                decision = decision.model_copy(update={"importance": 4, "decision": "notify"})
        if scheduled:
            # Scheduled items cannot be downgraded by the judgement hook.
            decision = decision.model_copy(
                update={
                    "importance": 4 if reminder else max(3, decision.importance),
                    "decision": "notify",
                    "message": decision.message or event.title,
                }
            )
        if internal:
            decision = decision.model_copy(
                update={"importance": max(1, min(2, decision.importance)), "decision": "digest"}
            )
        decision = decision.model_copy(
            update={"urgency": ("low", "low", "normal", "high", "critical")[decision.importance]}
        )
        row.update(status="filtered", decision=decision.model_dump(), action=grade(decision))
        event_log(key).log(
            t("proactive.triageResult", importance=decision.importance, value=_LEVELS[decision.importance], value2=_SUGGESTIONS[decision.decision], brief=brief(decision.reason, 120))
            + (t("proactive.routineNote") if scheduled else "")
        )
        await self.store.put("event", key, row)
        await self.short_term.record(
            key, "filtered", decision.reason, importance=decision.importance, action=row["action"]
        )

    async def act(self, row: dict) -> None:
        key = row["id"]
        decision = Decision.model_validate(row["decision"])
        if grade(decision) == "drop":
            event_log(key).log("proactive.log.dropped")
            row.update(status="discarded", processed_at=now().isoformat())
            await self.store.put("event", key, row)
            await self.short_term.record(key, "discarded", decision.reason)
            return
        event = row["event"]
        ctx = Context(PREPARE_SESSION + key, "owner", "agent", True, background=True)
        scoped_routine = False
        if event["source"] == "routine" and event["kind"] in ROUTINE_KINDS:
            session, actor = await self.routine_origin(row)
            if session != "owner":
                scoped_routine = True
                origin_ctx = await self.routine_context(session, actor, key)
                if origin_ctx is None:
                    reason = t("proactive.routineSessionGone")
                    row.update(status="discarded", action="drop", reason=reason)
                    await self.store.put("event", key, row)
                    await self.short_term.record(key, "discarded", reason)
                    return
                ctx = origin_ctx
        recipient = t("proactive.recipient.session") if scoped_routine else t("proactive.recipient.owner")
        must = (
            prompt_text("TASK_MUST_NOTIFY", recipient=recipient)
            if decision.importance == 4
            else prompt_text("TASK_MAY_SKIP")
        )
        prompt = (
            prompt_text("TASK_INTRO", recipient=recipient, recipient2=recipient)
            + must
            + prompt_text("TASK_RULES")
            + prompt_text("TASK_EVENT_HEAD")
            + describe_event(event, ZoneInfo(self.config.value.proactive.timezone))
            + prompt_text("TASK_TRIAGE_HEAD")
            + describe_decision(decision)
        )
        if delegated(event) and event["kind"] == "subagent.needs_input":
            run = event["payload"].get("run_id", "")
            prompt += (
                prompt_text("TASK_SUBAGENT_INPUT", run=run)
            )
        elif delegated(event):
            prompt += (
                prompt_text("TASK_SUBAGENT_DONE")
            )
        if event["source"] == "routine" and event["kind"] == "routine.job":
            instructions = event["payload"].get("routine_title", event["title"])
            prompt += prompt_text("TASK_JOB", recipient=recipient) + str(instructions)
        row.update(status="running", loop_started=now().timestamp())
        event_log(key).log("proactive.log.toAgent")
        await self.store.put("event", key, row)
        await self.short_term.record(key, "running", decision.prepare or decision.reason)
        try:
            if self.runtime is None:
                raise RuntimeError("runtime unavailable")
            if await self.runtime.submit(ctx, prompt) == "ignored":
                raise PublicError("proactive.taskSessionGone")
        except Exception as exc:
            event_log(key).error(Text("proactive.log.agentFailed", name=type(exc).__name__), exc=exc)
            await self.finish(row, "", failed=True)

    async def queue(
        self, key: str, source: str, decision: Decision, message: str, *, session: str = ""
    ) -> None:
        await self.store.insert(
            "notification",
            key,
            {
                **decision.model_dump(),
                "source": source,
                "status": "pending",
                "created": now().timestamp(),
                "message": message,
                **({"target_session": session} if session else {}),
            },
        )

    async def wait_for_interaction(
        self, session: str, request: InteractionRequired, requested_at: float
    ) -> None:
        async with self.lock:
            row = await self.get_event(session.removeprefix(PREPARE_SESSION))
            if row is None or row["status"] != "running":
                return
            row.update(
                status="waiting",
                wait_key=request.key,
                reason=request.reason,
                wait_started=requested_at,
                expires_at=now().timestamp() + request.ttl,
            )
            await self.store.put("event", row["id"], row)
            await self.short_term.record(row["id"], "waiting", request.reason)
            self.wake.set()

    async def resolve_interaction(self, key: str) -> None:
        # The owner may finish while a failing tool call is still returning.
        # Persist the resolution so even that late waiter is retried after a restart.
        async with self.interaction_lock:
            await self.store.put("interaction", key, {"resolved_at": now().timestamp()})
            await self.refresh_waiters(key)
            self.wake.set()

    async def refresh_waiters(self, resolved_key: str | None = None) -> None:
        """Called under interaction_lock. Notify once per overlapping set of waiters."""
        waits: dict[str, list[dict]] = {}
        for row in await self.store.with_status("event", ("waiting",), limit=-1):
            key = row["wait_key"]
            if resolved_key is None or key == resolved_key:
                waits.setdefault(key, []).append(row)
        for key, rows in waits.items():
            state = await self.store.get("interaction", key) or {}
            stamp = now()
            active = []
            for row in rows:
                if row["expires_at"] <= stamp.timestamp():
                    row.update(
                        status="processed",
                        reason=t("proactive.ownerTimeout"),
                        processed_at=stamp.isoformat(),
                    )
                elif state.get("resolved_at", 0) >= row["wait_started"]:
                    row.update(status="filtered", reason=t("proactive.ownerHandled"))
                else:
                    active.append(row)
                    continue
                for field in ("wait_key", "wait_started", "expires_at"):
                    row.pop(field)
                await self.store.put("event", row["id"], row)
                await self.short_term.record(row["id"], row["status"], row["reason"])
            if active and resolved_key is None:
                until = state.get("notified_until", 0)
                if until <= stamp.timestamp():
                    try:
                        await self.notify_interaction(
                            t("proactive.needsOwner", reason=active[0]['reason'])
                        )
                    except Exception as exc:
                        log.warning(Text("proactive.log.promptFailed", name=type(exc).__name__))
                        continue
                expires = max(row["expires_at"] for row in active)
                if expires != until:
                    await self.store.put("interaction", key, {**state, "notified_until": expires})

    async def prepared(self, session: str, text: str) -> None:
        """Accept a full Proactive Turn result (retaining the old callback name)."""
        async with self.lock:
            row = await self.get_event(session.removeprefix(PREPARE_SESSION))
            if row and row["status"] in {"running", "preparing"}:
                await self.finish(row, text)

    async def finish(self, row: dict, text: str, failed: bool = False) -> None:
        decision = Decision.model_validate(row["decision"])
        try:
            result = parse_loop_result(text)
        except (ValueError, TypeError):
            failed = True
            event_log(row["id"]).warning("proactive.log.noConclusion")
            result = LoopResult(
                notify=decision.decision in {"notify", "digest"},
                message=(decision.message or row["event"]["title"])
                + t("proactive.unfinished"),
                reason=t("proactive.loopFailed"),
            )
        must_notify = decision.importance == 4
        explicit_reminder = (
            row["event"]["source"] == "routine" and row["event"]["kind"] == "routine.reminder"
        )
        scheduled = row["event"]["source"] == "routine" and row["event"]["kind"] in ROUTINE_KINDS
        session, _ = await self.routine_origin(row) if scheduled else ("", "")
        if self.hooks is not None and self.hooks.has("proactive_done"):
            event = row["event"]
            protected = delegated(event) or scheduled
            outcome = await self.hooks.proactive_done(
                self.hooks.view(row["id"], event, protected),
                ProactiveOutcome(
                    notify=result.notify,
                    message=result.message,
                    reason=result.reason,
                    importance=decision.importance,
                    failed=failed,
                    forced=must_notify or explicit_reminder,
                ),
            )
            try:
                result = LoopResult(
                    notify=outcome.notify, message=outcome.message, reason=outcome.reason
                )
            except ValueError:
                event_log(row["id"]).warning("proactive.log.verdictInvalid")
        should_notify = (must_notify or explicit_reminder or result.notify) and (
            not scheduled or bool(session)
        )
        if must_notify:
            decision = decision.model_copy(update={"decision": "notify", "urgency": "critical"})
        elif result.notify and decision.decision not in {"notify", "digest"}:
            decision = decision.model_copy(update={"decision": "notify"})
        if should_notify:
            await self.queue(
                row["id"], row["event"]["source"], decision, result.message, session=session
            )
        row.update(
            status="processed",
            processed_at=now().isoformat(),
            result=result.model_dump(),
            notification_queued=should_notify,
            loop_failed=failed,
        )
        await self.store.put("event", row["id"], row)
        if should_notify:
            reason = t("proactive.reason.highest") if must_notify else t("proactive.reason.reminder") if explicit_reminder else ""
            recipient = t("proactive.recipient.session") if session and session != "owner" else t("proactive.recipient.owner")
            event_log(row["id"]).log(
                Text("proactive.log.willNotify", recipient=recipient, value=Text("common.detail", detail=reason) if reason else "", brief=brief(result.message, 100))
            )
        else:
            event_log(row["id"]).log(Text("proactive.log.noNotify", brief=brief(result.reason, 120)))
        await self.short_term.record(
            row["id"], "queued" if should_notify else "silent", result.reason
        )
        if self.memory_sink is not None and (not scheduled or session == "owner"):
            try:
                await self.memory_sink(row)
            except Exception as exc:
                event_log(row["id"]).warning(Text("proactive.log.memoryFailed", name=type(exc).__name__))
        self.wake.set()

    async def tick(self) -> None:
        async with self.lock:
            for row in await self.store.with_status("event", ("pending",)):
                await self.handle(row)
            async with self.interaction_lock:
                await self.refresh_waiters()
            filtered = await self.store.with_status("event", ("filtered",))
            for row in sorted(filtered, key=lambda r: r["decision"]["importance"], reverse=True):
                await self.act(row)
            limit = self.config.value.policy.max_turn_seconds + 60
            for row in await self.store.with_status("event", ("running", "preparing")):
                if (
                    now().timestamp() - row.get("loop_started", row.get("prepare_started", 0))
                    > limit
                ):
                    event_log(row["id"]).warning(
                        Text("proactive.log.timeout", limit=limit)
                    )
                    await self.finish(row, "", failed=True)
            await self.flush_notifications()

    async def flush_notifications(self) -> None:
        cfg = self.config.value.proactive
        local = now().astimezone(ZoneInfo(cfg.timezone))
        pending = await self.store.with_status("notification", ("pending",))
        by_target: dict[tuple[str, str], list[dict]] = {}
        for note in pending:
            target = note.get("target_session", "")
            if note["source"] == "routine" and not target:
                event = await self.get_event(note["id"])
                if event:
                    target, _ = await self.routine_origin(event)
                if not target:
                    note.update(status="failed", reason=t("proactive.routineSessionGone"))
                    await self.store.put("notification", note["id"], note)
                    continue
                note["target_session"] = target
            if note["urgency"] != "critical":
                if quiet_hours(cfg.quiet_start, cfg.quiet_end, local):
                    if not note.get("deferred"):
                        # Log once so a held notification is visible instead of silently waiting.
                        note["deferred"] = True
                        await self.store.put("notification", note["id"], note)
                        detail = (
                            t("proactive.quietHours", quiet_start=cfg.quiet_start, quiet_end=cfg.quiet_end, timezone=cfg.timezone, quiet_end2=cfg.quiet_end)
                        )
                        await self.short_term.record(note["id"], "deferred", detail)
                        event_log(note["id"]).log(detail)
                        await self.store.activity("notify.deferred", f"{note['source']}: {detail}")
                    continue
                if note["decision"] == "digest" and now().timestamp() - note["created"] < max(
                    60, cfg.throttle_seconds
                ):
                    continue
            by_target.setdefault((note["source"], target), []).append(note)
        for (source, target), notes in by_target.items():
            scoped = bool(target and target != "owner")
            throttle_key = f"{source}:{target}" if scoped else source
            last = await self.store.get("notify_last", throttle_key)
            if (
                last
                and now().timestamp() - last["at"] < cfg.throttle_seconds
                and all(n["urgency"] != "critical" for n in notes)
            ):
                continue
            text = "\n\n".join(n["message"] for n in notes)
            recipient = t("proactive.recipient.session") if scoped else t("proactive.recipient.owner")
            log.log(Text("proactive.log.sending", recipient=recipient, count=len(notes), source=source, brief=brief(text, 120)))
            try:
                if scoped:
                    if self.notify_session is None:
                        raise PublicError("proactive.routineDeliveryMissing")
                    await self.notify_session(target, text)
                else:
                    await self.notify(text)
            except PublicError as exc:
                if not scoped:
                    raise
                for note in notes:
                    note.update(status="failed", reason=str(exc))
                    await self.store.put("notification", note["id"], note)
                    await self.short_term.record(note["id"], "failed", str(exc))
                continue
            for note in notes:
                note["status"] = "sent"
                await self.store.put("notification", note["id"], note)
                await self.short_term.record(note["id"], "notified", t("proactive.handedToAdapter", recipient=recipient))
            await self.store.activity(
                "notify.sent", t("proactive.sentSummary", source=source, count=len(notes), recipient=recipient)
            )
            await self.store.put("notify_last", throttle_key, {"at": now().timestamp()})
            if not scoped:
                await self.broadcast(source, text)

    async def broadcast(self, source: str, text: str) -> None:
        for plugin, handlers in list(self.channels.items()):
            for handler in handlers:
                try:
                    await handler(source, text)
                except Exception as exc:
                    log.warning(Text("proactive.log.channelFailed", plugin=plugin, name=type(exc).__name__))
                    await self.store.activity(
                        "notify.channel_failed", f"{plugin}: {type(exc).__name__}"
                    )

    async def run(self) -> None:
        while True:
            self.wake.clear()
            try:
                await self.tick()
            except Exception as exc:
                log.error(Text("proactive.log.loopError", name=type(exc).__name__), exc=exc)
                await self.store.activity("proactive.failed", type(exc).__name__)
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=10)
            except TimeoutError:
                pass
