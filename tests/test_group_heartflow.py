import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from shiritaibot.adapters.onebot11 import AdapterHub, OneBotNotConnected
from shiritaibot.agent import AgentRuntime
from shiritaibot.config import ConfigService, GroupContextConfig, HeartflowConfig, Settings
from shiritaibot.core import EventBus, Reply
from shiritaibot.heartflow import DIMENSIONS, Heartflow, cost, judge
from shiritaibot.store import Store

SESSION = "group:qq:123"


@pytest.mark.parametrize(
    ("legacy", "mode"),
    [
        ("", "off"),
        ("interject = true", "simple"),
        ("interject = false", "off"),
        ('interject = true\ninterject_mode = "off"', "off"),
    ],
)
def test_config_roundtrip(tmp_path, legacy, mode):
    async def run():
        service = ConfigService(tmp_path)
        service.path.write_text(f"[group_context]\n{legacy}\ninterject_cooldown = 72\n")
        await service.load()
        assert service.value.group_context.interject_mode == mode
        await service.save(service.value)
        await service.load()
        assert service.value.group_context.interject_cooldown == 72
        assert service.value.group_context.interject_mode == mode
        assert "interject =" not in service.path.read_text(encoding="utf8")

    asyncio.run(run())


def test_config_constraints():
    with pytest.raises(ValidationError):
        GroupContextConfig.model_validate({"interject_mode": "auto"})
    for curve in ("flat", "linear", "exponential"):
        cfg = HeartflowConfig.model_validate({"energy_curve": curve, "max_streak": 5})
        assert sum(cost(cfg, k) for k in range(5)) == pytest.approx(0.9)


@asynccontextmanager
async def group_bot(path, mode="heartflow"):
    cfg = Settings.model_validate(
        {
            "adapters": [{"id": "qq", "groups": ["123", "456"]}],
            "policy": {"debounce_enabled": False},
            "proactive": {"quiet_start": "00:00", "quiet_end": "00:00"},
            "group_context": {
                "enabled": True,
                "interject_mode": mode,
                "interject_probability": 1,
                "heartflow": {"debounce_seconds": 0.1, "min_judge_interval": 1},
            },
        }
    )
    config = SimpleNamespace(value=cfg)
    store = Store(path, EventBus())
    await store.open()
    llm = SimpleNamespace(
        decide=AsyncMock(return_value={k: {"noul": 1} for k in DIMENSIONS}),
        complete=AsyncMock(return_value=Reply("可以坐末班车。")),
    )
    hub = AdapterHub(config, None, store)
    registry = SimpleNamespace(
        permissions=SimpleNamespace(context=lambda ctx: ctx), specs=lambda ctx: []
    )
    runtime = AgentRuntime(config, store, llm, registry, None, hub.enqueue)
    hub.runtime, hub.permissions = runtime, Mock()
    hub.heartflow = Heartflow(runtime, hub.enqueue)
    runtime.deliver_group_reply = hub.deliver_group_reply
    bot = SimpleNamespace(
        config=cfg.adapters[0],
        self_id="999",
        log=Mock(),
        status="connected",
        call=AsyncMock(return_value={"message_id": 11}),
    )
    hub.instances["qq"] = bot
    try:
        yield hub, bot, llm
    finally:
        await hub.close()
        await runtime.close()
        await store.close()


async def receive(hub, bot, text, msg_id, *, wake=False, group="123"):
    parts = [{"type": "text", "data": {"text": text}}]
    if wake:
        parts.append({"type": "at", "data": {"qq": bot.self_id}})
    await hub.receive(
        bot,
        {
            "post_type": "message",
            "message_type": "group",
            "group_id": group,
            "message_id": msg_id,
            "user_id": 42,
            "message": parts,
        },
    )


async def until(check):
    async with asyncio.timeout(3):
        while not await check():  # noqa: ASYNC110 - observe persisted outbox/actor state
            await asyncio.sleep(0.01)


async def pending(hub):
    return await hub.store.with_status("outbox", ("pending",))


async def flush(hub, bot, row):
    await hub.flush_onebot(bot, row, "group", "123", await hub.outgoing(row))


