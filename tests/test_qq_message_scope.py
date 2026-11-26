import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from shiritaibot.adapters.onebot11 import OneBot
from shiritaibot.auth import Permissions
from shiritaibot.config import Settings
from shiritaibot.core import Context, PublicError, session_dir
from shiritaibot.i18n import use_locale
from shiritaibot.tools import ToolRegistry, qq

FRIEND = "201574690"
BOT_ID = "3918903271"


def _app(tmp_path):
    bot = OneBot(SimpleNamespace(id="qq", self_id=BOT_ID, token="", url=""), None, None, None)
    bot.call = AsyncMock()
    bot.status = "connected"
    app = SimpleNamespace(
        home=tmp_path,
        config=SimpleNamespace(value=Settings()),
        adapters=SimpleNamespace(instances={"qq-main": bot}),
    )
    tools = {tool.name: tool for tool in qq.qq_tools(app)}
    return app, bot, tools


def _friend_ctx(session="private:qq-main:201574690"):
    return Context(session, FRIEND, "chat", False, session.startswith("group:"))


def _owner_ctx():
    return Context("private:qq-main:615592777", "owner", "agent", True, False)


def _msg(sender=BOT_ID, group_id=None):
    raw = {
        "message_id": 1,
        "message_type": "group" if group_id else "private",
        "sender": {"user_id": int(sender), "nickname": "n"},
        "message": [{"type": "text", "data": {"text": "hi"}}],
        "time": 1,
    }
    if group_id:
        raw["group_id"] = group_id
    return raw


def test_friend_history_is_scoped_to_own_chat(tmp_path):
    app, bot, tools = _app(tmp_path)
    bot.call.return_value = {"messages": [_msg()]}
    result = asyncio.run(tools["qq_message_history"].handler(_friend_ctx(), {}))
    bot.call.assert_awaited_once_with("get_friend_msg_history", {"user_id": int(FRIEND), "count": 20, "message_seq": 0})
    assert len(result["messages"]) == 1

    with pytest.raises(PublicError, match="只能读取当前会话"):
        asyncio.run(tools["qq_message_history"].handler(_friend_ctx(), {"target": "private", "id": 999}))

    with pytest.raises(PublicError, match="只能读取当前会话"):
        asyncio.run(tools["qq_message_history"].handler(_friend_ctx(), {"target": "group", "id": 123}))


def test_friend_group_history_is_scoped_to_own_group(tmp_path):
    app, bot, tools = _app(tmp_path)
    bot.call.return_value = {"messages": [_msg(group_id=123)]}
    ctx = _friend_ctx("group:qq-main:123")
    result = asyncio.run(tools["qq_message_history"].handler(ctx, {"target": "group", "id": 123}))
    bot.call.assert_awaited_once_with("get_group_msg_history", {"group_id": 123, "count": 20, "message_seq": 0})
    assert len(result["messages"]) == 1

    with pytest.raises(PublicError, match="只能读取当前会话"):
        asyncio.run(tools["qq_message_history"].handler(ctx, {"target": "group", "id": 456}))


def test_friend_message_get_allows_own_chat_both_directions(tmp_path):
    app, bot, tools = _app(tmp_path)
    bot.call.side_effect = lambda action, params: _msg(sender=FRIEND)
    result = asyncio.run(tools["qq_message_get"].handler(_friend_ctx(), {"message_id": 1}))
    assert result["sender"]["user_id"] == int(FRIEND)

    bot.call.side_effect = lambda action, params: (
        _msg() if action == "get_msg" else {"messages": [_msg()]}
    )
    result = asyncio.run(tools["qq_message_get"].handler(_friend_ctx(), {"message_id": 1}))
    assert result["sender"]["user_id"] == int(BOT_ID)

    with pytest.raises(PublicError, match="只能读取当前会话"):
        asyncio.run(tools["qq_message_get"].handler(_friend_ctx(), {"message_id": 2}))

    bot.call.side_effect = lambda action, params: _msg(sender="999")
    with pytest.raises(PublicError, match="只能读取当前会话"):
        asyncio.run(tools["qq_message_get"].handler(_friend_ctx(), {"message_id": 3}))


