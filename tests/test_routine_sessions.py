import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from shiritaibot.agent import AgentRuntime
from shiritaibot.auth import Permissions
from shiritaibot.config import Settings
from shiritaibot.core import (
    Context,
    EventBus,
    ExternalEvent,
    PublicError,
    Reply,
    Tool,
    canonical,
    now,
)
from shiritaibot.dependencies import RuntimeEnvironment
from shiritaibot.memory import ShortTermMemory
from shiritaibot.proactive import PREPARE_SESSION, ProactiveEngine
from shiritaibot.routine import RoutineManager
from shiritaibot.store import Store
from shiritaibot.tools import ToolRegistry
from shiritaibot.tools.builtin import register_builtin
from shiritaibot.web import create_app


def test_routine_tools_scope_reads_and_writes_to_the_current_session(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        try:
            config = SimpleNamespace(
                value=Settings.model_validate(
                    {
                        "policy": {
                            "group_default": {"mode": "agent", "risks": {"dangerous": "allow"}}
                        }
                    }
                )
            )
            emit = AsyncMock()
            routine = RoutineManager(store, emit)
            registry = ToolRegistry(Permissions(config, store), store)
            await register_builtin(
                SimpleNamespace(
                    config=config,
                    store=store,
                    routine=routine,
                    registry=registry,
                    vault=SimpleNamespace(resolve=AsyncMock(return_value="")),
                    memes=SimpleNamespace(core=None),
                    skills=None,
                )
            )
            owner = Context("web:owner", "owner", "agent", owner=True)
            group = Context("group:qq:1", "member", "agent", group=True)
            other_group = Context("group:qq:2", "member", "agent", group=True)

            async def call(ctx, name, **args):
                return json.loads(await registry.execute(ctx, f"routine_{name}", args))

            mine = await call(owner, "create", kind="todo", title="个人待办")
            due = (now() - timedelta(minutes=1)).isoformat()
            theirs = await call(group, "create", kind="reminder", title="群提醒", due=due)
            assert mine["scope"] == owner.session
            assert theirs["scope"] == group.session
            assert await call(owner, "list") == [mine]
            assert await call(group, "list") == [theirs]
            assert await call(other_group, "list") == []

            missing = {"error": "日程不存在或已删除"}
            assert await call(group, "update", id=mine["id"], title="越界") == missing
            assert await call(group, "update", id=mine["id"], status="completed") == missing
            assert await call(other_group, "delete", id=theirs["id"]) == missing
            assert (await call(group, "update", id=theirs["id"], title="群提醒已修改"))[
                "scope"
            ] == group.session

            await routine.tick()
            emit.assert_awaited_once()
            assert emit.await_args.args[0].title.endswith("群提醒已修改")
            assert emit.await_args.kwargs == {
                "routine_session": group.session,
                "routine_actor": group.actor,
            }
            assert await call(group, "list") == []
            fired = (await call(group, "list", include_finished=True))[0]
            assert (fired["status"], fired["scope"]) == ("fired", group.session)
            assert (await call(group, "delete", id=theirs["id"]))["deleted"] == theirs["id"]
            assert await call(owner, "list") == [mine]
        finally:
            await store.close()

    asyncio.run(run())


def test_existing_routines_execute_and_notify_in_their_own_sessions(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        config = SimpleNamespace(
            value=Settings.model_validate(
                {
                    "policy": {
                        "private_default": {
                            "mode": "agent",
                            "risks": {"safe": "allow", "dangerous": "deny"},
                        }
                    },
                    "proactive": {"quiet_start": "00:00", "quiet_end": "00:00"},
                }
            )
        )
        job_session, rem_session = "private:qq:123", "group:qq:456"
        seen = []

        async def inspect_scope(ctx, args):
            seen.append((ctx.session, ctx.actor, ctx.owner))
            return {"ok": True}

        async def complete(route, system, history, tools, *, settings, scope):
            if scope == job_session:
                assert "旧定时任务" in history[-1]["content"] or history[-1]["role"] == "tool"
                assert any(message["content"] == "原会话历史" for message in history)
                names = {tool["function"]["name"] for tool in tools}
                assert "inspect_scope" in names
                assert "owner_tool" not in names
                if history[-1]["role"] != "tool":
                    return Reply(
                        calls=[
                            {
                                "id": "inspect",
                                "type": "function",
                                "function": {"name": "inspect_scope", "arguments": "{}"},
                            }
                        ]
                    )
            return Reply(text=canonical({"notify": True, "message": scope, "reason": "done"}))

        llm = SimpleNamespace(complete=complete)
        notify_owner, notify_session = AsyncMock(), AsyncMock()
        engine = ProactiveEngine(
            config,
            store,
            llm,
            notify_owner,
            ShortTermMemory(store, llm),
            notify_session=notify_session,
        )
        registry = ToolRegistry(Permissions(config, store), store)
        registry.add(
            Tool("inspect_scope", "Check scope", {"type": "object"}, inspect_scope, "safe")
        )
        registry.add(
            Tool("owner_tool", "Restricted", {"type": "object"}, inspect_scope, "dangerous")
        )
        runtime = AgentRuntime(config, store, llm, registry, None, engine.prepared)
        engine.runtime = runtime
        routine = RoutineManager(store, engine.emit)
        try:
            due = now() - timedelta(minutes=1)
            for key, scope, kind, title in (
                ("job", job_session, "job", "旧定时任务"),
                ("rem", rem_session, "reminder", "旧群提醒"),
            ):
                await store.put(
                    "routine",
                    key,
                    {
                        "kind": kind,
                        "title": title,
                        "status": "scheduled",
                        "cron": "* * * * *",
                        "timezone": "Asia/Shanghai",
                        "next_fire": due.timestamp(),
                    },
                    scope,
                )
            await store.append(job_session, "past", {"role": "user", "content": "原会话历史"})
            await routine.tick()
            await engine.tick()
            for _ in range(200):
                events = await store.list("event")
                if len(events) == 2 and all(event["status"] == "processed" for event in events):
                    break
                await asyncio.sleep(0.01)
            assert len(events) == 2 and all(not event["loop_failed"] for event in events)
            await engine.flush_notifications()
            assert seen == [(job_session, "123", False)]
            assert {call.args for call in notify_session.await_args_list} == {
                (job_session, job_session),
                (rem_session, rem_session),
            }
            notify_owner.assert_not_awaited()
            assert all(session.startswith(PREPARE_SESSION) for session in runtime.actors)
            assert len(await store.history(job_session)) == 1
        finally:
            await runtime.close()
            await store.close()

    asyncio.run(run())


def test_old_pending_reminder_recovers_origin_and_external_events_cannot_set_it(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        try:
            cfg = SimpleNamespace(value=Settings())
            llm = SimpleNamespace()
            notify_owner, notify_session = AsyncMock(), AsyncMock()
            engine = ProactiveEngine(
                cfg,
                store,
                llm,
                notify_owner,
                ShortTermMemory(store, llm),
                notify_session=notify_session,
            )
            session = "web:original-chat"
            event = ExternalEvent(
                source="routine",
                kind="routine.reminder",
                title="待发送",
                dedup_key="rem:1",
                payload={"routine_id": "rem", "routine_session": "web:wrong"},
            )
            try:
                await engine.emit(event)
            except PublicError:
                pass
            else:
                raise AssertionError("external event accepted as a scheduled reminder")
            await store.put("routine", "rem", {"kind": "reminder"}, session)
            await store.put("event", "old-event", {"event": event.model_dump(mode="json")})
            await store.put(
                "notification",
                "old-event",
                {
                    "source": "routine",
                    "status": "pending",
                    "urgency": "critical",
                    "message": "待发送",
                },
            )
            await engine.flush_notifications()
            notify_session.assert_awaited_once_with(session, "待发送")
            notify_owner.assert_not_awaited()
        finally:
            await store.close()

    asyncio.run(run())


def test_web_routines_keep_legacy_rows_and_recover_known_session(tmp_path, monkeypatch):
    async def seed_legacy():
        store = Store(tmp_path, EventBus())
        await store.open()
        try:
            for key in ("from-chat", "unknown"):
                await store.put(
                    "routine", key, {"kind": "todo", "title": key, "status": "open"}, "owner"
                )
            await store.append(
                "web:planning",
                "old-turn",
                {
                    "role": "tool",
                    "_tool_name": "routine_create",
                    "content": canonical({"id": "from-chat"}),
                },
            )
            await store.append(
                "web:owner",
                "old-turn",
                {"role": "tool", "_tool_name": "routine_create", "content": "invalid JSON"},
            )
        finally:
            await store.close()

    asyncio.run(seed_legacy())
    monkeypatch.setattr(RuntimeEnvironment, "prepare", AsyncMock())
    headers = {"Authorization": "Bearer local-test"}
    with TestClient(create_app(tmp_path, token="local-test")) as client:
        assert client.get("/api/routines").status_code == 401
        response = client.get("/api/routines", headers=headers)
        assert response.status_code == 200
        assert {row["id"]: row["scope"] for row in response.json()} == {
            "from-chat": "web:planning",
            "unknown": "owner",
        }

        draft = {"kind": "todo", "title": "新待办"}
        response = client.post("/api/routines", headers=headers, json=draft)
        assert response.status_code == 200
        assert response.json()["scope"] == "web:owner"
        response = client.post(
            "/api/routines", headers=headers, json={**draft, "session": "group:qq:1"}
        )
        assert response.status_code == 200
        assert response.json()["scope"] == "group:qq:1"
        assert (
            client.post(
                "/api/routines", headers=headers, json={**draft, "session": "owner"}
            ).status_code
            == 422
        )
        for key, scope in [(response.json()["id"], "group:qq:1"), ("unknown", "owner")]:
            completed = client.post(f"/api/routines/{key}/completed", headers=headers)
            assert completed.status_code == 200
            assert (completed.json()["status"], completed.json()["scope"]) == ("completed", scope)
