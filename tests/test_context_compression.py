import asyncio
import json
from types import SimpleNamespace

import pytest

from shiritaibot.agent.context import ContextManager
from shiritaibot.config import Settings
from shiritaibot.core import EventBus, PublicError
from shiritaibot.store import Store


def test_compression_resume_failover_and_discard(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        cfg = Settings()
        cfg.context.strategy = "compress"
        cfg.context.max_turns = 1
        cfg.context.keep_recent_turns = 0
        cfg.context.compression_model = "preferred"
        cfg.context.compression_failure = "error"
        cfg.routes["light"] = ["preferred", "fallback"]
        cfg.context.compression_tool_chars = 9
        session = "private:qq:1"
        requests = []
        replies = iter(["chunk", "", "tool", "", ""])

        async def complete(route, system, messages, tools, settings):
            requests.append((settings.routes[route][0], messages[0]["content"]))
            answer = next(replies)
            return SimpleNamespace(text=answer, calls=[1] if answer == "tool" else [])

        manager = ContextManager(store, SimpleNamespace(complete=complete))
        incoming = {"role": "user", "content": "next"}
        try:
            await store.append(session, "1", {"role": "user", "content": "a" * 17000})
            await store.append(
                session, "1", {"role": "tool", "content": "left" + "b" * 500 + "tail"}
            )
            with pytest.raises(PublicError):
                await manager.prepare(session, cfg, incoming, owner=True)
            assert [model for model, _ in requests] == ["preferred"] * 3 + ["fallback"] * 2
            assert "left…tail" in requests[-1][1]
            state = await store.get("context_state", session)
            assert state and len(state["chunks"]) == 1 and not state.get("seq")
            first_chunk = requests[0][1]
            requests.clear()
            replies = iter(["", "", "chunk2", "done"])
            messages = await manager.prepare(session, cfg, incoming, owner=True)
            assert first_chunk not in [content for _, content in requests]
            assert messages[0]["content"].endswith("done")
            state = await store.get("context_state", session)
            assert state and state["seq"] == 2 and "chunks" not in state
            await store.append(session, "2", {"role": "user", "content": "old"})
            replies = iter([""] * 4)
            cfg.context.compression_failure = "discard"
            assert await manager.prepare(session, cfg, incoming, owner=True) == []
            state = await store.get("context_state", session)
            assert state and state["seq"] == 3 and not state["summary"]
            rows = await store.query("SELECT data FROM messages ORDER BY seq")
            assert len(json.loads(rows[1]["data"])["content"]) == 508
        finally:
            await store.close()

    asyncio.run(run())
