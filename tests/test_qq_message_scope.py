import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shiritaibot.adapters.onebot11 import OneBot
from shiritaibot.core import Context, PublicError
from shiritaibot.i18n import use_locale
from shiritaibot.tools import qq

FRIEND = "201574690"
BOT_ID = "3918903271"


def _app(tmp_path):
    bot = OneBot(SimpleNamespace(id="qq", self_id=BOT_ID, token="", url=""), None, None, None)
    bot.call = AsyncMock()
    bot.status = "connected"
    app = SimpleNamespace(
        home=tmp_path,
        config=SimpleNamespace(
            value=SimpleNamespace(proactive=SimpleNamespace(timezone="Asia/Shanghai"))
        ),
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
    result = asyncio.run(tools["qq_message_history"].handler(_friend_ctx(), {"target": "private", "id": int(FRIEND)}))
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

    bot.call.side_effect = lambda action, params: _msg(sender=BOT_ID)
    result = asyncio.run(tools["qq_message_get"].handler(_friend_ctx(), {"message_id": 2}))
    assert result["sender"]["user_id"] == int(BOT_ID)

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
    ("locale", "other_message", "other_history", "no_adapter"),
    [
        ("zh-CN", "只能读取当前会话的消息", "只能读取当前会话的消息记录", "会话所属 OneBot 实例不可用"),
        (
            "en",
            "Only messages from this chat can be read",
            "Only this chat's message history can be read",
            "This chat's OneBot instance isn't available",
        ),
    ],
)
def test_scope_errors_follow_the_session_language(tmp_path, locale, other_message, other_history, no_adapter):
    app, bot, tools = _app(tmp_path)

    def error(tool, args):
        with pytest.raises(PublicError) as raised:
            asyncio.run(tools[tool].handler(_friend_ctx(), args))
        return str(raised.value)

    with use_locale(locale):
        bot.call.side_effect = lambda action, params: _msg(sender="999")
        assert error("qq_message_get", {"message_id": 3}) == other_message
        assert error("qq_message_history", {"target": "private", "id": 999}) == other_history
        bot.status = "disconnected"
        assert error("qq_message_history", {"target": "private", "id": int(FRIEND)}) == no_adapter
