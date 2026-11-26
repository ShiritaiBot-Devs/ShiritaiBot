import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from shiritaibot.agent import AgentRuntime, repair_history
from shiritaibot.agent.context import ContextManager
from shiritaibot.agent.execution import TurnExecution, TurnState
from shiritaibot.auth import Permissions
from shiritaibot.config import Settings
from shiritaibot.core import Context, EventBus, Reply, Tool
from shiritaibot.store import Store
from shiritaibot.tools import ToolRegistry


def tool_call(key):
    return {
        "id": key, "type": "function",
        "function": {"name": "probe", "arguments": "{}"},
    }


async def make_runtime(tmp_path, llm, **policy):
    config = SimpleNamespace(value=Settings.model_validate({
        "policy": {"owner_default": {"mode": "agent"}, **policy},
        "memory": {"inject": False},
    }))
    store = Store(tmp_path, EventBus())
    await store.open()
    registry = ToolRegistry(Permissions(config, store), store)
    registry.add(Tool("probe", "Probe", {"type": "object"}, AsyncMock(), "safe"))
    return AgentRuntime(config, store, llm, registry, None, AsyncMock())


async def wait_for_status(runtime, ctx, status):
    async with asyncio.timeout(5):
        while True:
            goal = await runtime.goals.get(ctx.session)
            if goal and goal["status"] == status:
                return goal
            await asyncio.sleep(0.005)


def test_turn_reserves_a_tool_free_handoff(tmp_path):
    async def run():
        complete = AsyncMock(side_effect=[
            Reply("", [tool_call("first")]), Reply("Handoff", []),
        ])
        runtime = await make_runtime(tmp_path, SimpleNamespace(complete=complete), max_steps=2)
        ctx = Context("web:owner", "owner", mode="agent", owner=True)
        finished = asyncio.Event()
        runtime.hooks = SimpleNamespace(
            has=lambda name: name == "agent_done",
            call=AsyncMock(side_effect=lambda name, *args: finished.set() if name == "agent_done" else None),
        )
        try:
            await runtime.submit(ctx, "Do the work")
            await asyncio.wait_for(finished.wait(), 5)
            # agent_done precedes persistence of the terminal status.
            async with asyncio.timeout(5):
                while True:
                    turn, = await runtime.store.list("turn")
                    if turn["status"] == "budget_stopped":
                        break
                    await asyncio.sleep(0.005)
            assert turn["status"] == "budget_stopped"
            assert turn["model_steps"] == 2
            request = complete.await_args
            assert request.args[3] == [] and request.kwargs["tool_free"]
            assert "Do the work" in str(request.args[2])
            assert (await runtime.store.history(ctx.session))[-1]["content"] == "Handoff"
        finally:
            await runtime.close()
            await runtime.store.close()

    asyncio.run(run())


def test_compaction_projection_survives_next_turn(tmp_path):
    async def run():
        llm = SimpleNamespace(complete=AsyncMock(return_value=Reply("Verified prior work", [])))
        runtime = await make_runtime(tmp_path, llm, max_context_chars=4000)
        cfg = runtime.config.value
        store = runtime.store
        session, turn = "web:owner", "old-turn"
        history = [{"role": "user", "content": "Original instruction"}]
        for i in range(6):
            history.extend([
                {"role": "assistant", "content": "", "tool_calls": [tool_call(str(i))]},
                {"role": "tool", "tool_call_id": str(i), "content": str(i) * 1800},
            ])
        try:
            for message in history:
                message["_seq"] = await store.append(session, turn, message)
            execution = TurnExecution(cfg, llm, session)

            async def checkpoint(projected, summary, through):
                await store.put("turn_context", turn, {"summary": summary, "seq": through}, session)

            await execution.reduce(history, 0, "", checkpoint)
            assert execution.state == TurnState.RUNNING
            assert [m["tool_call_id"] for m in repair_history(history, None) if m["role"] == "tool"] == ["3", "4", "5"]
            assert len(await store.history(session)) == 13
            cfg.policy.max_context_chars = 12000
            next_turn = await ContextManager(store, llm).prepare(
                session, cfg, {"role": "user", "content": "Continue"}, owner=True,
            )
            assert next_turn[0]["content"] == "Original instruction"
            assert "Verified prior work" in next_turn[1]["content"]
            assert [m["tool_call_id"] for m in next_turn if m["role"] == "tool"] == ["3", "4", "5"]
        finally:
            await runtime.close()
            await store.close()

    asyncio.run(run())


