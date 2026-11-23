"""Internationalization foundation (docs/I18N.md): catalogs, locales, /lang, time zones."""

import asyncio
import json
import re
import string
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import shiritaibot.prompts.en as en_prompts
import shiritaibot.prompts.zh as zh_prompts
from shiritaibot.agent import AgentRuntime
from shiritaibot.auth import Permissions
from shiritaibot.config import Settings
from shiritaibot.core import Context, EventBus, PublicError
from shiritaibot.i18n import (
    FOLDER,
    LOCALES,
    Text,
    catalog,
    normalize,
    t,
    use_locale,
)
from shiritaibot.llm.media import ImageResolver
from shiritaibot.prompts import prompt
from shiritaibot.store import Store
from shiritaibot.store.statistics import statistics
from shiritaibot.web import create_app

ROOT = Path(__file__).resolve().parents[1]
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def flatten(data, prefix=""):
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            yield from flatten(value, path)
        else:
            yield path, value


def web_catalog(locale):
    folder = ROOT / "web" / "src" / "locales" / locale
    return {
        f"{file.stem}:{key}": value
        for file in sorted(folder.glob("*.json"))
        for key, value in flatten(json.loads(file.read_text(encoding="utf-8")))
    }


PLURAL = re.compile(r"_(zero|one|two|few|many|other)$")


def plural_merged(entries):
    # Languages have different plural forms (key_one, key_other); compare per key.
    merged = {}
    for key, value in entries.items():
        merged[PLURAL.sub("", key)] = merged.get(PLURAL.sub("", key), "") + " " + value
    return merged


def test_catalogs_match_across_locales():
    for name, raw in (("backend", catalog), ("web", web_catalog)):

        def load(locale, raw=raw):
            return plural_merged(raw(locale))

        reference = load("zh-CN")
        assert reference, name
        for locale in LOCALES:
            entries = load(locale)
            assert entries.keys() == reference.keys(), (name, locale)
            for key, text in entries.items():
                assert set(PLACEHOLDER.findall(text)) == set(PLACEHOLDER.findall(reference[key])), (
                    name,
                    locale,
                    key,
                )
    assert {path.name for path in FOLDER.iterdir()} == set(LOCALES)


def test_prompt_modules_define_the_same_fields():
    def fields(text):
        return {name for _, name, _, _ in string.Formatter().parse(text) if name}

    for name in dir(en_prompts):
        if name.isupper():
            assert hasattr(zh_prompts, name), name
            assert fields(getattr(en_prompts, name)) == fields(getattr(zh_prompts, name)), name
    with use_locale("en"):
        assert prompt("LEGACY_DEFAULT_PERSONA") == zh_prompts.LEGACY_DEFAULT_PERSONA


def test_chinese_prompts_are_unchanged():
    # Byte-identical to the strings earlier builds sent (prompt caches and behaviour).
    assert zh_prompts.IMAGE_CAPTION == (
        "你是图像转述器，为看不到图片的模型描述图片。用中文客观描述图片内容："
        "主体、场景、人物动作与表情、关键细节；图中文字按原文转录。"
        "不要推测图片之外的事，不超过 300 字，只输出描述。"
    )
    assert zh_prompts.MEME_CAPTION == (
        "你是表情包转述器，为看不到图片的模型描述表情包。用中文简洁描述："
        "画面主体与动作神态、图中文字（按原文转录）、这个表情表达的情绪或适合的使用场合。"
        "不超过 80 字，只输出描述。"
    )
    assert zh_prompts.ASSISTANT_NAME.format(name="Bot") == (
        "\n助手的称呼是 Bot。在对话、记忆和摘要中使用此称呼指代助手。"
    )
    assert zh_prompts.OWNER_NAME.format(name="主人") == "\n当前用户是主人，请称呼对方为 主人。"


def test_translate_falls_back_and_formats():
    assert t("commands.lang.owner_only", locale="en") == (
        "Only the owner can change the session language."
    )
    # Text that is not a key is returned as-is and never formatted.
    assert t('{"error":"原样"}', locale="en", value=1) == '{"error":"原样"}'
    with use_locale("en"):
        assert str(Text("commands.lang.status", name="English")) == "Language: English"
        error = PublicError("commands.lang.unknown", value="fr", options="en")
        assert str(error) == "Unsupported language: fr. Options: en."
    # Rendered when shown, not when raised.
    assert str(error) == "不支持的语言：fr。可选：en。"
    assert str(PublicError("中文 {未格式化}")) == "中文 {未格式化}"
    assert str(PublicError("a", "b")) == "('a', 'b')"


