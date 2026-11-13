import asyncio
import json
from dataclasses import replace

from test_extract import Model
from test_permissions import eventually
from test_proactive import Summarizer

from shiritaibot.app import Application
from shiritaibot.config import AdapterConfig, SessionPolicy
from shiritaibot.core import Context, Reply, now

SESSION = "private:work:feishu.cli_test/ou_owner"
FEISHU = Context(SESSION, "ou_owner", "agent", True, memory_profile="feishu")
DEFAULT = replace(FEISHU, memory_profile="default")


async def configure(app, profile):
    settings = app.config.value.model_copy(deep=True)
    settings.adapters = [
        AdapterConfig(
            id="work",
            type="feishu",
            app_id="cli_test",
            owner_id="ou_owner",
            enabled=True,
            memory_profile=profile,
        )
    ]
    settings.policy.owner_default = SessionPolicy(preset="full_access")
    await app.reconfigure(settings)


async def stop_workers(app):
    for task in app.tasks:
        task.cancel()
    await asyncio.gather(*app.tasks, return_exceptions=True)


async def call(app, ctx, name, **args):
    return json.loads(await app.registry.execute(ctx, name, args))


async def remember(app, ctx, text):
    await app.store.append(
        ctx.session, ctx.turn, {"role": "user", "content": text}, memory_profile=ctx.memory_profile
    )
    return await call(app, ctx, "memory_save", kind="fact", content=text, evidence=text)


async def test_profile_switch_isolates_turn_history_recall_and_explicit_tools(app):
    await configure(app, "feishu")
    await stop_workers(app)
    default = replace(DEFAULT, turn="default-fact")
    feishu = replace(FEISHU, turn="feishu-fact")
    default_fact = "交接代号是木棉"
    feishu_fact = "交接代号是白鹭"
    assert (await remember(app, default, default_fact))["id"] == "f1"
    assert (await remember(app, feishu, feishu_fact))["id"] == "f1"
    assert default_fact in (await call(app, default, "memory_get", id="f1"))["text"]
    assert feishu_fact in (await call(app, feishu, "memory_get", id="f1"))["text"]

    class Capture:
        def __init__(self):
            self.inputs = []

        async def complete(self, route, system, messages, tools, **kwargs):
            self.inputs.append("\n".join(m.get("content", "") for m in messages))
            return Reply(text="收到")

    model = app.runtime.llm = Capture()
    await app.runtime.submit(FEISHU, "请回忆交接代号")

    async def captured(count):
        return len(model.inputs) >= count

    await eventually(lambda: captured(1))
    assert feishu_fact in model.inputs[0] and default_fact not in model.inputs[0]
    await configure(app, "default")
    await app.runtime.submit(DEFAULT, "再说一次交接代号")
    await eventually(lambda: captured(2))
    assert default_fact in model.inputs[1] and feishu_fact not in model.inputs[1]
    assert "请回忆交接代号" not in model.inputs[1]
    history = await call(app, DEFAULT, "history_query")
    assert all(feishu_fact not in row["content"] for row in history["messages"])
    await call(app, DEFAULT, "memory_forget", id="f1", reason="测试遗忘")
    assert (await call(app, FEISHU, "memory_get", id="f1"))["status"] == "active"


async def test_extraction_and_summary_recover_for_original_profile_after_switch(tmp_path):
    app = Application(tmp_path)
    await app.start()
    try:
        await configure(app, "feishu")
        await stop_workers(app)
        await app.store.put(
            "turn",
            "interrupted-feishu",
            {
                "actor": FEISHU.actor,
                "status": "running",
                "memory_profile": "feishu",
                "memory_scope": "memory:feishu",
                "loop": "main",
            },
            SESSION,
        )
        for profile, turn, content in [
            ("feishu", "interrupted-feishu", "云杉项目使用内部交付清单"),
            ("default", "old-default", "海棠项目仅在默认记忆库"),
        ]:
            await app.store.append(
                SESSION, turn, {"role": "user", "content": content}, memory_profile=profile
            )
        await app.store.execute("UPDATE messages SET created=?", (now().timestamp() - 120,))
        await configure(app, "default")
    finally:
        await app.close()

    app = Application(tmp_path)
    await app.start()
    try:
        await stop_workers(app)
        assert app.config.value.adapters[0].memory_profile == "default"
        extractor = app.feishu_extractor
        assert extractor is not None
        model = extractor.llm = Model()
        app.config.value.routes["light"] = ["fake"]
        app.config.value.memory.extract_idle_minutes = 1
        model.extract.append(
            {
                "facts": [
                    {
                        "subject": "E1",
                        "content": "云杉项目使用内部交付清单",
                        "evidence_line": 1,
                        "evidence_quote": "云杉项目使用内部交付清单",
                    }
                ]
            }
        )
        await extractor.tick()
        assert "云杉项目" in model.prompts[0][1] and "海棠项目" not in model.prompts[0][1]
        assert (await app.memory_for("feishu").search("云杉项目"))["results"]
        assert not (await app.memory_for("default").search("云杉项目"))["results"]

        app.short_term.llm = Summarizer()
        await app.short_term.tick()
        assert not await app.short_term.recent()
        summaries = await app.short_term.recent(memory_profile="feishu")
        assert [row["id"] for row in summaries] == ["interrupted-feishu"]
        assert summaries[0]["turn_status"] == "interrupted"
    finally:
        await app.close()


async def test_pending_shared_feishu_turn_is_extracted_after_switch_to_chat(tmp_path):
    app = Application(tmp_path)
    await app.start()
    try:
        await configure(app, "default")
        await stop_workers(app)
        for turn, scope, text in [
            ("shared-agent", "owner", "橡树项目每周四交付"),
            ("shared-chat", SESSION, "枫叶项目只在聊天中提及"),
        ]:
            await app.store.put(
                "turn",
                turn,
                {
                    "actor": DEFAULT.actor,
                    "status": "completed",
                    "memory_profile": "default",
                    "memory_scope": scope,
                    "loop": "main",
                },
                SESSION,
            )
            await app.store.append(
                SESSION, turn, {"role": "user", "content": text}, memory_profile="default"
            )
        await app.store.execute("UPDATE messages SET created=?", (now().timestamp() - 120,))
        await configure(app, "feishu")
        await app.store.put(
            "session",
            SESSION,
            {
                "owner": True,
                "mode": "chat",
                "group": False,
                "background": False,
                "memory_profile": "feishu",
            },
        )
    finally:
        await app.close()

    app = Application(tmp_path)
    await app.start()
    try:
        await stop_workers(app)
        model = app.extractor.llm = Model()
        app.config.value.routes["light"] = ["fake"]
        app.config.value.memory.extract_idle_minutes = 1
        model.extract.append(
            {
                "facts": [
                    {
                        "subject": "E1",
                        "content": "橡树项目每周四交付",
                        "evidence_line": 1,
                        "evidence_quote": "橡树项目每周四交付",
                    }
                ]
            }
        )
        await app.extractor.tick()
        prompt = model.prompts[0][1]
        assert "橡树项目" in prompt and "枫叶项目" not in prompt
        assert (await app.memory_for("default").search("橡树项目"))["results"]
        assert not (await app.memory_for("feishu").search("橡树项目"))["results"]
    finally:
        await app.close()
