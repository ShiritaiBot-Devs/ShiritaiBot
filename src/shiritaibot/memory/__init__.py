from __future__ import annotations

import asyncio
import json
from datetime import timedelta

from shiritaibot.agent.context import transcript
from shiritaibot.core import Context, PublicError, canonical, now, record_usage_for
from shiritaibot.core.logs import brief, describe_session, get_logger
from shiritaibot.i18n import Text, t
from shiritaibot.memory.longterm import MemoryManager
from shiritaibot.memory.recall import MemoryRecall
from shiritaibot.prompts import prompt as prompt_text
from shiritaibot.store import MEMORY_PROFILE_FILTER, owner_memory_scope

__all__ = ["MemoryManager", "MemoryRecall", "ShortTermMemory"]
log = get_logger("memory.log.source")


class ShortTermMemory:
    """Model-compressed turns; proactive stage traces are kept separately for audit."""

    def __init__(self, store, llm, days: int = 7, config=None):
        self.store, self.llm, self.days = store, llm, days
        self.config = config
        self.wake = asyncio.Event()
        self.lock = asyncio.Lock()

    async def record(self, key: str, stage: str, detail: str, **fields) -> dict:
        stamp = now().isoformat()
        row = await self.store.get("proactive_trace", key) or {"steps": [], "created": stamp}
        row.update(fields, stage=stage, updated=stamp)
        row["steps"] = [*row["steps"], {"at": stamp, "stage": stage, "detail": detail[:1000]}]
        return await self.store.put("proactive_trace", key, row, "owner")

    async def enqueue(self, ctx: Context, status: str) -> None:
        if not ctx.summary_eligible:
            return
        owner_scope = owner_memory_scope(ctx.memory_profile)
        turn = await self.store.get("turn", ctx.turn) or {}
        scope = turn.get("memory_scope") or (owner_scope if ctx.mode == "agent" else ctx.session)
        cfg = self.config.value if self.config else None
        await self.store.insert(
            "memory_summary",
            ctx.turn,
            {
                "session": ctx.session,
                "turn_status": status,
                "status": "pending",
                "memory_scope": scope,
                "memory_profile": ctx.memory_profile,
                "loop": "proactive" if ctx.background else "main",
                "assistant_name": turn.get(
                    "assistant_name", cfg.assistant_name if cfg else "ShiritaiBot"
                ),
                "owner_name": turn.get("owner_name", cfg.owner_name if cfg else t("memory.transcript.owner"))
                if scope == owner_scope
                else t("memory.transcript.user"),
            },
        )
        self.wake.set()

    async def recover(self) -> None:
        """Recover turns interrupted between their final state and summary submission."""
        rows = await self.store.query(
            "SELECT data FROM records WHERE kind='turn' "
            "AND json_extract(data,'$.memory_scope') IS NOT NULL "
            "AND COALESCE(json_extract(data,'$.summary_eligible'),1)=1 "
            "AND COALESCE(json_extract(data,'$.summary_enqueued'),0)=0"
        )
        for item in rows:
            row = json.loads(item["data"])
            profile = row.get("memory_profile", "default")
            private = row["memory_scope"] == owner_memory_scope(profile)
            if "summary_eligible" not in row:
                # Older turns recorded Agent ownership in the memory scope. Owner
                # Chat turns used the session scope, just like guest Chat turns.
                session = row["scope"]
                if session.startswith("group:") or not (
                    private
                    or row["loop"] == "proactive"
                    or (
                        row["memory_scope"] == session
                        and self.config
                        and self.config.value.personal_memory_session(session)
                    )
                ):
                    continue
            await self.enqueue(
                Context(
                    row["scope"],
                    row["actor"],
                    "agent" if private else "chat",
                    owner=True,
                    background=row["loop"] == "proactive",
                    turn=row["id"],
                    memory_profile=profile,
                ),
                row["status"],
            )
            row["summary_enqueued"] = True
            await self.store.put("turn", row["id"], row, row["scope"])

    async def compress(self, row: dict) -> None:
        messages = await self.store.query(
            f"SELECT data FROM messages WHERE session=? AND turn=? AND {MEMORY_PROFILE_FILTER} ORDER BY seq",
            (row["session"], row["id"], row.get("memory_profile", "default")),
        )
        # Provider-specific opaque reasoning is not part of an episode summary.
        assistant_name = row.get("assistant_name", "ShiritaiBot")
        owner_name = row.get("owner_name", t("memory.transcript.owner"))
        material_data = {
            "status": row["turn_status"],
            "messages": transcript(
                [json.loads(m["data"]) for m in messages],
                assistant_name,
                owner_name,
            ),
        }
        if row["session"].startswith("background:proactive:"):
            event = await self.store.get(
                "event", row["session"].removeprefix("background:proactive:")
            )
            if event:
                material_data["outcome"] = {
                    key: event.get(key)
                    for key in ("decision", "result", "notification_queued", "loop_failed")
                }
        material = canonical(material_data)
        system = (
            prompt_text("MEMORY_COMPRESS", assistant_name=assistant_name, owner_name=owner_name)
        )
        cfg = self.config.value.with_session_routes(row["session"]) if self.config else None

        async def summarize(content: str) -> str:
            async with asyncio.timeout(60):
                with record_usage_for(row["session"]):
                    reply = await self.llm.complete(
                        "light", system, [{"role": "user", "content": content}], [], settings=cfg
                    )
            if not reply.text.strip() or reply.calls:
                raise PublicError("memory.summaryInvalid")
            return reply.text.strip()[:4000]

        # Bound every request, including merges; a long turn gets multiple model calls.
        while len(material) > 16000:
            summaries = [
                await summarize(material[offset : offset + 16000])
                for offset in range(0, len(material), 16000)
            ]
            material = "\n".join(summaries)
        summary = await summarize(material)
        await self.store.put(
            "short_term",
            row["id"],
            {
                "summary": summary,
                "session": row["session"],
                "loop": row["loop"],
                "turn_status": row["turn_status"],
                "created": now().isoformat(),
                "assistant_name": assistant_name,
            },
            row["memory_scope"],
        )
        await self.store.delete("memory_summary", row["id"])
        log.debug(Text("memory.log.summarized", brief=brief(summary, 100)), describe_session(row["session"]))

    async def tick(self) -> None:
        async with self.lock:
            for row in await self.store.with_status("memory_summary", ("pending",)):
                if row.get("retry_at", 0) > now().timestamp():
                    continue
                try:
                    await self.compress(row)
                except Exception as exc:
                    log.warning(
                        Text("memory.log.summaryFailed", name=type(exc).__name__),
                        describe_session(row["session"]),
                    )
                    row.update(retry_at=now().timestamp() + 60, error=type(exc).__name__)
                    await self.store.put("memory_summary", row["id"], row)
            await self.store.execute(
                "DELETE FROM records WHERE kind='short_term' AND updated<?",
                (now().timestamp() - self.days * 86400,),
            )

    async def run(self) -> None:
        while True:
            self.wake.clear()
            try:
                await self.tick()
            except Exception as exc:
                log.error(Text("memory.log.loopError", name=type(exc).__name__), exc=exc)
                await self.store.activity("memory.summary_failed", type(exc).__name__)
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=10)
            except TimeoutError:
                pass

    async def recent(
        self, query: str = "", limit: int = 20, *, memory_profile: str = "default"
    ) -> list[dict]:
        rows = await self.store.list(
            "short_term", scope=owner_memory_scope(memory_profile), limit=200
        )
        cutoff = (now() - timedelta(days=self.days)).isoformat()
        rows = [r for r in rows if r.get("summary") and r["created"] >= cutoff]
        terms = query.lower().split()
        if terms:
            texts = [(r, canonical(r).lower()) for r in rows]
            matched = [r for r, text in texts if all(t in text for t in terms)]
            if not matched and len(terms) > 1:
                # Fall back to any-term matches, ranked by how many terms hit, newest first.
                hits = [(r, sum(t in text for t in terms)) for r, text in texts]
                hits = [(r, n) for r, n in hits if n]
                hits.sort(key=lambda item: (item[1], item[0]["created"]), reverse=True)
                matched = [r for r, _ in hits]
            rows = matched
        return rows[:limit]
