import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from shiritaibot.auth import Permissions
from shiritaibot.config import Settings
from shiritaibot.core import Context, EventBus, canonical, now
from shiritaibot.dependencies import RuntimeEnvironment
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
            assert await call(group, "list") == []
            fired = (await call(group, "list", include_finished=True))[0]
            assert (fired["status"], fired["scope"]) == ("fired", group.session)
            assert (await call(group, "delete", id=theirs["id"]))["deleted"] == theirs["id"]
            assert await call(owner, "list") == [mine]
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
