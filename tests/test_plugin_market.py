import asyncio
import io
import json
import zipfile
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from shiritaibot.config import OFFICIAL_PLUGIN_MARKET, PluginMarketConfig
from shiritaibot.core import PublicError
from shiritaibot.dependencies import RuntimeEnvironment
from shiritaibot.plugin.core import market as market_module
from shiritaibot.plugin.core.market import MarketIndexError, PluginMarket, validate_index
from shiritaibot.plugin.core.packages import download_github
from shiritaibot.web import create_app

SHA = "3" * 40
REPO = "https://github.com/octo-author/srtb_plugin_weather"


def entry(**changes):
    base = {
        "id": "weather",
        "name": {"zh-CN": "天气查询", "en": "Weather"},
        "description": {"zh-CN": "查询城市天气。", "en": "Looks up the weather."},
        "short_description": "",
        "author": "octo-author",
        "owner": "octo-author",
        "repo": REPO,
        "version": "1.0.0",
        "tag": "v1.0.0",
        "commit": SHA,
        "api": 2,
        "tags": ["weather"],
        "support_platforms": ["qq", "web"],
        "shiritaibot_version": "",
        "updated_at": "2026-11-25T04:58:25Z",
        "disclosures": [
            {
                "kind": "data_sharing",
                "text": {"zh-CN": "城市名会发送到 wttr.in。", "en": "The city name is sent to wttr.in."},
                "location": "main.py:16",
            }
        ],
        "page": "https://plugin.shiritaibot.dev/plugins/weather",
    }
    return {**base, **changes}


def index(*entries):
    return {
        "format": "shiritaibot-plugin-index",
        "version": 1,
        "generated_at": "2026-11-25T08:00:00Z",
        "store": "https://plugin.shiritaibot.dev",
        "plugins": list(entries),
    }


def plugin_zip():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        top = "octo-author-srtb_plugin_weather-3333333/"
        archive.writestr(
            top + "metadata.yaml",
            "display_name: {zh-CN: 天气查询, en: Weather}\nversion: 1.0.0\nauthor: octo-author\n",
        )
        archive.writestr(top + "main.py", "from shiritaibot.api import Plugin\n\nclass Weather(Plugin):\n    pass\n")
    return data.getvalue()


class FakeWeb:
    """Serves plugin indexes and GitHub zipballs through httpx's MockTransport."""

    def __init__(self):
        self.indexes: dict[str, object] = {}
        self.zipballs: dict[str, bytes] = {}
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url in self.indexes:
            body = self.indexes[url]
            if isinstance(body, int):
                return httpx.Response(body)
            if isinstance(body, bytes):
                return httpx.Response(200, content=body)
            return httpx.Response(200, json=body)
        if url in self.zipballs:
            return httpx.Response(200, content=self.zipballs[url])
        raise httpx.ConnectError("unreachable", request=request)

    def install(self, monkeypatch):
        real = httpx.AsyncClient
        monkeypatch.setattr(
            httpx,
            "AsyncClient",
            lambda **kwargs: real(transport=httpx.MockTransport(self.handler), **kwargs),
        )


def test_index_validation_keeps_good_entries_and_drops_bad_ones():
    entries, dropped = validate_index(
        index(
            entry(),
            entry(id="Bad-ID"),
            entry(id="core"),
            entry(id="other_host", repo="https://gitlab.com/octo/x"),
            entry(id="branch", commit="main"),
            entry(id="platform", support_platforms=["wechat"]),
            entry(id="no_text", disclosures=[{"kind": "telemetry", "text": ""}]),
            entry(id="weather"),  # a duplicate ID
            entry(id="future", disclosures=[{"kind": "brand_new", "text": "x"}], extra_field=True),
            entry(id="old_framework", shiritaibot_version="<0.0.1"),
            "not an object",
        )
    )
    assert [item["id"] for item in entries] == ["weather", "future", "old_framework"]
    assert dropped == 8
    assert entries[0]["name"] == {"zh-CN": "天气查询", "en": "Weather"}
    assert entries[0]["compatible"] is True
    # A disclosure kind from a later index version is shown as "other".
    assert entries[1]["disclosures"][0]["kind"] == "other"
    assert entries[2]["compatible"] is False
    for document, code in [
        ({"format": "something-else", "version": 1, "plugins": []}, "format"),
        ({**index(), "version": 2}, "version"),
        ([], "format"),
    ]:
        with pytest.raises(MarketIndexError) as raised:
            validate_index(document)
        assert raised.value.args[0] == code


