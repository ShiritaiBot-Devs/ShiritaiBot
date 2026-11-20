import asyncio
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from shiritaibot.core import EventBus
from shiritaibot.plugin.core import PluginManager
from shiritaibot.store import Store
from shiritaibot.tools.shell_diff import file_diffs, snapshot
from shiritaibot.web import create_app


def test_legacy_chats_have_titles_and_independent_persistent_actions(tmp_path, monkeypatch):
    async def seed():
        store = Store(tmp_path, EventBus())
        await store.open()
        try:
            await store.append(
                "web:123", "old", {"role": "user", "content": "first line\n<context>hidden"}
            )
            await store.append("private:qq:9", "qq", {"role": "user", "content": "private"})
        finally:
            await store.close()

    asyncio.run(seed())
    monkeypatch.setattr(PluginManager, "start", AsyncMock())
    auth = {"Authorization": "Bearer token"}
    with TestClient(create_app(tmp_path, token="token"), headers=auth) as client:

        def listing():
            return {row["id"]: row for row in client.get("/api/web-chats").json()}

        assert listing()["web:123"]["title"] == "first line"
        url = "/api/web-chats/web:123"
        assert client.put(url, json={"title": " renamed "}).is_success
        assert client.put(url, json={"pinned": True}).is_success
        assert listing()["web:123"] == {"id": "web:123", "title": "renamed", "pinned": True}
        assert client.delete("/api/web-chats/private:qq:9").status_code == 422
        assert client.delete(url).is_success
        assert client.delete(url).is_success
        assert client.put(url, json={"pinned": True}).is_error
        assert "web:123" not in listing()
        assert client.get("/api/history?session=web:123").json() == []
        assert client.get("/api/history?session=private:qq:9").json()[0]["content"] == "private"
        assert client.delete("/api/web-chats/web:owner").is_success
    with TestClient(create_app(tmp_path, token="token"), headers=auth) as client:
        assert client.get("/api/web-chats").json() == []


def test_shell_diff_uses_observed_text_and_redacts_secrets(tmp_path):
    (tmp_path / "before.txt").write_text("keep\nold\n", encoding="utf8")
    (tmp_path / "removed.txt").write_text("bye", encoding="utf8")
    before = snapshot(tmp_path)
    (tmp_path / "before.txt").write_text("keep\nnew\n", encoding="utf8")
    (tmp_path / "removed.txt").unlink()
    (tmp_path / "created.txt").write_text("sample-secret", encoding="utf8")
    (tmp_path / "binary").write_bytes(b"\x00bytes")
    changes = {
        row["path"]: row for row in file_diffs(before, snapshot(tmp_path), ("sample-secret",))
    }
    assert set(changes) == {"before.txt", "removed.txt", "created.txt"}
    assert changes["before.txt"]["added"] == changes["before.txt"]["removed"] == 1
    assert " keep\n-old\n+new" in changes["before.txt"]["patch"]
    assert "sample-secret" not in changes["created.txt"]["patch"]
    assert changes["removed.txt"]["removed"] == 1


def test_shell_presentation_is_web_only_and_removed_with_the_chat(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        try:
            session = "web:shell"
            await store.append(
                session,
                "turn",
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "call", "function": {"name": "shell", "arguments": "{}"}}
                    ],
                },
            )
            result = {
                "role": "tool",
                "tool_call_id": "call",
                "content": '{"artifact":"large","truncated":true}',
            }
            await store.append(session, "turn", result)
            await store.put(
                "artifact",
                "large",
                {"content": '{"exit_code":2,"stdout":"out","stderr":"err"}'},
                session,
            )
            await store.put("tool_diff", "turn:call", {"files": [{"path": "example.py"}]}, session)
            assert (await store.history(session))[-1] == result
            timeline = await store.history(session, timeline=True)
            assert timeline[-1]["_shell_output"]["exit_code"] == 2
            assert timeline[-1]["_file_diffs"] == [{"path": "example.py"}]
            await store.delete_web_chat(session)
            await store.append(
                session, "turn", {"role": "assistant", "content": "late"}, require_turn=True
            )
            assert await store.history(session) == []
            assert await store.get("artifact", "large") is None
            assert await store.get("tool_diff", "turn:call") is None
        finally:
            await store.close()

    asyncio.run(run())