def test_passive_focus_followup_receipt_and_split_retry(tmp_path):
    async def run():
        async with group_bot(tmp_path) as (hub, bot, llm):
            flow = hub.heartflow
            await receive(hub, bot, "还有公交车吗", 1, wake=True)
            await until(lambda: pending(hub))
            (row,) = await pending(hub)
            assert row["group_wake"] and flow.groups[SESSION].phase == "LURK"
            await flush(hub, bot, row)
            g = flow.groups[SESSION]
            assert g.phase == "FOCUS" and not g.sent
            llm.complete.return_value = Reply("我" * 3501)
            await receive(hub, bot, "那末班车呢", 2)
            await receive(hub, bot, "几点出发", 3)
            await until(lambda: pending(hub))
            rows = await pending(hub)
            assert len(rows) == 2 and not g.sent
            material = llm.decide.await_args.args[1]
            assert material["state"] == "FOCUS" and len(material["new_messages"]) == 2
            assert material["previous_reply"] == "可以坐末班车。"
            assert llm.complete.await_args.args[3] == []
            assert len(await hub.store.history(SESSION)) == 2
            bot.call.side_effect = OneBotNotConnected("offline")
            await flush(hub, bot, rows[0])
            assert rows[0]["status"] == "pending" and not g.sent
            bot.call.side_effect = None
            for row in rows:
                await flush(hub, bot, row)
            assert len(g.sent) == 1 and g.streak == 1 and g.observation is None
            assert [m["content"] for m in (await hub.store.history(SESSION))[-2:]] == [
                "我" * 3500,
                "我",
            ]
            flow.settled(SESSION, rows[-1])
            assert len(g.sent) == 1
            await receive(hub, bot, "隔壁群的话题", 4, group="456")
            assert flow.groups["group:qq:456"].phase == "LURK"

    asyncio.run(run())


@pytest.mark.parametrize("mode", ["off", "simple"])
def test_existing_modes(tmp_path, mode):
    async def run():
        async with group_bot(tmp_path, mode) as (hub, bot, llm):
            llm.complete.return_value = Reply("<pass>")
            await receive(hub, bot, "聊聊车次", 1)
            if mode == "simple":

                async def finished():
                    return llm.complete.await_count and not hub.runtime.busy(SESSION)

                await until(finished)
                await receive(hub, bot, "接着聊", 2)
                assert llm.complete.await_count == 1 and len(hub.interject_log[SESSION]) == 1
            else:
                llm.complete.assert_not_called()
            llm.decide.assert_not_called()
            assert not hub.heartflow.groups and not await pending(hub)

    asyncio.run(run())


@pytest.mark.parametrize(
    "outcome", ["pass", "low", "topic", "invalid", "quota", "failure", "busy", "quiet"]
)
def test_silent_paths(tmp_path, outcome):
    async def run():
        async with group_bot(tmp_path) as (hub, bot, llm):
            cfg = hub.config.value
            g, _ = hub.heartflow.arrival(SESSION)
            if outcome == "quiet":
                from datetime import timedelta
                from zoneinfo import ZoneInfo

                from shiritaibot.core import now

                local = now().astimezone(ZoneInfo(cfg.proactive.timezone))
                cfg.proactive.quiet_start = (local - timedelta(minutes=2)).strftime("%H:%M")
                cfg.proactive.quiet_end = (local + timedelta(minutes=2)).strftime("%H:%M")
            elif outcome == "busy":
                hub.runtime.busy = lambda _: True
            elif outcome == "quota":
                g.sent.extend([g.updated] * cfg.group_context.interject_max_per_hour)
            elif outcome == "low":
                llm.decide.return_value = {k: {"noul": 0} for k in DIMENSIONS}
                g.passes = 2
            elif outcome == "topic":
                hub.heartflow.passive_sent(SESSION, hub.heartflow.wake(SESSION), "刚才的话")
                llm.decide.return_value["coherence"] = {"noul": 0.1}
            elif outcome == "invalid":
                llm.decide.return_value = {"content": {"noul": True}}
            elif outcome == "failure":
                llm.complete.side_effect = RuntimeError("unavailable")
            else:
                llm.complete.return_value = Reply("<pass>")
            await receive(hub, bot, "普通的群消息", 1)
            await asyncio.sleep(0.25)
            assert not await pending(hub) and not await hub.store.history(SESSION)
            assert g.energy == 1 and (not g.sent or outcome == "quota")
            assert g.passes == (1 if outcome == "pass" else 0)
            if outcome in ("quiet", "busy", "quota"):
                llm.decide.assert_not_called()
            if outcome == "low":
                assert g.phase == "REST"
            if outcome == "topic":
                assert g.phase == "LURK"

    asyncio.run(run())


