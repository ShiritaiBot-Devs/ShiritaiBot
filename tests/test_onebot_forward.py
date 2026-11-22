import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from shiritaibot.adapters.onebot11 import AdapterHub
from shiritaibot.config import Settings


@pytest.mark.parametrize(("kind", "id_field"), [("group", "group_id"), ("private", "user_id")])
def test_long_onebot_reply_is_delivered_as_one_forward(kind, id_field):
    async def run():
        config = SimpleNamespace(value=Settings(onebot_forward_long_messages=True))
        store = SimpleNamespace(put=AsyncMock(), with_status=AsyncMock())
        hub = AdapterHub(config, None, store)
        delivered = asyncio.Event()

        async def record(table, key, row, scope):
            if row["status"] == "sent":
                delivered.set()

        store.put.side_effect = record
        bot = SimpleNamespace(
            self_id="999",
            status="connected",
            log=Mock(),
            call=AsyncMock(return_value={"message_id": 8, "res_id": "forward"}),
        )
        hub.instances["qq"] = bot
        session = f"{kind}:qq:123"
        reply = "长" * 3500 + "消息"
        await hub.enqueue(session, reply)
        store.put.assert_awaited_once()
        _, key, row, _ = store.put.await_args.args
        assert row["text"] == reply
        row.update(id=key, scope=session)
        store.with_status.return_value = [row]

        task = asyncio.create_task(hub.flush())
        try:
            await asyncio.wait_for(delivered.wait(), timeout=2)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        bot.call.assert_awaited_once()
        action, params = bot.call.await_args.args
        assert action == f"send_{kind}_forward_msg"
        assert params[id_field] == 123
        assert [node["type"] for node in params["messages"]] == ["node", "node"]
        assert [node["data"]["content"][0]["data"]["text"] for node in params["messages"]] == [
            "长" * 3500,
            "消息",
        ]
        assert row["receipt"]["res_id"] == "forward"

    asyncio.run(run())


def test_forward_setting_respects_switch_threshold_and_channel():
    async def run():
        cfg = Settings(onebot_forward_threshold=3)
        store = SimpleNamespace(put=AsyncMock())
        hub = AdapterHub(SimpleNamespace(value=cfg), None, store)
        await hub.enqueue("private:qq:123", "长消息啊")
        assert "forward" not in store.put.await_args.args[2]
        cfg.onebot_forward_long_messages = True
        await hub.enqueue("private:qq:123", "长消息")
        assert "forward" not in store.put.await_args.args[2]
        await hub.enqueue("private:qq:123", "长消息啊")
        assert "forward" in store.put.await_args.args[2]
        await hub.enqueue("web:owner", "长消息啊")
        assert store.put.await_args.args[0] == "reply"
        assert "forward" not in store.put.await_args.args[2]

    asyncio.run(run())
