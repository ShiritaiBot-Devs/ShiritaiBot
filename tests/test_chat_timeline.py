import asyncio

from shiritaibot.core import EventBus
from shiritaibot.store import Store


def test_timeline_attaches_elapsed_only_to_final_reply_without_changing_model_history(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        try:
            session = "web:owner"
            messages = [
                {"role": "user", "content": "hello", "_client_ids": ["first"]},
                {"role": "assistant", "content": "checking"},
                {"role": "user", "content": "hello", "_client_ids": ["second"]},
                {"role": "assistant", "content": "done"},
            ]
            for message in messages:
                await store.append(session, "turn1", message)
            await store.put("turn", "turn1", {"status": "running"}, session)
            assert all(
                "_elapsed_seconds" not in m for m in await store.history(session, timeline=True)
            )

            await store.put(
                "turn",
                "turn1",
                {
                    "status": "completed",
                    "started": "2026-11-20T08:00:00+00:00",
                    "finished": "2026-11-20T08:01:07+00:00",
                },
                session,
            )
            await store.append(
                session, "other", {"role": "user", "content": "private"}, memory_profile="other"
            )
            assert await store.history(session) == messages
            timeline = await store.history(session, timeline=True)
            assert [m.get("_elapsed_seconds") for m in timeline] == [None, None, None, 67]
            assert timeline[-1]["_turn_status"] == "completed"
            assert [m["_client_ids"] for m in timeline if m["role"] == "user"] == [
                ["first"],
                ["second"],
            ]
            assert len({m["_seq"] for m in timeline}) == 4
            await store.reset_history(session)
            assert await store.history(session, timeline=True) == []
        finally:
            await store.close()

    asyncio.run(run())
