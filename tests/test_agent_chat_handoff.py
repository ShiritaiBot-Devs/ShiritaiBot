import asyncio
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from shiritaibot.agent import WORK_HANDOFF, AgentRuntime
from shiritaibot.auth import Permissions
from shiritaibot.config import Settings
from shiritaibot.core import Context, EventBus, Tool
from shiritaibot.llm import ProviderManager
from shiritaibot.prompts import prompt

HEADER = prompt("MEMORY_HEADER_IDS", "zh-CN")
from shiritaibot.store import Store, memory_context_key
from shiritaibot.tools import ToolRegistry


@pytest.mark.parametrize(
    ("enabled", "session", "mode", "decision", "answer", "models"),
    [
        (True, "private:qq:10", "agent", "chat", "正常回复", ["chat"]),
        (True, "private:qq:10", "agent", "chat", f"引用 {WORK_HANDOFF}", ["chat"]),
        (True, "private:qq:10", "agent", "chat", WORK_HANDOFF, ["chat", "work"]),
        (True, "web:guest", "agent", "chat", f" {WORK_HANDOFF}\n", ["chat", "work"]),
        (True, "private:qq:10", "agent", "work", "", ["work"]),
        (None, "private:qq:10", "agent", "chat", "原有回复", ["chat"]),
        (True, "private:qq:10", "chat", "chat", "原有回复", ["chat"]),
        (True, "group:qq:20", "agent", "chat", "原有回复", ["chat"]),
    ],
)
def test_agent_chat_handoff(tmp_path, enabled, session, mode, decision, answer, models):
    async def run():
        options = {"agent_chat_handoff": enabled} if enabled is not None else {}
        cfg = Settings.model_validate(
            {
                **options,
                "agent_split": True,
                "providers": [{"id": "provider", "base_url": "https://provider.example.test"}],
                "models": [
                    {
                        "id": "chat",
                        "provider": "provider",
                        "model": "chat",
                        "extra_body": {"tool_choice": "auto"},
                    },
                    {"id": "work", "provider": "provider", "model": "work"},
                ],
                "routes": {
                    "chat": ["chat"],
                    "agent_chat": ["chat"],
                    "agent": ["work"],
                    "router": ["chat"],
                },
                "policy": {
                    "private_default": {"mode": mode},
                    "group_default": {"mode": mode},
                    "sessions": {session: {"mode": mode, "tools": {"disabled_tool": "deny"}}},
                    "max_steps": 1,
                    "max_context_chars": 4000,
                },
                "context": {"strategy": "compress"},
                "memory": {"inject": False},
                "meme": {"enabled": True, "inject": True},
            }
        )
        config = SimpleNamespace(value=cfg)
        store = Store(tmp_path, EventBus())
        await store.open()
        llm = ProviderManager(config, SimpleNamespace(resolve=AsyncMock(return_value="")), store)
        llm.decide = AsyncMock(return_value={"route": {"probabilities": {decision: 1}}})
        requests = []

        async def generate(_provider, _path, body, *_):
            requests.append(body)
            content = "Work 最终回复" if body["model"] == "work" else answer
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        llm._generate = generate
        deliver = AsyncMock()
        registry = ToolRegistry(Permissions(config, store), store)
        for name in ("skill_read", "disabled_tool"):
            registry.add(Tool(name, name, {"type": "object"}, AsyncMock(), "safe"))
        runtime = AgentRuntime(
            config,
            store,
            llm,
            registry,
            None,
            deliver,
            skills=SimpleNamespace(prompt=AsyncMock(return_value="技能工具提示")),
        )

        async def hook(name, info, *args):
            if name == "llm_request":
                requests_from_hook.append((info.id, info.route, args[0].step))
                args[0].system_prompt += "插件工具提示"

        requests_from_hook = []
        runtime.hooks = SimpleNamespace(has=lambda _: True, call=hook)
        ctx = Context(session, "10", group=session.startswith("group:"))
        prior_tool_call = {
            "id": "previous-call",
            "type": "function",
            "function": {"name": "skill_read", "arguments": "{}"},
        }
        probe = enabled and mode == "agent" and decision == "chat" and not ctx.group
        raw_result = "原始工具结果" * (1000 if probe and models == ["chat"] else 1)
        try:
            await store.put(
                "context_state",
                memory_context_key(session, "default"),
                {"summary": "更早的对话摘要", "seq": 0},
            )
            for message in [
                {
                    "role": "user",
                    "_original_content": "之前的问题",
                    "content": f"之前的问题\n<context local_time='previous'>\n{HEADER}\n记忆事实\n</context>",
                },
                {"role": "assistant", "content": "可见进度", "tool_calls": [prior_tool_call]},
                {"role": "tool", "tool_call_id": "previous-call", "content": raw_result},
                {"role": "assistant", "content": "可见结论，产物 file-1"},
            ]:
                await store.append(session, "prior", message)
            await runtime.turn(SimpleNamespace(steering=deque()), ctx, "当前用户消息")

            assert [request["model"] for request in requests] == models
            (turn,) = await store.list("turn")
            delivered = "Work 最终回复" if models[-1] == "work" else answer
            deliver.assert_awaited_once_with(session, delivered)
            assert turn["status"] == "completed"
            assert turn["steps"] == 0  # A transfer still leaves the full Work step budget.
            assert turn.get("agent_chat_handoff", False) == (len(models) == 2)
            history = await store.history(session)
            assert len(history) == 6
            assert history[2]["content"] == raw_result
            assert history[-1]["content"] == delivered

            if probe:
                chat = requests[0]
                assert "tools" not in chat and "tool_choice" not in chat
                assert all("tool_calls" not in message for message in chat["messages"])
                assert all(message["role"] != "tool" for message in chat["messages"])
                assert "可见进度" in str(chat) and "可见结论，产物 file-1" in str(chat)
                assert "记忆事实" in str(chat) and "memory_get" not in str(chat)
                assert "更早的对话摘要" in str(chat) and "history_query" not in str(chat)
                assert WORK_HANDOFF in str(chat) and "工具提示" not in str(chat)
                assert "meme_search" not in str(chat)
            if models[-1] == "work":
                assert turn["route"] == "agent"
                assert requests_from_hook == [(turn["id"], "agent", 0)]
                work = requests[-1]
                assert {spec["function"]["name"] for spec in work["tools"]} == {"skill_read"}
                assert "原始工具结果" in str(work) and "插件工具提示" in str(work)
                assert "history_query" in str(work)
                assert "技能工具提示" in str(work) and WORK_HANDOFF not in str(work)
            elif not probe:
                assert requests[0]["tool_choice"] == "auto"
                assert "原始工具结果" in str(requests[0])
        finally:
            await llm.client.aclose()
            await store.close()

    asyncio.run(run())
