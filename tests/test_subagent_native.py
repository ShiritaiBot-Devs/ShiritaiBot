import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from shiritaibot.config import Settings
from shiritaibot.core import Context, Reply, Tool, session_dir
from shiritaibot.plugin.core import PluginManager
from shiritaibot.tools import schema
from shiritaibot.web import create_app


@pytest.mark.parametrize("model", ["unknown", "embedding", "decision"])
def test_native_model_must_generate_text(model):
    with pytest.raises(ValidationError, match="文本生成模型"):
        Settings.model_validate(
            {
                "providers": [{"id": "normal"}, {"id": "judge", "protocol": "systemone"}],
                "models": [
                    {"id": "embedding", "kind": "embedding", "model": "vec", "provider": "normal"},
                    {"id": "decision", "model": "judge", "provider": "judge"},
                ],
                "subagents": [{"id": "worker", "kind": "shiritaibot", "model": model}],
            }
        )


def test_delegation_skills_permissions_and_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(PluginManager, "reload_all", AsyncMock(return_value=[]))
    app = create_app(tmp_path, token="test-token")
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as client:
        runtime = app.state.runtime
        cfg = client.get("/api/config").json()
        cfg["providers"] = [{"id": "local"}]
        cfg["models"] = [{"id": m, "model": m, "provider": "local"} for m in ("parent", "child")]
        cfg["routes"]["agent"] = ["parent"]
        cfg["policy"]["group_default"] = {"mode": "agent", "preset": "full_access"}
        cfg["policy"]["denied_tools"] = ["history_query"]
        cfg["subagents"] = [
            {
                "kind": "shiritaibot",
                "id": "reader",
                "model": "child",
                "native_tools": ["probe", "history_query", "subagent_start"],
                "native_skills": ["skill-creator"],
                "instructions": "Check the reference.",
            }
        ]
        assert client.put("/api/config", json=cfg).status_code == 200
        assert client.post("/api/subagents/reader/detect").status_code == 400
        origin = Context("group:qq:123", "guest", mode="agent", group=True)
        runtime.subagents.emit = AsyncMock()
        calls = []

        async def probe(ctx, args):
            calls.append(
                (ctx.actor, ctx.owner, ctx.group, session_dir(tmp_path, "workspaces", ctx.session))
            )
            return "checked"

        runtime.registry.add(Tool("probe", "test tool", schema({}), probe, "safe"))
        requests = []

        async def complete(route, system, messages, tools, *, settings, scope):
            requests.append((route, system, messages.copy(), tools, settings.routes[route], scope))
            if len(requests) == 1:
                return Reply(
                    calls=[
                        {
                            "id": str(i),
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                        for i, (name, args) in enumerate(
                            [
                                ("probe", {}),
                                ("skill_list", {}),
                                ("skill_read", {"name": "skill-creator"}),
                                ("skill_read", {"name": "shiritai-plugin-dev"}),
                                ("subagent_start", {"instance": "reader", "prompt": "nested"}),
                                ("history_query", {}),
                                ("current_time", {}),
                            ]
                        )
                    ]
                )
            return Reply(text="NEEDS_INPUT: Which file?" if len(requests) == 2 else "done")

        monkeypatch.setattr(runtime.llm, "complete", complete)

        async def run():
            async def call(ctx, name, args):
                return json.loads(await runtime.registry.execute(ctx, name, args))

            assert (await call(origin, "subagent_instances", {}))[0]["model"] == "child"
            row = await call(
                origin, "subagent_start", {"instance": "reader", "prompt": "Read the reference"}
            )
            key = row["id"]
            await runtime.subagents.tasks[key]
            row = await runtime.store.get("subagent_run", key)
            assert row["status"] == "needs_input" and row["question"] == "Which file?"
            # A proactive owner reply must continue using the original group member's identity.
            owner = Context("web:owner", "owner", "agent", owner=True)
            runtime.config.value.policy.owner_default.tools["subagent_send"] = "allow"
            await call(owner, "subagent_send", {"id": key, "message": "the second file"})
            await runtime.subagents.tasks[key]
            row = await runtime.store.get("subagent_run", key)
            assert row["status"] == "completed" and row["result"] == "done"
            assert calls == [("guest", False, True, Path(row["workspace"]))]
            assert await runtime.store.history(origin.session) == []
            assert [c.args[0].kind for c in runtime.subagents.emit.await_args_list] == [
                "subagent.needs_input",
                "subagent.completed",
            ]

        client.portal.call(run)
        assert len(requests) == 3
        for route, system, _, tools, models, scope in requests:
            assert route == "agent" and models == ["child"] and scope == origin.session
            assert "Check the reference." in system
            assert "skill-creator" in system and "shiritai-plugin-dev" not in system
            assert {t["function"]["name"] for t in tools} == {"probe", "skill_list", "skill_read"}
        history = requests[-1][2]
        assert history[0]["content"] == "Read the reference"
        assert history[-1]["content"] == "the second file"
        results = [json.loads(m["content"]) for m in history if m["role"] == "tool"]
        assert results[0] == "checked"
        assert [s["name"] for s in results[1]["skills"]] == ["skill-creator"]
        assert results[2]["name"] == "skill-creator"
        assert all("error" in result for result in results[3:])
        assert runtime.config.value.routes["agent"] == ["parent"]


def test_native_manual_approval_waits_for_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(PluginManager, "reload_all", AsyncMock(return_value=[]))
    app = create_app(tmp_path, token="test-token")
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as client:
        runtime = app.state.runtime
        cfg = client.get("/api/config").json()
        cfg["policy"]["owner_default"]["risks"] = {
            "safe": "allow",
            "normal": "allow",
            "sensitive": "allow",
            "dangerous": "allow",
        }
        cfg["subagents"] = [
            {
                "id": "worker",
                "kind": "shiritaibot",
                "native_tools": ["current_time"],
                "native_manual_approval": True,
            }
        ]
        assert client.put("/api/config", json=cfg).status_code == 200
        runtime.subagents.emit = AsyncMock()
        runtime.llm.complete = AsyncMock(
            side_effect=[
                Reply(
                    calls=[{"id": "clock", "function": {"name": "current_time", "arguments": "{}"}}]
                ),
                Reply(text="done"),
            ]
        )

        async def run():
            ctx = Context("web:owner", "owner", "agent", owner=True)
            pending = asyncio.Event()

            async def submit(*args):
                pending.set()

            monkeypatch.setattr(runtime.permissions.menu, "submit", submit)
            start = await runtime.subagents.start(ctx, "worker", "Read time")
            task = runtime.subagents.tasks[start["id"]]
            await asyncio.wait_for(pending.wait(), 3)
            assert not task.done()
            (approval,) = await runtime.store.list("approval")
            assert approval["status"] == "pending" and approval["subagent_run"] == start["id"]
            await runtime.permissions.resolve(approval["id"], False)
            await task
            history = runtime.llm.complete.await_args.args[2]
            assert "拒绝" in next(m["content"] for m in history if m["role"] == "tool")

        client.portal.call(run)
