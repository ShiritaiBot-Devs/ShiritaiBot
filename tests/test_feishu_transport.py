import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from lark_channel.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
from lark_channel.channel import channel as sdk_channel
from lark_channel.channel.bot_identity import BotIdentity
from lark_channel.core.json import JSON
from lark_channel.ws import client as sdk_ws
from lark_channel.ws.pb.pbbp2_pb2 import Frame

from shiritaibot.adapters import feishu_transport as feishu


def message_payload():
    return {
        "schema": "2.0",
        "header": {
            "event_type": "im.message.receive_v1",
            "app_id": "cli_test",
            "tenant_key": "tenant_test",
            "event_id": "event_test",
            "token": "verification-secret",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_sender"}, "sender_type": "user"},
            "message": {
                "message_id": "om_9223372036854775808",
                "chat_id": "oc_9223372036854775808",
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": "@_user_1 帮我提醒 @_user_10"}),
                "mentions": [
                    {"key": "@_user_1", "id": {"open_id": "ou_bot"}, "name": "机器人"},
                    {"key": "@_user_10", "id": {"open_id": "ou_friend"}, "name": "李明"},
                ],
            },
        },
    }


def test_raw_sdk_message_keeps_ids_and_strips_only_the_bots_own_mention():
    # FeishuChannel emits the typed SDK event serialized back into a dict.
    event = P2ImMessageReceiveV1(message_payload())
    payload = json.loads(JSON.marshal(event))
    parsed = feishu._parse_message(payload, "cli_test", "ou_bot")
    assert parsed is not None
    assert parsed.tenant_key == "tenant_test"
    assert parsed.chat_id == "oc_9223372036854775808"
    assert parsed.message_id == "om_9223372036854775808"
    assert parsed.sender_id == "ou_sender"
    assert parsed.sender_type == "user"
    assert parsed.mentions == ("ou_bot", "ou_friend")
    assert parsed.text == "帮我提醒 @李明"
    assert "verification-secret" not in repr(parsed.raw)
    assert feishu._parse_message(payload, "cli_other", "ou_bot") is None


@pytest.mark.parametrize(
    ("body", "status", "scope", "error"),
    [
        ({"code": 0, "access_token": "secret", "scope": feishu.GROUP_SCOPE}, 200, True, ""),
        ({"code": 0, "access_token": "secret"}, 200, False, ""),
        ({"code": 20050, "error_description": "secret"}, 500, None, ""),
        ({"code": 20002, "error_description": "secret"}, 400, None, "应用密钥无效"),
    ],
)
async def test_scope_probe_distinguishes_absence_from_unavailable(
    monkeypatch, body, status, scope, error
):
    def reply(request):
        assert str(request.url) == "https://accounts.larksuite.com/oauth/v3/token"
        assert request.content == b"grant_type=client_credentials"
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(status, json=body)

    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        feishu.httpx,
        "AsyncClient",
        lambda **kw: client_class(transport=httpx.MockTransport(reply), **kw),
    )
    transport = feishu.FeishuTransport("cli_test", "secret", "lark", AsyncMock())
    await transport._probe_permissions()
    assert transport.group_message_scope is scope
    assert transport.credential_error == error


async def test_transport_schedules_messages_on_app_loop_and_disconnects(monkeypatch):
    app_loop = asyncio.get_running_loop()
    ready = asyncio.Event()
    received = []

    async def receive(message):
        assert asyncio.get_running_loop() is app_loop
        received.append(message)

    class Channel:
        def __init__(self, **kwargs):
            assert kwargs["domain"] == "https://open.feishu.cn"
            assert kwargs["inbound"].emit_raw_events
            assert kwargs["outbound"].retry.max_attempts == 1
            self.bot_identity = SimpleNamespace(open_id="ou_bot")
            self.send = AsyncMock(return_value=SimpleNamespace(success=True, message_id="om_sent"))
            self.disconnect = AsyncMock()
            self.handler = None

        def on(self, name, handler):
            assert name == "raw"
            self.handler = handler

        async def connect_until_ready(self, **kwargs):
            assert kwargs == {"timeout": None}
            ready.set()

        def connection_snapshot(self):
            return SimpleNamespace(ready=True, state="connected")

    monkeypatch.setattr(feishu, "FeishuChannel", Channel)
    transport = feishu.FeishuTransport("cli_test", "secret", "feishu", receive)
    monkeypatch.setattr(transport, "_probe_permissions", AsyncMock())
    task = asyncio.create_task(transport.run())
    await asyncio.wait_for(ready.wait(), timeout=1)
    channel = transport._channel
    try:
        assert transport.status == "connected"
        await asyncio.to_thread(asyncio.run, channel.handler(message_payload()))
        assert [event.text for event in received] == ["帮我提醒 @李明"]
        assert await transport.send_text("收到", chat_id="oc_target", request_id="stable_id") == {
            "message_id": "om_sent"
        }
        channel.send.assert_awaited_once_with(
            "oc_target", {"text": "收到"}, {"receive_id_type": "chat_id", "uuid": "stable_id"}
        )
        channel.send.side_effect = RuntimeError("wss://test.invalid/?ticket=secret")
        with pytest.raises(feishu.FeishuSendError) as raised:
            await transport.send_text("收到", open_id="ou_target", request_id="stable_id_2")
        assert raised.value.uncertain
        assert "secret" not in str(raised.value)
        assert channel.send.await_count == 2
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    channel.disconnect.assert_awaited_once()
    assert transport.status == "disconnected"


