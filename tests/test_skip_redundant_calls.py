import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from shiritaibot.auth import Permissions
from shiritaibot.config import Settings
from shiritaibot.core import Context, EventBus, Tool
from shiritaibot.emotion import EmotionManager
from shiritaibot.emotion.rules import Screen
from shiritaibot.meme.core import MemeCore
from shiritaibot.memory import MemoryManager
from shiritaibot.memory.extract import MemoryExtractor
from shiritaibot.memory.longterm import ref
from shiritaibot.memory.recall import MemoryRecall
from shiritaibot.store import Store


def _systemone_settings(**extra):
    return Settings.model_validate(
        {
            "providers": [
                {"id": "jev", "protocol": "systemone", "enabled": True},
                {"id": "text", "protocol": "chat_completions", "enabled": True},
            ],
            "models": [
                {"id": "jev-m", "provider": "jev", "model": "jev", "enabled": True},
                {"id": "text-m", "provider": "text", "model": "gpt", "enabled": True, "modalities": ["text", "image"]},
            ],
            "routes": {
                "light": ["jev-m", "text-m"],
                "memory": ["jev-m", "text-m"],
                "meme": ["jev-m", "text-m"],
            },
            **extra,
        }
    )


def test_emotion_skips_situation_when_below_threshold(tmp_path, monkeypatch):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        try:
            cfg = SimpleNamespace(
                value=_systemone_settings(emotion={"enabled": True, "sensitivity": 0.5})
            )
            # semantic = 0.65 * 0.35 + 0.35 * 0.2 = 0.2975 (>= SITUATION_FLOOR=0.2),
            # but combined raw stays below threshold (0.50).
            low_answers = {
                "frustrated": {"noul": 0.3},
                "anxious": {"noul": 0.1},
                "sad": {"noul": 0.1},
                "angry": {"noul": 0.1},
                "tired": {"noul": 0.1},
                "intensity": {"noul": 0.35},
                "impact": {"noul": 0.2},
            }
            llm = SimpleNamespace(
                decide=AsyncMock(return_value=low_answers),
                complete=AsyncMock(return_value=SimpleNamespace(text="近况概括")),
            )
            monkeypatch.setattr(
                "shiritaibot.emotion.rules.screen",
                AsyncMock(return_value=Screen(categories=("x",), level=0.2, negativity=0.2)),
            )
            manager = EmotionManager(store, llm, cfg)
            ctx = Context("private:qq:99", "99", "agent", owner=True)

            await manager.process(ctx, "有点烦", recent=[], failed=False)
            llm.decide.assert_awaited_once()
            llm.complete.assert_not_awaited()

            # Strong emotion crosses threshold and should request a situation summary.
            high_answers = {**low_answers, "frustrated": {"noul": 0.9}, "intensity": {"noul": 0.9}, "impact": {"noul": 0.8}}
            llm.decide.return_value = high_answers
            monkeypatch.setattr(
                "shiritaibot.emotion.rules.screen",
                AsyncMock(return_value=Screen(categories=("x",), level=0.8, negativity=0.8)),
            )
            await manager.process(ctx, "气死我了", recent=[], failed=False)
            llm.complete.assert_awaited_once()
            saved = await store.get("emotion", "owner")
            assert saved["situation"] == "近况概括"
        finally:
            await store.close()

    asyncio.run(run())


