import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from shiritaibot.config import SubagentConfig
from shiritaibot.core import Context, EventBus
from shiritaibot.store import Store
from shiritaibot.subagent import SubagentManager, command_for, parse_output


def test_proxy_is_per_instance(monkeypatch):
    manager = SubagentManager(None, None, None, None)
    cfg = SubagentConfig(id="legacy")
    assert manager.child_env(cfg) is None
    monkeypatch.setenv("HTTP_PROXY", "http://inherited:1234")
    proxy = "socks5h://127.0.0.1:1080"
    proxied = SubagentConfig(id="custom", kind="generic", proxy=f" {proxy} ")
    assert manager.child_env(proxied)["HTTP_PROXY"] == proxy
    base = dict(os.environ, GIT_AUTHOR_NAME="test", NO_PROXY="localhost")
    manager.env = lambda: base
    child = manager.child_env(proxied)
    for key in ("http_proxy", "https_proxy", "all_proxy"):
        assert child[key] == child[key.upper()] == proxy
    assert child["GIT_AUTHOR_NAME"] == "test"
    assert child["NO_PROXY"] == "localhost"
    assert manager.child_env(cfg) is base
    assert base["HTTP_PROXY"] == os.environ["HTTP_PROXY"] == "http://inherited:1234"
    with pytest.raises(ValidationError):
        SubagentConfig(id="bad", proxy="http://example.com:invalid")


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (None, "未返回最终结果"),
        ({"status": "ERROR", "error": "invalid model"}, "invalid model"),
        ({"status": "INTERRUPTED", "response": "partial"}, "partial"),
        (
            {"status": "SUCCESS", "response": "partial", "finished_reason": "print_timeout"},
            "partial",
        ),
        ({"status": "SUCCESS", "response": ""}, "SUCCESS"),
    ],
)
def test_incomplete_antigravity_turn_fails(result, expected):
    events = [json.dumps({"event": "init", "conversation_id": "one"})]
    if result:
        events.append(json.dumps({"event": "result", "result": result}))
    parsed = parse_output("antigravity", "\n".join(events))
    assert parsed["native"] == "one"
    assert parsed["error"] and expected in parsed["result"]


def test_antigravity_start_and_resume(tmp_path, monkeypatch):
    cfg = SubagentConfig(
        id="google",
        kind="antigravity",
        model="my-model",
        antigravity_mode="plan",
        add_dirs=[str(tmp_path)],
        proxy="http://localhost:1234",
    )
    assert command_for(cfg, tmp_path) == [
        "agy",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--model",
        "my-model",
        "--mode",
        "plan",
        "--add-dir",
        str(tmp_path),
    ]
    calls = []

    async def process(command, workspace, stdin, timeout_seconds, *, env, limit, on_line):
        turn = len(calls)
        assert env["HTTPS_PROXY"] == cfg.proxy
        assert command == command_for(cfg, workspace) + (["--conversation", "one"] if turn else [])
        assert stdin.endswith("\n") and len(stdin.splitlines()) == 1
        message = json.loads(stdin)["message"]["content"]
        assert message.startswith("continue" if turn else "multi\nline")
        assert ("plugin note" in message) == (turn == 0)
        calls.append(workspace)
        on_line(b'{"event":"init","conversation_id":"one"}')
        on_line(
            json.dumps(
                {
                    "event": "result",
                    "result": {
                        "conversation_id": "one",
                        "status": "SUCCESS" if turn else "WAITING",
                        "response": "done" if turn else "which branch?",
                    },
                }
            )
        )
        return {"stdout": "", "exit_code": 0, "truncated": False}

    monkeypatch.setattr("shiritaibot.subagent.run_process", process)

    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        emit = AsyncMock()
        manager = SubagentManager(
            SimpleNamespace(value=SimpleNamespace(subagents=[cfg])), store, tmp_path, emit
        )
        manager.notes = lambda: "\nplugin note"
        ctx = Context("s", "owner", owner=True)
        try:
            row = await manager.start(ctx, cfg.id, "multi\nline")
            key = row["id"]
            await manager.tasks[key]
            row = await store.get("subagent_run", key)
            assert row["status"] == "needs_input" and row["question"] == "which branch?"
            await manager.send(ctx, key, "continue")
            await manager.tasks[key]
            row = await store.get("subagent_run", key)
            assert row["status"] == "completed" and row["result"] == "done"
            assert calls[0] == calls[1] and row["native_session_id"] == "one"
            assert [call.args[0].kind for call in emit.await_args_list] == [
                "subagent.needs_input",
                "subagent.completed",
            ]
        finally:
            await manager.close()
            await store.close()

    asyncio.run(run())
