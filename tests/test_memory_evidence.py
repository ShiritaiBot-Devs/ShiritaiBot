import asyncio
from types import SimpleNamespace

import pytest

from shiritaibot.config import Settings
from shiritaibot.core import Context, EventBus, PublicError
from shiritaibot.memory.longterm import MemoryManager
from shiritaibot.store import Store


def test_evidence_uses_archived_span_and_refuses_ambiguity(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        memory = MemoryManager(store, SimpleNamespace(value=Settings()))
        ctx = Context("private:qq:1", "1", owner=True, turn="turn")

        async def append(role, text):
            await store.append(ctx.session, ctx.turn, {"role": role, "content": text})

        try:
            await memory.open()
            await append("user", "确认：我喜欢，苹果。")
            await append("tool", '{"course": "信号系统", "room": "六教"}')
            await append("assistant", "我喜欢橘子")
            fact = await memory.save_fact(ctx, "课程在六教", "信号系统 room 六教")
            event = await memory.save_event(ctx, "确认喜好", "我喜欢苹果", time_text="当天")
            for item, expected in [(fact, '信号系统", "room": "六教'), (event, "我喜欢，苹果")]:
                rows = await store.query(
                    "SELECT quote FROM mem_evidence WHERE target=?", (item["id"],)
                )
                assert rows == [{"quote": expected}]
            assert await memory.owner_words(ctx, "我喜欢，苹果") == "owner:turn"
            with pytest.raises(PublicError):
                await memory.owner_words(ctx, "我喜欢苹果")
            with pytest.raises(PublicError):
                await memory.evidence(ctx, "我喜欢橘子")
            await append("tool", "我喜欢：苹果")
            with pytest.raises(PublicError, match="多处"):
                await memory.evidence(ctx, "我喜欢苹果")
        finally:
            await store.close()

    asyncio.run(run())
