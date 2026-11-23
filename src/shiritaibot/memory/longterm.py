"""Long-term memory: people, things, facts and events with evidence (docs/MEMORY.md)."""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from contextlib import asynccontextmanager
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import jieba

from shiritaibot.core import Context, PublicError, fingerprint, now
from shiritaibot.core.logs import brief, get_logger
from shiritaibot.i18n import Text, TranslatedDict, join_list, t
from shiritaibot.memory.judge import MemoryJudge
from shiritaibot.memory.times import month, parse_bound, parse_time, relative_words, render_time
from shiritaibot.memory.vectors import VectorIndex
from shiritaibot.store import MEMORY_PROFILE_FILTER

log = get_logger("memory.longterm.log.source")

REF = re.compile(r"^([efvc])(\d+)$")
TABLES = {"e": "mem_entity", "f": "mem_fact", "v": "mem_event", "c": "mem_conflict"}
TYPES = {"e": "entity", "f": "fact", "v": "event", "c": "conflict"}
LABELS = TranslatedDict({"person": "memory.longterm.labels.person", "thing": "memory.longterm.labels.thing"})
AUTHORITY = {"agent": 0, "derived": 1, "direct": 2, "owner": 3}
CONFIDENCE = {"agent": 0.5, "derived": 0.7, "direct": 0.85, "owner": 0.95}
SOURCES = TranslatedDict({
    "owner": "memory.longterm.sources.owner",
    "msg": "memory.longterm.sources.msg",
    "tool": "memory.longterm.sources.tool",
    "event": "memory.longterm.sources.event",
    "legacy": "memory.longterm.sources.legacy",
})
EVENT_SOURCES = TranslatedDict({"email": "memory.longterm.sources.email", "routine": "memory.longterm.sources.routine"})
LIVE = ("active", "conflicted")
CARD_FACTS = 6
# Function words that never identify a memory on their own.
STOPWORDS = set(
    "一下 一个 什么 怎么 怎样 为什么 这个 那个 这些 那些 我们 你们 他们 她们 它们 就是 还是 "  # i18n: ok
    "可以 没有 现在 帮我 帮忙 一起 已经 然后 因为 所以 但是 如果 知道 告诉 看看 关于 记得 记住 "  # i18n: ok
    "the and for with that this what how".split()
)


def ref(prefix: str, key: int) -> str:
    return f"{prefix}{key}"


def parse_ref(value: str, prefixes: str = "efvc") -> tuple[str, int]:
    match = REF.match(value.strip())
    if not match or match.group(1) not in prefixes:
        raise PublicError("memory.longterm.refInvalid", brief=brief(value, 20))
    return match.group(1), int(match.group(2))


QUOTE_MARKS = frozenset("\"'“”‘’\\`")
QUOTE_IGNORED = re.compile(r"[\s" + re.escape("".join(QUOTE_MARKS)) + r"]+")


def normalize(text: str) -> str:
    return QUOTE_IGNORED.sub("", text).casefold()


def quote_span(content: str, target: str, *, loose: bool) -> tuple[str, bool] | None:
    """Map a normalized match (including casefold expansions) back to source offsets."""
    chars = []
    offsets = []
    for i, char in enumerate(content):
        if (
            char.isspace()
            or char in QUOTE_MARKS
            or (loose and unicodedata.category(char).startswith("P"))
        ):
            continue
        folded = char.casefold()
        chars.append(folded)
        offsets.extend([i] * len(folded))
    haystack = "".join(chars)
    start = haystack.find(target)
    if start < 0:
        return None
    end = start + len(target) - 1
    return content[offsets[start] : offsets[end] + 1], haystack.find(target, start + 1) >= 0


@dataclass(frozen=True, slots=True)
class Proof:
    """Where a memory came from: authority, source ref, verbatim quote and when it was said."""

    authority: str
    source: str
    quote: str
    observed: float
    verdict: str = "quoted"  # quoted (code-checked) | verified | unverified (decision model)
    confidence: float | None = None  # Overrides the authority default when set.

    @property
    def weight(self) -> float:
        return self.confidence if self.confidence is not None else CONFIDENCE[self.authority]


def is_live(item: dict) -> bool:
    """Whether an item takes part in default recall."""
    if item["type"] == "entity":
        return item["status"] != "archived" and item["role"] == ""
    if item["type"] == "fact":
        return item["status"] in LIVE
    if item["type"] == "event":
        return item["status"] == "active"
    return item["status"] == "open"


