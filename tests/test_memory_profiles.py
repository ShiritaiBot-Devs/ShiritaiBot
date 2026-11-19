import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from shiritaibot.config import AdapterConfig, MemoryConfig, MemoryProfile, Settings
from shiritaibot.core import EventBus, now
from shiritaibot.memory.extract import MemoryExtractor
from shiritaibot.plugin.core import PluginManager
from shiritaibot.store import Store
from shiritaibot.web import create_app


def test_profile_binding_distinguishes_private_and_shared_sessions():
    owner = "private:qq:10"
    visitor = "private:qq:11"
    first, second = "group:qq:20", "group:qq:21"
    settings = Settings(
        adapters=[
            AdapterConfig(id="qq", owner_id="10", groups=["20", "21", "22"]),
            AdapterConfig(id="fs", type="feishu", app_id="cli_test", owner_id="ou_owner"),
        ],
        memory=MemoryConfig(
            profiles={
                "personal": MemoryProfile(name="Personal"),
                "club": MemoryProfile(name="Club", audience="shared"),
            },
            sessions={owner: "personal", visitor: "personal", first: "club", second: "club"},
        ),
    )
    assert settings.session_memory(owner) == "personal"
    assert settings.session_memory(visitor) == "default"
    assert settings.session_memory(first) == settings.session_memory(second) == "club"
    assert settings.session_memory("group:qq:22") == "default"
    assert settings.session_memory(settings.adapters[1].owner_session()) == "feishu"
    settings.memory.sessions[first] = "default"
    with pytest.raises(ValueError, match="群聊需绑定"):
        Settings.model_validate(settings.model_dump())


def test_history_context_and_extraction_are_partitioned(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        session = "group:qq:20"
        try:
            await store.append(session, "legacy", {"role": "user", "content": "old"})
            await store.append(
                session, "new", {"role": "user", "content": "new"}, memory_profile="club"
            )
            assert [m["content"] for m in await store.history(session)] == ["old"]
            shared_history = await store.history(session, memory_profile="club")
            assert [m["content"] for m in shared_history] == ["new"]
            await store.record_group(session, "legacy", "10", "old-name", "old", False)
            await store.record_group(
                session, "new", "10", "new-name", "new", False, memory_profile="club"
            )
            assert await store.group_sender_name(session, "10") == "old-name"
            shared = await store.group_messages(session, 5, memory_profile="club")
            assert [m["text"] for m in shared] == ["new"]
            await store.put("turn", "legacy", {"memory_scope": session, "loop": "main"}, session)
            await store.put("turn", "new", {"memory_scope": "shared:club", "loop": "main"}, session)
            memory = AsyncMock()
            memory.zone = now().tzinfo
            memory.rows.return_value = []
            cfg = SimpleNamespace(value=Settings())
            extractor = MemoryExtractor(memory, store, cfg, None, profile="club", shared=True)
            assert await extractor.sessions() == [session]
            line = extractor.chat_line(
                session, {"created": 0, "seq": 1}, {"role": "user", "content": "new"}, {}
            )
            assert line is not None and line.role == "participant"
            assert (
                extractor.chat_line(
                    session, {"created": 0, "seq": 1}, {"role": "tool", "content": "secret"}, {}
                )
                is None
            )
        finally:
            await store.close()

    asyncio.run(run())


def test_web_memory_profile_creation_and_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(PluginManager, "reload_all", AsyncMock(return_value=[]))
    with TestClient(create_app(tmp_path, token="test-auth")) as client:
        headers = {"Authorization": "Bearer test-auth"}
        config = client.get("/api/config", headers=headers).json()
        config["memory"]["profiles"]["club"] = {"name": "Club", "audience": "shared"}
        config["memory"]["sessions"]["group:qq:20"] = "club"
        assert client.put("/api/config", json=config, headers=headers).status_code == 200
        assert client.get("/api/memory?profile=club", headers=headers).status_code == 200
        assert client.get("/api/memory?profile=missing", headers=headers).status_code == 404
        config["memory"]["sessions"].clear()
        config["memory"]["profiles"].clear()
        assert client.put("/api/config", json=config, headers=headers).status_code == 200
        assert client.get("/api/memory?profile=club", headers=headers).status_code == 404
        config["memory"]["profiles"]["club"] = {"name": "Club", "audience": "personal"}
        assert client.put("/api/config", json=config, headers=headers).status_code == 400
        config["memory"]["profiles"]["club"]["audience"] = "shared"
        assert client.put("/api/config", json=config, headers=headers).status_code == 200
    with TestClient(create_app(tmp_path, token="test-auth")) as client:
        profiles = client.get("/api/memory/profiles", headers=headers).json()
        assert {profile["id"] for profile in profiles} == {"default", "club"}
