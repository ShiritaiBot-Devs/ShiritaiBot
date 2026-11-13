import asyncio
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from test_permissions import eventually

from shiritaibot.adapters import feishu
from shiritaibot.adapters.feishu_transport import FeishuInbound, FeishuSendError
from shiritaibot.adapters.onebot11 import OneBot
from shiritaibot.config import AdapterConfig, SessionPolicy, is_feishu_session
from shiritaibot.core import Context, Reply, session_dir
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
        self.files = []

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

    async def send_file(self, name, data, *, open_id):
        self.files.append((name, data, open_id))
        return {"message_id": "om_file"}


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
        while len(history := await app.store.group_messages(turns[0][0].session, 20)) < 3:  # noqa: ASYNC110 - wait for outbox history persistence
            await asyncio.sleep(0.02)
    assert len(turns) == 3
    assert reloaded.sent
    assert [row["text"] for row in history] == [
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


@pytest.mark.parametrize(
    ("change", "owner_valid", "group_valid"),
    [
        ({"app_id": "cli_rebound"}, False, False),
        ({"domain": "lark"}, False, False),
        ({"owner_id": "ou_rebound"}, False, True),
        ({"groups": []}, True, False),
        ({"enabled": False}, False, False),
        ({"memory_profile": "default"}, True, True),
    ],
)
async def test_feishu_session_permissions_keep_channel_and_validate_binding(
    app, change, owner_valid, group_valid
):
    settings = app.config.value.model_copy(deep=True)
    adapter = AdapterConfig(
        id="work", type="feishu", app_id="cli_example", owner_id="ou_owner", groups=["oc_group"]
    )
    owner = Context(adapter.owner_session(), "ou_owner", owner=True, memory_profile="feishu")
    group = Context("group:work:feishu.cli_example/oc_group", "ou_member", group=True)
    settings.adapters = [adapter]
    settings.meme.enabled = True
    settings.policy.owner_default = SessionPolicy(tools={"shell": "allow"})
    settings.policy.sessions[group.session] = SessionPolicy(mode="agent", tools={"shell": "allow"})
    await app.reconfigure(settings)
    assert app.config.value.feishu_context_valid(owner)
    assert app.config.value.feishu_context_valid(group)

    settings = app.config.value.model_copy(deep=True)
    settings.adapters[0] = adapter.model_copy(update=change)
    await app.reconfigure(settings)
    assert is_feishu_session(owner.session) and is_feishu_session(group.session)
    assert app.config.value.feishu_context_valid(owner) is owner_valid
    assert app.config.value.feishu_context_valid(group) is group_valid
    assert not app.config.value.feishu_context_valid(replace(owner, actor="ou_other"))
    assert not app.config.value.feishu_context_valid(replace(group, owner=True))
    assert app.permissions.context(group).mode == "chat"
    assert app.permissions.decision(group, app.registry.tools["shell"]) == "deny"
    assert app.permissions.decision(group, app.registry.tools["current_time"]) == (
        "allow" if group_valid else "deny"
    )
    assert app.permissions.decision(owner, app.registry.tools["shell"]) == (
        "allow" if owner_valid else "deny"
    )

    qq = OneBot(AdapterConfig(id="qq", owner_id="1"), app.vault, app.store, None)
    qq.status = "connected"
    app.adapters.instances["qq"] = qq
    qq_owner = Context("private:qq:1", "1", "agent", owner=True)
    for name in ("qq_group_list", "meme_search"):
        tool = app.registry.tools[name]
        assert tool.available(qq_owner)
        assert not tool.available(owner)
        assert not tool.available(group)


async def test_feishu_reconfigure_cancels_current_and_queued_work_for_removed_group(
    app, monkeypatch
):
    monkeypatch.setattr(feishu, "FeishuTransport", Transport)
    await connect(app, owner_id="ou_owner")
    app.config.value.policy.debounce_enabled = False
    group = Context("group:work:feishu.cli_example/oc_group", "ou_member", group=True)
    entered = asyncio.Event()
    turns = []

    async def complete(route, system, messages, tools, **kwargs):
        if kwargs.get("scope") != group.session:
            return Reply(text="本轮已取消。")
        turns.append(messages[-1]["content"])
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr(app.runtime.llm, "complete", complete)
    assert await app.runtime.submit(group, "正在处理的消息") == "queued"
    await asyncio.wait_for(entered.wait(), timeout=2)
    assert await app.runtime.submit(replace(group, actor="ou_other"), "排队中的消息") == "queued"
    actor = app.runtime.actors[group.session]

    storing = asyncio.Event()
    resume = asyncio.Event()
    put = app.store.put

    async def pause_session_write(kind, key, data, *args):
        if kind == "session" and key == group.session:
            storing.set()
            await resume.wait()
        return await put(kind, key, data, *args)

    monkeypatch.setattr(app.store, "put", pause_session_write)
    command = asyncio.create_task(app.runtime.submit(group, "/help"))
    try:
        await asyncio.wait_for(storing.wait(), timeout=2)
        settings = app.config.value.model_copy(deep=True)
        settings.adapters[0].groups = []
        await app.reconfigure(settings)
    finally:
        resume.set()

    assert await asyncio.wait_for(command, timeout=2) == "ignored"
    assert actor.task.cancelled()
    assert group.session not in app.runtime.actors
    assert len(turns) == 1
    assert await app.runtime.submit(group, "旧群消息") == "ignored"
    assert len(await app.store.with_status("turn", ("cancelled",))) == 1


async def test_pending_tool_is_rechecked_after_feishu_owner_changes(app, monkeypatch):
    monkeypatch.setattr(feishu, "FeishuTransport", Transport)
    await connect(app, owner_id="ou_owner")
    session = app.config.value.adapters[0].owner_session()
    owner = Context(session, "ou_owner", "agent", owner=True, memory_profile="feishu")
    app.config.value.policy.owner_default.tools["current_time"] = "manual"
    request = asyncio.create_task(app.registry.execute(owner, "current_time", {}))
    try:
        approvals = await eventually(lambda: app.store.with_status("approval", ("pending",)))
        settings = app.config.value.model_copy(deep=True)
        settings.adapters[0].owner_id = "ou_rebound"
        await app.reconfigure(settings)
        await app.permissions.resolve(approvals[0]["id"], True)
        result = json.loads(await asyncio.wait_for(request, timeout=2))
        assert result == {"error": "审批期间权限已变更，未执行"}
    finally:
        request.cancel()
        await asyncio.gather(request, return_exceptions=True)


@pytest.mark.parametrize("qq_connected", [False, True])
async def test_feishu_owner_dm_sends_workspace_file_as_attachment(app, monkeypatch, qq_connected):
    monkeypatch.setattr(feishu, "FeishuTransport", Transport)
    transport = await connect(app, owner_id="ou_owner")
    app.config.value.meme.enabled = True
    app.config.value.policy.owner_default.tools["feishu_file_send"] = "allow"
    session = app.adapters.instances["work"].config.owner_session()
    if qq_connected:
        qq = OneBot(AdapterConfig(id="qq", owner_id="1"), app.vault, app.store, None)
        qq.status = "connected"
        app.adapters.instances["qq"] = qq
        qq_ctx = Context("private:qq:1", "1", "agent", owner=True)
        qq_specs = {tool["function"]["name"] for tool in app.registry.specs(qq_ctx)}
        assert "qq_group_list" in qq_specs and "feishu_file_send" not in qq_specs
    feishu_ctx = Context(session, "ou_owner", "agent", owner=True, memory_profile="feishu")
    rejected = await app.registry.execute(feishu_ctx, "qq_group_list", {"adapter": "qq"})
    assert json.loads(rejected) == {"error": "工具被权限策略拒绝"}
    workspace = session_dir(app.home, "workspaces", session)
    workspace.mkdir(parents=True)
    data = b"int main(void) { return 0; }\n"
    name = "caculator.c"
    (workspace / name).write_bytes(data)
    done = asyncio.Event()
    advertised = set()
    results = []

    async def complete(route, system, messages, tools, **kwargs):
        advertised.update(tool["function"]["name"] for tool in tools)
        results.extend(
            json.loads(message["content"]) for message in messages if message["role"] == "tool"
        )
        if results:
            done.set()
            return Reply(text="文件已发送。")
        return Reply(
            calls=[
                {
                    "id": "call-file",
                    "type": "function",
                    "function": {
                        "name": "feishu_file_send",
                        "arguments": json.dumps({"path": name}),
                    },
                }
            ]
        )

    monkeypatch.setattr(app.runtime.llm, "complete", complete)
    await transport.accept(
        incoming("om_dm_file", f"把 {name} 发给我", chat_type="p2p", sender_id="ou_owner")
    )
    await asyncio.wait_for(done.wait(), timeout=5)

    assert "feishu_file_send" in advertised
    assert not any(name.startswith(("qq_", "meme_")) for name in advertised)
    assert transport.files == [(name, data, "ou_owner")]
    assert results == [{"sent": True, "message_id": "om_file", "name": name, "bytes": len(data)}]


async def test_feishu_file_tool_rejects_other_sessions_and_workspace_escapes(app, monkeypatch):
    monkeypatch.setattr(feishu, "FeishuTransport", Transport)
    transport = await connect(app, owner_id="ou_owner")
    tool = "feishu_file_send"
    app.config.value.policy.owner_default.tools[tool] = "allow"
    session = app.adapters.instances["work"].config.owner_session()
    ctx = Context(session, "ou_owner", "agent", owner=True, memory_profile="feishu")
    assert app.permissions.decision(ctx, app.registry.tools[tool]) == "allow"
    workspace = session_dir(app.home, "workspaces", session)
    workspace.mkdir(parents=True)
    (workspace / "allowed.txt").write_text("allowed")
    outside = app.home / "outside.txt"
    outside.write_text("outside")
    (workspace / "link.txt").symlink_to(outside)

    for other in (
        replace(ctx, actor="ou_stranger", owner=False),
        replace(ctx, session="private:work:feishu.cli_example/ou_stranger"),
        replace(ctx, session="group:work:feishu.cli_example/oc_group", group=True),
        replace(ctx, session="web:owner"),
        replace(ctx, background=True),
    ):
        assert "error" in json.loads(
            await app.registry.execute(other, tool, {"path": "allowed.txt"})
        )
    for path in (str(outside), "link.txt"):
        assert "error" in json.loads(await app.registry.execute(ctx, tool, {"path": path}))
    assert transport.files == []


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