async def test_sdk_websocket_closes_on_reload_and_during_startup(monkeypatch):
    app_loop = asyncio.get_running_loop()
    connecting = asyncio.Event()
    received = asyncio.Queue()
    opened = asyncio.Queue()
    sockets = []
    ws_loops = []

    class Socket:
        def __init__(self):
            self.loop = asyncio.get_running_loop()
            self.queue = asyncio.Queue()
            self.closed = False
            self.acks = 0
            self.acked = asyncio.Event()

        async def recv(self):
            message = await self.queue.get()
            if message is None:
                raise RuntimeError("mock socket closed")
            return message

        async def send(self, data):
            if Frame.FromString(data).method == 1:
                self.acks += 1
                app_loop.call_soon_threadsafe(self.acked.set)

        async def close(self):
            self.closed = True
            self.queue.put_nowait(None)

        def emit(self):
            frame = Frame(
                SeqID=1,
                LogID=2,
                service=3,
                method=1,
                payload=json.dumps(message_payload()).encode(),
            )
            for key, value in {
                "message_id": "evt",
                "trace_id": "trace",
                "sum": "1",
                "seq": "0",
                "type": "event",
            }.items():
                frame.headers.add(key=key, value=value)
            self.loop.call_soon_threadsafe(self.queue.put_nowait, frame.SerializeToString())

    async def open_socket(*args, **kwargs):
        ws_loops.append(asyncio.get_running_loop())
        app_loop.call_soon_threadsafe(connecting.set)
        await asyncio.sleep(0.1)
        socket = Socket()
        sockets.append(socket)
        app_loop.call_soon_threadsafe(opened.put_nowait, socket)
        return socket

    async def receive(message):
        assert asyncio.get_running_loop() is app_loop
        received.put_nowait(message)

    monkeypatch.setattr(sdk_ws, "loop", app_loop)
    monkeypatch.setattr(
        sdk_channel, "fetch_bot_identity", AsyncMock(return_value=BotIdentity("ou_bot"))
    )
    def endpoint(client, **kwargs):
        client._reconnect_nonce = 0
        return "wss://example.invalid/ws?device_id=1&service_id=3&ticket=mock"

    monkeypatch.setattr(sdk_ws.Client, "_get_conn_url", endpoint)
    monkeypatch.setattr(sdk_ws.websockets, "connect", open_socket)
    transport = feishu.FeishuTransport("cli_test", "secret", "feishu", receive)
    monkeypatch.setattr(transport, "_probe_permissions", AsyncMock())

    for attempt in range(3):
        connecting.clear()
        task = asyncio.create_task(transport.run())
        try:
            async with asyncio.timeout(4):
                await connecting.wait()
                if attempt < 2:
                    await transport._channel.wait_ready()
                    socket = await opened.get()
                    socket.emit()
                    assert (await received.get()).text == "帮我提醒 @李明"
                    await socket.acked.wait()
                    assert socket.acks == 1
                    if attempt == 0:
                        socket.loop.call_soon_threadsafe(socket.queue.put_nowait, None)
                        reconnected = await opened.get()
                        assert reconnected is not socket and socket.closed
                        reconnected.emit()
                        assert (await received.get()).text == "帮我提醒 @李明"
                        await reconnected.acked.wait()
                        assert reconnected.acks == 1
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert sdk_ws.loop is app_loop
        assert ws_loops[-1].is_closed()
        assert transport.status == "disconnected"

    assert len(sockets) == 4
    assert all(socket.closed for socket in sockets)


def test_sdk_logger_drops_credential_bearing_sdk_records(caplog):
    sdk_logger = logging.getLogger("Lark")
    with caplog.at_level(logging.ERROR, logger="Lark"):
        record = logging.LogRecord(
            "Lark",
            logging.ERROR,
            feishu._SDK_DIRECTORY + "ws/client.py",
            1,
            "failed wss://example.invalid/?ticket=secret",
            (),
            None,
        )
        sdk_logger.handle(record)
    assert "secret" not in caplog.text
