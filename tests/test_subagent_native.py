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


def test_dynamic_spawn_inherits_permissions_and_resumes(tmp_path, monkeypatch):
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
        cfg["subagents"] = []
        assert client.put("/api/config", json=cfg).status_code == 200

        origin = Context("group:qq:456", "guest", mode="agent", group=True)
        runtime.subagents.emit = AsyncMock()
        calls = []

        async def probe(ctx, args):
            calls.append((ctx.actor, ctx.owner, ctx.group))
            return "ok"

        runtime.registry.add(Tool("probe", "test tool", schema({}), probe, "safe"))
        requests = []

        async def complete(route, system, messages, tools, *, settings, scope):
            requests.append((route, system, messages.copy(), tools, settings.routes[route], scope))
            if len(requests) == 1:
                return Reply(
                    calls=[
                        {"id": "0", "type": "function", "function": {"name": "probe", "arguments": "{}"}},
                        {
                            "id": "1",
                            "type": "function",
                            "function": {
                                "name": "subagent_spawn",
                                "arguments": json.dumps({"prompt": "nested"}),
                            },
                        },
                        {
                            "id": "2",
                            "type": "function",
                            "function": {"name": "history_query", "arguments": "{}"},
                        },
                    ]
                )
            return Reply(text="NEEDS_INPUT: Need target?" if len(requests) == 2 else "all done")

        monkeypatch.setattr(runtime.llm, "complete", complete)

        async def run():
            async def call(ctx, name, args):
                return json.loads(await runtime.registry.execute(ctx, name, args))

            row = await call(
                origin,
                "subagent_spawn",
                {"role": "researcher", "instructions": "Stay concise.", "prompt": "Inspect"},
            )
            assert row["instance"] == "dynamic:researcher"
            key = row["id"]
            await runtime.subagents.tasks[key]
            stored = await runtime.store.get("subagent_run", key)
            assert stored["dynamic"] is True
            assert stored["status"] == "needs_input" and stored["question"] == "Need target?"

            owner = Context("web:owner", "owner", "agent", owner=True)
            runtime.config.value.policy.owner_default.tools["subagent_send"] = "allow"
            await call(owner, "subagent_send", {"id": key, "message": "target A"})
            await runtime.subagents.tasks[key]
            stored = await runtime.store.get("subagent_run", key)
            assert stored["status"] == "completed" and stored["result"] == "all done"
            assert calls == [("guest", False, True)]

        client.portal.call(run)
        assert len(requests) == 3
        for route, system, _, tools, models, scope in requests:
            tool_names = {t["function"]["name"] for t in tools}
            assert route == "agent" and models == ["parent"] and scope == origin.session
            assert "Stay concise." in system
            assert "skill-creator" in system and "shiritai-plugin-dev" in system
            assert "probe" in tool_names and "skill_read" in tool_names
            assert "history_query" not in tool_names
            assert not any(name.startswith("subagent_") for name in tool_names)
        history = requests[-1][2]
        results = [json.loads(m["content"]) for m in history if m["role"] == "tool"]
        assert results[0] == "ok"
        assert "error" in results[1] and "error" in results[2]


def test_dynamic_spawn_tightening_model_override_and_toggle(tmp_path, monkeypatch):
    monkeypatch.setattr(PluginManager, "reload_all", AsyncMock(return_value=[]))
    app = create_app(tmp_path, token="test-token")
    with TestClient(app, headers={"Authorization": "Bearer test-token"}) as client:
        runtime = app.state.runtime
        cfg = client.get("/api/config").json()
        cfg["providers"] = [{"id": "local"}, {"id": "judge", "protocol": "systemone"}]
        cfg["models"] = [
            {"id": "parent", "model": "parent", "provider": "local"},
            {"id": "fast", "model": "fast", "provider": "local"},
            {"id": "vec", "kind": "embedding", "model": "vec", "provider": "local"},
            {"id": "dec", "model": "dec", "provider": "judge"},
        ]
        cfg["routes"]["agent"] = ["parent"]
        cfg["policy"]["owner_default"] = {"mode": "agent", "preset": "full_access"}
        assert client.put("/api/config", json=cfg).status_code == 200

        async def noop(ctx, args):
            return "ok"

        runtime.registry.add(Tool("safe_probe", "safe", schema({}), noop, "safe"))
        runtime.registry.add(Tool("write_probe", "normal", schema({}), noop, "normal"))

        seen = []

        async def complete(route, system, messages, tools, *, settings, scope):
            seen.append((system, {t["function"]["name"] for t in tools}, settings.routes[route]))
            return Reply(text="done")

        monkeypatch.setattr(runtime.llm, "complete", complete)

        async def run():
            owner = Context("web:owner", "owner", "agent", owner=True)

            async def call(ctx, name, args):
                return json.loads(await runtime.registry.execute(ctx, name, args))

            # read_only=True keeps only safe tools; model override selects fast; skills=[] disables skill tools.
            row = await call(
                owner,
                "subagent_spawn",
                {
                    "prompt": "Read only check",
                    "read_only": True,
                    "skills": [],
                    "model": "fast",
                },
            )
            await runtime.subagents.tasks[row["id"]]
            system, tool_names, models = seen[-1]
            assert models == ["fast"]
            assert "safe_probe" in tool_names
            assert "write_probe" not in tool_names
            assert "skill_list" not in tool_names and "skill_read" not in tool_names
            assert "<skills>" not in system

            # Explicit tools + skills strictly intersect parent's allowed_tools (cannot escalate).
            restricted = Context(
                "web:owner",
                "owner",
                "agent",
                owner=True,
                allowed_tools=("subagent_spawn", "safe_probe", "skill_list", "skill_read"),
            )
            row2 = await call(
                restricted,
                "subagent_spawn",
                {
                    "prompt": "Narrow check",
                    "tools": ["safe_probe", "write_probe"],
                    "skills": ["skill-creator"],
                },
            )
            await runtime.subagents.tasks[row2["id"]]
            system2, tool_names2, models2 = seen[-1]
            assert models2 == ["parent"]
            assert tool_names2 == {"safe_probe", "skill_list", "skill_read"}
            assert "skill-creator" in system2 and "shiritai-plugin-dev" not in system2

            # Invalid models are rejected.
            for bad_model in ("unknown", "vec", "dec"):
                err = await call(owner, "subagent_spawn", {"prompt": "x", "model": bad_model})
                assert "error" in err and "文本生成模型" in err["error"]

            # Disabling model override rejects model parameter.
            runtime.config.value.policy.dynamic_subagent_model_override = False
            err = await call(owner, "subagent_spawn", {"prompt": "x", "model": "fast"})
            assert "error" in err and "不允许动态子代理指定模型" in err["error"]

            # Disabling dynamic_subagent hides the tool and blocks resume.
            runtime.config.value.policy.dynamic_subagent = False
            spec_names = {t["function"]["name"] for t in runtime.registry.specs(owner)}
            assert "subagent_spawn" not in spec_names
            err_spawn = await call(owner, "subagent_spawn", {"prompt": "x"})
            assert "error" in err_spawn
            err_send = await call(owner, "subagent_send", {"id": row2["id"], "message": "more"})
            assert "error" in err_send and "未启用" in err_send["error"]

        client.portal.call(run)
