import asyncio
from dataclasses import replace

from fastapi.testclient import TestClient

from shiritaibot.adapters import feishu
from shiritaibot.adapters.feishu_transport import FeishuInbound, FeishuSendError
from shiritaibot.config import AdapterConfig, SessionPolicy
from shiritaibot.web import create_app


class Transport:
    status = "disconnected"
    bot_open_id = "ou_bot"
    credential_error = connection_error = ""
    group_message_scope = True

    def __init__(self, app_id, secret, domain, callback):
        self.accept = callback
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.sent = []

    async def run(self):
        self.status = "connected"
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.status = "disconnected"
            self.stopped.set()

    async def send_text(self, text, **route):
        self.sent.append((text, route))
        return {"message_id": "om_receipt"}


def incoming(message_id, text, **options):
    event = FeishuInbound(
        app_id="cli_example",
        tenant_key="tenant",
        message_id=message_id,
        chat_id="oc_group",
        chat_type="group",
        sender_id="ou_member",
        sender_type="user",
        message_type="text",
        text=text,
        mentions=(),
        raw={},
    )
    return replace(event, **options)


async def connect(app, **options):
    cfg = app.config.value.model_copy(deep=True)
    cfg.adapters = [
        AdapterConfig(
            id="work",
            type="feishu",
            app_id="cli_example",
            app_secret="fixture-secret",
            groups=["oc_group"],
            **options,
        )
    ]
    group_session = "group:work:feishu.cli_example/oc_group"
    cfg.policy.sessions[group_session] = SessionPolicy(mode="agent")
    await app.reconfigure(cfg)
    bot = app.adapters.instances["work"]
    async with asyncio.timeout(2):
        while bot.transport is None:  # noqa: ASYNC110 - observe the asynchronous Hub start
            await asyncio.sleep(0.01)
        await bot.transport.started.wait()
    return bot.transport


async def test_feishu_routes_text_and_rejects_non_wake_replays_and_unbound_users(app, monkeypatch):
    monkeypatch.setattr(feishu, "FeishuTransport", Transport)
    turns = []

    async def submit(ctx, text):
        turns.append((app.permissions.context(ctx), text))
        await app.deliver(ctx.session, "收到")
        return "queued"

    monkeypatch.setattr(app.runtime, "submit", submit)
    unbound = await connect(app)
    dm = incoming("om_dm", "记住项目进度", chat_type="p2p", sender_id="ou_owner")
    await unbound.accept(dm)
    assert "ou_owner" in app.adapters.diagnostics()["work"]
    assert not turns
    transport = await connect(app, owner_id="ou_owner")
    assert unbound.stopped.is_set()
    transport.group_message_scope = False
    assert "缺少群消息权限 im:message.group_msg" in app.adapters.diagnostics()["work"]
    transport.group_message_scope = True

    group = incoming("om_group", "小知，帮我整理一下", sender_id="ou_owner")
    for message in (
        replace(dm, message_id="om_stranger", sender_id="ou_stranger"),
        incoming("om_normal", "我们刚提到了小知"),
        incoming("om_other", "小知，回复", chat_id="oc_other"),
        incoming("om_otherapp", "小知，回复", app_id="cli_other"),
        incoming("om_bot", "小知，回复", sender_id="ou_bot", sender_type="app"),
        group,
        group,
        dm,
    ):
        await transport.accept(message)
    async with asyncio.timeout(5):
        while len(transport.sent) < 2:  # noqa: ASYNC110 - observe the actual outbox worker
            await asyncio.sleep(0.02)

    assert [(ctx.mode, ctx.owner, ctx.memory_profile, text) for ctx, text in turns] == [
        ("chat", False, "default", "帮我整理一下"),
        ("agent", True, "feishu", "记住项目进度"),
    ]
    assert transport.sent[0][1]["chat_id"] == "oc_group"
    assert transport.sent[1][1]["open_id"] == "ou_owner"
    assert all(route["request_id"] for _, route in transport.sent)
    assert not await app.store.group_messages(turns[0][0].session, 20)
    reloaded = await connect(app, owner_id="ou_owner", feishu_group_context=True)
    await reloaded.accept(group)
    await reloaded.accept(incoming("om_context", "这是刚才讨论的背景"))
    await reloaded.accept(incoming("om_next", "小知，继续"))
    async with asyncio.timeout(5):
        while not reloaded.sent:  # noqa: ASYNC110 - observe the actual outbox worker
            await asyncio.sleep(0.02)
    assert len(turns) == 3
    assert [row["text"] for row in await app.store.group_messages(turns[0][0].session, 20)] == [
        "这是刚才讨论的背景",
        "小知，继续",
        "收到",
    ]


async def test_feishu_outbox_binds_recipient_and_keeps_uncertain_receipt(app, monkeypatch):
    monkeypatch.setattr(feishu, "FeishuTransport", Transport)
    transport = await connect(app, owner_id="ou_owner")
    bot = app.adapters.instances["work"]
    assert not bot.can_deliver("group", "feishu.cli_other/oc_group")
    assert not bot.can_deliver("private", "feishu.cli_example/ou_stranger")

    async def timed_out(*args, **kwargs):
        raise FeishuSendError("unconfirmed", uncertain=True)

    monkeypatch.setattr(transport, "send_text", timed_out)
    session = "private:work:feishu.cli_example/ou_owner"
    await app.adapters.enqueue(session, "等待发送确认")
    async with asyncio.timeout(4):
        while not await app.store.with_status("outbox", ("uncertain",)):  # noqa: ASYNC110
            await asyncio.sleep(0.02)
    await connect(app, owner_id="ou_owner")
    assert not await app.store.with_status("outbox", ("pending",))


def test_feishu_config_preserves_secret_on_blank_edit_but_not_for_new_app(tmp_path):
    api = create_app(tmp_path, token="admin")
    headers = {"Authorization": "Bearer admin"}
    secret = "fixture-only-feishu-secret"
    with TestClient(api, headers=headers) as client:
        cfg = client.get("/api/config").json()
        cfg["adapters"] = [
            {
                "id": "work",
                "type": "feishu",
                "enabled": False,
                "app_id": "cli_example",
                "app_secret": secret,
            }
        ]
        assert client.put("/api/config", json=cfg).status_code == 200
        assert secret not in (tmp_path / "config.toml").read_text()
        response = client.get("/api/config")
        assert secret not in response.text and "secret:inline" not in response.text
        cfg = response.json()
        adapter = cfg["adapters"][0]
        assert adapter["app_secret"] == "" and adapter["app_secret_configured"]
        adapter["owner_id"] = "ou_owner"
        assert client.put("/api/config", json=cfg).status_code == 200
        runtime = api.state.runtime
        stored = runtime.config.value.adapters[0].app_secret
        assert client.portal.call(runtime.vault.resolve, stored) == secret

        adapter["app_id"] = "cli_new"
        assert client.put("/api/config", json=cfg).status_code == 200
        assert not client.get("/api/config").json()["adapters"][0]["app_secret_configured"]
        adapter.update(app_secret=secret, owner_id="invalid")
        rejected = client.put("/api/config", json=cfg)
        assert rejected.status_code == 422 and secret not in rejected.text