def test_normalize_locale_tags():
    assert normalize("en-US") == normalize("English") == normalize("英文") == "en"
    assert normalize("zh") == normalize("zh_TW") == normalize("中文") == "zh-CN"
    assert normalize("fr") is None and normalize("") is None


def test_replies_use_the_default_language():
    legacy = Settings.model_validate({"persona": zh_prompts.LEGACY_DEFAULT_PERSONA})
    assert legacy.persona == zh_prompts.DEFAULT_PERSONA
    custom = Settings.model_validate({"persona": "你是猫娘。用中文回答。"})
    assert custom.persona == "你是猫娘。用中文回答。"

    # Out of the box replies are Chinese, as before; only the persona no longer says so.
    zh = legacy.system_prompt(owner=True)
    assert zh.startswith(zh_prompts.DEFAULT_PERSONA)
    assert "用中文回答" not in zh and zh.endswith("用简体中文回复。")
    assert "请称呼对方为 主人" in zh

    en = Settings(locale="en").system_prompt(owner=True)
    assert en.startswith(en_prompts.DEFAULT_PERSONA)
    assert "主人" not in en and en.endswith(en_prompts.REPLY_DEFAULT)

    forced = custom.system_prompt(locale="en", forced=True)
    assert forced.startswith("你是猫娘。用中文回答。")
    assert forced.endswith(en_prompts.REPLY_FORCED)
    named = Settings(locale="en", owner_name="Sam").system_prompt(owner=True)
    assert "Address them as Sam." in named


def test_message_merge_treats_full_stop_as_end():
    assert "." in Settings().policy.debounce_suffixes


def runtime_for(tmp_path, settings=None):
    store = Store(tmp_path, EventBus())
    config = SimpleNamespace(value=settings or Settings())
    registry = SimpleNamespace(permissions=Permissions(config, store), tools={})
    runtime = AgentRuntime(config, store, None, registry, None, AsyncMock())
    return runtime, store


def test_lang_command_sets_forced_session_language(tmp_path):
    async def run():
        runtime, store = runtime_for(tmp_path)
        await store.open()
        try:
            owner = Context("web:owner", "owner", owner=True)
            guest = Context("group:qq:1", "42", group=True)

            async def reply(ctx, text):
                await runtime.submit(ctx, text)
                return runtime.deliver.await_args.args[1]

            assert await runtime.session_locale("web:owner") == ("zh-CN", False)
            answer = await reply(owner, "/lang en")
            assert answer.startswith("Language for this session: English (set with /lang")
            assert await runtime.session_locale("web:owner") == ("en", True)
            # /lang lives outside SessionPolicy, so mode and tool permissions are untouched.
            assert runtime.config.value.policy.sessions == {}
            help_text = await reply(owner, "/help")
            assert "/lang — Show the bot language" in help_text
            assert "Language: English" in await reply(owner, "/status")

            # Background work speaks the language of the chat it reports to.
            assert await runtime.session_locale("background:proactive:x") == ("en", True)

            assert await reply(guest, "/lang en") == "只有主人可以切换会话语言。"
            assert (await reply(guest, "/lang")).startswith("本会话语言：简体中文（全局默认")
            # Errors use the session's current language (English here).
            assert await reply(owner, "/lang klingon") == (
                "Unsupported language: klingon. Options: zh-CN (简体中文), en (English)."
            )

            answer = await reply(owner, "/lang default")
            assert answer.startswith("本会话语言：简体中文（全局默认")
            assert await runtime.session_locale("web:owner") == ("zh-CN", False)
        finally:
            await store.close()

    asyncio.run(run())