@pytest.mark.parametrize("interruption", ["wake", "traffic", "switch"])
def test_interruption_during_generation(tmp_path, interruption):
    async def run():
        async with group_bot(tmp_path) as (hub, bot, llm):
            started, release = asyncio.Event(), asyncio.Event()

            async def complete(*args, **kwargs):
                started.set()
                await release.wait()
                return Reply("候选回复")

            llm.complete.side_effect = complete
            await receive(hub, bot, "原来的话题", 1)
            await asyncio.wait_for(started.wait(), 2)
            if interruption == "wake":
                await receive(hub, bot, "重新问个问题", 2, wake=True)
            elif interruption == "traffic":
                for i in range(6):
                    await receive(hub, bot, "新消息", i + 2)
            else:
                hub.config.value.group_context.interject_mode = "off"
                await hub.heartflow.reset()
            release.set()
            await asyncio.sleep(0.25)
            assert all("heartflow" not in row for row in await pending(hub))
            history = await hub.store.history(SESSION)
            if interruption == "wake":
                assert any(row.get("group_wake") for row in await pending(hub))
            else:
                assert not history

    asyncio.run(run())


def test_light_fallback():
    async def run():
        scores = {key: 0.75 for key in DIMENSIONS}
        llm = SimpleNamespace(
            decide=AsyncMock(return_value=None),
            complete=AsyncMock(return_value=Reply(json.dumps(scores))),
        )
        assert await judge(llm, {}) == scores
        assert llm.complete.await_args.args[0] == "light"
        llm.complete.return_value = Reply(json.dumps({**scores, "timing": 2}))
        with pytest.raises(ValueError):
            await judge(llm, {})

    asyncio.run(run())


def test_queued_candidate_expires_before_protocol_send(tmp_path):
    async def run():
        async with group_bot(tmp_path) as (hub, bot, _):
            await receive(hub, bot, "聊聊末班车", 1)
            await until(lambda: pending(hub))
            (row,) = await pending(hub)
            await hub.heartflow.reset()
            # A restarted process has no authorization for an old observation either.
            await flush(hub, bot, row)
            assert row["status"] == "expired"
            bot.call.assert_not_awaited()
            assert not await hub.store.history(SESSION)

    asyncio.run(run())


def test_streak_rest_and_passive_wake(tmp_path):
    async def run():
        async with group_bot(tmp_path) as (hub, bot, llm):
            hub.config.value.group_context.heartflow.max_streak = 1
            await receive(hub, bot, "这班车会经过哪里", 1)
            await until(lambda: pending(hub))
            (row,) = await pending(hub)
            await flush(hub, bot, row)
            group = hub.heartflow.groups[SESSION]
            assert group.phase == "REST" and group.energy < 0.2
            await receive(hub, bot, "还有一个问题", 2)
            assert group.timer is None
            await receive(hub, bot, "那在哪里换乘", 3, wake=True)
            await until(lambda: pending(hub))
            (row,) = await pending(hub)
            await flush(hub, bot, row)
            assert group.phase == "FOCUS" and not group.streak and len(group.sent) == 1
            group.until = 0
            hub.heartflow.refresh(SESSION, group)
            assert group.phase == "LURK"
            llm.decide.assert_awaited_once()

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["blocked", "unconfirmed"])
def test_unsent_candidate_releases_reservation(tmp_path, outcome):
    async def run():
        async with group_bot(tmp_path) as (hub, bot, llm):
            if outcome == "blocked":
                hub.hooks = SimpleNamespace(
                    dispatch=AsyncMock(return_value=False), decorate=AsyncMock(return_value=None)
                )
            await receive(hub, bot, "末班车几点开", 1)
            if outcome == "unconfirmed":
                await until(lambda: pending(hub))
                (row,) = await pending(hub)
                bot.call.return_value = {}
                await flush(hub, bot, row)
                assert row["status"] == "uncertain"
            else:
                await asyncio.sleep(0.25)
                hub.hooks.decorate.assert_awaited_once()
            llm.complete.assert_awaited_once()
            group = hub.heartflow.groups[SESSION]
            assert not group.sent and group.observation is None and group.energy == 1
            assert not await pending(hub) and not await hub.store.history(SESSION)

    asyncio.run(run())