def test_sources_are_cached_merged_and_fall_back_to_the_last_good_copy(tmp_path, monkeypatch):
    web = FakeWeb()
    web.install(monkeypatch)
    first = "https://plugin.shiritaibot.dev/api/v1/plugins.json"
    second = "https://mirror.example/plugins.json"
    web.indexes[first] = index(entry())
    web.indexes[second] = index(entry(name="Mirror copy"), entry(id="calendar", name="Calendar"))

    async def run():
        market = PluginMarket(tmp_path)
        listing = await market.listing([first, second])
        assert [(item["id"], item["source"]) for item in listing["plugins"]] == [
            ("weather", first),
            ("calendar", second),
        ]
        assert listing["plugins"][0]["name"] == {"zh-CN": "天气查询", "en": "Weather"}
        assert [source["error"] for source in listing["sources"]] == ["", ""]

        # Fresh copies are reused for a while; refresh fetches again.
        count = len(web.requests)
        await market.listing([first, second])
        assert len(web.requests) == count
        web.indexes[first] = 500
        failed = await market.listing([first, second], refresh=True)
        assert failed["sources"][0]["stale"] is True
        assert failed["sources"][0]["error"] == "http"
        assert [item["id"] for item in failed["plugins"]] == ["weather", "calendar"]

        # A failed source isn't asked again right away, unless refresh is pressed.
        count = len(web.requests)
        await market.listing([first, second])
        assert len(web.requests) == count

        # A restarted bot falls back to the copy on disk.
        restarted = PluginMarket(tmp_path)
        web.indexes.pop(first)
        offline = await restarted.listing([first])
        assert offline["sources"][0]["error"] == "network"
        assert offline["sources"][0]["fetched_at"]
        assert [item["id"] for item in offline["plugins"]] == ["weather"]

        # No cache and no connection: nothing listed, and the reason is reported.
        missing = await restarted.listing(["https://down.example/plugins.json"])
        assert missing["plugins"] == []
        assert missing["sources"][0] == {
            "url": "https://down.example/plugins.json",
            "count": 0,
            "dropped": 0,
            "fetched_at": "",
            "stale": True,
            "error": "network",
        }

    asyncio.run(run())


def test_oversized_and_unknown_indexes_are_refused(tmp_path, monkeypatch):
    web = FakeWeb()
    web.install(monkeypatch)
    web.indexes["https://big.example/i.json"] = b" " * (market_module.MAX_INDEX + 1)
    web.indexes["https://new.example/i.json"] = {**index(entry()), "version": 2}
    web.indexes["https://html.example/i.json"] = b"<html>"

    async def run():
        listing = await PluginMarket(tmp_path).listing(
            ["https://big.example/i.json", "https://new.example/i.json", "https://html.example/i.json"]
        )
        assert [source["error"] for source in listing["sources"]] == ["index_size", "index_version", "json"]
        assert listing["plugins"] == []

    asyncio.run(run())


def test_download_github_pins_a_commit_and_only_talks_to_github(monkeypatch):
    web = FakeWeb()
    web.install(monkeypatch)
    web.zipballs[f"https://api.github.com/repos/octo-author/srtb_plugin_weather/zipball/{SHA}"] = b"zip"
    web.zipballs["https://api.github.com/repos/octo-author/srtb_plugin_weather/zipball"] = b"default"
    assert asyncio.run(download_github(REPO, SHA)) == b"zip"
    assert asyncio.run(download_github(REPO + ".git")) == b"default"
    for url, commit in [
        (REPO, "main"),
        (REPO, "A" * 40),
        ("https://evil.example/octo/x", SHA),
        ("https://github.com/octo/x/archive/main.zip", None),
    ]:
        with pytest.raises(PublicError):
            asyncio.run(download_github(url, commit))


def test_market_sources_config_rejects_plain_http_and_too_many_sources():
    assert PluginMarketConfig().sources == [OFFICIAL_PLUGIN_MARKET]
    config = PluginMarketConfig(sources=[" https://a.example/i.json ", "https://a.example/i.json", "http://127.0.0.1:8790/api/v1/plugins.json"])
    assert config.sources == ["https://a.example/i.json", "http://127.0.0.1:8790/api/v1/plugins.json"]
    with pytest.raises(ValueError):
        PluginMarketConfig(sources=["http://plugins.example/i.json"])
    with pytest.raises(ValueError):
        PluginMarketConfig(sources=["file:///etc/passwd"])
    with pytest.raises(ValueError):
        PluginMarketConfig(sources=[f"https://s{n}.example/i.json" for n in range(11)])