def test_friend_group_message_get_scoped_to_own_group(tmp_path):
    app, bot, tools = _app(tmp_path)
    ctx = _friend_ctx("group:qq-main:123")
    bot.call.side_effect = lambda action, params: _msg(group_id=123)
    result = asyncio.run(tools["qq_message_get"].handler(ctx, {"message_id": 1}))
    assert result["sender"]["user_id"] == int(BOT_ID)

    bot.call.side_effect = lambda action, params: _msg(group_id=456)
    with pytest.raises(PublicError, match="只能读取当前会话"):
        asyncio.run(tools["qq_message_get"].handler(ctx, {"message_id": 2}))


def test_owner_agent_session_stays_unrestricted(tmp_path):
    app, bot, tools = _app(tmp_path)
    bot.call.side_effect = lambda action, params: _msg(sender="999")
    result = asyncio.run(tools["qq_message_get"].handler(_owner_ctx(), {"message_id": 1}))
    assert result["sender"]["user_id"] == 999


@pytest.mark.parametrize(
    ("locale", "outside_chat", "disconnected_adapter"),
    [
        ("zh-CN", "只能读取当前会话的消息", "当前会话所属的 OneBot 实例未连接"),
        (
            "en",
            "Only messages in the current chat can be read",
            "The OneBot instance for this chat is disconnected",
        ),
    ],
)
def test_scope_errors_follow_the_session_language(tmp_path, locale, outside_chat, disconnected_adapter):
    _, bot, tools = _app(tmp_path)

    def error(tool, args):
        with pytest.raises(PublicError) as raised:
            asyncio.run(tools[tool].handler(_friend_ctx(), args))
        return str(raised.value)

    with use_locale(locale):
        bot.call.side_effect = lambda action, params: _msg(sender="999")
        assert error("qq_message_get", {"message_id": 3}) == outside_chat
        assert error("qq_message_history", {"target": "private", "id": 999}) == outside_chat
        bot.status = "disconnected"
        assert error("qq_message_history", {"target": "private", "id": int(FRIEND)}) == disconnected_adapter


