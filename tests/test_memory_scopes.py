import asyncio
import json
from contextlib import AsyncExitStack
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from shiritaibot.agent import AgentRuntime
from shiritaibot.auth import Permissions
from shiritaibot.config import Settings
from shiritaibot.core import Context, EventBus, Reply
from shiritaibot.memory import MemoryManager, ShortTermMemory
from shiritaibot.store import Store
from shiritaibot.tools import ToolRegistry
from shiritaibot.tools.builtin import register_builtin

GROUP = "group:qq:123"
GUEST = "private:qq:42"
OWNER = "private:qq:99"


def config_for(mode="chat"):
    return SimpleNamespace(
        value=Settings.model_validate(
            {
                "adapters": [{"id": "qq", "owner_id": "99"}],
                "memory": {
                    "inject": False,
                    "profiles": {p: {"name": p, "audience": "shared"} for p in ("alpha", "beta")},
                    "sessions": {GROUP: "alpha", GUEST: "alpha", "group:qq:456": "beta"},
                },
                "policy": {"group_default": {"mode": mode}},
            }
        )
    )


@pytest.mark.parametrize("mode", ["chat", "agent"])
def test_group_tools_read_only_bound_store(tmp_path, mode):
    async def run():
        config = config_for(mode)
        async with AsyncExitStack() as stack:
            memories = {}
            for profile in ("default", "alpha", "beta"):
                store = Store(tmp_path / profile, EventBus())
                await store.open()
                stack.push_async_callback(store.close)
                memories[profile] = MemoryManager(store, config)
                # Every library has an f1 with different content.
                await store.execute(
                    "INSERT INTO mem_fact(content, as_of, authority, confidence, status, created, updated) "
                    "VALUES (?, 1, 'direct', 1, 'active', 1, 1)",
                    (profile,),
                )
            store = memories["default"].store
            permissions = Permissions(config, store)
            registry = ToolRegistry(permissions, store)
            app = SimpleNamespace(
                config=config,
                permissions=permissions,
                registry=registry,
                memory_for=memories.__getitem__,
                memes=Mock(),
                skills=Mock(),
                adapters=SimpleNamespace(instances={}),
                vault=AsyncMock(),
            )
            await register_builtin(app)
            ctx = Context(GROUP, "42", mode, group=True, memory_profile="alpha")
            reads = {"memory_search", "memory_get"}

            def names(context):
                return {s["function"]["name"] for s in registry.specs(context)}

            async def call(context, tool, **args):
                return json.loads(await registry.execute(context, tool, args))

            for member in (ctx, replace(ctx, owner=True, actor="99")):
                assert {n for n in names(member) if n.startswith("memory_")} == reads
                assert "proactive_recall" not in names(member)
                (hit,) = (await call(member, "memory_search"))["results"]
                assert hit["id"] == "f1" and "alpha" in hit["text"]
                assert "alpha" in (await call(member, "memory_get", id="f1"))["text"]
                assert "error" in await call(member, "memory_get", id="f1", profile="beta")
                assert "error" in await call(member, "memory_forget", id="f1", reason="test")
            for denied in (
                replace(ctx, memory_profile="beta"),
                replace(ctx, memory_profile="default"),
                replace(ctx, session="group:qq:789", memory_profile="default"),
                Context(GUEST, "42", "agent", memory_profile="alpha"),
            ):
                assert reads.isdisjoint(names(denied))
                assert "error" in await call(denied, "memory_get", id="f1")
            config.value.memory.sessions[GROUP] = "beta"
            assert "error" in await call(ctx, "memory_search")
            rebound = replace(ctx, memory_profile="beta")
            assert "beta" in (await call(rebound, "memory_get", id="f1"))["text"]
            del config.value.memory.sessions[GROUP]
            assert "error" in await call(rebound, "memory_get", id="f1")
            owner = Context(OWNER, "99", "agent", owner=True)
            assert "default" in (await call(owner, "memory_get", id="f1"))["text"]

    asyncio.run(run())


def test_short_term_queue_and_restart(tmp_path):
    async def run():
        config = config_for()
        store = Store(tmp_path, EventBus())
        await store.open()
        try:
            llm = SimpleNamespace(
                complete=AsyncMock(
                    return_value=Reply('{"notify": false, "message": "ok", "reason": "ok"}')
                )
            )
            registry = ToolRegistry(Permissions(config, store), store)
            summaries = ShortTermMemory(store, llm, config=config)
            runtime = AgentRuntime(config, store, llm, registry, None, AsyncMock(), summaries)
            contexts = (
                Context(GROUP, "99", owner=True, group=True, memory_profile="alpha"),
                Context("group:qq:789", "42", group=True),
                Context(GUEST, "42", memory_profile="alpha"),
                Context("private:qq:43", "43"),
                Context(OWNER, "99", owner=True),
                Context("web:owner", "owner", owner=True),
                Context("background:proactive:event", "owner", owner=True, background=True),
            )
            config.value.policy.owner_default.mode = "chat"
            config.value.policy.private_default.mode = "agent"
            for ctx in contexts:
                await runtime.turn(SimpleNamespace(steering=[]), ctx, "hello")
            turns = await store.list("turn")
            assert len(turns) == len(contexts) and all(t["status"] == "completed" for t in turns)
            expected = {ctx.session for ctx in contexts[-3:]}
            assert {t["session"] for t in await store.list("memory_summary")} == expected
            assert next(t for t in turns if t["scope"] == GROUP)["memory_scope"] == "shared:alpha"
            # Recreate the crash window, with both current and pre-upgrade turn records.
            for turn in turns:
                await store.delete("memory_summary", turn["id"])
                turn.pop("summary_enqueued", None)
                await store.put("turn", turn["id"], turn, turn["scope"])
                turn.pop("summary_eligible")
                await store.put("turn", "legacy-" + turn["id"], turn, turn["scope"])
            await summaries.recover()
            await summaries.recover()
            pending = await store.list("memory_summary")
            assert len(pending) == len(expected) * 2
            assert {t["session"] for t in pending} == expected
        finally:
            await store.close()

    asyncio.run(run())