def test_image_captions_follow_the_conversation_language(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        try:
            replies = iter(["一只猫", "A cat"])
            calls = []

            async def complete(route, system, messages, tools, settings):
                calls.append((system, messages[0]["content"][1]["text"]))
                return SimpleNamespace(text=next(replies))

            llm = SimpleNamespace(store=store, complete=complete)
            settings = SimpleNamespace(
                vision=SimpleNamespace(caption=True), routes={"vision": ["m"]}
            )

            async def caption(locale):
                resolver = ImageResolver(llm, "web:owner", settings)
                resolver.image = AsyncMock(return_value="data:image/png;base64,AA==")
                with use_locale(locale):
                    return await resolver.caption("img-1")

            assert await caption("zh-CN") == "一只猫"
            assert await caption("en") == "A cat"
            assert calls == [
                (zh_prompts.IMAGE_CAPTION, zh_prompts.IMAGE_CAPTION_REQUEST),
                (en_prompts.IMAGE_CAPTION, en_prompts.IMAGE_CAPTION_REQUEST),
            ]
            # The stored caption is reused only for the language it was written in.
            assert await caption("en") == "A cat" and len(calls) == 2
        finally:
            await store.close()

    asyncio.run(run())


def test_statistics_use_the_owner_time_zone(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        try:
            end = datetime(2026, 11, 23, 12, tzinfo=ZoneInfo("UTC"))
            for zone in ("America/New_York", "Asia/Shanghai"):
                result = await statistics(store, 30 * 24, end, zone=zone)
                assert result["timezone"] == zone
                last = datetime.fromisoformat(result["buckets"][-1]["hour"])
                assert last.astimezone(ZoneInfo(zone)).hour == 0
            fallback = await statistics(store, 24, end, zone="Not/AZone")
            assert fallback["timezone"] == "Asia/Shanghai"
        finally:
            await store.close()

    asyncio.run(run())


def test_web_api_uses_the_requested_language(tmp_path):
    async def seed():
        store = Store(tmp_path, EventBus())
        await store.open()
        await store.put("session_locale", "private:qq:7", {"locale": "en"})
        await store.put("session_locale", "web:stale", {"locale": "tlh"})
        await store.close()

    asyncio.run(seed())
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(create_app(tmp_path, token="test-token")) as client:
        english = client.get("/api/session-locales", headers={"X-ShiritaiBot-Locale": "en"})
        assert english.status_code == 401
        assert english.json()["detail"] == "A WebUI admin token is required"
        # Without the WebUI header the instance default applies; the browser is not guessed.
        browser = client.get("/api/session-locales", headers={"Accept-Language": "en-US,en"})
        assert browser.json()["detail"] == "需要 WebUI 管理令牌"
        assert client.get("/api/session-locales").json()["detail"] == "需要 WebUI 管理令牌"

        assert client.get("/api/session-locales", headers=headers).json() == [
            {"session": "private:qq:7", "locale": "en"}
        ]
        response = client.delete(
            "/api/session-locales", params={"session": "private:qq:7"}, headers=headers
        )
        assert response.status_code == 200
        assert client.get("/api/session-locales", headers=headers).json() == []


def test_web_api_defaults_to_the_instance_language(tmp_path):
    (tmp_path / "config.toml").write_text('locale = "en"\n', encoding="utf-8")
    with TestClient(create_app(tmp_path, token="test-token")) as client:
        assert client.get("/api/session-locales").json()["detail"] == (
            "A WebUI admin token is required"
        )
        chinese = client.get("/api/session-locales", headers={"X-ShiritaiBot-Locale": "zh-CN"})
        assert chinese.json()["detail"] == "需要 WebUI 管理令牌"


def test_logs_use_the_instance_language(caplog):
    from shiritaibot.core.logs import get_logger
    from shiritaibot.i18n import set_instance_locale

    log = get_logger("chats.log.source")
    set_instance_locale("en")
    with use_locale("zh-CN"), caplog.at_level("INFO", logger="shiritaibot"):
        # A Chinese chat's /lang never leaks into the log file.
        log.log(Text("app.log.starting", home="/data"))
    assert caplog.records[-1].getMessage() == "Starting with data folder /data"
    assert caplog.records[-1].source == "Activity"


def test_tool_descriptions_follow_the_session():
    from shiritaibot.core import Tool

    async def handler(ctx, args):
        return None

    tool = Tool(
        "current_time",
        "tools.current_time.description",
        {
            "type": "object",
            "properties": {"x": {"type": "string", "description": "tools.qq.param.target"}},
        },
        handler,
        risk="safe",
    )
    with use_locale("zh-CN"):
        spec = tool.spec()["function"]
        assert spec["description"] == "查询当前时间与时区"
        assert spec["parameters"]["properties"]["x"]["description"] == "私聊为 QQ 号，群聊为群号"
    with use_locale("en"):
        assert tool.spec()["function"]["description"] == "Get the current time and time zone"
    # Plugin tools may still pass plain text.
    tool.description = "自定义说明"
    with use_locale("en"):
        assert tool.spec()["function"]["description"] == "自定义说明"


def test_migrated_prompts_render_like_before():
    # Chinese prompts moved into prompts/zh.py must send the exact bytes they used to.
    assert prompt("APPROVAL_REVIEW", "zh-CN").endswith(
        '仅输出 JSON：{"allow":true或false,"reason":"具体原因"}。'
    )
    assert prompt("SYSTEM_NOTE", "zh-CN", note="x") == "[系统备注] x"
    assert prompt("SYSTEM_NOTE", "en", note="x") == "[System note] x"
    observe = prompt("GROUP_OBSERVE", "zh-CN", speaker="甲", brief="你好")
    assert observe.startswith("[群聊观察] 群里刚有人发言，这条没有 @ 你。\n最新一条来自 甲：你好")


def test_plugins_translate_their_own_text(tmp_path):
    from shiritaibot.i18n import localize, plugin_text
    from shiritaibot.plugin.core.manifest import read_manifest

    (tmp_path / "locales").mkdir()
    (tmp_path / "locales" / "zh-CN.json").write_text('{"hello": "你好，{name}"}', encoding="utf-8")
    (tmp_path / "locales" / "en.json").write_text('{"hello": "Hello, {name}"}', encoding="utf-8")
    with use_locale("en"):
        assert plugin_text(tmp_path, "hello", name="Sam") == "Hello, Sam"
        assert plugin_text(tmp_path, "missing") == "missing"
    with use_locale("zh-CN"):
        assert plugin_text(tmp_path, "hello", name="小明") == "你好，小明"

    email = read_manifest(ROOT / "src" / "shiritaibot" / "plugin" / "email")
    # The stored default stays plain Chinese text for validators and older code.
    assert email["description"].startswith("IMAP 收件")
    with use_locale("en"):
        shown = localize(email)
        assert shown["description"].startswith("Incoming IMAP mail")
        field = shown["settings_schema"]["properties"]["imap_security"]
        assert field["title"] == "IMAP encryption"
        assert field["x-labels"][-1] == "None (local only)"
        assert "x-l10n" not in field


def test_control_words_work_in_english():
    from shiritaibot.adapters.onebot11 import ALLOW_WORDS, STOP_WORDS, approval_reply

    assert approval_reply("approve")[1].lower() in ALLOW_WORDS
    assert approval_reply("Deny 0123456789")[2] == "0123456789"
    assert approval_reply("同意") and not approval_reply("maybe")
    assert {"stop", "cancel", "停", "取消"} <= STOP_WORDS


def test_english_words_match_their_other_forms():
    import sqlite3

    from shiritaibot.knowledge.core import index_tokens
    from shiritaibot.memory.longterm import keywords, stem, tokenize_sync

    assert {stem(w) for w in ("meetings", "meeting", "meet")} == {"meet"}
    assert stem("running") == "run" and stem("studies") == "study" and stem("boxes") == "box"
    assert stem("status") == "status" and stem("会议") == "会议"
    # Chinese tokens are unchanged; English ones gain their stems.
    assert tokenize_sync("明天开会") == list(__import__("jieba").cut_for_search("明天开会"))

    db = sqlite3.connect(":memory:")
    db.execute("CREATE VIRTUAL TABLE fts USING fts5(tokens)")
    db.execute(
        "INSERT INTO fts(rowid, tokens) VALUES (1, ?)", (index_tokens("Weekly meetings agenda"),)
    )
    db.execute("INSERT INTO fts(rowid, tokens) VALUES (2, ?)", (index_tokens("周会议程"),))

    def search(query):
        words = keywords(tokenize_sync(query))
        match = " OR ".join('"' + w + '"' for w in words)
        return [row[0] for row in db.execute("SELECT rowid FROM fts WHERE fts MATCH ?", (match,))]

    assert search("meeting") == [1]
    assert search("Meet") == [1]
    assert search("周会") == [2]


def test_skills_read_their_language_variant(tmp_path):
    from shiritaibot.skill import localized

    skill = tmp_path / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: 中文\n---\n", encoding="utf-8")
    assert localized(skill / "SKILL.md") == skill / "SKILL.md"
    (skill / "SKILL.en.md").write_text(
        "---\nname: demo\ndescription: English\n---\n", encoding="utf-8"
    )
    with use_locale("en"):
        assert localized(skill / "SKILL.md") == skill / "SKILL.en.md"
    with use_locale("zh-CN"):
        assert localized(skill / "SKILL.md") == skill / "SKILL.md"