def test_memory_recall_skips_empty_recall_and_already_shown_rerank(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        try:
            cfg = SimpleNamespace(value=_systemone_settings())
            llm = SimpleNamespace(
                decide=AsyncMock(return_value={"recall": {"noul": 0.9}}),
                complete=AsyncMock(),
                embedding_model=lambda: None,
            )
            memory = MemoryManager(store, cfg, llm=llm)
            recall = MemoryRecall(memory)
            ctx = Context("private:qq:99", "99", "agent", owner=True, turn="t1")

            # Empty memory store: candidates() must not ask judge.recall().
            scores, pinned = await recall.candidates("那件事后来怎么样了")
            assert scores == {}
            assert pinned == set()
            llm.decide.assert_not_awaited()

            # Insert one fact and mark it as already shown in this session.
            await store.execute(
                "INSERT INTO mem_fact(content, as_of, authority, confidence, status, created, updated) "
                "VALUES (?, 1, 'owner', 1, 'active', 1, 1)",
                ("项目代号叫北极星",),
            )
            await memory.index([ref("f", 1)])
            await store.append(ctx.session, "t1", {"role": "user", "content": "北极星"})
            llm.decide.return_value = {"relevant_0": {"noul": 0.9}, "instructs_0": {"noul": 0.0}}
            first = await recall.render(ctx, "北极星", 2000)
            assert "北极星" in first
            assert llm.decide.await_count == 1

            # Next turn with the exact same unchanged fact already shown: skip rerank completely.
            ctx2 = Context("private:qq:99", "99", "agent", owner=True, turn="t2")
            second = await recall.render(ctx2, "北极星", 2000)
            assert second == ""
            assert llm.decide.await_count == 1
        finally:
            await store.close()

    asyncio.run(run())


def test_require_manual_approval_skips_auto_review(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        try:
            cfg = SimpleNamespace(
                value=Settings.model_validate(
                    {"policy": {"owner_default": {"tools": {"shell": "auto"}}}}
                )
            )
            llm = SimpleNamespace(decide=AsyncMock(), complete=AsyncMock())
            perms = Permissions(cfg, store)
            perms.llm = llm
            perms.auto_review = AsyncMock()
            perms.menu.submit = AsyncMock()
            tool = Tool(
                name="shell",
                description="run",
                schema={"type": "object", "properties": {}},
                risk="dangerous",
                handler=AsyncMock(),
            )
            ctx = Context(
                "private:qq:99",
                "99",
                "agent",
                owner=True,
                require_manual_approval=True,
            )

            async def approve_pending():
                while not perms.pending:
                    await asyncio.sleep(0.01)
                future = next(iter(perms.pending.values()))
                future.set_result(True)

            approver = asyncio.create_task(approve_pending())
            await perms.check(ctx, tool, {"command": "echo hi"})
            await approver

            perms.auto_review.assert_not_awaited()
            llm.decide.assert_not_awaited()
            llm.complete.assert_not_awaited()
        finally:
            await store.close()

    asyncio.run(run())


def test_memory_extractor_skips_internal_emotion_and_reminder_events(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        try:
            cfg = SimpleNamespace(value=_systemone_settings())
            memory = MemoryManager(store, cfg)
            extractor = MemoryExtractor(memory, store, cfg, SimpleNamespace())

            await extractor.enqueue_event(
                {"id": "e1", "event": {"source": "memory", "kind": "memory.conflict"}}
            )
            await extractor.enqueue_event(
                {"id": "e2", "event": {"source": "情绪", "kind": "care"}}
            )
            await extractor.enqueue_event(
                {"id": "e3", "event": {"source": "routine", "kind": "routine.reminder"}}
            )
            await extractor.enqueue_event(
                {"id": "e4", "event": {"source": "routine", "kind": "routine.job"}}
            )
            await extractor.enqueue_event(
                {"id": "e5", "event": {"source": "email", "kind": "email.received"}}
            )

            pending = await store.with_status("memory_extract", ("pending",))
            assert {row["event"] for row in pending} == {"e4", "e5"}
        finally:
            await store.close()

    asyncio.run(run())


def test_meme_judge_skips_category_fallback_when_fit_rejected(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        try:
            cfg = SimpleNamespace(value=_systemone_settings(meme={"fit_threshold": 0.6}))
            # Decision model answers fit (below threshold) but omits category.
            llm = SimpleNamespace(
                decide=AsyncMock(return_value={"fit": {"noul": 0.2}}),
                complete=AsyncMock(),
                embedding_model=lambda: None,
            )
            lib = MemeCore(store, cfg, llm)
            score, picked = await lib.judge("一个表情包", cfg.value, fit=True, category=True)
            assert score == 0.2
            assert picked is None
            llm.decide.assert_awaited_once()
            llm.complete.assert_not_awaited()
        finally:
            await store.close()

    asyncio.run(run())
