from __future__ import annotations

import json

from shiritaibot.core import PublicError, canonical, fingerprint
from shiritaibot.core.logs import describe_session, get_logger
from shiritaibot.i18n import LOCALES, Text, t
from shiritaibot.prompts import prompt as prompt_text
from shiritaibot.store import MEMORY_PROFILE_FILTER, memory_context_key

log = get_logger("agent.context.log.source")


def context_chars(messages: list[dict]) -> int:
    # Archive metadata (including the exact user text) is never sent to providers.
    return len(
        canonical(
            [
                {k: v for k, v in message.items() if not k.startswith("_") or k == "_opaque"}
                for message in messages
            ]
        )
    )


def dialogue_message(message: dict) -> dict:
    role, content = message["role"], message["content"]
    original = message.get("_original_content")
    if role == "user" and isinstance(original, str) and isinstance(content, str):
        if content.startswith(original + "\n<context "):
            context = content[len(original) :]
            # Stored history may hold the recall header in any language.
            for locale in LOCALES:
                context = context.replace(
                    f"\n{prompt_text('MEMORY_HEADER_IDS', locale)}\n",
                    f"\n{prompt_text('MEMORY_HEADER', locale)}\n",
                )
            content = original + context
    return {"role": role, "content": content}


def transcript(messages: list[dict], assistant_name: str, owner_name: str) -> list[dict]:
    """Name speakers without changing provider roles or storing internal reasoning."""
    return [
        {
            **{k: v for k, v in message.items() if not k.startswith("_")},
            "content": message.get("_original_content", message.get("content", "")),
            "speaker": message.get("_speaker")
            or (
                assistant_name
                if message["role"] == "assistant"
                else owner_name
                if message["role"] == "user"
                else prompt_text("CONTEXT_ROLE_TOOL")
            ),
        }
        for message in messages
    ]


