import asyncio
import json

import pytest

from shiritaibot.core import Context, PublicError, Reply, Tool
from shiritaibot.tools import schema

OWNER = Context("private:qq:1", "owner", "agent", True)


async def eventually(check, timeout: float = 3):
    async with asyncio.timeout(timeout):
        while not (result := await check()):
            await asyncio.sleep(0.01)
        return result


async def sent_texts(app):
    # web:* 会话的回复落在 reply，QQ 会话落在 outbox。
    return [row["text"] for row in await app.store.list("outbox")] + [
        row["text"] for row in await app.store.list("reply")
    ]


async def wait_text(app, needle="回数字"):
    async def find():
        for text in await sent_texts(app):
            if needle in text:
                return text
        return None

    return await eventually(find)


async def no_text(app, needle, wait=0.8):
    await asyncio.sleep(wait)
    return not any(needle in text for text in await sent_texts(app))


async def pending(app, name, risk="dangerous", args=None, ctx=OWNER):
    async def handler(context, arguments):
        return {"ok": True}

    if name not in app.registry.tools:
        app.registry.add(Tool(name, "", schema({}), handler, risk=risk))
    task = asyncio.create_task(app.registry.execute(ctx, name, args or {}))

    async def has_row():
        rows = await app.store.list("approval", scope=ctx.session)
        return next((row for row in rows if row["tool"] == name), None)

    await eventually(has_row)
    return task


@pytest.fixture
def window(app, monkeypatch):
    import shiritaibot.auth.menu as menu

    monkeypatch.setattr(menu, "WINDOW", 0.5)
    monkeypatch.setattr(menu, "REASON_BUDGET", 0.05)
    app.permissions.menu.target = "web:owner"
    return app


async def test_single_request_menu_has_four_options(window):
    task = await pending(window, "danger_one")
    try:
        text = await wait_text(window)
        for line in [
            "需要确认：danger_one",
            "1 同意",
            "2 拒绝",
            "3 五分钟内 danger_one 都同意",
            "4 以后 danger_one 都同意",
        ]:
            assert line in text
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_reason_falls_back_when_model_unavailable(window):
    task = await pending(window, "danger_one")
    try:
        text = await wait_text(window)
        assert "要做：" in text
        assert "为什么：" in text
        assert "说明生成失败" in text
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_two_requests_share_one_list_and_approve_all(window):
    first = await pending(window, "danger_one")
    second = await pending(window, "danger_two")
    text = await wait_text(window, "有 2 条待确认")
    assert "1 全部同意" in text
    assert "2 全部拒绝" in text
    assert "3 看详情" in text
    assert await window.permissions.menu.reply("web:owner", "1") is True
    assert json.loads(await asyncio.wait_for(first, 3))["ok"] is True
    assert json.loads(await asyncio.wait_for(second, 3))["ok"] is True


async def test_list_deny_all(window):
    first = await pending(window, "danger_one")
    second = await pending(window, "danger_two")
    await wait_text(window, "有 2 条待确认")
    assert await window.permissions.menu.reply("web:owner", "2") is True
    assert "拒绝" in json.loads(await asyncio.wait_for(first, 3))["error"]
    assert "拒绝" in json.loads(await asyncio.wait_for(second, 3))["error"]


async def test_resolved_before_window_prints_no_menu(window):
    task = await pending(window, "danger_one")
    rows = await window.store.list("approval", scope=OWNER.session)
    await window.permissions.resolve(rows[0]["id"], True)
    await asyncio.wait_for(task, 3)
    assert await no_text(window, "回数字")


async def test_menu_ignores_unrelated_text(window):
    task = await pending(window, "danger_one")
    await wait_text(window)
    try:
        assert await window.permissions.menu.reply("web:owner", "你好") is False
        assert await window.permissions.menu.reply("web:owner", "同意") is False
        assert await window.permissions.menu.reply("private:qq:1", "1") is False
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_new_request_queues_until_current_menu_closes(window):
    first = await pending(window, "danger_one")
    await wait_text(window, "需要确认：danger_one")
    second = await pending(window, "danger_two")
    assert await no_text(window, "需要确认：danger_two")
    assert await window.permissions.menu.reply("web:owner", "1") is True
    text = await wait_text(window, "需要确认：danger_two")
    assert "2 拒绝" in text
    assert json.loads(await asyncio.wait_for(first, 3))["ok"] is True
    second.cancel()
    await asyncio.gather(second, return_exceptions=True)


async def test_detail_groups_by_risk_then_tool(window):
    first = await pending(window, "danger_one", risk="dangerous")
    second = await pending(window, "qq_send", risk="normal")
    third = await pending(window, "shell_like", risk="sensitive")
    await wait_text(window, "有 3 条待确认")
    assert await window.permissions.menu.reply("web:owner", "3") is True
    detail = await wait_text(window, "详情，按类型分")
    assert detail.index("危险 · danger_one") < detail.index("敏感 · shell_like") < detail.index(
        "一般 · qq_send"
    )
    for task in (first, second, third):
        task.cancel()
    await asyncio.gather(first, second, third, return_exceptions=True)


