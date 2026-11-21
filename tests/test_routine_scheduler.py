import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock

from shiritaibot.core import EVENT_TITLE_LIMIT, EventBus, now
from shiritaibot.routine import RoutineManager
from shiritaibot.store import Store


def test_existing_long_routines_fire_with_full_text(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        emit = AsyncMock()
        manager = RoutineManager(store, emit)
        fire = (now() - timedelta(minutes=2)).timestamp()
        try:
            for kind, length in [("job", 2000), ("reminder", EVENT_TITLE_LIMIT)]:
                title = "甲" * (length - 1) + "终"
                await store.put(
                    "routine",
                    kind,
                    dict(
                        kind=kind,
                        title=title,
                        cron="0 22 * * *",
                        timezone="Asia/Shanghai",
                        status="scheduled",
                        next_fire=fire,
                    ),
                    "owner",
                )
            await manager.tick()
            await manager.tick()
            assert emit.await_count == 2
            for (event,), _ in emit.await_args_list:
                assert len(event.title) == EVENT_TITLE_LIMIT
                assert event.title.startswith("（延迟提醒）") and event.title.endswith("…")
                key = event.payload["routine_id"]
                row = await store.get("routine", key)
                assert event.payload["routine_title"] == row["title"]
                assert event.dedup_key == f"{key}:{fire}"
                if key == "job":
                    assert row["next_fire"] > now().timestamp()
                else:
                    assert row["status"] == "fired" and row["next_fire"] is None
        finally:
            await store.close()

    asyncio.run(run())


def test_failed_delivery_backs_off_without_blocking_next_reminder(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        emit = AsyncMock(side_effect=[RuntimeError("temporary"), None, None])
        manager = RoutineManager(store, emit)
        fire = now().timestamp() - 2
        try:
            for key in ["a", "b"]:
                await store.put(
                    "routine",
                    key,
                    dict(kind="reminder", title=key, next_fire=fire, status="scheduled"),
                    "owner",
                )
            assert 0 < await manager.tick() <= 5
            assert (await store.get("routine", "a"))["status"] == "scheduled"
            assert (await store.get("routine", "b"))["status"] == "fired"
            await manager.tick()
            assert emit.await_count == 2
            manager.retries[("a", fire)] = (0, 5)
            await manager.tick()
            assert (await store.get("routine", "a"))["status"] == "fired"
            first, other, retry = [call.args[0] for call in emit.await_args_list]
            assert first.dedup_key == retry.dedup_key
            assert other.title == "b" and "routine_title" not in other.payload
        finally:
            await store.close()

    asyncio.run(run())
