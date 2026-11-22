import asyncio
import base64
import json
from io import BytesIO
from pathlib import Path
from shutil import copytree
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from shiritaibot.adapters.feishu_transport import FeishuTransport
from shiritaibot.adapters.onebot11 import AdapterHub
from shiritaibot.adapters.qq_official import QQOfficial
from shiritaibot.agent import help_menu
from shiritaibot.config import Settings
from shiritaibot.dependencies import RuntimeEnvironment
from shiritaibot.web import create_app


def png_data() -> bytes:
    output = BytesIO()
    Image.new("RGB", (3, 3)).save(output, "PNG")
    return output.getvalue()


def test_help_image_is_rendered_and_saved_without_system_fonts(tmp_path, monkeypatch):
    monkeypatch.setattr(help_menu, "system_cjk_font", lambda: None)
    monkeypatch.setattr(httpx, "stream", Mock(side_effect=AssertionError("unexpected download")))
    monkeypatch.setattr(RuntimeEnvironment, "prepare", AsyncMock())
    (tmp_path / "config.toml").write_text(
        'help_menu_style = "dark"\n[policy.sessions."web:help-test".tools]\nmemory_search = "deny"\n',
        encoding="utf8",
    )
    app = create_app(tmp_path, token="local-test")
    headers = {"Authorization": "Bearer local-test"}
    session = "web:help-test"
    with TestClient(app) as client:
        listed = client.get("/api/commands", params={"session": session}, headers=headers)
        assert listed.status_code == 200
        assert "memory" not in {item["name"] for item in listed.json()}
        sent = client.post(
            "/api/messages", headers=headers, json={"session": session, "text": "/help"}
        )
        assert sent.json() == {"status": "command"}
        history = client.get("/api/history", params={"session": session}, headers=headers).json()
        assert "/memory" not in history[-1]["content"]
        path = f"/api/help-images/{history[-1]['_help_image']}"
        assert client.get(path).status_code == 401
        response = client.get(path, headers=headers)
        assert response.headers["content-type"] == "image/png"
        with Image.open(BytesIO(response.content)) as image:
            assert image.format == "PNG" and image.width == 1120
            assert image.getpixel((0, 0)) == (20, 18, 29)
        assert client.delete(f"/api/web-chats/{session}", headers=headers).status_code == 200
        assert client.get(path, headers=headers).status_code == 404


def test_help_menu_layout_is_shared_by_text_and_image(tmp_path, monkeypatch):
    example = Path(__file__).resolve().parents[1] / "examples" / "plugins" / "hello_world"
    copytree(example, tmp_path / "plugins" / "hello_world")
    (tmp_path / "config.toml").write_text(
        '[[plugins]]\nid = "hello_world"\nenabled = true\n', encoding="utf8"
    )
    plan = SimpleNamespace(supports=lambda *_: True)
    monkeypatch.setattr(RuntimeEnvironment, "prepare", AsyncMock(return_value=plan))
    render = Mock(return_value=png_data())
    monkeypatch.setattr("shiritaibot.agent.commands.render_help_menu", render)
    headers = {"Authorization": "Bearer local-test"}
    with TestClient(create_app(tmp_path, token="local-test")) as client:
        catalog = client.get("/api/help-menu/sections", headers=headers).json()
        assert {entry["id"] for entry in catalog} == {
            "builtin:session",
            "builtin:models",
            "builtin:memory",
            "builtin:tools",
            "plugin:hello_world",
        }
        config = client.get("/api/config", headers=headers).json()
        config["help_menu_sections"] = [
            {"id": "plugin:hello_world", "visible": True},
            {"id": "plugin:temporarily-unavailable", "visible": False},
            {"id": "builtin:models", "visible": False},
        ]

        def menu():
            response = client.post("/api/messages", headers=headers, json={"text": "/help"})
            assert response.json() == {"status": "command"}
            return client.get("/api/history", headers=headers).json()[-1]

        assert client.put("/api/config", headers=headers, json=config).status_code == 200
        text = menu()["content"]
        assert text.index("插件 · hello_world") < text.index("\n会话\n")
        assert "/models" not in text and "/permissions" in text

        config["help_menu_style"] = "dark"
        assert client.put("/api/config", headers=headers, json=config).status_code == 200
        image = menu()
        assert image["content"] == text and image["_help_image"]
        sections = render.call_args.args[1]
        assert sections[0][0] == "插件 · hello_world"
        assert "模型与人格" not in [title for title, _ in sections]

        config["help_menu_sections"][0]["visible"] = False
        assert client.put("/api/config", headers=headers, json=config).status_code == 200
        assert "/hello" not in menu()["content"]
        assert "hello" in {
            item["name"] for item in client.get("/api/commands", headers=headers).json()
        }


def test_onebot_help_image_takes_precedence_over_folding():
    async def run():
        settings = Settings(onebot_forward_long_messages=True, onebot_forward_threshold=1)
        store = SimpleNamespace(put=AsyncMock())
        hub = AdapterHub(SimpleNamespace(value=settings), None, store)
        png = png_data()
        await hub.enqueue("private:qq:123", "菜单正文", image=png)
        store.put.assert_awaited_once()
        row = store.put.await_args.args[2]
        assert "forward" not in row
        message = await hub.outgoing(row)
        assert message is not None and message[0]["type"] == "image"
        assert base64.b64decode(message[0]["data"]["file"].removeprefix("base64://")) == png

    asyncio.run(run())


def test_feishu_uploads_image_then_replies_with_key():
    async def run():
        transport = FeishuTransport("app", "", "feishu", AsyncMock())
        channel = Mock()
        channel.connection_snapshot.return_value = SimpleNamespace(ready=True, state="connected")
        channel.upload_media = AsyncMock(return_value="image_key")
        channel.send = AsyncMock(return_value=SimpleNamespace(success=True, message_id="om_id"))
        transport._channel = channel
        png = png_data()
        assert await transport.send_image(png, "oc_group", "chat_id", "request") == {
            "message_id": "om_id"
        }
        assert channel.upload_media.await_args.args[0].buffer == png
        assert channel.upload_media.await_args.kwargs["kind"] == "image"
        recipient, message, options = channel.send.await_args.args
        assert recipient == "oc_group" and message["image"]["source"].key == "image_key"
        assert options == {"receive_id_type": "chat_id", "uuid": "request"}

    asyncio.run(run())


def test_qq_official_uploads_image_without_using_an_extra_reply():
    async def run():
        bot = QQOfficial(SimpleNamespace(app_id="app", qq_sandbox=False), None, AsyncMock(), Mock())
        bot.access_token = AsyncMock(return_value="test")
        png = base64.b64encode(png_data()).decode("ascii")
        seen = []

        def respond(request):
            seen.append((request.url.path, json.loads(request.content)))
            if request.url.path.endswith("/files"):
                return httpx.Response(200, json={"file_info": "image_info"})
            return httpx.Response(200, json={"id": "receipt"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            bot.client = client
            assert await bot.send_image("group", "chat", png, "msg", 2) == {"message_id": "receipt"}

        assert seen == [
            ("/v2/groups/chat/files", {"file_type": 1, "file_data": png, "srv_send_msg": False}),
            (
                "/v2/groups/chat/messages",
                {
                    "msg_type": 7,
                    "media": {"file_info": "image_info"},
                    "msg_id": "msg",
                    "msg_seq": 2,
                },
            ),
        ]

    asyncio.run(run())