async def test_detail_approves_only_one_group(window):
    first = await pending(window, "danger_one", risk="dangerous")
    second = await pending(window, "danger_one", risk="dangerous")
    third = await pending(window, "qq_send", risk="normal")
    await wait_text(window, "有 3 条待确认")
    await window.permissions.menu.reply("web:owner", "3")
    detail = await wait_text(window, "详情，按类型分")
    assert "1 danger_one 全部同意" in detail
    assert "3 qq_send 全部同意" in detail
    assert "5 逐条处理" in detail
    assert await window.permissions.menu.reply("web:owner", "1") is True
    assert json.loads(await asyncio.wait_for(first, 3))["ok"] is True
    assert json.loads(await asyncio.wait_for(second, 3))["ok"] is True
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(third, 0.2)
    third.cancel()
    await asyncio.gather(third, return_exceptions=True)


async def test_step_by_step_walks_single_menus(window):
    first = await pending(window, "danger_one")
    second = await pending(window, "danger_two")
    await wait_text(window, "有 2 条待确认")
    await window.permissions.menu.reply("web:owner", "3")
    await window.permissions.menu.reply("web:owner", "5")
    assert await window.permissions.menu.reply("web:owner", "1") is True
    assert await window.permissions.menu.reply("web:owner", "2") is True
    assert json.loads(await asyncio.wait_for(first, 3))["ok"] is True
    # registry.execute 把审批拒绝转成错误 JSON，而不是抛 PublicError。
    assert "拒绝" in json.loads(await asyncio.wait_for(second, 3))["error"]


async def test_expired_request_releases_queued_batch(window):
    first = await pending(window, "danger_one")
    await wait_text(window, "需要确认：danger_one")
    second = await pending(window, "danger_two")
    rows = await window.store.list("approval", scope=OWNER.session)
    row = next(r for r in rows if r["tool"] == "danger_one")
    row["status"] = "expired"
    await window.store.put("approval", row["id"], row, OWNER.session)
    await window.permissions.menu._refresh()
    text = await wait_text(window, "需要确认：danger_two")
    assert "danger_two" in text
    first.cancel()
    second.cancel()
    await asyncio.gather(first, second, return_exceptions=True)


async def test_single_option_three_grants_for_five_minutes(window):
    first = await pending(window, "danger_one")
    await wait_text(window)
    assert await window.permissions.menu.reply("web:owner", "3") is True
    assert json.loads(await asyncio.wait_for(first, 3))["ok"] is True
    # 免问生效后 check 不再建审批行，直接执行。
    later = asyncio.create_task(window.registry.execute(OWNER, "danger_one", {}))
    assert json.loads(await asyncio.wait_for(later, 3))["ok"] is True
    rows = await window.store.list("approval", scope=OWNER.session)
    assert all(row["status"] != "pending" for row in rows)