def test_webui_lists_the_market_and_installs_the_pinned_commit(tmp_path, monkeypatch):
    web = FakeWeb()
    web.install(monkeypatch)
    web.indexes[OFFICIAL_PLUGIN_MARKET] = index(entry())
    web.zipballs[f"https://api.github.com/repos/octo-author/srtb_plugin_weather/zipball/{SHA}"] = plugin_zip()
    monkeypatch.setattr(RuntimeEnvironment, "prepare", AsyncMock())
    headers = {"Authorization": "Bearer local-test"}
    with TestClient(create_app(tmp_path, token="local-test")) as client:
        assert client.get("/api/plugins/market").status_code == 401
        listing = client.get("/api/plugins/market", headers=headers).json()
        assert listing["sources"][0]["url"] == OFFICIAL_PLUGIN_MARKET
        plugin = listing["plugins"][0]
        assert (plugin["id"], plugin["repo"], plugin["commit"]) == ("weather", REPO, SHA)
        assert plugin["disclosures"][0]["text"]["en"] == "The city name is sent to wttr.in."

        bad = client.post(
            "/api/plugins/install/github", headers=headers, json={"id": "weather", "url": REPO, "commit": "v1.0.0"}
        )
        assert bad.status_code == 422
        installed = client.post(
            "/api/plugins/install/github",
            headers=headers,
            json={"id": plugin["id"], "url": plugin["repo"], "commit": plugin["commit"]},
        )
        assert installed.status_code == 200, installed.text
        assert any(item["id"] == "weather" for item in installed.json())
        assert (tmp_path / "plugins" / "weather" / "main.py").is_file()
        assert web.requests[-1] == f"https://api.github.com/repos/octo-author/srtb_plugin_weather/zipball/{SHA}"
        rows = client.get("/api/plugins", headers=headers).json()
        assert next(item for item in rows if item["id"] == "weather")["version"] == "1.0.0"


def test_source_errors_follow_the_request_language(tmp_path, monkeypatch):
    web = FakeWeb()
    web.install(monkeypatch)
    monkeypatch.setattr(RuntimeEnvironment, "prepare", AsyncMock())
    headers = {"Authorization": "Bearer local-test"}
    with TestClient(create_app(tmp_path, token="local-test")) as client:
        chinese = client.get("/api/plugins/market", headers=headers).json()
        english = client.get(
            "/api/plugins/market?refresh=true", headers={**headers, "X-ShiritaiBot-Locale": "en"}
        ).json()
    assert chinese["sources"][0]["error"] == "无法连接"
    assert english["sources"][0]["error"] == "Couldn't connect"
    assert "plugins.market" not in json.dumps(english)


def test_install_errors_are_shown_in_the_readers_language(tmp_path, monkeypatch):
    """The "restart needed" note was rendered once, in the installer's language."""
    web = FakeWeb()
    web.install(monkeypatch)
    web.zipballs[f"https://api.github.com/repos/octo-author/srtb_plugin_weather/zipball/{SHA}"] = plugin_zip()

    class Plan:
        def supports(self, key, state):
            return False

    monkeypatch.setattr(RuntimeEnvironment, "prepare", AsyncMock(return_value=Plan()))
    headers = {"Authorization": "Bearer local-test"}
    with TestClient(create_app(tmp_path, token="local-test")) as client:
        body = {"id": "weather", "url": REPO, "commit": SHA}
        installed = client.post("/api/plugins/install/github", headers=headers, json=body)
        assert installed.status_code == 200
        chinese = next(item for item in installed.json() if item["id"] == "weather")
        english = next(
            item
            for item in client.get("/api/plugins", headers={**headers, "X-ShiritaiBot-Locale": "en"}).json()
            if item["id"] == "weather"
        )
    assert chinese["error"] == "插件依赖已准备完成，请重启 ShiritaiBot 后启用"
    assert english["status"] == "error"
    assert "restart" in english["error"].lower()
    assert not any("一" <= char <= "鿿" for char in english["error"])
