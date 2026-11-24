import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from shiritaibot.config import Settings
from shiritaibot.core import EventBus, ExternalEvent
from shiritaibot.memory import ShortTermMemory
from shiritaibot.proactive import Decision, PREPARE_SESSION, ProactiveEngine
from shiritaibot.store import Store

RESULT = '{"notify": true, "message": "任务完成", "reason": "ok"}'


def _config():
    return SimpleNamespace(
        value=Settings.model_validate(
            {"proactive": {"enabled": True, "timezone": "Asia/Shanghai"}}
        )
    )


def _event(session: str | None) -> dict:
    payload = {
        "run_id": "r1",
        "instance": "Codex",
        "status": "completed",
        "task": "t",
        "result": "r",
    }
    if session is not None:
        payload["session"] = session
    return {
        "source": "subagent",
        "kind": "subagent.completed",
        "dedup_key": "r1:0",
        "title": "子代理 Codex: completed",
        "urgency": "high",
        "payload": payload,
    }


def _row(key: str, event: dict) -> dict:
    return {
        "id": key,
        "event": event,
        "status": "filtered",
        "decision": Decision(
            importance=4, decision="notify", message=event["title"], reason="delegated"
        ).model_dump(),
    }


def test_delegated_results_follow_their_origin_session(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        llm = SimpleNamespace(decide=AsyncMock(return_value=None), complete=AsyncMock())
        engine = ProactiveEngine(_config(), store, llm, AsyncMock(), ShortTermMemory(store, llm))
        engine.runtime = SimpleNamespace(submit=AsyncMock(return_value=RESULT))
        try:
            # Non-owner origin: the loop runs in the origin session, notify targets it.
            row1 = _row("k1", _event("private:qq-main:201574690"))
            await engine.act(row1)
            assert engine.runtime.submit.await_args.args[0].session == "private:qq-main:201574690"
            await engine.finish(row1, RESULT)
            note = await store.get("notification", "k1")
            assert note["target_session"] == "private:qq-main:201574690"

            # Owner origin (web): classic background loop, notification to the owner.
            row2 = _row("k2", _event("web:main"))
            await engine.act(row2)
            ctx = engine.runtime.submit.await_args.args[0]
            assert ctx.session == PREPARE_SESSION + "k2"
            assert ctx.owner
            await engine.finish(row2, RESULT)
            note = await store.get("notification", "k2")
            assert "target_session" not in note

            # Missing origin: same owner fallback.
            row3 = _row("k3", _event(None))
            await engine.act(row3)
            ctx = engine.runtime.submit.await_args.args[0]
            assert ctx.session == PREPARE_SESSION + "k3"
            await engine.finish(row3, RESULT)
            note = await store.get("notification", "k3")
            assert "target_session" not in note
        finally:
            await store.close()

    asyncio.run(run())