async def test_quick_grant_does_not_cross_sessions(window):
    first = await pending(window, "danger_one")
    await wait_text(window)
    await window.permissions.menu.reply("web:owner", "3")
    await asyncio.wait_for(first, 3)
    other = Context("private:qq:2", "owner", "agent", True)
    task = await pending(window, "danger_one", ctx=other)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(task, 0.2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_sensitive_shell_has_no_grant_options(window):
    task = await pending(
        window, "shell", risk="dangerous", args={"command": "cat config.toml"}
    )
    text = await wait_text(window)
    assert "3 五分钟内" not in text
    assert "4 以后" not in text
    assert "只提供 1/2" in text
    assert await window.permissions.menu.reply("web:owner", "3") is False
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_permanent_grant_updates_policy_record_and_memory(window):
    task = await pending(window, "danger_one", risk="dangerous")
    await wait_text(window)
    assert await window.permissions.menu.reply("web:owner", "4") is True
    assert json.loads(await asyncio.wait_for(task, 3))["ok"] is True

    profile = window.config.value.policy.sessions[OWNER.session]
    assert profile.tools["danger_one"] == "allow"
    rows = await window.store.list("approval_grant", scope=OWNER.session)
    assert rows[0]["tool"] == "danger_one"

    facts = await window.store.query("SELECT content FROM mem_fact")
    assert any("danger_one" in row["content"] for row in facts)

    later = asyncio.create_task(window.registry.execute(OWNER, "danger_one", {}))
    assert json.loads(await asyncio.wait_for(later, 3))["ok"] is True


async def test_sensitive_shell_cannot_get_permanent_grant(window):
    task = await pending(
        window, "shell", risk="dangerous", args={"command": "cat master.key"}
    )
    await wait_text(window)
    assert await window.permissions.menu.reply("web:owner", "4") is False
    profile = window.config.value.policy.sessions.get(
        OWNER.session, window.config.value.policy.owner_default
    )
    assert "shell" not in profile.tools
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_background_approval_only_offers_agree_and_deny(window):
    ctx = Context("background:job", "owner", "agent", True, background=True)
    task = await pending(window, "danger_one", ctx=ctx)
    text = await wait_text(window)
    assert "1 同意" in text and "2 拒绝" in text
    assert "3 五分钟内" not in text and "4 以后" not in text
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_grants_command_lists_and_revokes(window):
    from shiritaibot.agent.commands import command

    task = await pending(window, "danger_one", risk="dangerous")
    await wait_text(window)
    await window.permissions.menu.reply("web:owner", "4")
    await asyncio.wait_for(task, 3)

    ctx = Context("private:qq:1", "owner", "agent", True)
    await command(window.runtime, ctx, "/grants")
    listing = await wait_text(window, "免问授权：")
    assert "danger_one" in listing

    await command(window.runtime, ctx, "/grants 撤销 danger_one")
    await wait_text(window, "已撤销 private:qq:1 的 danger_one")

    await command(window.runtime, ctx, "/grants")
    await wait_text(window, "没有免问授权")


async def test_grants_command_is_owner_private_only():
    from shiritaibot.agent.commands import COMMANDS

    assert "grants" in COMMANDS


async def test_stale_detail_number_does_not_hit_renumbered_group(window):
    first = await pending(window, "danger_one", risk="dangerous")
    second = await pending(window, "danger_one", risk="dangerous")
    third = await pending(window, "qq_send", risk="normal")
    await wait_text(window, "有 3 条待确认")
    await window.permissions.menu.reply("web:owner", "3")
    await wait_text(window, "详情，按类型分")
    rows = await window.store.list("approval", scope=OWNER.session)
    for row in rows:
        if row["tool"] == "danger_one":
            row["status"] = "expired"
            await window.store.put("approval", row["id"], row, OWNER.session)
    # 主人看到的还是三分组编号，此时回 1 不得落到重新编号后的 qq_send 上。
    assert await window.permissions.menu.reply("web:owner", "1") is True
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(third, 0.2)
    for task in (first, second, third):
        task.cancel()
    await asyncio.gather(first, second, third, return_exceptions=True)


async def test_approve_all_seven_keeps_queued_batch_alive(window):
    tasks = [await pending(window, f"danger_{index}") for index in range(7)]
    await wait_text(window, "有 5 条待确认")
    assert await window.permissions.menu.reply("web:owner", "1") is True
    for task in tasks[:5]:
        assert json.loads(await asyncio.wait_for(task, 5))["ok"] is True
    await wait_text(window, "有 2 条待确认")
    assert await window.permissions.menu.reply("web:owner", "1") is True
    for task in tasks[5:]:
        assert json.loads(await asyncio.wait_for(task, 5))["ok"] is True


async def test_finished_batch_skips_expired_queued_requests(window):
    await pending(window, "danger_0")
    await pending(window, "danger_1")
    await pending(window, "danger_2")
    await pending(window, "danger_3")
    await pending(window, "danger_4")
    six = await pending(window, "danger_5")
    await wait_text(window, "有 5 条待确认")
    rows = await window.store.list("approval", scope=OWNER.session)
    row = next(r for r in rows if r["tool"] == "danger_5")
    row["status"] = "expired"
    await window.store.put("approval", row["id"], row, OWNER.session)
    assert await window.permissions.menu.reply("web:owner", "1") is True
    await asyncio.sleep(0.3)
    assert window.permissions.menu.batch is None
    assert await no_text(window, "有 1 条待确认")
    six.cancel()
    await asyncio.gather(six, return_exceptions=True)


async def test_grants_command_sees_other_sessions_in_owner_private(window):
    from shiritaibot.agent.commands import command

    from shiritaibot.config import SessionPolicy

    group_ctx = Context("group:qq:9", "2", "agent")
    # 群默认是 chat 模式，工具会被直接拒绝；这里显式切到 agent 才能走到审批。
    await window.permissions.set_session(group_ctx, SessionPolicy(mode="agent"))
    task = await pending(window, "danger_one", ctx=group_ctx)
    await wait_text(window)
    await window.permissions.menu.reply("web:owner", "4")
    await asyncio.wait_for(task, 3)

    owner = Context("private:qq:1", "owner", "agent", True)
    await command(window.runtime, owner, "/grants")
    listing = await wait_text(window, "group:qq:9")
    assert "danger_one" in listing

    await command(window.runtime, owner, "/grants 撤销 danger_one group:qq:9")
    assert "已撤销" in await wait_text(window, "已撤销")


async def test_reason_comes_from_light_model(window, monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        window.llm, "complete", AsyncMock(return_value=Reply(text="要做：查看状态\n为什么：你要确认"))
    )
    task = await pending(window, "danger_one")
    try:
        text = await wait_text(window)
        assert "要做：查看状态" in text
        assert "为什么：你要确认" in text
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
