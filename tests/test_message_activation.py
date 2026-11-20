import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from shiritaibot.adapters.feishu import Feishu
from shiritaibot.adapters.feishu_transport import FeishuInbound
from shiritaibot.adapters.onebot11 import AdapterHub
from shiritaibot.adapters.qq_official import QQMessage
from shiritaibot.api import hook
from shiritaibot.config import AdapterConfig, Settings
from shiritaibot.plugin.core.hooks import HookBus, collect


class Commands:
    @hook.command_group("demo", aliases={"d"})
    def tools(self):
        """示例指令组。"""

    @tools.command("echo")
    async def echo(self, event, value: str):
        assert event.is_wake
        return value


@pytest.fixture(params=["qq", "feishu", "official"])
def chat(request):
    platform = request.param
    config = SimpleNamespace(value=Settings(wake_keywords=["小知"]))
    store = AsyncMock()
    seen = set()

    def remember(sql, params):
        key, _ = params
        if key in seen:
            return 0
        seen.add(key)
        return 1

    store.execute.side_effect = remember
    bus = HookBus(store, lambda handler: True)
    bus.set("demo", collect("demo", Commands(), bus.counter))
    bus.send = AsyncMock()
    runtime = AsyncMock()
    menu = Mock()
    menu.accepts.return_value = False
    menu.reply = AsyncMock(return_value=False)
    permissions = SimpleNamespace(menu=menu)
    owner, friend, group_id, bot_id = "ou_owner", "ou_friend", "oc_group", "ou_bot"
    if platform == "official":
        owner, friend, group_id = "OwnerId", "FriendId", "GroupId"
    cfg = AdapterConfig(
        id=platform,
        type={"qq": "onebot11", "feishu": "feishu", "official": "qq_official"}[platform],
        app_id="12345" if platform == "official" else "cli_demo",
        owner_id=owner,
        friends=[friend],
        groups=[group_id],
    )
    if platform != "feishu":
        config.value.adapters = [cfg]
        bot = SimpleNamespace(config=cfg, self_id=bot_id, log=Mock())
        hub = AdapterHub(config, None, store)
        hub.hooks, hub.runtime, hub.permissions = bus, runtime, permissions
    else:
        fs = Feishu(cfg, None, store, runtime, permissions, bus.send, bus, settings=config)
        fs.transport = SimpleNamespace(bot_open_id=bot_id)

    async def send(text, *, group=True, mention=False, sender=owner, message_id=None):
        message_id = message_id or str(len(seen) + 1)
        if platform == "qq":
            await hub.receive(
                bot,
                {
                    "post_type": "message",
                    "message_type": "group" if group else "private",
                    "user_id": sender,
                    "group_id": group_id,
                    "message_id": message_id,
                    "message": (f"[CQ:at,qq={bot_id}] " if mention else "") + text,
                },
            )
        elif platform == "official":
            await hub.receive_qq_official(
                bot,
                QQMessage(
                    message_id, sender, group_id if group else sender, group, mention, text, 0, {}
                ),
            )
        else:
            await fs.accept(
                FeishuInbound(
                    cfg.app_id,
                    "",
                    message_id,
                    group_id,
                    "group" if group else "p2p",
                    sender,
                    "user",
                    "text",
                    text,
                    (bot_id,) if mention else (),
                    {},
                )
            )
            if not fs.events.empty():
                await fs.receive(fs.events.get_nowait())
        await asyncio.gather(*bus.tasks)

    return SimpleNamespace(
        settings=config.value,
        send=send,
        replies=bus.send,
        runtime=runtime,
        platform=platform,
        friend=friend,
    )


def test_group_commands(chat):
    async def run():
        assert not chat.settings.group_command_require_wake
        await chat.send("/d echo hello", message_id="repeat")
        await chat.send("/d echo hello", message_id="repeat")
        chat.replies.assert_awaited_once()
        assert chat.replies.await_args.args[1] == "hello"
        await chat.send("/demo")
        assert "/demo echo" in chat.replies.await_args.args[1]
        await chat.send("/unknown")
        await chat.send("/help")
        await chat.send("聊天")
        assert [c.args[1] for c in chat.runtime.submit.await_args_list] == ["/help"]
        chat.settings.group_command_require_wake = True
        await chat.send("/help")
        await chat.send("/status", mention=True)
        await chat.send("小知，/models")
        await chat.send("/d echo ignored")
        assert chat.replies.await_count == 2
        await chat.send("/d echo mentioned", mention=True)
        await chat.send("小知，/d echo keyword")
        assert [call.args[1] for call in chat.replies.await_args_list[2:]] == [
            "mentioned",
            "keyword",
        ]
        assert [c.args[1] for c in chat.runtime.submit.await_args_list] == [
            "/help",
            "/status",
            "/models",
        ]

    asyncio.run(run())


def test_private_keyword(chat):
    async def run():
        assert not chat.settings.private_require_keyword
        await chat.send("你好", group=False)
        chat.settings.private_require_keyword = True
        await chat.send("你好", group=False)
        if chat.platform == "qq":
            await chat.send("你好", group=False, sender=chat.friend)
            await chat.send("你好，小知", group=False, sender=chat.friend)
        await chat.send("你好，小知", group=False)
        chat.settings.wake_keyword_mode = "prefix"
        await chat.send("你好，小知", group=False)
        await chat.send("小知你好", group=False)
        await chat.send("/stop", group=False)
        await chat.send("/d echo command", group=False)
        assert [call.args[1] for call in chat.runtime.submit.await_args_list] == [
            "你好",
            *(["你好，小知"] if chat.platform == "qq" else []),
            "你好，小知",
            "小知你好",
            "/stop",
        ]
        chat.replies.assert_awaited_once()
        assert chat.replies.await_args.args[1] == "command"

    asyncio.run(run())
