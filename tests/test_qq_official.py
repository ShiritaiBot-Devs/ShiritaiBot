import asyncio
import json
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from shiritaibot.adapters.qq_official import TOKEN_URL, QQMessage, QQOfficial
from shiritaibot.config import AdapterConfig
from shiritaibot.core import now
from shiritaibot.plugin.core import PluginManager
from shiritaibot.web import create_app


def test_signed_webhook_dispatch_and_message_bound_reply(tmp_path, monkeypatch):
    monkeypatch.setattr(PluginManager, "reload_all", AsyncMock(return_value=[]))
    secret = "test-webhook-secret"
    key = Ed25519PrivateKey.from_private_bytes((secret.encode() * 32)[:32])
    sent = []
    replied = Event()

    async def credentials(bot):
        bot.status = "connected"

    async def send_text(bot, kind, target, text, message_id, sequence):
        sent.append((kind, target, text, message_id, sequence))
        if len(sent) == 2:
            replied.set()
        return {"message_id": f"out-{sequence}"}

    monkeypatch.setattr(QQOfficial, "credentials", credentials)
    monkeypatch.setattr(QQOfficial, "send_text", send_text)
    app = create_app(tmp_path, token="test-auth")
    with TestClient(app) as client:
        auth = {"Authorization": "Bearer test-auth"}
        config = client.get("/api/config", headers=auth).json()
        config["adapters"] = [
            {
                "id": "official",
                "type": "qq_official",
                "app_id": "12345",
                "app_secret": secret,
                "groups": ["GroupOpenID"],
            }
        ]
        assert client.put("/api/config", json=config, headers=auth).status_code == 200
        redacted = client.get("/api/config", headers=auth).json()
        assert redacted["adapters"][0]["app_secret_configured"]
        assert not redacted["adapters"][0]["app_secret"]
        assert client.put("/api/config", json=redacted, headers=auth).status_code == 200

        runtime = app.state.runtime
        received = []

        async def submit(ctx, text):
            received.append((ctx.session, ctx.owner, ctx.group, text))
            await runtime.adapters.enqueue(ctx.session, "first")
            await runtime.adapters.enqueue(ctx.session, "second")

        monkeypatch.setattr(runtime.runtime, "submit", submit)
        path = "/api/adapters/qq-official/official/callback"
        challenge = client.post(
            path,
            headers={"X-Bot-Appid": "12345"},
            json={"op": 13, "d": {"event_ts": "1725442341", "plain_token": "TestToken"}},
        )
        assert challenge.json() == {
            "plain_token": "TestToken",
            "signature": key.sign(b"1725442341TestToken").hex(),
        }
        body = json.dumps(
            {
                "op": 0,
                "t": "GROUP_AT_MESSAGE_CREATE",
                "d": {
                    "id": "incoming-id",
                    "author": {"member_openid": "MemberOpenID"},
                    "group_openid": "GroupOpenID",
                    "content": "<@12345> hello",
                    "timestamp": now().isoformat(),
                },
            }
        ).encode()
        timestamp = str(int(now().timestamp()))
        headers = {
            "X-Signature-Ed25519": key.sign(timestamp.encode() + body).hex(),
            "X-Signature-Timestamp": timestamp,
        }
        assert client.post(path, content=body).status_code == 401
        assert client.post(path, content=body + b" ", headers=headers).status_code == 401
        for _ in range(2):
            assert client.post(path, content=body, headers=headers).json() == {"op": 12}
        assert replied.wait(5)
        assert received == [("group:official:qqofficial.12345/GroupOpenID", False, True, "hello")]
        assert sent == [
            ("group", "GroupOpenID", "first", "incoming-id", 1),
            ("group", "GroupOpenID", "second", "incoming-id", 2),
        ]


def test_openapi_token_cache_and_send_endpoints():
    async def run():
        requests = []

        def api(request):
            requests.append((request.method, str(request.url), json.loads(request.content)))
            if str(request.url) == TOKEN_URL:
                return httpx.Response(200, json={"access_token": "mock-token", "expires_in": 7200})
            assert request.headers["authorization"] == "QQBot mock-token"
            return httpx.Response(200, json={"id": "receipt"})

        cfg = AdapterConfig(id="official", type="qq_official", app_id="12345")
        vault = SimpleNamespace(resolve=AsyncMock(return_value="mock-app-secret"))
        bot = QQOfficial(cfg, vault, None, None)
        sample = {
            "op": 0,
            "t": "C2C_MESSAGE_CREATE",
            "d": {"id": "reply-to", "author": {"user_openid": "FirstUser"}, "content": "hi"},
        }
        incoming = QQMessage.parse(sample, now().timestamp())
        assert incoming is not None and not incoming.group and incoming.target == "FirstUser"
        async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as client:
            bot.client = client
            assert await bot.send_text("private", incoming.target, "one", incoming.message_id, 1) == {
                "message_id": "receipt"
            }
            await bot.send_text("group", "FirstGroup", "two", "second-message", 1)
        assert requests == [
            ("POST", TOKEN_URL, {"appId": "12345", "clientSecret": "mock-app-secret"}),
            (
                "POST",
                "https://api.bot.qq.com/v2/users/FirstUser/messages",
                {"content": "one", "msg_type": 0, "msg_id": "reply-to", "msg_seq": 1},
            ),
            (
                "POST",
                "https://api.bot.qq.com/v2/groups/FirstGroup/messages",
                {"content": "two", "msg_type": 0, "msg_id": "second-message", "msg_seq": 1},
            ),
        ]

    asyncio.run(run())
