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


def test_catalogs_match_across_locales():
    for name, load in (("backend", catalog), ("web", web_catalog)):
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
