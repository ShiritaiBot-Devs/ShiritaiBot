import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from shiritaibot.config import Settings
from shiritaibot.core import EventBus, ExternalEvent
from shiritaibot.memory import ShortTermMemory
from shiritaibot.proactive import ProactiveEngine
from shiritaibot.store import Store


def _config(*, quiet_start: str = "00:00", quiet_end: str = "23:59"):
    return SimpleNamespace(
        value=Settings.model_validate(
            {
                "proactive": {
                    "enabled": True,
                    "timezone": "Asia/Shanghai",
                    "quiet_start": quiet_start,
                    "quiet_end": quiet_end,
                }
            }
        )
    )


def _llm():
    llm = SimpleNamespace(
        decide=AsyncMock(return_value=None),
        complete=AsyncMock(
            return_value=SimpleNamespace(
                text='{"importance": 2, "decision": "log", "message": "ok", "reason": "triaged"}'
            )
        ),
    )
    return llm


def test_scheduled_and_memory_events_skip_judge(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        try:
            llm = _llm()
            notify = AsyncMock()
            engine = ProactiveEngine(_config(), store, llm, notify, ShortTermMemory(store, llm))

            events = [
                ExternalEvent(
                    source="routine",
                    kind="routine.reminder",
                    dedup_key="rem:1",
                    title="提醒：交周报",
                ),
                ExternalEvent(
                    source="routine",
                    kind="routine.job",
                    dedup_key="job:1",
                    title="定时巡检服务状态",
                ),
                ExternalEvent(
                    source="memory",
                    kind="memory.conflict",
                    dedup_key="mem:1",
                    title="记忆里有 2 个问题需要主人确认",
                ),
            ]
            keys = [
                await engine.emit(ev, routine_session="owner" if ev.source == "routine" else None)
                for ev in events
            ]
            for key in keys:
                row = await engine.get_event(key)
                await engine.handle(row)

            # None of these events should call the triage/decision model.
            llm.decide.assert_not_awaited()
            llm.complete.assert_not_awaited()

            rem = (await engine.get_event(keys[0]))["decision"]
            assert rem["importance"] == 4
            assert rem["decision"] == "notify"
            assert rem["urgency"] == "critical"

            job = (await engine.get_event(keys[1]))["decision"]
            assert job["importance"] == 3
            assert job["decision"] == "notify"
            assert job["urgency"] == "high"

            mem = (await engine.get_event(keys[2]))["decision"]
            assert mem["importance"] == 1
            assert mem["decision"] == "digest"
            assert mem["urgency"] == "low"
        finally:
            await store.close()

    asyncio.run(run())


def test_external_event_still_uses_judge(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        try:
            llm = _llm()
            engine = ProactiveEngine(_config(), store, llm, AsyncMock(), ShortTermMemory(store, llm))

            key = await engine.emit(
                ExternalEvent(
                    source="email",
                    kind="email.received",
                    dedup_key="mail:1",
                    title="账单通知",
                )
            )
            await engine.handle(await engine.get_event(key))

            llm.decide.assert_awaited_once()
            llm.complete.assert_awaited_once()
            decision = (await engine.get_event(key))["decision"]
            assert decision["importance"] == 2
            assert decision["decision"] == "log"
        finally:
            await store.close()

    asyncio.run(run())


def test_reminder_bypasses_quiet_hours_while_job_respects_it(tmp_path, monkeypatch):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        try:
            fixed = datetime(2026, 11, 22, 2, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
            monkeypatch.setattr("shiritaibot.proactive.now", lambda: fixed)

            notify = AsyncMock()
            llm = _llm()
            engine = ProactiveEngine(
                _config(quiet_start="23:00", quiet_end="08:00"),
                store,
                llm,
                notify,
                ShortTermMemory(store, llm),
            )

            rem_key = await engine.emit(
                ExternalEvent(
                    source="routine",
                    kind="routine.reminder",
                    dedup_key="rem:quiet",
                    title="提醒：喝水",
                ),
                routine_session="owner",
            )
            job_key = await engine.emit(
                ExternalEvent(
                    source="routine",
                    kind="routine.job",
                    dedup_key="job:quiet",
                    title="定时巡检",
                ),
                routine_session="owner",
            )

            for key, msg in ((rem_key, "该喝水啦"), (job_key, "巡检完成")):
                row = await engine.get_event(key)
                await engine.handle(row)
                row = await engine.get_event(key)
                await engine.finish(
                    row,
                    f'{{"notify": true, "message": "{msg}", "reason": "done"}}',
                )

            await engine.flush_notifications()

            notify.assert_awaited_once_with("该喝水啦")
            rem_note = await store.get("notification", rem_key)
            job_note = await store.get("notification", job_key)
            assert rem_note["status"] == "sent"
            assert job_note["status"] == "pending"
            assert job_note["deferred"] is True
        finally:
            await store.close()

    asyncio.run(run())