def test_private_chat_uses_bound_onebot_for_sending_and_reading(tmp_path):
    app, bot, tools = _app(tmp_path)
    ctx = _friend_ctx()
    other_bot = OneBot(SimpleNamespace(id="other", self_id="12"), None, None, None)
    other_bot.status = "connected"
    app.adapters.instances["other"] = other_bot
    app.config.value.policy.private_default.tools.update(
        qq_message_send="allow", qq_file_send="allow", qq_forward_send="allow"
    )
    registry = ToolRegistry(Permissions(app.config, None), SimpleNamespace(activity=AsyncMock()))
    for tool in tools.values():
        registry.add(tool)
    names = {spec["function"]["name"] for spec in registry.specs(ctx)}
    assert {
        "qq_message_send", "qq_file_send", "qq_forward_send",
        "qq_message_history", "qq_message_get", "qq_file_download",
    } <= names
    assert not {"qq_friend_list", "qq_message_recall"} & names

    workspace = session_dir(app.home, "workspaces", ctx.session)
    workspace.mkdir(parents=True)
    (workspace / "report.xlsx").write_bytes(b"report")
    shared = app.home / "plugin_data" / "report.xlsx"
    shared.parent.mkdir()
    shared.write_bytes(b"shared")
    bot.call.return_value = {"message_id": 10, "res_id": "forward"}

    async def run():
        assert json.loads(await registry.execute(ctx, "qq_message_send", {"text": "ready"})) == {
            "message_id": 10,
        }
        assert json.loads(await registry.execute(ctx, "qq_file_send", {"path": "report.xlsx"})) == {
            "sent": True, "name": "report.xlsx", "bytes": 6,
        }
        assert json.loads(await registry.execute(ctx, "qq_forward_send", {
            "target": "current", "nodes": [{"text": "report ready"}],
        })) == {"message_id": 10, "forward_id": "forward"}
        for bad in (
            {"target": "private", "id": 999},
            {"target": "group", "id": 123},
            {"target": "current", "adapter": "other"},
        ):
            result = json.loads(await registry.execute(ctx, "qq_message_send", {"text": "x", **bad}))
            assert "当前 QQ 私聊" in result["error"]
        result = json.loads(await registry.execute(ctx, "qq_file_send", {"path": str(shared)}))
        assert "只能发送当前会话" in result["error"]

    asyncio.run(run())
    assert bot.call.await_args_list == [
        call("send_private_msg", {"user_id": int(FRIEND), "message": [
            {"type": "text", "data": {"text": "ready"}},
        ]}),
        call("upload_private_file", {
            "user_id": int(FRIEND), "file": "base64://cmVwb3J0", "name": "report.xlsx",
        }),
        call("send_private_forward_msg", {"user_id": int(FRIEND), "messages": [{
            "type": "node", "data": {
                "user_id": BOT_ID, "nickname": app.config.value.assistant_name,
                "content": [{"type": "text", "data": {"text": "report ready"}}],
            },
        }]}),
    ]
    send = tools["qq_message_send"]
    for unbound in (
        _friend_ctx("group:qq-main:123"),
        replace(ctx, background=True),
        replace(ctx, actor="999"),
    ):
        assert registry.permissions.decision(unbound, send) == "deny"
        with pytest.raises(PublicError, match="当前 QQ 私聊"):
            asyncio.run(send.handler(unbound, {"text": "x"}))


def test_private_send_checks_where_quoted_and_forwarded_messages_came_from(tmp_path):
    _, bot, tools = _app(tmp_path)
    ctx = _friend_ctx()
    send = tools["qq_message_send"].handler
    forward = tools["qq_forward_send"].handler
    bot.call.side_effect = [
        _msg(sender=FRIEND, group_id=123),
        _msg(sender=BOT_ID),
        {"messages": []},
        _msg(sender=FRIEND),
        {"message_id": 99, "res_id": "forward"},
    ]

    async def run():
        with pytest.raises(PublicError, match="只能读取当前会话"):
            await send(ctx, {"text": "reply", "reply_to": 1})
        with pytest.raises(PublicError, match="只能读取当前会话"):
            await forward(ctx, {"nodes": [{"message_id": 2}]})
        with pytest.raises(PublicError, match="使用 Bot 的身份"):
            await forward(ctx, {"nodes": [{"text": "fake", "user_id": 999}]})
        return await forward(ctx, {"nodes": [{"message_id": 1}]})

    assert asyncio.run(run()) == {"message_id": 99, "forward_id": "forward"}
    assert bot.call.await_args_list == [
        call("get_msg", {"message_id": 1}),
        call("get_msg", {"message_id": 2}),
        call("get_friend_msg_history", {"user_id": int(FRIEND), "count": 50, "message_seq": 0}),
        call("get_msg", {"message_id": 1}),
        call("send_private_forward_msg", {"user_id": int(FRIEND), "messages": [
            {"type": "node", "data": {"id": "1"}},
        ]}),
    ]


def test_owner_agent_can_still_send_to_an_explicit_recipient(tmp_path):
    _, bot, tools = _app(tmp_path)
    bot.call.return_value = {"message_id": 15}
    args = {"target": "private", "id": 999, "text": "hello"}
    ctx = _owner_ctx()
    assert asyncio.run(tools["qq_message_send"].handler(ctx, args)) == {"message_id": 15}
    bot.call.assert_awaited_once_with("send_private_msg", {"user_id": 999, "message": [
        {"type": "text", "data": {"text": "hello"}},
    ]})
