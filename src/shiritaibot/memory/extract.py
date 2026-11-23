"""Automatic long-term memory extraction (docs/MEMORY.md §3).

Owner private chats are read in windows; each handled Proactive event is read once.
A text model proposes entities, facts and events with verbatim evidence; code decides
what survives: quotes must be in the cited line, assistant lines are never evidence,
times must be absolute, and names must appear in the source.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from fnmatch import fnmatchcase
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from shiritaibot.core import PublicError, canonical, model_usage_session, now, record_usage_for
from shiritaibot.core.logs import brief, describe_session, get_logger
from shiritaibot.i18n import Text, join_list, t
from shiritaibot.memory.judge import DECIDED, GATE_SKIP, SAME_EVENT
from shiritaibot.memory.longterm import (
    LABELS,
    MemoryManager,
    Proof,
    normalize,
    parse_ref,
    ref,
)
from shiritaibot.memory.times import parse_time, relative_words
from shiritaibot.proactive import PREPARE_SESSION, describe_event, first_object
from shiritaibot.prompts import prompt as prompt_text
from shiritaibot.store import MEMORY_PROFILE_FILTER, owner_memory_scope

log = get_logger("memory.extract.log.extractSource")
# Feishu's durable private session key includes its region and app binding.
FEISHU_PRIVATE_SESSIONS = ("private:*:feishu.cli_*/ou_*", "private:*:lark.cli_*/ou_*")
# Tool output that is itself memory or history would only be learned back in a loop.
EXCLUDED_TOOLS = {
    "memory_search",
    "memory_get",
    "memory_save",
    "memory_forget",
    "memory_resolve",
    "proactive_recall",
    "history_query",
}
MAX_LINES = 60
MAX_CHARS = 16000
LINE_CHARS = 2000
GAP = 3 * 3600
MAX_ATTEMPTS = 6
# A user typing "[P1]" must not impersonate a catalog entry.
MARKER = re.compile(r"\[(?=(?:[PE]\d+|new:\d+)\])")




class Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class EntityOut(Loose):
    ref: str
    kind: Literal["person", "thing"] = "person"
    name: str = ""
    aliases: list[str] = []


class FactOut(Loose):
    subject: str | None = None
    content: str
    valid_from: str | None = None
    evidence_line: int
    evidence_quote: str

    @field_validator("subject", "valid_from")
    @classmethod
    def blank(cls, value):
        return None if value in ("", "null", "None") else value


class EventOut(Loose):
    content: str
    time: str | None = None
    time_text: str = ""
    phase: Literal["happened", "planned", "cancelled"] = "happened"
    participants: list[str] = []
    evidence_line: int
    evidence_quote: str


@dataclass
class Line:
    role: Literal["owner", "participant", "assistant", "tool", "event"]
    text: str
    at: float
    source: str
    authors: set[str] = field(default_factory=set)  # catalog refs that wrote this line


@dataclass
class Entry:
    """A catalog entry: an existing entity, or a person known only by platform identity."""

    entity: int | None
    name: str
    identity: str = ""
    role: str = ""
    aliases: list[str] = field(default_factory=list)


@dataclass
class Window:
    owner: int
    lines: list[Line]
    catalog: dict[str, Entry]
    known: list[str]  # current facts about catalog entities, for comparison only
    label: str


def items(data: dict, key: str, model: type[Loose]) -> list:
    """Valid items of one output list; a malformed item never sinks the rest."""
    result = []
    for raw in data.get(key) or []:
        try:
            result.append(model.model_validate(raw))
        except ValidationError:
            log.debug(Text("memory.extract.log.invalidEntry", key=key))
    return result


def open_questions(questions: dict, facts: list, events: list) -> dict:
    """The judge questions still open after the decision model."""
    return {
        "facts": [q for q in questions["facts"] if facts[q["i"]] is not None],
        "events": [q for q in questions["events"] if events[q["i"]] is not None],
    }


class MemoryExtractor:
    def __init__(
        self,
        memory: MemoryManager,
        store,
        config,
        llm,
        *,
        profile: str = "default",
        shared: bool = False,
    ):
        self.memory, self.store, self.config, self.llm = memory, store, config, llm
        self.profile = profile
        self.shared = shared
        self.scope = f"shared:{profile}" if shared else owner_memory_scope(profile)
        self.wake = asyncio.Event()
        self.lock = asyncio.Lock()
        self.backoff: dict[str, tuple[float, float]] = {}

    # ---- scheduling ----------------------------------------------------------

    async def sessions(self) -> list[str]:
        if self.profile != "default":
            rows = await self.store.query(
                "SELECT DISTINCT scope AS id FROM records WHERE kind='turn' "
                "AND json_extract(data,'$.memory_scope')=? AND json_extract(data,'$.loop')='main'",
                (self.scope,),
            )
            return [row["id"] for row in rows]
        rows = await self.store.query(
            "SELECT id FROM records WHERE kind='session' "
            "AND json_extract(data,'$.owner')=1 AND json_extract(data,'$.mode')='agent' "
            "AND json_extract(data,'$.group')=0 AND json_extract(data,'$.background')=0 "
            "UNION SELECT scope AS id FROM records WHERE kind='turn' "
            "AND json_extract(data,'$.memory_scope')='owner' AND json_extract(data,'$.loop')='main' "
            "AND (scope GLOB ? OR scope GLOB ?)",
            FEISHU_PRIVATE_SESSIONS,
        )
        return [row["id"] for row in rows]

    async def open(self) -> None:
        """Skip legacy history without dropping unprocessed profile-tagged messages."""
        if self.profile != "default":
            return
        for session in await self.sessions():
            if any(fnmatchcase(session, pattern) for pattern in FEISHU_PRIVATE_SESSIONS):
                continue  # Feishu uses persisted per-turn eligibility from its first message.
            await self.store.execute(
                "INSERT OR IGNORE INTO mem_cursor SELECT ?,COALESCE(MAX(seq),0),? FROM messages "
                "WHERE session=? AND json_extract(data,'$._memory_profile') IS NULL",
                (session, now().timestamp(), session),
            )

    async def enqueue_event(self, row: dict) -> None:
        """Called by the Proactive Engine once an event is handled; reading happens later."""
        event = row["event"]
        if event["source"] in {"memory", "情绪"} or (
            event["source"] == "routine" and event["kind"] == "routine.reminder"
        ):
            return  # Internal prompts and owner reminders carry no new external facts.
        await self.store.insert(
            "memory_extract",
            "event:" + row["id"],
            {"status": "pending", "event": row["id"], "attempts": 0, "retry_at": 0},
        )
        self.wake.set()

    def ready(self) -> bool:
        cfg = self.config.value
        return (
            cfg.memory.profile(self.profile) is not None
            and cfg.memory.extract
            and any(
                routes.get("memory") or routes.get("light")
                for routes in (cfg.routes, *cfg.session_routes.values())
            )
        )

    async def tick(self) -> None:
        if not self.ready():
            return
        async with self.lock:
            stamp = now().timestamp()
            routes = self.config.value.routes
            jobs = (
                await self.store.with_status("memory_extract", ("pending",))
                if self.profile == "default" and (routes.get("memory") or routes.get("light"))
                else []
            )
            for job in jobs:
                if job.get("retry_at", 0) > stamp:
                    continue
                try:
                    await self.event_job(job["event"])
                    await self.store.delete("memory_extract", job["id"])
                except Exception as exc:
                    attempts = job.get("attempts", 0) + 1
                    job.update(
                        attempts=attempts,
                        retry_at=stamp + min(3600, 60 * 2**attempts),
                        error=type(exc).__name__,
                        status="failed" if attempts >= MAX_ATTEMPTS else "pending",
                    )
                    await self.store.put("memory_extract", job["id"], job)
                    log.warning(
                        t("memory.extract.log.eventFailed", name=type(exc).__name__)
                        + (t("memory.extract.log.gaveUp") if attempts >= MAX_ATTEMPTS else t("memory.extract.log.retryLater", attempts=attempts)),
                        Text("memory.extract.log.eventTag", value=job['event'][:8]),
                    )
            for session in await self.sessions():
                retry, _ = self.backoff.get(session, (0, 0))
                if retry > stamp:
                    continue
                try:
                    for _ in range(3):
                        if not await self.chat_job(session):
                            break
                    self.backoff.pop(session, None)
                except Exception as exc:
                    _, delay = self.backoff.get(session, (0, 30))
                    delay = min(3600, delay * 2)
                    self.backoff[session] = (stamp + delay, delay)
                    log.warning(
                        Text("memory.extract.log.sessionFailed", name=type(exc).__name__, delay=delay),
                        describe_session(session),
                    )

    async def run(self) -> None:
        while True:
            self.wake.clear()
            try:
                await self.tick()
            except Exception as exc:
                log.error(Text("memory.extract.log.loopError", name=type(exc).__name__), exc=exc)
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=30)
            except TimeoutError:
                pass

    # ---- windows -------------------------------------------------------------

    async def chat_job(self, session: str) -> bool:
        """Read one due window of a private session; True when there may be more."""
        settings = self.config.value.with_session_routes(session)
        if not (settings.routes["memory"] or settings.routes["light"]):
            return False
        cfg = settings.memory
        cursor = await self.memory.rows("SELECT seq FROM mem_cursor WHERE session=?", (session,))
        start = cursor[0]["seq"] if cursor else 0
        args = (session, start, self.profile)
        eligibility = ""
        if self.profile != "default" or any(
            fnmatchcase(session, pattern) for pattern in FEISHU_PRIVATE_SESSIONS
        ):
            eligibility = (
                " AND turn IN (SELECT id FROM records WHERE kind='turn' AND scope=? "
                "AND json_extract(data,'$.memory_scope')=? AND json_extract(data,'$.loop')='main')"
            )
            args += (session, self.scope)
        rows = await self.store.query(
            "SELECT seq,turn,data,created FROM messages WHERE session=? AND seq>? "
            f"AND {MEMORY_PROFILE_FILTER}{eligibility} ORDER BY seq LIMIT 400",
            args,
        )
        if not rows:
            return False
        running = {
            row["id"]
            for row in await self.store.with_status("turn", ("running", "waiting_approval"))
            if row.get("scope") == session
        }
        names: dict[str, str] = {}
        kept: list[tuple[int, Line | None, float]] = []
        for row in rows:
            if row["turn"] in running:
                break  # Never read a turn that is still being written.
            message = json.loads(row["data"])
            for call in message.get("tool_calls") or []:
                names[call.get("id", "")] = call.get("function", {}).get("name", "")
            kept.append((row["seq"], self.chat_line(session, row, message, names), row["created"]))
        if not kept:
            return False
        content = sum(1 for _, line, _ in kept if line)
        idle = now().timestamp() - kept[-1][2] >= cfg.extract_idle_minutes * 60
        if content < cfg.extract_messages and not idle:
            return False
        segment: list[Line] = []
        end = start
        chars = 0
        zone = self.memory.zone
        for seq, line, _ in kept:
            if line is not None and segment:
                previous = segment[-1]
                day = datetime.fromtimestamp(previous.at, zone).date()
                if (
                    line.at - previous.at > GAP
                    or datetime.fromtimestamp(line.at, zone).date() != day
                    or len(segment) >= MAX_LINES
                    or chars + len(line.text) > MAX_CHARS
                ):
                    break
            if line is not None:
                segment.append(line)
                chars += len(line.text)
            end = seq
        if any(line.role in {"owner", "participant"} for line in segment):
            with record_usage_for(session):
                await self.process(await self.window(segment, [], describe_session(session)))
        await self.memory.store.execute(
            "INSERT INTO mem_cursor VALUES(?,?,?) ON CONFLICT(session) DO UPDATE "
            "SET seq=excluded.seq,updated=excluded.updated",
            (session, end, now().timestamp()),
        )
        return end < kept[-1][0]

    def chat_line(self, session: str, row: dict, message: dict, names: dict) -> Line | None:
        if message.get("_command"):
            return None
        role = message.get("role")
        text = message.get("_original_content", message.get("content", ""))
        if not isinstance(text, str) or not text.strip():
            return None
        source = f"{session}:{row['seq']}"
        if role == "user":
            # Handoffs and Proactive results are the assistant's own words.
            if self.shared:
                speaker = message.get("_speaker") or "??"
                return Line(
                    "participant",
                    f"{speaker}：{text}"[:LINE_CHARS],
                    row["created"],
                    "msg:" + source,
                )
            return Line("owner", text[:LINE_CHARS], row["created"], "msg:" + source)
        if role == "assistant":
            return Line("assistant", text[:LINE_CHARS], row["created"], "")
        if (
            not self.shared
            and role == "tool"
            and names.get(message.get("tool_call_id", "")) not in EXCLUDED_TOOLS
        ):
            return Line("tool", text[:LINE_CHARS], row["created"], "tool:" + source)
        return None

    async def event_job(self, key: str) -> None:
        row = await self.store.get("event", key)
        if not row:
            return
        event = row["event"]
        stamp = datetime.fromisoformat(event["occurred_at"]).timestamp()
        catalog: dict[str, Entry] = {}
        authors = set()
        for actor in event.get("actors") or []:
            entity = await self.memory.identity_entity(actor["identity"])
            if entity is not None:
                ref_ = ref("E", entity)
                name = (await self.memory.row("e", entity))["name"]
            else:
                ref_ = f"P{sum(1 for k in catalog if k.startswith('P')) + 1}"
                name = actor.get("name") or actor["identity"].split(":", 1)[1]
            catalog.setdefault(ref_, Entry(entity, name, actor["identity"], actor.get("role", "")))
            if actor.get("role") in {"sender", "author"}:
                authors.add(ref_)
        lines = [
            Line(
                "event",
                describe_event(event, self.memory.zone)[: LINE_CHARS * 2],
                stamp,
                f"event:{event['source']}:{key}",
                authors,
            )
        ]
        names: dict[str, str] = {}
        session = PREPARE_SESSION + key
        loop = await self.store.query(
            "SELECT seq,data,created FROM messages WHERE session=? ORDER BY seq", (session,)
        )
        chars = len(lines[0].text)
        for item in loop:
            message = json.loads(item["data"])
            for call in message.get("tool_calls") or []:
                names[call.get("id", "")] = call.get("function", {}).get("name", "")
            if message.get("role") == "user":
                continue  # The Loop prompt restates the event already shown above.
            line = self.chat_line(session, item, message, names)
            if line is None or chars + len(line.text) > MAX_CHARS:
                continue
            chars += len(line.text)
            lines.append(line)
        hint = (row.get("decision") or {}).get("remember")
        window = await self.window(lines, list(catalog.items()), t("memory.extract.eventLabel", value=key[:8]))
        await self.process(window, hint)

    async def window(
        self, lines: list[Line], actors: list[tuple[str, Entry]], label: str
    ) -> Window:
        memory = self.memory
        owner = await memory.owner_id()
        catalog: dict[str, Entry] = {ref("E", owner): Entry(owner, self.config.value.owner_name)}
        catalog.update(actors)
        text = "\n".join(line.text for line in lines if line.role != "assistant")
        linked = [e for e in memory.link(text) if ref("E", e) not in catalog][:30]
        cards = await memory.load([ref("e", e) for e in linked])
        for entity in linked:
            card = cards[ref("e", entity)]
            catalog[ref("E", entity)] = Entry(entity, card["name"], aliases=card["aliases"])
        for entry in catalog.values():
            if entry.entity is not None and entry.entity != owner and not entry.aliases:
                card = (await memory.load([ref("e", entry.entity)]))[ref("e", entry.entity)]
                entry.aliases = card["aliases"]
        known = []
        for key, entry in catalog.items():
            if entry.entity is None:
                continue
            facts = await memory.load(await memory.card_facts(entry.entity))
            known += [f"[{key}] {fact['content']}" for fact in facts.values()]
        return Window(owner, lines, catalog, known, label)

    def render(self, window: Window) -> str:
        zone = self.memory.zone
        catalog = []
        for key, entry in window.catalog.items():
            if entry.entity == window.owner:
                catalog.append(prompt_text("MEMORY_OWNER_ENTRY", key=key, name=entry.name))
            elif entry.entity is None:
                role = {"sender": prompt_text("MEMORY_SENDER"), "recipient": prompt_text("MEMORY_RECIPIENT")}.get(entry.role, entry.role)
                catalog.append(
                    f"[{key}] {entry.name} <{entry.identity}>"
                    + (f"（{role}）" if role else "")
                    + prompt_text("MEMORY_NOT_RECORDED")
                )
            else:
                catalog.append(
                    f"[{key}] {entry.name}"
                    + (prompt_text("MEMORY_ALIASES", aliases=join_list(entry.aliases)) if entry.aliases else "")
                )
        speakers = {
            "participant": prompt_text("MEMORY_ROLE_MEMBER"),
            "owner": prompt_text("MEMORY_ROLE_OWNER"),
            "assistant": prompt_text("MEMORY_ROLE_ASSISTANT"),
            "tool": prompt_text("MEMORY_ROLE_TOOL"),
            "event": prompt_text("MEMORY_ROLE_EVENT"),
        }
        body = []
        for number, line in enumerate(window.lines, 1):
            local = datetime.fromtimestamp(line.at, zone)
            stamp = local.strftime("%Y-%m-%d %H:%M ") + t(f"tools.weekdays.{local.weekday()}")
            who = speakers[line.role]
            if line.authors:
                who += prompt_text("MEMORY_FROM", authors=join_list(sorted(line.authors)))
            body.append(f"[{number}] {stamp} {who}：{MARKER.sub('［', line.text)}")
        parts = [prompt_text("MEMORY_SECTION_CATALOG"), *catalog]
        if window.known:
            parts += [prompt_text("MEMORY_SECTION_KNOWN"), *window.known[:60]]
        parts += [prompt_text("MEMORY_SECTION_MATERIAL"), *body]
        return "\n".join(parts)

    async def ask(self, system: str, content: str) -> dict:
        cfg = self.config.value.with_session_routes(model_usage_session.get())
        if not cfg.routes.get("memory"):
            cfg.routes["memory"] = list(cfg.routes.get("light", []))
        async with asyncio.timeout(180):
            reply = await self.llm.complete(
                "memory", system, [{"role": "user", "content": content}], [], settings=cfg
            )
        if reply.calls:
            raise ValueError("memory model called tools")
        return first_object(reply.text)

    # ---- processing ----------------------------------------------------------

    async def process(self, window: Window, hint: float | None = None) -> dict:
        """Extract, check, align and merge one window; returns counts for logs and tests."""
        judge = self.memory.judge
        if hint is None:
            hint = await judge.gate(self.render(window))
        elif judge.available():
            await judge.record(
                "gate", [{"jev": hint, "final": hint >= GATE_SKIP, "from": "triage"}]
            )
        if hint is not None and hint < GATE_SKIP:
            log.debug(Text("memory.extract.log.gateSkip", hint=hint), window.label)
            return {"facts": 0, "events": 0, "dropped": 0, "skipped": True}
        prompt = prompt_text("MEMORY_EXTRACT_SHARED" if self.shared else "MEMORY_EXTRACT")
        data = await self.ask(prompt, self.render(window))
        entities = {e.ref: e for e in items(data, "entities", EntityOut)}
        stats = {"facts": 0, "events": 0, "dropped": 0}
        facts, events = [], []
        for fact in items(data, "facts", FactOut):
            checked = await self.check(
                window, fact, entities, [fact.subject] if fact.subject else []
            )
            if checked:
                facts.append((fact, checked))
            else:
                stats["dropped"] += 1
        for event in items(data, "events", EventOut):
            checked = await self.check(window, event, entities, event.participants)
            if checked:
                events.append((event, checked))
            else:
                stats["dropped"] += 1
        facts, events = await self.verify(window, facts, events, stats)
        used = {
            *(f.subject for f, _ in facts if f.subject),
            *(p for e, _ in events for p in e.participants),
        }
        owner_refs = {
            r
            for item, (proof, _) in [*facts, *events]
            if proof.authority == "owner"
            for r in ([item.subject] if isinstance(item, FactOut) else item.participants)
            if r
        }
        resolved = await self.align(window, entities, used)
        await self.memory.sighted(
            set(resolved.values()), {resolved[r] for r in owner_refs if r in resolved}
        )
        questions: dict = {"facts": [], "events": []}
        pending_facts: list[tuple | None] = []
        pending_events: list[tuple | None] = []
        for fact, (proof, extra) in facts:
            subject = resolved.get(fact.subject) if fact.subject else None
            if fact.subject and subject is None:
                stats["dropped"] += 1
                continue
            existing = await self.related_facts(subject, fact.content)
            if not existing or any(
                normalize(r["content"]) == normalize(fact.content) for r in existing
            ):
                await self.memory.write_fact(subject, fact.content, proof, valid_from=extra)
                stats["facts"] += 1
                continue
            questions["facts"].append(
                {
                    "i": len(pending_facts),
                    "candidate": fact.content,
                    "existing": [
                        {"id": ref("f", r["id"]), "content": r["content"]} for r in existing
                    ],
                }
            )
            pending_facts.append((subject, fact, proof, extra, existing, None))
        for event, (proof, extra) in events:
            people = [resolved[p] for p in event.participants if p in resolved]
            occurred, granularity = extra
            existing = await self.related_events(occurred, people)
            if not existing:
                await self.write_event(event, proof, occurred, granularity, people)
                stats["events"] += 1
                continue
            questions["events"].append(
                {
                    "i": len(pending_events),
                    "candidate": event.content,
                    "existing": [
                        {"id": ref("v", r["id"]), "content": r["content"]} for r in existing
                    ],
                }
            )
            pending_events.append((event, proof, occurred, granularity, people, existing, None))
        await self.decide_merges(pending_facts, pending_events, questions, stats)
        if any(pending_facts) or any(pending_events):
            answer = await self.ask(
                prompt_text("MEMORY_JUDGE"), canonical(open_questions(questions, pending_facts, pending_events))
            )
            for decision in answer.get("facts") or []:
                index, target, relation = self.decision(decision, pending_facts)
                if index is None:
                    continue
                subject, fact, proof, extra, existing, jev = pending_facts[index]  # type: ignore[misc]
                pending_facts[index] = None
                old = next((r for r in existing if ref("f", r["id"]) == target), None)
                await self.merge_fact(subject, fact, proof, extra, old, relation)
                stats["facts"] += 1
                if jev is not None:
                    await self.memory.judge.record(
                        "merge",
                        [{"candidate": brief(fact.content, 60), "jev": jev, "fallback": relation}],
                    )
            for decision in answer.get("events") or []:
                index, target, _ = self.decision(decision, pending_events, key="same_as")
                if index is None:
                    continue
                event, proof, occurred, granularity, people, existing, jev = pending_events[index]  # type: ignore[misc]
                pending_events[index] = None
                same = next((r["id"] for r in existing if ref("v", r["id"]) == target), None)
                await self.write_event(event, proof, occurred, granularity, people, same)
                stats["events"] += 1
                if jev is not None:
                    await self.memory.judge.record(
                        "event",
                        [{"candidate": brief(event.content, 60), "jev": jev, "fallback": target}],
                    )
            # Unanswered questions default to keeping the new item alongside the old.
            for pending in pending_facts:
                if pending is not None:
                    subject, fact, proof, extra, *_ = pending
                    await self.memory.write_fact(subject, fact.content, proof, valid_from=extra)
                    stats["facts"] += 1
            for pending in pending_events:
                if pending is not None:
                    event, proof, occurred, granularity, people, *_ = pending
                    await self.write_event(event, proof, occurred, granularity, people)
                    stats["events"] += 1
        await self.memory.load_aliases()
        log.log(
            t("memory.extract.log.done", facts=stats['facts'], events=stats['events'])
            + (t("memory.extract.log.dropped", dropped=stats['dropped']) if stats["dropped"] else ""),
            window.label,
        )
        return stats

    async def verify(self, window: Window, facts: list, events: list, stats: dict):
        """Drop items the decision model finds unsupported or instructive; grade the rest."""
        checked = [*facts, *events]
        if not checked or not self.memory.judge.available():
            return facts, events
        cited = sorted({item.evidence_line for item, _ in checked})
        lines = {f"L{n}": window.lines[n - 1].text for n in cited}
        verdicts = await self.memory.judge.verify(
            lines, [(f"L{item.evidence_line}", item.content) for item, _ in checked]
        )
        kept: list = []
        for (item, (proof, extra)), verdict in zip(checked, verdicts):
            if verdict is None:
                kept.append((item, (proof, extra)))
            elif not verdict.supported:
                stats["dropped"] += 1
                self.drop(window, item, t("memory.extract.drop.unsupported"))
            elif verdict.verified:
                kept.append((item, (replace(proof, verdict="verified"), extra)))
            else:
                # Supported but not confidently: kept, marked, and trusted a little less.
                weak = replace(proof, verdict="unverified", confidence=proof.weight * 0.8)
                kept.append((item, (weak, extra)))
        return (
            [pair for pair in kept if isinstance(pair[0], FactOut)],
            [pair for pair in kept if isinstance(pair[0], EventOut)],
        )

    async def decide_merges(self, facts: list, events: list, questions: dict, stats: dict):
        """Settle merges the decision model is sure about; leave the rest pending."""
        judge = self.memory.judge
        if not judge.available():
            return
        pairs, owners = [], []
        for index, pending in enumerate(facts):
            for row in pending[4]:
                pairs.append((pending[1].content, row["content"]))
                owners.append((index, row))
        answers = await judge.relations(pairs) if pairs else []
        grouped: dict[int, list] = {}
        for (index, row), answer in zip(owners, answers):
            grouped.setdefault(index, []).append((row, answer))
        logged = []
        for index, results in grouped.items():
            subject, fact, proof, extra, existing, _ = facts[index]
            sure = [(row, a) for row, a in results if a is not None and a[1] >= DECIDED]
            chosen = next(
                (
                    (row, a[0])
                    for rank in ("same", "newer_state", "contradicts")
                    for row, a in sure
                    if a[0] == rank
                ),
                None,
            )
            raw = [(ref("f", row["id"]), a) for row, a in results]
            if chosen is None and len(sure) < len(results):
                facts[index] = (subject, fact, proof, extra, existing, raw)
                continue  # Not sure about every pair: the text model decides.
            old, relation = chosen if chosen else (None, "compatible")
            await self.merge_fact(subject, fact, proof, extra, old, relation)
            stats["facts"] += 1
            facts[index] = None
            logged.append({"candidate": brief(fact.content, 60), "jev": raw, "final": relation})
        pairs, owners = [], []
        for index, pending in enumerate(events):
            for row in pending[5]:
                pairs.append((pending[0].content, row["content"]))
                owners.append((index, row))
        answers = await judge.same_events(pairs) if pairs else []
        grouped = {}
        for (index, row), answer in zip(owners, answers):
            grouped.setdefault(index, []).append((row, answer))
        for index, results in grouped.items():
            event, proof, occurred, granularity, people, existing, _ = events[index]
            same = next(
                (row["id"] for row, p in results if p is not None and p >= SAME_EVENT[1]), None
            )
            raw = [(ref("v", row["id"]), p) for row, p in results]
            if same is None and not all(p is not None and p <= SAME_EVENT[0] for _, p in results):
                events[index] = (event, proof, occurred, granularity, people, existing, raw)
                continue
            await self.write_event(event, proof, occurred, granularity, people, same)
            stats["events"] += 1
            events[index] = None
            logged.append({"candidate": brief(event.content, 60), "jev": raw, "final": same})
        await judge.record("merge", logged)

    @staticmethod
    def decision(raw, pending: list, key: str = "target") -> tuple[int | None, str | None, str]:
        if not isinstance(raw, dict):
            return None, None, ""
        index = raw.get("i")
        if not isinstance(index, int) or not 0 <= index < len(pending) or pending[index] is None:
            return None, None, ""
        target = raw.get(key)
        relation = raw.get("relation", "compatible")
        return index, target if isinstance(target, str) else None, str(relation)

    async def merge_fact(self, subject, fact, proof, valid_from, old, relation) -> None:
        memory = self.memory
        if old is None or relation == "compatible":
            await memory.write_fact(subject, fact.content, proof, valid_from=valid_from)
        elif relation == "same":
            await memory.reuse_fact(old, proof)
        elif relation == "newer_state":
            # Which state is newer is decided by when each was said, in code.
            await memory.write_fact(
                subject,
                fact.content,
                proof,
                valid_from=valid_from,
                old=old,
                history=proof.observed < old["as_of"],
            )
        elif relation == "contradicts":
            await memory.write_fact(
                subject, fact.content, proof, valid_from=valid_from, old=old, equal_wins=False
            )
        else:
            await memory.write_fact(subject, fact.content, proof, valid_from=valid_from)

    async def write_event(self, event, proof, occurred, granularity, people, same=None) -> None:
        await self.memory.write_event(
            event.content,
            proof,
            occurred,
            granularity,
            time_text=event.time_text if granularity == "fuzzy" else "",
            phase=event.phase,
            people=people,
            same_as=same,
        )

    async def related_facts(self, subject: int | None, content: str) -> list[dict]:
        rows = await self.memory.rows(
            "SELECT * FROM mem_fact WHERE subject IS ? AND status IN ('active','conflicted') "
            "ORDER BY as_of DESC LIMIT 40",
            (subject,),
        )
        if subject is None:
            # General knowledge only compares with facts sharing several characters.
            chars = set(content)
            rows = [r for r in rows if len(chars & set(r["content"])) >= max(4, len(chars) // 3)]
        return rows[:8]

    async def related_events(self, occurred: float | None, people: list[int]) -> list[dict]:
        if occurred is None or not people:
            return []
        marks = ",".join("?" * len(people))
        return await self.memory.rows(
            "SELECT DISTINCT v.* FROM mem_event v JOIN mem_participant p ON p.event=v.id "
            f"WHERE v.status='active' AND p.entity IN ({marks}) AND v.occurred_at BETWEEN ? AND ? "
            "LIMIT 8",
            (*people, occurred - 86400, occurred + 86400),
        )

    # ---- checks --------------------------------------------------------------

    async def check(
        self, window: Window, item: FactOut | EventOut, entities: dict, refs: list[str]
    ) -> tuple[Proof, object] | None:
        """(proof, parsed time) when an item passes every code check, else None."""
        number = item.evidence_line
        if not 1 <= number <= len(window.lines):
            return self.drop(window, item, t("memory.extract.drop.noLine"))
        line = window.lines[number - 1]
        if self.shared and line.role != "participant":
            return self.drop(window, item, t("memory.extract.drop.notMember"))
        if line.role == "assistant":
            return self.drop(window, item, t("memory.extract.drop.assistant"))
        quote = normalize(item.evidence_quote)
        if len(quote) < 2 or quote not in normalize(line.text):
            return self.drop(window, item, t("memory.extract.drop.quoteMissing"))
        content = item.content.strip()
        if not content or relative_words(content):
            return self.drop(window, item, t("memory.extract.drop.relativeTime"))
        for value in refs:
            if value not in window.catalog and not (value.startswith("new:") and value in entities):
                return self.drop(window, item, t("memory.extract.drop.unknownId", value=value))
        if line.role == "owner":
            authority = "owner"
        elif line.role == "event" and line.authors & set(refs):
            authority = "direct"  # The sender speaking about themselves.
        else:
            authority = "derived"
        try:
            await self.memory.guard_tombstone(content, authority)
        except PublicError:
            return self.drop(window, item, t("memory.extract.drop.forgotten"))
        proof = Proof(authority, line.source, item.evidence_quote, line.at)
        if isinstance(item, FactOut):
            start = None
            if item.valid_from:
                try:
                    start = parse_time(item.valid_from, self.memory.zone)[0]
                except PublicError:
                    start = None
            return proof, start
        if item.time:
            try:
                occurred, granularity = parse_time(item.time, self.memory.zone)
            except PublicError:
                return self.drop(window, item, t("memory.extract.drop.notAbsolute"))
            if item.phase == "happened" and occurred > line.at + 3600:
                return self.drop(window, item, t("memory.extract.drop.future"))
        elif item.time_text.strip():
            occurred, granularity = None, "fuzzy"
        else:
            return self.drop(window, item, t("memory.extract.drop.noTime"))
        return proof, (occurred, granularity)

    def drop(self, window: Window, item, reason: str) -> None:
        log.debug(Text("memory.extract.log.droppedItem", brief=brief(item.content, 40), reason=reason), window.label)
        return None

    # ---- entity alignment ------------------------------------------------------

    def sources_text(self, window: Window) -> str:
        return normalize("\n".join(line.text for line in window.lines if line.role != "assistant"))

    async def align(self, window: Window, entities: dict, used: set[str]) -> dict[str, int]:
        """Catalog and new refs → entity ids; uncertain matches go to the owner."""
        memory = self.memory
        text = self.sources_text(window)
        resolved: dict[str, int] = {}
        questions, pending = [], []
        for value in sorted(used):
            entry = window.catalog.get(value)
            out = entities.get(value)
            names = [
                n.strip()
                for n in ([out.name, *out.aliases] if out else [])
                if n.strip() and normalize(n) in text
            ]
            if entry and entry.entity is not None:
                resolved[value] = entry.entity
                if names and entry.entity != window.owner:
                    await memory.add_aliases(entry.entity, names, "extracted")
                continue
            if entry:
                names = list(dict.fromkeys([entry.name, *names]))
            if not names:
                continue  # A name the source never mentions is not evidence of anyone.
            kind = out.kind if out else "person"
            exact = sorted({e for n in names for e in memory.aliases.get(n.casefold(), set())})
            if len(exact) == 1:
                await self.link(exact[0], names, entry)
                resolved[value] = exact[0]
                continue
            candidates = exact or self.similar(names)
            if not candidates:
                resolved[value] = await self.create(kind, names, entry)
                continue
            cards = await memory.load([ref("e", c) for c in candidates[:5]])
            questions.append(
                {
                    "i": len(pending),
                    "new": "、".join(names) + (f" <{entry.identity}>" if entry else ""),
                    "existing": [{"id": k, "card": memory.plain(v)} for k, v in cards.items()],
                }
            )
            pending.append((value, kind, names, entry, list(cards)))
        if not questions:
            return resolved
        decisions = await self.decide_entities(questions)
        unsure = [q for q in questions if q["i"] not in decisions]
        if unsure:
            answer = await self.ask(prompt_text("MEMORY_JUDGE"), canonical({"entities": unsure}))
            for raw in answer.get("entities") or []:
                if isinstance(raw, dict) and raw.get("i") in {q["i"] for q in unsure}:
                    decisions[raw["i"]] = raw
        for index, (value, kind, names, entry, options) in enumerate(pending):
            decision = decisions.get(index, {})
            target = decision.get("target") if decision.get("target") in options else None
            relation = decision.get("relation") if target else "different"
            if relation == "same" and target:
                key = parse_ref(target, "e")[1]
                await self.link(key, names, entry)
                resolved[value] = key
                continue
            key = await self.create(kind, names, entry)
            resolved[value] = key
            if relation == "maybe_same" and target:
                other = await memory.row("e", parse_ref(target, "e")[1])
                async with memory.tx() as db:
                    await memory.open_conflict(
                        db,
                        "identity",
                        [target, ref("e", key)],
                        f"「{names[0]}」"
                        + (f"（{entry.identity}）" if entry else "")
                        + t("memory.extract.conflict.maybeSame", target=target, name=other['name'])
                        + LABELS.get(kind, t("memory.extract.conflict.thing")),
                    )
        return resolved

    async def decide_entities(self, questions: list[dict]) -> dict[int, dict]:
        """Alignment answers the decision model is sure about, keyed by question index."""
        judge = self.memory.judge
        if not judge.available():
            return {}
        pairs = [(q["new"], e["card"]) for q in questions for e in q["existing"]]
        answers = await judge.same_entities(pairs)
        decided, logged, position = {}, [], 0
        for question in questions:
            results = []
            for existing in question["existing"]:
                results.append((existing["id"], answers[position]))
                position += 1
            sure = [(key, a) for key, a in results if a is not None and a[1] >= DECIDED]
            same = next((key for key, a in sure if a[0] == "same"), None)
            maybe = next((key for key, a in sure if a[0] == "maybe_same"), None)
            if same:
                decided[question["i"]] = {"target": same, "relation": "same"}
            elif len(sure) == len(results):
                decided[question["i"]] = (
                    {"target": maybe, "relation": "maybe_same"}
                    if maybe
                    else {"target": None, "relation": "different"}
                )
            if question["i"] in decided:
                logged.append(
                    {"new": question["new"], "jev": results, "final": decided[question["i"]]}
                )
        await judge.record("align", logged)
        return decided

    def similar(self, names: list[str]) -> list[int]:
        """Entities whose name contains, or is contained in, one of these names."""
        found = []
        for alias, entities in self.memory.aliases.items():
            for name in names:
                folded = name.casefold()
                if min(len(alias), len(folded)) >= 2 and (alias in folded or folded in alias):
                    found += entities
        return sorted(set(found))

    async def link(self, entity: int, names: list[str], entry: Entry | None) -> None:
        await self.memory.add_aliases(entity, names, "extracted")
        if entry and entry.identity:
            await self.memory.bind_identity(entry.identity, entity, entry.name)

    async def create(self, kind: str, names: list[str], entry: Entry | None) -> int:
        key = await self.memory.create_entity(kind, names[0], names[1:], "extracted")
        if entry and entry.identity:
            await self.memory.bind_identity(entry.identity, key, entry.name)
        await self.memory.load_aliases()
        return key