class ContextManager:
    """A durable model-facing projection; the messages table is an immutable archive."""

    def __init__(self, store, llm):
        self.store, self.llm = store, llm

    async def prepare(
        self,
        session: str,
        cfg,
        incoming: dict,
        *,
        owner: bool,
        current_turn: str = "",
        memory_profile: str = "default",
        dialogue_only: bool = False,
    ) -> list[dict]:
        key = memory_context_key(session, memory_profile)
        epoch = await self.store.get("session_epoch", key)
        epoch_seq = epoch["seq"] if epoch else 0
        state = await self.store.get("context_state", key) or {}
        if state.get("epoch_seq", 0) != epoch_seq:
            state = {}
        query = (
            "SELECT seq,turn,data FROM messages WHERE session=? AND seq>? AND turn!=? "
            f"AND {MEMORY_PROFILE_FILTER} AND COALESCE(json_extract(data,'$._command'),0)=0"
        )
        args = (session, max(epoch_seq, state.get("seq", 0)), current_turn, memory_profile)
        if dialogue_only:
            rows = await self.store.query(
                query
                + " AND (json_extract(data,'$.role')='user' OR (json_extract(data,'$.role')='assistant'"
                + " AND length(trim(json_extract(data,'$.content')))>0)) ORDER BY seq DESC LIMIT ?",
                (*args, cfg.context.max_turns * 4),
            )
            rows.reverse()
        else:
            rows = await self.store.query(query + " ORDER BY seq", args)

        summaries = {}
        if not dialogue_only and rows:
            # Only use projections for turns present in the current memory epoch.
            projections = await self.store.query(
                "SELECT id,data FROM records WHERE kind='turn_context' AND scope=? "
                "AND json_extract(data,'$.seq')>?",
                (session, max(epoch_seq, state.get("seq", 0))),
            )
            summaries = {row["id"]: json.loads(row["data"]) for row in projections}
        groups: list[list[dict]] = []
        previous = None
        for row in rows:
            if row["turn"] != previous:
                groups.append([])
                previous = row["turn"]
            data = json.loads(row["data"])
            projection = summaries.get(row["turn"])
            if projection and row["seq"] <= projection["seq"]:
                if data["role"] != "user":
                    if row["seq"] == projection["seq"]:
                        groups[-1].append({
                            "role": "user",
                            "content": t("agent.execution.summary", summary=projection["summary"]),
                            "_seq": row["seq"],
                        })
                    continue
            if dialogue_only:
                groups[-1].append(dialogue_message(data))
            else:
                groups[-1].append({**data, "_seq": row["seq"]})
        summary = state.get("summary", "")
        budget = cfg.policy.max_context_chars - context_chars([incoming]) - 200
        if budget < 100:
            raise PublicError("agent.context.limit")

        def size(items):
            return context_chars([m for group in items for m in group])

        if dialogue_only:
            # Read the last saved summary without running compression or advancing Work's cursor.
            summary = summary[: min(4000, budget // 4)]
            split = max(0, len(groups) + 1 - cfg.context.max_turns)
            while split < len(groups) and size(groups[split:]) + len(summary) > budget:
                split += 1
            groups = groups[split:]
        elif len(groups) + 1 > cfg.context.max_turns or size(groups) + len(summary) > budget:
            if cfg.context.strategy == "compress":
                # Always leave room for the incoming turn. Tight character budgets can
                # require compressing more than the configured recent-turn target.
                keep = min(cfg.context.keep_recent_turns, len(groups))
                split = len(groups) - keep
                summary_limit = min(4000, budget // 4)
                while split < len(groups) and size(groups[split:]) + summary_limit > budget:
                    split += 1
                if split or len(summary) > summary_limit:
                    records = transcript(
                        [m for group in groups[:split] for m in group],
                        cfg.assistant_name,
                        cfg.owner_name if owner else prompt_text("CONTEXT_ROLE_USER"),
                    )
                    limit = cfg.context.compression_tool_chars
                    for record in records:
                        if record["role"] == "tool" and limit:
                            content = record["content"]
                            if not isinstance(content, str):
                                content = canonical(content)
                            if len(content) > limit:
                                marker = "…"
                                tail = (limit - len(marker)) // 2
                                record["content"] = (
                                    content[: limit - tail - 1]
                                    + marker
                                    + (content[-tail:] if tail else "")
                                )
                    material = canonical({"previous_summary": summary, "messages": records})
                    settings = cfg.model_copy(deep=True)
                    candidates = list(
                        dict.fromkeys([cfg.context.compression_model, *cfg.routes.get("light", [])])
                    )
                    system = (
                        prompt_text("CONTEXT_COMPRESS", assistant_name=cfg.assistant_name, value=cfg.owner_name if owner else prompt_text("CONTEXT_ROLE_USER"))
                    )

                    async def summarize(content):
                        for model_id in filter(None, candidates):
                            settings.routes["light"] = [model_id]
                            for _ in range(2):
                                try:
                                    reply = await self.llm.complete(
                                        "light",
                                        system,
                                        [{"role": "user", "content": content}],
                                        [],
                                        settings=settings,
                                    )
                                except PublicError:
                                    # Transport retries have already run inside the provider.
                                    break
                                if reply.text.strip() and not reply.calls:
                                    return reply.text.strip()[:summary_limit]
                        raise PublicError("agent.context.compressFailed")

                    cache = state.setdefault("chunks", {})
                    try:
                        while len(material) > 16000:
                            chunks = []
                            for offset in range(0, len(material), 16000):
                                chunk = material[offset : offset + 16000]
                                digest = fingerprint([system, summary_limit, chunk])
                                if digest not in cache:
                                    cache[digest] = await summarize(chunk)
                                    await self.store.put(
                                        "context_state",
                                        key,
                                        {
                                            **state,
                                            "epoch_seq": epoch_seq,
                                        },
                                    )
                                chunks.append(cache[digest])
                            material = "\n".join(chunks)
                        summary = await summarize(material)
                    except PublicError:
                        if cfg.context.compression_failure == "error":
                            raise
                        summary = ""
                        log.warning(
                            "agent.context.log.compressFailed", describe_session(session)
                        )
                    if summary:
                        log.log(
                            Text("agent.context.log.compressed", count=split, chars=len(summary)),
                            describe_session(session),
                        )
            else:
                summary = ""
                split = max(0, len(groups) + 1 - cfg.context.max_turns)
                while split < len(groups) and size(groups[split:]) > budget:
                    split += 1
            if split or summary != state.get("summary", ""):
                await self.store.put(
                    "context_state",
                    key,
                    {
                        "seq": groups[split - 1][-1]["_seq"]
                        if split
                        else state.get("seq", epoch_seq),
                        "summary": summary,
                        "epoch_seq": epoch_seq,
                        "assistant_name": cfg.assistant_name,
                    },
                )
                groups = groups[split:]
        messages = [m for group in groups for m in group]
        if summary:
            messages.insert(
                0,
                {
                    "role": "user",
                    "content": (
                        prompt_text("CONTEXT_SUMMARY_HEAD")
                        if dialogue_only
                        else prompt_text("CONTEXT_SUMMARY_HEAD_QUERY")
                    )
                    + summary,
                },
            )
        return messages
