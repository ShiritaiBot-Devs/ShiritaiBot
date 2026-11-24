import asyncio
import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shiritaibot.adapters import onebot11
from shiritaibot.adapters.onebot11 import OneBot
from shiritaibot.core import PublicError, session_dir
from shiritaibot.tools import qq


def test_download_napcat_file_from_base64_without_a_usable_url(tmp_path, monkeypatch):
    session = "private:qq:42"
    payload = b"test QQ attachment\n" * 75000
    encoded = base64.b64encode(payload).decode("ascii")
    config = SimpleNamespace(id="qq", self_id="", token="secret:qq", url="ws://127.0.0.1:3001")
    vault = SimpleNamespace(resolve=AsyncMock(return_value="fixture-token"))
    bot = OneBot(config, vault, None, None)
    bot.call = AsyncMock(return_value={"url": "http://192.0.2.1:80/attachment"})
    original_socket = bot.ws = object()
    row = {
        "scope": session,
        "adapter": "qq",
        "group_id": "",
        "data": {
            "file_id": "platform-id",
            "file": "attachment.csv",
            "file_size": str(len(payload)),
        },
    }
    app = SimpleNamespace(
        home=tmp_path,
        store=SimpleNamespace(get=AsyncMock(return_value=row)),
        adapters=SimpleNamespace(instances={"qq": bot}),
    )

    class DownloadSocket:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def send(self, value):
            self.request = json.loads(value)

        async def recv(self):
            return json.dumps(
                {
                    "echo": self.request["echo"],
                    "status": "ok",
                    "retcode": 0,
                    "data": {
                        "url": "",
                        "file": "/napcat/temp/cache.csv",
                        "file_size": len(payload),
                        "base64": encoded,
                    },
                }
            )

    socket = DownloadSocket()

    def connect(url, *, additional_headers, max_size, max_queue):
        assert url == config.url
        assert additional_headers == {"Authorization": "Bearer fixture-token"}
        assert max_size > len(encoded) > 2**20
        return socket

    monkeypatch.setattr(onebot11, "connect", connect)

    async def run():
        with pytest.raises(PublicError, match="不属于当前会话"):
            await qq.download_qq_file(app, "private:qq:99", "ref")
        bot.call.assert_not_awaited()
        return await qq.download_qq_file(app, session, "ref")

    result = asyncio.run(run())
    bot.call.assert_awaited_once_with("get_private_file_url", {"file_id": "platform-id"})
    assert socket.request["action"] == "get_file"
    assert socket.request["params"] == {"file_id": "platform-id"}
    assert bot.ws is original_socket
    assert result["bytes"] == len(payload)
    assert result["sha256"] == hashlib.sha256(payload).hexdigest()
    assert result["name"] == "attachment.csv"
    assert Path(result["path"]).read_bytes() == payload
    downloads = session_dir(tmp_path, "workspaces", session) / "downloads"
    assert Path(result["path"]).is_relative_to(downloads)
    assert list(downloads.glob("*.part")) == []
    assert not session_dir(tmp_path, "files", session).exists()


def test_download_source_keeps_https_and_rejects_path_only_or_invalid_data(monkeypatch):
    bot = OneBot(SimpleNamespace(id="qq", self_id=""), None, None, None)
    bot.call = AsyncMock(return_value={"url": "https://f.qq.com/file"})
    bot.get_file = AsyncMock(return_value={"file": "/napcat/temp/cache.txt", "url": ""})

    async def run():
        assert (
            await qq.qq_file_source(bot, "42", {"file_id": "remote", "busid": 102})
            == "https://f.qq.com/file"
        )
        bot.call.assert_awaited_once_with(
            "get_group_file_url", {"group_id": 42, "file_id": "remote", "busid": 102}
        )
        bot.get_file.assert_not_awaited()
        bot.call.return_value = {"url": "http://192.0.2.1:80/attachment"}
        with pytest.raises(PublicError, match="未返回可用"):
            await qq.qq_file_source(bot, "", {"file_id": "remote", "file": "/etc/passwd"})
        bot.get_file.return_value = {"base64": "not-base64!"}
        with pytest.raises(PublicError, match="不是有效 Base64"):
            await qq.qq_file_source(bot, "", {"file_id": "remote"})
        monkeypatch.setattr(qq, "MAX_DOWNLOAD", 5)
        bot.get_file.return_value = {"base64": base64.b64encode(b"123456").decode("ascii")}
        with pytest.raises(PublicError, match="超过 50 MiB"):
            await qq.qq_file_source(bot, "", {"file_id": "remote"})

    asyncio.run(run())