def keywords(words: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for word in words:
        word = word.strip().casefold()
        if len(word) < 2 or word in STOPWORDS or not any(c.isalnum() for c in word):
            continue
        seen[word] = None
    return list(seen)


def stem(word: str) -> str:
    """A light English stem ("meetings" / "meeting" -> "meet", "studies" -> "study"),
    conservative on purpose; anything that isn't a plain ASCII word is returned folded."""
    word = word.casefold()
    if not (word.isascii() and word.isalpha()):
        return word
    while len(word) > 4:
        for suffix, replacement in (
            ("ies", "y"),
            ("ied", "y"),
            ("ing", ""),
            ("ed", ""),
            ("es", ""),
            ("s", ""),
        ):
            if not word.endswith(suffix) or len(word) - len(suffix) < 3:
                continue
            if suffix == "s" and word.endswith(("ss", "us", "is")):
                continue
            base = word[: -len(suffix)] + replacement
            if suffix == "es" and not base.endswith(("s", "x", "z", "ch", "sh")):
                base = word[:-1]  # "notes" -> "note", but "boxes" -> "box"
            if suffix in ("ing", "ed") and len(base) > 3 and base[-1] == base[-2] and base[-1] not in "lsz":
                base = base[:-1]  # "running" -> "run", "stopped" -> "stop"
            break
        else:
            return word
        if base == word:
            return word
        word = base
    return word


def tokenize_sync(text: str, tokenizer: jieba.Tokenizer = jieba.dt) -> list[str]:
    words = list(tokenizer.cut_for_search(text))
    # English words are indexed and searched with their stems too, so "meetings" finds
    # "meeting"; Chinese tokens are unchanged.
    return words + [base for word in words if (base := stem(word)) != word.casefold()]


async def tokenize(text: str) -> list[str]:
    return await asyncio.to_thread(tokenize_sync, text)


class MemoryManager:
    def __init__(self, store, config, llm=None, *, history_store=None):
        self.store, self.config, self.llm = store, config, llm
        self.history_store = history_store if history_store is not None else store
        self.tokenizer = jieba.dt if history_store is None else jieba.Tokenizer()
        self.vectors = VectorIndex(store, llm)
        self.judge = MemoryJudge(llm, config, store)
        self.aliases: dict[str, set[int]] = {}
        self.wake = asyncio.Event()

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.config.value.proactive.timezone)

    # ---- storage helpers -------------------------------------------------

    @asynccontextmanager
    async def tx(self):
        async with self.store.lock:
            try:
                yield self.store.db
                await self.store.db.commit()
            except BaseException:
                await self.store.db.rollback()
                raise

    async def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        return await self.store.query(sql, args)

    async def row(self, prefix: str, key: int) -> dict:
        found = await self.rows(f"SELECT * FROM {TABLES[prefix]} WHERE id=?", (key,))
        if not found:
            raise PublicError("memory.longterm.refMissing", ref=ref(prefix, key))
        return found[0]

    async def open(self) -> None:
        stamp = now().timestamp()
        name = self.config.value.owner_name
        owner = await self.rows("SELECT id,name FROM mem_entity WHERE role='owner'")
        if not owner:
            await self.store.execute(
                "INSERT INTO mem_entity(kind,name,role,status,created,updated) "
                "VALUES('person',?,'owner','confirmed',?,?)",
                (name, stamp, stamp),
            )
        elif owner[0]["name"] != name:
            await self.store.execute(
                "UPDATE mem_entity SET name=?,updated=? WHERE id=?", (name, stamp, owner[0]["id"])
            )
        await self.migrate_legacy()
        await self.add_stems()
        await self.load_aliases()

    async def add_stems(self) -> None:
        """One time: reindex entries written before English stems were indexed."""
        if await self.store.get("memory_migration", "stems"):
            return
        refs = [row["id"] for row in await self.rows("SELECT id FROM mem_fts")]
        for start in range(0, len(refs), 200):
            await self.index(refs[start : start + 200])
        await self.store.put("memory_migration", "stems", {"rows": len(refs)})

    async def migrate_legacy(self) -> None:
        """Carry explicit memories from the flat v1 table over once, as owner-stated facts."""
        if await self.store.get("memory_migration", "legacy"):
            return
        rows = await self.rows("SELECT * FROM memory WHERE status='active' ORDER BY created")
        for row in rows:
            async with self.tx() as db:
                cursor = await db.execute(
                    "INSERT INTO mem_fact(subject,content,as_of,authority,confidence,status,"
                    "created,updated) VALUES(NULL,?,?,'owner',?,'active',?,?)",
                    (
                        row["content"],
                        row["created"],
                        CONFIDENCE["owner"],
                        row["created"],
                        now().timestamp(),
                    ),
                )
                key = ref("f", cursor.lastrowid or 0)
                await db.execute(
                    "INSERT INTO mem_evidence(target,source,quote,observed_at,verdict) "
                    "VALUES(?,?,?,?,'unverified')",
                    (key, f"legacy:{row['id']}", row["content"], row["created"]),
                )
            await self.index([key])
        await self.store.put("memory_migration", "legacy", {"facts": len(rows)})
        if rows:
            log.log(Text("memory.longterm.log.migrated", count=len(rows)))

    async def owner_id(self) -> int:
        return (await self.rows("SELECT id FROM mem_entity WHERE role='owner'"))[0]["id"]

    # ---- aliases and entity linking -----------------------------------------

    async def load_aliases(self) -> None:
        rows = await self.rows(
            "SELECT a.alias AS name, a.entity AS id FROM mem_alias a JOIN mem_entity e "
            "ON e.id=a.entity WHERE e.status!='archived' AND e.role='' "
            "UNION SELECT name, id FROM mem_entity WHERE status!='archived' AND role=''"
        )
        index: dict[str, set[int]] = {}
        for row in rows:
            index.setdefault(row["name"].casefold(), set()).add(row["id"])
        self.aliases = index
        # Whole names keep the tokenizer from splitting people and projects apart.
        words = [row["name"] for row in rows]
        await asyncio.to_thread(lambda: [self.tokenizer.add_word(word) for word in words])

    async def tokens(self, text: str) -> list[str]:
        return await asyncio.to_thread(tokenize_sync, text, self.tokenizer)

    def link(self, text: str) -> list[int]:
        """Entities whose name or alias occurs in the text, longest match first."""
        folded = text.casefold()
        found: dict[int, int] = {}
        for alias, entities in self.aliases.items():
            if len(alias) < 2:
                continue
            if alias.isascii():
                hit = re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", folded)
            else:
                hit = alias in folded
            if hit:
                for entity in entities:
                    found[entity] = max(found.get(entity, 0), len(alias))
        return sorted(found, key=lambda e: -found[e])

    async def resolve_entity(self, value: str) -> int:
        """An entity id from e<id> or an exact, unambiguous name or alias."""
        value = value.strip()
        if REF.match(value):
            _, key = parse_ref(value, "e")
            row = await self.row("e", key)
            if row["status"] == "archived":
                if row["merged_into"]:
                    return row["merged_into"]
                raise PublicError("memory.longterm.entityForgotten", key=key)
            return key
        if value.casefold() in {self.config.value.owner_name.casefold(), t("memory.longterm.owner"), "owner"}:
            return await self.owner_id()
        found = sorted(self.aliases.get(value.casefold(), set()))
        if len(found) == 1:
            return found[0]
        if not found:
            raise PublicError(
                "memory.longterm.entityUnknown", brief=brief(value, 30)
            )
        names = await self.entity_names(found)
        raise PublicError(
            t("memory.longterm.entityAmbiguous", brief=brief(value, 30))
            + join_list(f"e{key} {names[key]}" for key in found)
            + t("memory.longterm.pickId")
        )

    async def entity_names(self, keys) -> dict[int, str]:
        keys = list(keys)
        if not keys:
            return {}
        marks = ",".join("?" * len(keys))
        rows = await self.rows(f"SELECT id,name FROM mem_entity WHERE id IN ({marks})", tuple(keys))
        return {row["id"]: row["name"] for row in rows}

    # ---- evidence ----------------------------------------------------------

    async def evidence(
        self, ctx: Context, quote: str, *, strict: bool = False, min_len: int = 2
    ) -> tuple[str, str, str]:
        """Return authority, source and an actual span of this turn's archived text."""
        target = normalize(quote)
        if len(target) < min_len:
            raise PublicError("memory.longterm.evidenceShort")
        rows = await self.history_store.query(
            f"SELECT data FROM messages WHERE session=? AND turn=? AND {MEMORY_PROFILE_FILTER} ORDER BY seq",
            (ctx.session, ctx.turn, ctx.memory_profile),
        )
        sources = []
        samples: list[str] = []
        for item in rows:
            message = json.loads(item["data"])
            if message.get("role") not in {"user", "tool"}:
                continue
            content = message.get("_original_content", message.get("content", ""))
            if not isinstance(content, str):
                continue
            if len(samples) < 3 and content.strip():
                samples.append(brief(content, 40))
            if message["role"] == "user" and ctx.owner and not ctx.background:
                authority, kind = "owner", "owner"
            else:
                authority = "derived"
                kind = "event:proactive" if message["role"] == "user" else "tool"
            sources.append((authority, f"{kind}:{ctx.turn}", content))

        # Preserve the exact tier's existing precedence for the owner's own words.
        for authority, source, content in sorted(sources, key=lambda row: row[0] != "owner"):
            if quote in content:
                return authority, source, quote
            if target in normalize(content):
                match = quote_span(content, target, loose=False)
                if match:
                    return authority, source, match[0]

        compact = "".join(c for c in target if not unicodedata.category(c).startswith("P"))
        if not strict and len(compact) >= 2:
            found = None
            for authority, source, content in sources:
                match = quote_span(content, compact, loose=True)
                if match:
                    if found or match[1]:
                        raise PublicError("memory.longterm.evidenceAmbiguous")
                    found = authority, source, match[0]
            if found:
                return found
        hint = t("memory.longterm.evidenceSources", sources=' / '.join(samples)) if samples else ""
        raise PublicError(
            t("memory.longterm.evidenceRule") + hint
        )

    async def owner_words(self, ctx: Context, quote: str) -> str:
        authority, source, _ = await self.evidence(ctx, quote, strict=True, min_len=1)
        if authority != "owner":
            raise PublicError("memory.longterm.ownerQuote")
        return source

    # ---- indexing ----------------------------------------------------------

    async def documents(self, refs: list[str] | None = None) -> dict[str, str]:
        """Searchable text for live items: what FTS and embeddings see."""
        items = await self.load(refs) if refs is not None else await self.live()
        return {key: self.plain(item) for key, item in items.items() if is_live(item)}

    async def live(self) -> dict[str, dict]:
        refs = [
            ref("e", r["id"])
            for r in await self.rows(
                "SELECT id FROM mem_entity WHERE status!='archived' AND role=''"
            )
        ]
        refs += [
            ref("f", r["id"])
            for r in await self.rows(
                "SELECT id FROM mem_fact WHERE status IN ('active','conflicted')"
            )
        ]
        refs += [
            ref("v", r["id"])
            for r in await self.rows("SELECT id FROM mem_event WHERE status='active'")
        ]
        return await self.load(refs)

    async def index(self, refs: list[str]) -> None:
        items = await self.load(refs)
        texts = {key: self.plain(item) for key, item in items.items()}
        tokens = {key: " ".join(await self.tokens(text)) for key, text in texts.items()}
        async with self.tx() as db:
            for key in refs:
                await db.execute("DELETE FROM mem_fts WHERE id=?", (key,))
                item = items.get(key)
                if item and is_live(item):
                    await db.execute(
                        "INSERT INTO mem_fts(id,tokens) VALUES(?,?)", (key, tokens[key])
                    )
        self.wake.set()

    async def bm25(self, words: list[str], limit: int = 30) -> list[tuple[str, int]]:
        """(ref, matched keyword count), best BM25 first."""
        if not words:
            return []
        match = " OR ".join('"' + w.replace('"', '""') + '"' for w in words[:32])
        rows = await self.rows(
            "SELECT id,tokens FROM mem_fts WHERE mem_fts MATCH ? ORDER BY bm25(mem_fts) LIMIT ?",
            (match, limit),
        )
        result = []
        for row in rows:
            present = set(row["tokens"].casefold().split())
            result.append((row["id"], sum(word in present for word in words)))
        return result

    # ---- loading and rendering ---------------------------------------------

    async def load(self, refs: list[str]) -> dict[str, dict]:
        """Rows with what rendering needs: aliases, subjects, participants, sources."""
        groups: dict[str, list[int]] = {"e": [], "f": [], "v": [], "c": []}
        for value in refs:
            prefix, key = parse_ref(value)
            groups[prefix].append(key)
        items: dict[str, dict] = {}

        async def fetch(prefix: str, sql: str = "") -> list[dict]:
            keys = groups[prefix]
            if not keys:
                return []
            marks = ",".join("?" * len(keys))
            return await self.rows(
                sql or f"SELECT * FROM {TABLES[prefix]} WHERE id IN ({marks})", tuple(keys)
            )

        facts = await fetch("f")
        subjects = {row["subject"] for row in facts if row["subject"] is not None}
        groups["e"] = sorted(set(groups["e"]) | subjects)
        entities = await fetch("e")
        if entities:
            marks = ",".join("?" * len(entities))
            alias_rows = await self.rows(
                f"SELECT alias,entity FROM mem_alias WHERE entity IN ({marks}) ORDER BY first_seen",
                tuple(row["id"] for row in entities),
            )
        else:
            alias_rows = []
        by_entity = {
            row["id"]: {**row, "aliases": [], "type": "entity", "ref": ref("e", row["id"])}
            for row in entities
        }
        for row in alias_rows:
            by_entity[row["entity"]]["aliases"].append(row["alias"])
        for row in by_entity.values():
            items[row["ref"]] = row
        for row in facts:
            items[ref("f", row["id"])] = {
                **row,
                "type": "fact",
                "ref": ref("f", row["id"]),
                "entity": by_entity.get(row["subject"]),
            }
        events = await fetch("v")
        if events:
            marks = ",".join("?" * len(events))
            keys = tuple(row["id"] for row in events)
            people = await self.rows(
                "SELECT p.event,e.id,e.name FROM mem_participant p JOIN mem_entity e "
                f"ON e.id=p.entity WHERE p.event IN ({marks})",
                keys,
            )
            sources = await self.rows(
                f"SELECT target,source FROM mem_evidence WHERE target IN ({marks}) ORDER BY id",
                tuple(ref("v", key) for key in keys),
            )
            for row in events:
                key = ref("v", row["id"])
                source = next((s["source"] for s in sources if s["target"] == key), "")
                items[key] = {
                    **row,
                    "type": "event",
                    "ref": key,
                    "participants": [p for p in people if p["event"] == row["id"]],
                    "source": source,
                }
        for row in await fetch("c"):
            items[ref("c", row["id"])] = {
                **row,
                "type": "conflict",
                "ref": ref("c", row["id"]),
                "items": json.loads(row["items"]),
            }
        return {key: items[key] for key in refs if key in items}

    def head(self, entity: dict) -> str:
        text = entity["name"]
        if entity["aliases"]:
            text += t("memory.longterm.render.aliases") + join_list(entity["aliases"]) + "）"
        if entity["status"] == "candidate":
            text += t("memory.longterm.render.new")
        return text

    def fact_text(self, fact: dict) -> str:
        text = brief(fact["content"], 240)
        if fact["status"] == "conflicted":
            text += t("memory.longterm.render.conflicted")
        elif fact["status"] == "superseded":
            text += (
                t("memory.longterm.render.superseded", superseded_by=fact['superseded_by']) if fact["superseded_by"] else t("memory.longterm.render.invalid")
            )
        return text + f" #{fact['ref']}"

    def facts_text(self, facts: list[dict]) -> str:
        """Facts in order, each run sharing an as-of month marked once."""
        parts, run, current = [], [], None
        for fact in facts:
            stamp = month(fact["as_of"], self.zone)
            if run and stamp != current:
                parts.append("；".join(run) + t("memory.longterm.render.asOf", current=current))
                run = []
            run.append(self.fact_text(fact))
            current = stamp
        if run:
            parts.append("；".join(run) + t("memory.longterm.render.asOf", current=current))
        return "；".join(parts)

    def source_label(self, source: str) -> str:
        kind, _, rest = source.partition(":")
        if kind == "event":
            name = rest.split(":", 1)[0]
            return EVENT_SOURCES.get(name, SOURCES["event"])
        return SOURCES.get(kind, "")

    def event_text(self, event: dict) -> str:
        when = render_time(
            event["occurred_at"], event["granularity"], self.zone, event["time_text"]
        )
        if event["granularity"] == "fuzzy" and event["time_text"]:
            when += t("memory.longterm.render.mentioned", month=month(event['created'], self.zone))
        text = f"{when} {brief(event['content'], 240)}"
        if event["phase"] == "planned":
            text = t("memory.longterm.render.planned") + text
            if event["occurred_at"] and event["occurred_at"] < now().timestamp():
                text += t("memory.longterm.render.unconfirmed")
        elif event["phase"] == "cancelled":
            text += t("memory.longterm.render.cancelled")
        if event["status"] == "superseded":
            text += t("memory.longterm.render.corrected", superseded_by=event['superseded_by'])
        label = self.source_label(event["source"])
        return text + (f"〔{label}〕" if label else "")

    def plain(self, item: dict) -> str:
        if item["type"] == "entity":
            text = self.head(item)
            return text + (f"：{item['summary']}" if item["summary"] else "")
        if item["type"] == "fact":
            prefix = self.head(item["entity"]) + "：" if item["entity"] else ""
            return prefix + item["content"]
        if item["type"] == "event":
            names = " ".join(p["name"] for p in item["participants"])
            return self.event_text(item) + (f" {names}" if names else "")
        return item["note"]

    def line(self, item: dict, facts: list[dict] | None = None) -> str:
        """One injected or listed line; entity cards carry the given facts."""
        if item["type"] == "entity":
            label = t("memory.longterm.labels.personCap") if item["role"] == "owner" else LABELS.get(item["kind"], t("memory.longterm.labels.thing"))
            body = "；".join(x for x in [item["summary"], self.facts_text(facts or [])] if x)
            return (
                f"[{label}] {self.head(item)}" + (f"：{body}" if body else "") + f"  #{item['ref']}"
            )
        if item["type"] == "fact":
            if item["entity"]:
                return self.line(item["entity"], [item])
            return t("memory.longterm.render.fact", facts_text=self.facts_text([item]), ref=item['ref'])
        if item["type"] == "event":
            return t("memory.longterm.render.event", event_text=self.event_text(item), ref=item['ref'])
        return t("memory.longterm.render.pending", ref=item['ref'], note=item['note'])

    async def card_facts(self, entity: int, limit: int = CARD_FACTS) -> list[str]:
        rows = await self.rows(
            "SELECT id FROM mem_fact WHERE subject=? AND status IN ('active','conflicted') "
            "ORDER BY as_of DESC LIMIT ?",
            (entity, limit),
        )
        return [ref("f", row["id"]) for row in rows]

    async def entity_events(self, entities: list[int], limit: int = 5) -> list[str]:
        """Events involving the entities: most shared participants first, then newest."""
        if not entities:
            return []
        marks = ",".join("?" * len(entities))
        rows = await self.rows(
            "SELECT v.id, COUNT(*) AS shared FROM mem_participant p JOIN mem_event v "
            f"ON v.id=p.event WHERE p.entity IN ({marks}) AND v.status='active' "
            "GROUP BY v.id ORDER BY shared DESC, COALESCE(v.occurred_at, v.created) DESC LIMIT ?",
            (*entities, limit),
        )
        return [ref("v", row["id"]) for row in rows]

    # ---- writes ------------------------------------------------------------

    async def save_entity(
        self,
        ctx: Context,
        name: str,
        kind: str = "",
        aliases: list[str] | None = None,
        summary: str | None = None,
        target: str = "",
        merge: list[str] | None = None,
    ) -> dict:
        name = name.strip()
        aliases = [a.strip() for a in aliases or [] if a.strip()]
        if relative_words(summary or ""):
            raise PublicError("memory.longterm.summaryRelative")
        trusted = ctx.owner and not ctx.background
        stamp = now().timestamp()
        if target:
            key = await self.resolve_entity(target)
        else:
            matches = sorted(self.aliases.get(name.casefold(), set()))
            if len(matches) > 1:
                names = await self.entity_names(matches)
                raise PublicError(
                    t("memory.longterm.nameAmbiguous", name=name)
                    + join_list(f"e{m} {names[m]}" for m in matches)
                    + t("memory.longterm.useEntity")
                )
            key = matches[0] if matches else 0
        if not key and kind not in LABELS:
            raise PublicError("memory.longterm.entityKind")
        async with self.tx() as db:
            if key:
                row = (
                    await (
                        await db.execute("SELECT * FROM mem_entity WHERE id=?", (key,))
                    ).fetchall()
                )[0]
                if name and name != row["name"]:
                    # A rename keeps the old name reachable as an alias.
                    aliases.append(row["name"])
                    await db.execute("UPDATE mem_entity SET name=? WHERE id=?", (name, key))
                if summary is not None:
                    await db.execute(
                        "UPDATE mem_entity SET summary=? WHERE id=?", (summary.strip(), key)
                    )
                if trusted and row["status"] == "candidate":
                    await db.execute("UPDATE mem_entity SET status='confirmed' WHERE id=?", (key,))
                await db.execute("UPDATE mem_entity SET updated=? WHERE id=?", (stamp, key))
            else:
                cursor = await db.execute(
                    "INSERT INTO mem_entity(kind,name,summary,status,created,updated) "
                    "VALUES(?,?,?,?,?,?)",
                    (
                        kind,
                        name,
                        (summary or "").strip(),
                        "confirmed" if trusted else "candidate",
                        stamp,
                        stamp,
                    ),
                )
                key = cursor.lastrowid or 0
            current = (
                await (
                    await db.execute("SELECT name FROM mem_entity WHERE id=?", (key,))
                ).fetchall()
            )[0]["name"]
            for alias in dict.fromkeys(aliases):
                if alias.casefold() == current.casefold():
                    continue
                await db.execute(
                    "INSERT INTO mem_alias VALUES(?,?,?,?,?) ON CONFLICT(alias,entity) "
                    "DO UPDATE SET last_seen=excluded.last_seen",
                    (alias, key, "owner" if trusted else "agent", stamp, stamp),
                )
        merged = []
        for other in merge or []:
            source = await self.resolve_entity(other)
            if source != key:
                await self.merge_entities(key, source)
                merged.append(ref("e", source))
        await self.load_aliases()
        refs = [ref("e", key), *await self.card_facts(key, 1000)]
        await self.index(refs)
        shared = [
            alias for alias in [name, *aliases] if len(self.aliases.get(alias.casefold(), ())) > 1
        ]
        item = (await self.load([ref("e", key)]))[ref("e", key)]
        log.log(Text("memory.longterm.log.entitySaved", head=self.head(item)))
        result: dict = {"id": ref("e", key), "text": self.line(item)}
        if merged:
            result["merged"] = merged
        if shared:
            result["note"] = t("memory.longterm.sharedNames") + join_list(
                shared
            )
        return result

    async def create_entity(
        self, kind: str, name: str, aliases: list[str], source: str, *, confirmed: bool = False
    ) -> int:
        stamp = now().timestamp()
        async with self.tx() as db:
            cursor = await db.execute(
                "INSERT INTO mem_entity(kind,name,status,created,updated) VALUES(?,?,?,?,?)",
                (kind, name, "confirmed" if confirmed else "candidate", stamp, stamp),
            )
            key = cursor.lastrowid or 0
        await self.add_aliases(key, aliases, source)
        log.log(Text("memory.longterm.log.learned", kind=LABELS.get(kind, t("memory.longterm.labels.thing")), name=name))
        return key

    async def add_aliases(self, entity: int, aliases: list[str], source: str) -> None:
        stamp = now().timestamp()
        rows = await self.rows("SELECT name FROM mem_entity WHERE id=?", (entity,))
        name = rows[0]["name"].casefold() if rows else ""
        values = [
            (alias, entity, source, stamp, stamp)
            for alias in dict.fromkeys(a.strip() for a in aliases)
            if alias and alias.casefold() != name
        ]
        if values:
            async with self.tx() as db:
                await db.executemany(
                    "INSERT INTO mem_alias VALUES(?,?,?,?,?) ON CONFLICT(alias,entity) "
                    "DO UPDATE SET last_seen=excluded.last_seen",
                    values,
                )

    async def identity_entity(self, identity: str) -> int | None:
        """The live entity a platform identity belongs to, following merges."""
        rows = await self.rows("SELECT entity FROM mem_identity WHERE identity=?", (identity,))
        if not rows:
            return None
        entity = await self.row("e", rows[0]["entity"])
        if entity["status"] == "archived":
            return entity["merged_into"]
        return entity["id"]

    async def bind_identity(self, identity: str, entity: int, display: str = "") -> None:
        stamp = now().timestamp()
        await self.store.execute(
            "INSERT INTO mem_identity VALUES(?,?,?,?,?) ON CONFLICT(identity) DO UPDATE SET "
            "entity=excluded.entity,display=excluded.display,last_seen=excluded.last_seen",
            (identity, entity, display, stamp, stamp),
        )

    async def sighted(self, entities: set[int], confirmed: set[int]) -> None:
        """Count a sighting; candidates seen three times or vouched for by the owner are kept."""
        stamp = now().timestamp()
        async with self.tx() as db:
            for entity in entities:
                await db.execute(
                    "UPDATE mem_entity SET seen_count=seen_count+1,last_seen=?,"
                    "status=CASE WHEN status='candidate' AND (seen_count+1>=3 OR ?) "
                    "THEN 'confirmed' ELSE status END WHERE id=?",
                    (stamp, entity in confirmed, entity),
                )

    async def reuse_fact(self, row: dict, proof: Proof) -> None:
        """Another piece of evidence for an existing fact."""
        stronger = max(row["authority"], proof.authority, key=AUTHORITY.__getitem__)
        async with self.tx() as db:
            await db.execute(
                "UPDATE mem_fact SET as_of=MAX(as_of,?),authority=?,confidence=MAX(confidence,?),"
                "updated=? WHERE id=?",
                (
                    proof.observed,
                    stronger,
                    proof.weight,
                    now().timestamp(),
                    row["id"],
                ),
            )
            await self.add_evidence(db, ref("f", row["id"]), proof)
        await self.index([ref("f", row["id"])])

    async def merge_entities(self, target: int, source: int) -> None:
        if source == await self.owner_id():
            raise PublicError("memory.longterm.ownerMerge")
        stamp = now().timestamp()
        affected = await self.card_facts(source, 10000)
        async with self.tx() as db:
            row = (
                await (
                    await db.execute("SELECT name FROM mem_entity WHERE id=?", (source,))
                ).fetchall()
            )[0]
            await db.execute(
                "INSERT OR IGNORE INTO mem_alias SELECT alias,?,source,first_seen,last_seen "
                "FROM mem_alias WHERE entity=?",
                (target, source),
            )
            await db.execute(
                "INSERT OR IGNORE INTO mem_alias VALUES(?,?,'owner',?,?)",
                (row["name"], target, stamp, stamp),
            )
            await db.execute("DELETE FROM mem_alias WHERE entity=?", (source,))
            await db.execute("UPDATE mem_identity SET entity=? WHERE entity=?", (target, source))
            await db.execute("UPDATE mem_fact SET subject=? WHERE subject=?", (target, source))
            await db.execute(
                "INSERT OR IGNORE INTO mem_participant SELECT event,? FROM mem_participant WHERE entity=?",
                (target, source),
            )
            await db.execute("DELETE FROM mem_participant WHERE entity=?", (source,))
            await db.execute(
                "UPDATE mem_entity SET status='archived',merged_into=?,updated=? WHERE id=?",
                (target, stamp, source),
            )
            await db.execute(
                "UPDATE mem_entity SET status='confirmed',updated=? WHERE id=? AND status='candidate'",
                (stamp, target),
            )
        await self.index([ref("e", source), ref("e", target), *affected])
        log.log(Text("memory.longterm.log.merged", source=source, target=target))

    def check_content(self, content: str) -> str:
        content = content.strip()
        if not content:
            raise PublicError("memory.longterm.contentEmpty")
        words = relative_words(content)
        if words:
            raise PublicError(
                t("memory.longterm.relativeWords") + join_list(words) + t("memory.longterm.useAbsolute")
            )
        return content

    async def guard_tombstone(self, content: str, authority: str) -> None:
        rows = await self.rows(
            "SELECT target FROM mem_tombstone WHERE hash=?", (fingerprint(normalize(content)),)
        )
        if rows and authority != "owner":
            raise PublicError("memory.longterm.forgottenBefore")

    # Low-level writes shared by the tools and automatic extraction; callers hold tx().

    async def add_evidence(self, db, target: str, proof: Proof) -> None:
        await db.execute(
            "INSERT INTO mem_evidence(target,source,quote,observed_at,verdict) VALUES(?,?,?,?,?)",
            (target, proof.source, proof.quote.strip()[:2000], proof.observed, proof.verdict),
        )

    async def open_conflict(self, db, kind: str, items: list[str], note: str) -> str:
        cursor = await db.execute(
            "INSERT INTO mem_conflict(kind,items,note,status,created) VALUES(?,?,?,'open',?)",
            (kind, json.dumps(items), note, now().timestamp()),
        )
        return ref("c", cursor.lastrowid or 0)

    async def write_fact(
        self,
        subject: int | None,
        content: str,
        proof: Proof,
        *,
        valid_from: float | None = None,
        old: dict | None = None,
        equal_wins: bool = True,
        history: bool = False,
    ) -> tuple[int, str]:
        """Record a fact, reusing a duplicate; `old` is the fact it replaces.

        A stronger source replaces `old`; an equal one only when `equal_wins` (an explicit
        correction rather than a contradiction); otherwise both become a conflict for the
        owner. `history` stores an older state already replaced by `old`.
        """
        stamp = now().timestamp()
        same = await self.rows(
            "SELECT * FROM mem_fact WHERE subject IS ? AND status IN ('active','conflicted')",
            (subject,),
        )
        duplicate = next((r for r in same if normalize(r["content"]) == normalize(content)), None)
        refs = []
        async with self.tx() as db:
            if duplicate and (old is None or duplicate["id"] == old["id"]):
                key = duplicate["id"]
                stronger = max(duplicate["authority"], proof.authority, key=AUTHORITY.__getitem__)
                await db.execute(
                    "UPDATE mem_fact SET as_of=MAX(as_of,?),authority=?,"
                    "confidence=MAX(confidence,?),updated=? WHERE id=?",
                    (proof.observed, stronger, proof.weight, stamp, key),
                )
                await self.add_evidence(db, ref("f", key), proof)
                result = t("memory.longterm.result.refreshed")
            else:
                status = "superseded" if history else "active"
                cursor = await db.execute(
                    "INSERT INTO mem_fact(subject,content,valid_from,valid_to,as_of,authority,"
                    "confidence,status,superseded_by,created,updated) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        subject,
                        content,
                        valid_from,
                        proof.observed if history else None,
                        proof.observed,
                        proof.authority,
                        proof.weight,
                        status,
                        old["id"] if history and old else None,
                        stamp,
                        stamp,
                    ),
                )
                key = cursor.lastrowid or 0
                await self.add_evidence(db, ref("f", key), proof)
                result = t("memory.longterm.result.olderState", id=old['id']) if history and old else t("memory.longterm.result.recorded")
                if old and not history:
                    strength = AUTHORITY[proof.authority] - AUTHORITY[old["authority"]]
                    if strength > 0 or (strength == 0 and equal_wins):
                        await db.execute(
                            "UPDATE mem_fact SET status='superseded',superseded_by=?,valid_to=?,"
                            "updated=? WHERE id=?",
                            (key, proof.observed, stamp, old["id"]),
                        )
                        result = t("memory.longterm.result.replaced", id=old['id'])
                    else:
                        # The owner decides between sources that cannot overrule each other.
                        await db.execute(
                            "UPDATE mem_fact SET status='conflicted',updated=? WHERE id IN (?,?)",
                            (stamp, old["id"], key),
                        )
                        conflict = await self.open_conflict(
                            db,
                            "fact",
                            [ref("f", old["id"]), ref("f", key)],
                            t("memory.longterm.conflictText", brief=brief(old['content'], 80), brief2=brief(content, 80)),
                        )
                        result = t("memory.longterm.result.conflict", id=old['id'], conflict=conflict)
                    refs.append(ref("f", old["id"]))
        await self.index([ref("f", key), *refs])
        log.log(Text("memory.longterm.log.fact", result=result, brief=brief(content, 80)))
        return key, result

    async def write_event(
        self,
        content: str,
        proof: Proof,
        occurred: float | None,
        granularity: str,
        *,
        time_text: str = "",
        phase: str = "happened",
        people: list[int] | None = None,
        old: dict | None = None,
        same_as: int | None = None,
    ) -> tuple[int, str]:
        """Record an event, or add evidence to `same_as` / an exact duplicate."""
        stamp = now().timestamp()
        if same_as is None and old is None:
            found = await self.rows(
                "SELECT id FROM mem_event WHERE status='active' AND occurred_at IS ? AND content=?",
                (occurred, content),
            )
            same_as = found[0]["id"] if found else None
        async with self.tx() as db:
            if same_as is not None:
                key = same_as
                result = t("memory.longterm.result.evidenceAdded")
            else:
                cursor = await db.execute(
                    "INSERT INTO mem_event(content,occurred_at,tz,granularity,time_text,phase,"
                    "authority,status,created,updated) VALUES(?,?,?,?,?,?,?,'active',?,?)",
                    (
                        content,
                        occurred,
                        str(self.zone),
                        granularity,
                        time_text.strip(),
                        phase,
                        proof.authority,
                        stamp,
                        stamp,
                    ),
                )
                key = cursor.lastrowid or 0
                result = t("memory.longterm.result.recorded")
            await db.executemany(
                "INSERT OR IGNORE INTO mem_participant VALUES(?,?)",
                [(key, p) for p in dict.fromkeys(people or [])],
            )
            await self.add_evidence(db, ref("v", key), proof)
            if old and old["id"] != key:
                if AUTHORITY[proof.authority] < AUTHORITY[old["authority"]]:
                    raise PublicError(
                        "memory.longterm.eventAuthority", id=old['id']
                    )
                await db.execute(
                    "UPDATE mem_event SET status='superseded',superseded_by=?,updated=? WHERE id=?",
                    (key, stamp, old["id"]),
                )
                result = t("memory.longterm.result.corrected", id=old['id'])
        await self.index([ref("v", key), *([ref("v", old["id"])] if old else [])])
        log.log(Text("memory.longterm.log.event", result=result, brief=brief(content, 80)))
        return key, result

    async def save_fact(
        self,
        ctx: Context,
        content: str,
        evidence: str,
        subject: str = "",
        valid_from: str = "",
        replaces: str = "",
    ) -> dict:
        content = self.check_content(content)
        authority, source, evidence = await self.evidence(ctx, evidence)
        await self.guard_tombstone(content, authority)
        subject_id = await self.resolve_entity(subject) if subject else None
        start = parse_time(valid_from, self.zone)[0] if valid_from else None
        old = await self.row(*parse_ref(replaces, "f")) if replaces else None
        proof = Proof(authority, source, evidence, now().timestamp())
        key, result = await self.write_fact(subject_id, content, proof, valid_from=start, old=old)
        item = (await self.load([ref("f", key)]))[ref("f", key)]
        return {
            "id": ref("f", key),
            "result": result,
            "authority": authority,
            "text": self.line(item),
        }

    async def save_event(
        self,
        ctx: Context,
        content: str,
        evidence: str,
        time: str = "",
        time_text: str = "",
        phase: str = "happened",
        participants: list[str] | None = None,
        replaces: str = "",
    ) -> dict:
        content = self.check_content(content)
        authority, source, evidence = await self.evidence(ctx, evidence)
        await self.guard_tombstone(content, authority)
        if time:
            occurred, granularity = parse_time(time, self.zone)
        elif time_text.strip():
            occurred, granularity = None, "fuzzy"
        else:
            raise PublicError(
                "memory.longterm.eventTime"
            )
        stamp = now().timestamp()
        if phase == "happened" and occurred is not None and occurred > stamp + 3600:
            raise PublicError("memory.longterm.eventFuture")
        people = [await self.resolve_entity(p) for p in participants or []]
        old = await self.row(*parse_ref(replaces, "v")) if replaces else None
        key, result = await self.write_event(
            content,
            Proof(authority, source, evidence, stamp),
            occurred,
            granularity,
            time_text=time_text,
            phase=phase,
            people=people,
            old=old,
        )
        item = (await self.load([ref("v", key)]))[ref("v", key)]
        return {
            "id": ref("v", key),
            "result": result,
            "authority": authority,
            "text": self.line(item),
        }

    async def forget(self, value: str, reason: str) -> dict:
        prefix, key = parse_ref(value, "efv")
        row = await self.row(prefix, key)
        if prefix == "e" and row["role"] == "owner":
            raise PublicError("memory.longterm.ownerForget")
        stamp = now().timestamp()
        content = row.get("content") or row.get("name", "")
        async with self.tx() as db:
            await db.execute(
                f"UPDATE {TABLES[prefix]} SET status='archived',updated=? WHERE id=?", (stamp, key)
            )
            await db.execute(
                "INSERT INTO mem_tombstone VALUES(?,?,?,?) ON CONFLICT(hash) DO UPDATE SET reason=excluded.reason",
                (fingerprint(normalize(content)), ref(prefix, key), reason.strip()[:500], stamp),
            )
        if prefix == "e":
            await self.load_aliases()
        await self.index([ref(prefix, key)])
        log.log(Text("memory.longterm.log.forgotten", ref=ref(prefix, key), brief=brief(reason, 80)))
        return {"id": ref(prefix, key), "result": t("memory.longterm.result.forgotten")}

    async def resolve_conflict(
        self, ctx: Context, conflict: str, decision: str, owner_quote: str, note: str = ""
    ) -> dict:
        _, key = parse_ref(conflict, "c")
        row = await self.row("c", key)
        if row["status"] != "open":
            raise PublicError("memory.longterm.conflictHandled", key=key)
        source = await self.owner_words(ctx, owner_quote)
        items = json.loads(row["items"])
        stamp = now().timestamp()
        touched: list[str] = []
        if decision.startswith("keep:"):
            kept = decision.removeprefix("keep:").strip()
            if kept not in items:
                raise PublicError("memory.longterm.keptInvalid", kept=kept, key=key, entries='、'.join(items))
            prefix, kept_id = parse_ref(kept, "fv")
            table = TABLES[prefix]
            async with self.tx() as db:
                await db.execute(
                    f"UPDATE {table} SET status='active',authority='owner',updated=? WHERE id=?",
                    (stamp, kept_id),
                )
                if prefix == "f":
                    await db.execute("UPDATE mem_fact SET as_of=? WHERE id=?", (stamp, kept_id))
                await db.execute(
                    "INSERT INTO mem_evidence(target,source,quote,observed_at,verdict) VALUES(?,?,?,?,'quoted')",
                    (kept, source, owner_quote.strip()[:2000], stamp),
                )
                for other in items:
                    if other != kept:
                        other_prefix, other_id = parse_ref(other, "fv")
                        await db.execute(
                            f"UPDATE {TABLES[other_prefix]} SET status='superseded',superseded_by=?,updated=? WHERE id=?",
                            (kept_id, stamp, other_id),
                        )
            touched = items
        elif decision in {"both_valid", "neither"}:
            status = "active" if decision == "both_valid" else "archived"
            async with self.tx() as db:
                for item in items:
                    prefix, item_id = parse_ref(item, "fv")
                    await db.execute(
                        f"UPDATE {TABLES[prefix]} SET status=?,updated=? WHERE id=?",
                        (status, stamp, item_id),
                    )
            touched = items
        elif decision == "same_entity":
            first, *rest = [parse_ref(item, "e")[1] for item in items]
            for other in rest:
                await self.merge_entities(first, other)
        elif decision != "different_entity":
            raise PublicError(
                "memory.longterm.decisionInvalid"
            )
        resolution = decision + (f"：{note.strip()}" if note.strip() else "")
        await self.store.execute(
            "UPDATE mem_conflict SET status='resolved',resolution=?,resolved=? WHERE id=?",
            (resolution, stamp, key),
        )
        if touched:
            await self.index(touched)
        log.log(Text("memory.longterm.log.resolved", key=key, brief=brief(resolution, 80)))
        return {"id": ref("c", key), "result": t("memory.longterm.result.resolved"), "resolution": resolution}

    # ---- reads ---------------------------------------------------------------

    async def get(self, value: str) -> dict:
        prefix, key = parse_ref(value)
        items = await self.load([ref(prefix, key)])
        item = items.get(ref(prefix, key))
        if item is None:
            raise PublicError("memory.longterm.refMissing", ref=ref(prefix, key))
        result: dict = {"id": item["ref"], "type": item["type"], "status": item["status"]}
        if prefix == "e":
            facts = await self.load(await self.card_facts(key, 50))
            events = await self.load(await self.entity_events([key], 10))
            history = await self.rows(
                "SELECT id FROM mem_fact WHERE subject=? AND status IN ('superseded','archived') "
                "ORDER BY updated DESC LIMIT 10",
                (key,),
            )
            identities = await self.rows(
                "SELECT identity,display FROM mem_identity WHERE entity=?", (key,)
            )
            result.update(
                kind=item["kind"],
                name=item["name"],
                aliases=item["aliases"],
                summary=item["summary"],
                identities=identities,
                facts=[self.line(f) for f in facts.values()],
                events=[self.line(v) for v in events.values()],
                history=[
                    self.line(f)
                    for f in (await self.load([ref("f", h["id"]) for h in history])).values()
                ],
            )
            if item["merged_into"]:
                result["merged_into"] = ref("e", item["merged_into"])
            return result
        if prefix == "c":
            related = await self.load(item["items"])
            result.update(
                note=item["note"],
                items=[self.line(x) for x in related.values()],
                resolution=item["resolution"],
            )
            return result
        evidence = await self.rows(
            "SELECT source,quote,observed_at,verdict FROM mem_evidence WHERE target=? ORDER BY id",
            (item["ref"],),
        )
        table = TABLES[prefix]
        replaced = await self.rows(f"SELECT id FROM {table} WHERE superseded_by=?", (key,))
        conflicts = await self.rows(
            "SELECT id,note,status FROM mem_conflict WHERE items LIKE ?", (f'%"{item["ref"]}"%',)
        )
        result.update(
            text=self.line(item),
            authority=item["authority"],
            evidence=[
                {
                    "source": self.source_label(e["source"]) or e["source"],
                    "quote": e["quote"],
                    "recorded": render_time(e["observed_at"], "minute", self.zone),
                    "verdict": e["verdict"],
                }
                for e in evidence
            ],
            replaced=[ref(prefix, r["id"]) for r in replaced],
            conflicts=[
                {"id": ref("c", c["id"]), "note": c["note"], "status": c["status"]}
                for c in conflicts
            ],
        )
        if item["superseded_by"]:
            result["superseded_by"] = ref(prefix, item["superseded_by"])
        if prefix == "f":
            result["confidence"] = item["confidence"]
            result["as_of"] = render_time(item["as_of"], "minute", self.zone)
        return result

    async def search(
        self,
        query: str = "",
        entity: str = "",
        kind: str = "",
        since: str = "",
        until: str = "",
        include_history: bool = False,
        limit: int = 10,
    ) -> dict:
        start = parse_bound(since, self.zone, end=False) if since else None
        end = parse_bound(until, self.zone, end=True) if until else None
        ranked: dict[str, float] = {}

        def add(refs: list[str]) -> None:
            for rank, key in enumerate(refs):
                ranked[key] = ranked.get(key, 0) + 1 / (60 + rank)

        if entity:
            key = await self.resolve_entity(entity)
            add(
                [
                    ref("e", key),
                    *await self.card_facts(key, 50),
                    *await self.entity_events([key], 50),
                ]
            )
            if include_history:
                rows = await self.rows(
                    "SELECT id FROM mem_fact WHERE subject=? AND status IN ('superseded','archived')",
                    (key,),
                )
                add([ref("f", r["id"]) for r in rows])
        if query:
            linked = self.link(query)
            add([ref("e", e) for e in linked])
            add(await self.entity_events(linked, 20))
            add([key for key, _ in await self.bm25(keywords(await self.tokens(query)))])
            add([key for key, score in await self.vectors.search(query) if score >= 0.3])
            if include_history:
                pattern = f"%{query.strip()}%"
                for prefix in "fv":
                    rows = await self.rows(
                        f"SELECT id FROM {TABLES[prefix]} WHERE status IN ('superseded','archived') "
                        "AND content LIKE ? ORDER BY updated DESC LIMIT 20",
                        (pattern,),
                    )
                    add([ref(prefix, r["id"]) for r in rows])
        if not entity and not query:
            rows = await self.rows(
                "SELECT id FROM mem_event WHERE status='active' ORDER BY COALESCE(occurred_at,created) DESC LIMIT 50"
            )
            add([ref("v", r["id"]) for r in rows])
            rows = await self.rows(
                "SELECT id FROM mem_fact WHERE status IN ('active','conflicted') ORDER BY as_of DESC LIMIT 50"
            )
            add([ref("f", r["id"]) for r in rows])
        items = await self.load(sorted(ranked, key=lambda k: -ranked[k]))
        results = []
        for item in items.values():
            if kind and item["type"] != kind:
                continue
            if item["type"] == "conflict" or (item["type"] == "entity" and item["role"] == "owner"):
                continue
            if not is_live(item) and not include_history:
                continue
            stamp = (
                item["occurred_at"]
                if item["type"] == "event"
                else item["as_of"]
                if item["type"] == "fact"
                else None
            )
            if (start is not None or end is not None) and item["type"] != "entity":
                if (
                    stamp is None
                    or (start is not None and stamp < start)
                    or (end is not None and stamp > end)
                ):
                    continue
            elif (start is not None or end is not None) and item["type"] == "entity":
                continue
            if item["type"] == "fact" and any(f"#{item['ref']}" in r["text"] for r in results):
                continue  # Already listed inside its entity's card.
            line = self.line(item, [])
            if item["type"] == "entity":
                line = self.line(
                    item, list((await self.load(await self.card_facts(item["id"]))).values())
                )
            results.append(
                {"id": item["ref"], "type": item["type"], "status": item["status"], "text": line}
            )
            if len(results) >= limit:
                break
        return {
            "results": results,
            "note": t("memory.longterm.partialNote"),
        }

    async def browse(self, kind: str = "", query: str = "", limit: int = 200) -> list[dict]:
        """WebUI listing, including archived and superseded items."""
        if query:
            return (await self.search(query=query, kind=kind, include_history=True, limit=limit))[
                "results"
            ]
        tables = {"entity": "e", "fact": "f", "event": "v", "conflict": "c"}
        prefixes = [tables[kind]] if kind in tables else list("efvc")
        refs: list[str] = []
        for prefix in prefixes:
            rows = await self.rows(
                f"SELECT id FROM {TABLES[prefix]} ORDER BY id DESC LIMIT ?", (limit,)
            )
            refs += [ref(prefix, r["id"]) for r in rows]
        items = await self.load(refs)
        return [
            {"id": i["ref"], "type": i["type"], "status": i["status"], "text": self.line(i)}
            for i in items.values()
            if not (i["type"] == "entity" and i["role"] == "owner")
        ][:limit]

    async def browse_page(
        self, kind: str = "", query: str = "", page: int = 1, page_size: int = 20
    ) -> dict:
        """Browse all records, including history and conflicts, without recall candidate limits."""
        # Text browsing searches names, aliases, related people, content and conflict notes.
        entities = (
            "SELECT e.id FROM mem_entity e WHERE instr(lower(e.name||' '||e.summary),lower(?))>0 "
            "OR EXISTS (SELECT 1 FROM mem_alias a WHERE a.entity=e.id AND instr(lower(a.alias),lower(?))>0)"
        )
        branches, args = [], []
        for prefix, table in TABLES.items():
            if kind and TYPES[prefix] != kind:
                continue
            stamp = "COALESCE(resolved,created)" if prefix == "c" else "updated"
            where = "role!='owner'" if prefix == "e" else "1=1"
            if query:
                if prefix == "e":
                    where += f" AND id IN ({entities})"
                    args.extend([query, query])
                elif prefix in "fv":
                    relation = (
                        f"subject IN ({entities})"
                        if prefix == "f"
                        else f"id IN (SELECT event FROM mem_participant WHERE entity IN ({entities}))"
                    )
                    where += f" AND (instr(lower(content),lower(?))>0 OR {relation})"
                    args.extend([query, query, query])
                else:
                    where += " AND instr(lower(note||' '||resolution||' '||items),lower(?))>0"
                    args.append(query)
            branches.append(
                f"SELECT '{prefix}'||id AS ref,{stamp} AS stamp FROM {table} WHERE {where}"
            )
        sql = " UNION ALL ".join(branches)
        async with self.store.lock:
            async with self.store.db.execute(
                f"SELECT COUNT(*) FROM ({sql})", tuple(args)
            ) as cursor:
                total = (await cursor.fetchone())[0]
            page = min(page, max(1, (total + page_size - 1) // page_size))
            async with self.store.db.execute(
                f"SELECT ref FROM ({sql}) ORDER BY stamp DESC,ref DESC LIMIT ? OFFSET ?",
                (*args, page_size, (page - 1) * page_size),
            ) as cursor:
                refs = [row[0] for row in await cursor.fetchall()]
        items = await self.load(refs)
        results = [
            {"id": i["ref"], "type": i["type"], "status": i["status"], "text": self.line(i)}
            for key in refs
            if (i := items.get(key)) is not None
        ]
        return {"items": results, "total": total, "page": page, "page_size": page_size}

    # ---- background embedding ------------------------------------------------

    async def run(self) -> None:
        backoff = 30.0
        while True:
            self.wake.clear()
            try:
                if self.vectors.key() is not None:
                    done = await self.vectors.sync(await self.documents())
                    if done:
                        log.debug(Text("memory.longterm.log.vectors", done=done))
                backoff = 30.0
            except Exception as exc:
                if isinstance(exc, PublicError):
                    log.warning(Text("memory.longterm.log.vectorsPaused", exc=exc, backoff=backoff))
                else:
                    log.error(Text("memory.longterm.log.vectorsError", name=type(exc).__name__), exc=exc)
                # Failing providers are retried on a timer, not on every new memory.
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 900)
                continue
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=30)
            except TimeoutError:
                pass
