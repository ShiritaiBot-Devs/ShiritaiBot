import asyncio
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from shiritaibot.agent import AgentRuntime
from shiritaibot.auth import Permissions
from shiritaibot.config import Settings
from shiritaibot.core import Context, EventBus, ExternalEvent, now
from shiritaibot.learn import OneLearnPlugin
from shiritaibot.plugin_api import InteractionRequired, PluginContext, schema
from shiritaibot.proactive import PREPARE_SESSION, Decision, ProactiveEngine
from shiritaibot.store import Store
from shiritaibot.tools import ToolRegistry


def test_proactive_interaction_lifecycle(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        cfg = SimpleNamespace(value=Settings())
        cfg.value.memory.inject = False
        cfg.value.policy.proactive_default.preset = "full_access"
        notify = AsyncMock()
        pe = ProactiveEngine(cfg, store, None, notify, AsyncMock())
        registry = ToolRegistry(Permissions(cfg, store), store)
        plugin = PluginContext("school", {}, tmp_path, AsyncMock(), pe.emit)
        plugin._resolve_interaction = pe.resolve_interaction
        login = AsyncMock()
        plugin.register_tool("login", "login", schema({}), login, "safe", interactive=True)
        learn = OneLearnPlugin(plugin)
        await learn.start()
        learn.invoke = AsyncMock(return_value={"ok": False, "status": "session_expired"})
        for tool in plugin.tools:
            registry.add(tool)

        def call(name, args):
            return SimpleNamespace(
                text="",
                calls=[
                    {
                        "id": "call",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                ],
                opaque=None,
            )

        query = call("learn_query", {"operation": "courses"})
        report = call("proactive_report", {"notify": False, "message": "done", "reason": "checked"})
        llm = SimpleNamespace(complete=AsyncMock(side_effect=[query, query, report, report]))
        runtime = AgentRuntime(cfg, store, llm, registry, None, pe.prepared)
        runtime.wait_for_interaction = pe.wait_for_interaction
        pe.runtime = runtime

        async def seed(key):
            event = ExternalEvent(source="school", kind="job", title="check courses", dedup_key=key)
            return await store.put(
                "event",
                key,
                {
                    "event": event.model_dump(mode="json"),
                    "decision": Decision(reason="check").model_dump(),
                    "status": "filtered",
                },
            )

        changes = asyncio.Queue()
        store.bus.subscribers.add(changes)

        async def wait_status(key, status):
            async with asyncio.timeout(5):
                while (await pe.get_event(key))["status"] != status:
                    await changes.get()

        try:
            ctx = Context(PREPARE_SESSION + "a", "owner", owner=True, background=True)
            assert "login" not in [s["function"]["name"] for s in registry.specs(ctx)]
            assert "error" in json.loads(await registry.execute(ctx, "login", {}))
            login.assert_not_awaited()
            for key in ["a", "b"]:
                await seed(key)
            await pe.tick()
            for key in ["a", "b"]:
                await wait_status(key, "waiting")
                row = await pe.get_event(key)
                assert row["wait_key"] == "school:auth" and row["expires_at"] > now().timestamp()
            await pe.tick()
            await pe.tick()
            notify.assert_awaited_once()
            assert "私聊" in notify.await_args.args[0]
            assert llm.complete.await_count == 2
            await pe.resolve_interaction("other:auth")
            assert (await pe.get_event("a"))["status"] == "waiting"
            await plugin.resolve_interaction("auth")
            for key in ["a", "b"]:
                assert (await pe.get_event(key))["status"] == "filtered"
            await pe.tick()
            for key in ["a", "b"]:
                await wait_status(key, "processed")
                assert (await pe.get_event(key))["result"]["message"] == "done"

            row = await seed("expired")
            await store.put("event", "expired", {**row, "status": "running"})
            await pe.wait_for_interaction(
                PREPARE_SESSION + "expired",
                InteractionRequired("school:auth", "wait", 0),
                now().timestamp(),
            )
            await pe.tick()
            assert (await pe.get_event("expired"))["status"] == "processed"
            assert llm.complete.await_count == 4
        finally:
            await runtime.close()
            await store.close()

    asyncio.run(run())


def test_learn_auth_event_deduplicates_for_one_day(tmp_path, monkeypatch):
    async def run():
        ctx = PluginContext("school", {}, tmp_path, AsyncMock(), AsyncMock())
        learn = OneLearnPlugin(ctx)
        learn.invoke = AsyncMock(return_value={"ok": False, "status": "session_expired"})
        await learn.sync_once()
        await learn.sync_once()

        class Tomorrow(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(tz) + timedelta(days=1)

        monkeypatch.setattr("shiritaibot.learn.datetime", Tomorrow)
        await learn.sync_once()
        a, b, c = [call.args[0].dedup_key for call in ctx.emit.await_args_list]
        assert a == b and a != c

    asyncio.run(run())