def test_repeat_guard_resets_when_tool_result_changes():
    async def run():
        execution = TurnExecution(Settings(), None, "web:owner")
        function = tool_call("one")["function"]
        original = '{"result":"unchanged"}'
        assert execution.observe_result(function, original) == original
        assert execution.observe_result(function, original) == original
        assert '"warning"' in execution.observe_result(function, original)
        assert execution.observe_result(function, '{"result":"changed"}') == '{"result":"changed"}'
        assert execution.repeat_count == 1

    asyncio.run(run())


def test_goal_waits_for_subagents_and_judges_after_wake(tmp_path):
    async def run():
        runtime = await make_runtime(tmp_path, SimpleNamespace())
        ctx = Context("web:owner", "owner", mode="agent", owner=True)
        routes = []

        async def complete(route, system, history, tools, **kwargs):
            routes.append(route)
            if route == "light":
                assert kwargs["tool_free"] and not tools
                return Reply('{"verdict":"MET","feedback":""}', [])
            if len(routes) == 1:
                return Reply("", [tool_call("delegate")])
            if len(routes) == 2:
                return Reply("Delegated", [])
            assert "worker-status" in system and "worker-output" in system
            return Reply("Verified", [])

        async def delegate(tool_ctx, args):
            await runtime.goals.bind(tool_ctx, {"id": "worker-status"})
            return {"id": "worker-status"}

        runtime.llm.complete = complete
        runtime.registry.tools["probe"].handler = delegate
        try:
            await runtime.submit(ctx, "/goal Verify after delegation")
            waiting = await wait_for_status(runtime, ctx, "waiting_subagent")
            assert waiting["continuations_used"] == 0
            assert routes == ["agent", "agent"]
            assert await runtime.goals.subagent_result(
                ctx.session, {"id": "worker-status", "status": "completed", "result": "worker-output"},
            )
            assert await runtime.goals.subagent_result(
                ctx.session, {"id": "worker-status", "status": "completed", "result": "worker-output"},
            )
            completed = await wait_for_status(runtime, ctx, "completed")
            assert completed["continuations_used"] == 1
            assert completed["total_steps_used"] == 3
            assert routes == ["agent", "agent", "agent", "light"]
        finally:
            await runtime.close()
            await runtime.store.close()

    asyncio.run(run())


def test_user_message_supersedes_in_flight_goal_judge(tmp_path):
    async def run():
        runtime = await make_runtime(tmp_path, SimpleNamespace())
        ctx = Context("web:owner", "owner", mode="agent", owner=True)
        judging = asyncio.Event()
        judge_calls = 0
        main_inputs = []

        async def complete(route, system, history, tools, **kwargs):
            nonlocal judge_calls
            if route == "light":
                judge_calls += 1
                if judge_calls == 1:
                    judging.set()
                    await asyncio.Future()
                return Reply('{"verdict":"MET","feedback":""}')
            main_inputs.append(history[-1]["content"])
            return Reply("Verified")

        runtime.llm.complete = complete
        try:
            await runtime.submit(ctx, "/goal Verify the result")
            await asyncio.wait_for(judging.wait(), 5)
            assert await runtime.submit(ctx, "Check the additional result too") == "steering"
            goal = await wait_for_status(runtime, ctx, "completed")
            assert goal["continuations_used"] == 0
            assert judge_calls == 2
            assert len(main_inputs) == 2 and "additional result" in main_inputs[-1]
        finally:
            await runtime.close()
            await runtime.store.close()

    asyncio.run(run())
