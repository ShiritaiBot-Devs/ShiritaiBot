"""Regression tests for i18n bugs found in review: language data mistaken for text, catalog
keys shown untranslated, replies in the wrong language, and slow or lossy migrations."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from shiritaibot.adapters.onebot11 import AdapterHub
from shiritaibot.agent import AgentRuntime
from shiritaibot.auth import Permissions
from shiritaibot.config import Settings
from shiritaibot.core import Context, EventBus
from shiritaibot.i18n import active_locale, localize, t, use_locale
from shiritaibot.llm.media import ImageResolver
from shiritaibot.store import Store

MEME = "0123456789abcdef"


def test_localize_keeps_properties_named_like_languages():
    schema = {
        "type": "object",
        "properties": {
            "en": {"type": "string", "description": "English text"},
            "zh": {"type": "string", "description": "Chinese text"},
        },
        "required": ["en", "zh"],
    }
    with use_locale("en"):
        assert localize(schema) == schema
    # Per-language text still comes from x-l10n.
    field = {"title": "城市", "x-l10n": {"title": {"zh-CN": "城市", "en": "City"}}}
    with use_locale("en"):
        assert localize(field) == {"title": "City"}


def test_proactive_report_tool_and_triage_are_translated():
    from shiritaibot.proactive import Decision, describe_decision, report_spec

    with use_locale("en"):
        spec = json.dumps(report_spec())
        summary = describe_decision(Decision(importance=4, decision="notify", reason="r"))
    for text in (spec, summary):
        assert "proactive." not in text
    assert t("proactive.importance.4", locale="en") in summary


def test_qq_sticker_summary_is_recognized_in_any_language():
    from shiritaibot.meme.hooker import sticker_url

    url = "https://gchat.qpic.cn/sticker.gif"
    # QQ writes this summary in Chinese whatever the bot language.
    part = {"type": "image", "data": {"sub_type": 0, "summary": "[动画表情]", "url": url}}
    for locale in ("zh-CN", "en"):
        with use_locale(locale):
            assert sticker_url(part, stickers_only=True) == url


def test_sticker_markers_are_replaced_in_the_language_they_were_stored():
    async def run():
        settings = SimpleNamespace(vision=SimpleNamespace(max_images=0))
        resolver = ImageResolver(None, "web:owner", settings)
        model = SimpleNamespace(modalities=["text", "image"])
        stored = t("markers.stickerImage", locale="zh-CN", key=MEME)
        messages = [{"role": "tool", "content": json.dumps({"image": stored}, ensure_ascii=False)}]
        with use_locale("en"):
            [result] = await resolver.render(messages, model)
            assert stored not in result["content"]
            assert t("markers.stickerDropped", key=MEME) in result["content"]

    asyncio.run(run())


def test_skill_read_reports_the_file_it_read(tmp_path):
    from shiritaibot.skill import SkillManager

    async def run():
        skills = SkillManager(tmp_path, SimpleNamespace(value=Settings()))
        with use_locale("en"):
            read = await skills.read("skill-creator")
        assert read["path"] == "SKILL.en.md"
        line = next(line for line in read["content"].splitlines() if line.startswith("# "))
        # Editing what was just read targets the English file, not SKILL.md.
        await skills.update(
            "skill-creator", read["path"], old_text=line, new_text=line + " (edited)"
        )
        folder = tmp_path / "skills" / "skill-creator"
        assert line + " (edited)" in (folder / "SKILL.en.md").read_text(encoding="utf-8")
        assert "(edited)" not in (folder / "SKILL.md").read_text(encoding="utf-8")

    asyncio.run(run())


def test_callback_page_strings_are_valid_script():
    from shiritaibot.web.chatgpt_login_helper import script_text

    value = 'Copied "it" </script> & done'
    literal = script_text(value)
    assert json.loads(literal) == value
    assert not set("<>&") & set(literal) and "&quot;" not in literal


def test_direct_replies_use_the_chat_language(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        try:
            cfg = Settings.model_validate(
                {
                    "adapters": [{"id": "qq", "owner_id": "42"}],
                    "policy": {"debounce_enabled": False},
                }
            )
            hub = AdapterHub(SimpleNamespace(value=cfg), None, store)
            hub.runtime = SimpleNamespace(
                session_locale=AsyncMock(return_value=("en", True)), submit=AsyncMock()
            )
            hub.permissions = SimpleNamespace(
                menu=SimpleNamespace(accepts=lambda *_: False, reply=AsyncMock(return_value=False))
            )
            seen = []

            async def dispatch(event):
                seen.append(active_locale())
                return True

            hub.hooks = SimpleNamespace(dispatch=dispatch)
            bot = SimpleNamespace(
                config=cfg.adapters[0],
                self_id="999",
                log=SimpleNamespace(
                    log=lambda *_: None, debug=lambda *_: None, warning=lambda *_: None
                ),
            )
            event = {
                "post_type": "message",
                "message_type": "private",
                "message_id": 1,
                "user_id": 42,
                "message": [{"type": "text", "data": {"text": "/hello"}}],
            }
            assert active_locale() == "zh-CN"
            await hub.receive(bot, event)
            assert seen == ["en"]
            hub.runtime.session_locale.assert_awaited_with("private:qq:42")
        finally:
            await store.close()

    asyncio.run(run())


def test_grants_show_translated_risk_levels(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        config = SimpleNamespace(value=Settings())
        registry = SimpleNamespace(permissions=Permissions(config, store), tools={})
        runtime = AgentRuntime(config, store, None, registry, None, AsyncMock())
        await store.open()
        try:
            await store.put(
                "approval_grant",
                "web:owner|shell",
                {"tool": "shell", "risk": "dangerous", "reason": "r", "created": 1.0},
                "web:owner",
            )
            await runtime.submit(Context("web:owner", "owner", owner=True), "/grants")
            answer = runtime.deliver.await_args.args[1]
            assert t("approvals.risk.dangerous") in answer and "approvals.risk" not in answer
        finally:
            await store.close()

    asyncio.run(run())


def test_memory_reindex_keeps_known_names_whole(tmp_path):
    from shiritaibot.memory.longterm import MemoryManager, keywords

    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        try:
            # A fresh tokenizer, so names added here do not leak into other tests.
            memory = MemoryManager(store, SimpleNamespace(value=Settings()), history_store=store)
            name = "Zorblax工坊"
            await store.execute(
                "INSERT INTO mem_entity(kind,name,status,created,updated) "
                "VALUES('thing',?,'confirmed',1,1)",
                (name,),
            )
            await store.execute(
                "INSERT INTO mem_fact(subject,content,as_of,authority,confidence,status,"
                "created,updated) VALUES(NULL,?,1,'owner',0.9,'active',1,1)",
                (f"下周要去{name}开会",),
            )
            # Indexed before the upgrade, without English stems.
            await store.execute("INSERT INTO mem_fts(id,tokens) VALUES('f1','old')")
            await memory.open()
            hits = await memory.bm25(keywords(await memory.tokens(name)))
            assert [ref for ref, _ in hits] == ["f1"]
            assert len(await store.query("SELECT rowid FROM mem_fts WHERE id='f1'")) == 1
        finally:
            await store.close()

    asyncio.run(run())


def test_knowledge_reindex_works_in_batches(tmp_path, monkeypatch):
    import shiritaibot.knowledge.core as kb_core

    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        try:
            monkeypatch.setattr(kb_core, "STEM_BATCH", 2)
            await store.execute(
                "INSERT INTO kb_document(title,filename,format,bytes,sha256,chars,chunks,"
                "source,created) VALUES('Notes','n.txt','txt',1,'x',1,5,'web',1)"
            )
            for seq in range(5):
                await store.execute(
                    "INSERT INTO kb_chunk(document,seq,text) VALUES(1,?,?)",
                    (seq, f"weekly meetings {seq}"),
                )
            # Indexed before the upgrade, without English stems.
            await store.execute("INSERT INTO kb_fts(rowid,tokens) SELECT id,'old' FROM kb_chunk")
            knowledge = kb_core.KnowledgeBase(store, SimpleNamespace(value=Settings()), None)
            await knowledge.add_stems()
            rows = await store.query("SELECT rowid, tokens FROM kb_fts ORDER BY rowid")
            assert len(rows) == 5 and all(" meet " in f" {row['tokens']} " for row in rows)
            assert (await store.get("knowledge_migration", "stems"))["chunks"] == 5
        finally:
            await store.close()

    asyncio.run(run())


def test_english_counts_have_singular_forms():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    # "{count} tools", "{count} old memories": a plural noun right after the number.
    plural = re.compile(r"\{count\}(?:-\w+)?\s+(?:[a-z]+\s+)?[a-z]+s\b")

    def entries(folder):
        for file in sorted(folder.glob("*.json")):
            stack = [("", json.loads(file.read_text(encoding="utf-8")))]
            while stack:
                prefix, data = stack.pop()
                for key, value in data.items():
                    if isinstance(value, dict):
                        stack.append((f"{prefix}{key}.", value))
                    else:
                        yield f"{file.stem}:{prefix}{key}", value

    missing = []
    for folder in (root / "src/shiritaibot/locales/en", root / "web/src/locales/en"):
        catalog = dict(entries(folder))
        for key, value in catalog.items():
            if key.endswith("_one") or not plural.search(str(value)):
                continue
            base = key.removesuffix("_other")
            if f"{base}_one" not in catalog:
                missing.append(key)
    assert missing == []


def test_count_phrases_render_singular_and_keep_chinese():
    from shiritaibot.i18n import Text

    assert t("agent.log.turnDone", locale="en", count=1, value2=2.0) == (
        "Turn complete (1 step, 2.0s)"
    )
    assert t("adapters.reconnect", locale="en", name="E", count=1).endswith("about 1 second")
    started = Text(
        "app.log.startedOn",
        tools=Text("app.log.tools", count=1),
        plugins=Text("app.log.plugins", count=2),
        adapters=Text("app.log.connections", count=1),
        mcp=Text("app.log.mcpServers", count=0),
    )
    assert started.render("en") == (
        "Started: 1 tool, 2 plugins, 1 connection, 0 MCP servers; proactive engine on"
    )
    # Chinese text is the same as before the split into phrases.
    assert started.render("zh-CN") == (
        "启动完成：1 个工具，2 个插件，1 个协议端，0 个 MCP 服务；主动性引擎已启用"
    )
    upkeep = Text(
        "memory.log.maintenance",
        archived=Text("memory.log.staleNames", count=2),
        conflicts=Text("memory.log.openQuestions", count=1),
    )
    assert upkeep.render("zh-CN") == "记忆整理：归档 2 个久未出现的新名字，汇总 1 个待确认问题"
    assert upkeep.render("en") == (
        "Memory upkeep: archived 2 new names not seen for a long time; "
        "collected 1 question to confirm"
    )
    assert t("agent.context.log.compressed", locale="zh-CN", count=3, chars=120) == (
        "上下文已压缩：3 轮旧对话合并为 120 字摘要"
    )
    with use_locale("zh-CN"):
        done = t(
            "memory.extract.log.done",
            facts=t("memory.extract.log.facts", count=2),
            events=t("memory.extract.log.events", count=1),
        )
    assert done == "记忆抽取完成：2 条事实，1 个事件"
