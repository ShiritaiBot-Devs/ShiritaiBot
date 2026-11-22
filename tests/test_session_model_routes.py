import time
import tomllib
from collections import defaultdict
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from shiritaibot.config import Settings
from shiritaibot.dependencies import RuntimeEnvironment
from shiritaibot.web import create_app


@pytest.mark.parametrize(
    ("session", "overrides", "error"),
    [
        ("background:proactive:x", {"chat": ["global"]}, "Main"),
        ("web:owner", {"embedding": ["global"]}, "无法按会话"),
        ("web:owner", {"chat": ["missing"]}, "不存在的模型"),
        ("web:owner", {"vision": ["global"]}, "必须支持图像"),
    ],
)
def test_session_route_validation(session, overrides, error):
    with pytest.raises(ValidationError, match=error):
        Settings.model_validate(
            {
                "providers": [{"id": "local", "base_url": "https://provider.example.test"}],
                "models": [{"id": "global", "provider": "local", "model": "global"}],
                "session_routes": {session: overrides},
            }
        )


def test_saved_session_routes_are_used_for_replies_and_can_be_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(RuntimeEnvironment, "prepare", AsyncMock())
    app = create_app(tmp_path, token="session-route-test")
    requests = defaultdict(list)

    async def generate(_provider, _path, body, *_):
        message = body["messages"][-1]["content"].split("\n", 1)[0]
        model = body["model"]
        requests[message].append(model)
        if model == "unavailable":
            raise ValueError("fixture failure")
        return httpx.Response(200, json={"choices": [{"message": {"content": model}}]})

    with TestClient(app, headers={"Authorization": "Bearer session-route-test"}) as client:
        app.state.runtime.llm._generate = generate
        original = client.get("/api/config")
        assert original.json()["session_routes"] == {}
        provider = {"id": "local", "base_url": "https://provider.example.test"}
        config = {
            "providers": [provider],
            "models": [
                {"id": name, "provider": "local", "model": name}
                for name in ("global", "unavailable", "session")
            ],
            "routes": {"chat": ["global"]},
            "session_routes": {
                "web:custom": {"chat": ["unavailable", "session"], "agent": []},
            },
            "policy": {"owner_default": {"mode": "chat"}},
            "memory": {"inject": False, "extract": False},
            "emotion": {"enabled": False},
        }
        response = client.put(
            "/api/config",
            json=config,
            headers={"X-Config-Revision": original.headers["X-Config-Revision"]},
        )
        assert response.status_code == 200, response.text
        saved = client.get("/api/config").json()
        overrides = {"web:custom": {"chat": ["unavailable", "session"]}}
        assert saved["session_routes"] == overrides
        assert (
            tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))["session_routes"]
            == overrides
        )

        def send(session, message):
            response = client.post("/api/messages", json={"session": session, "text": message})
            assert response.status_code == 200, response.text

        def reply(session, message, text):
            for _ in range(100):
                history = client.get("/api/history", params={"session": session}).json()
                if (
                    len(history) >= 2
                    and history[-2]["role"] == "user"
                    and history[-2]["content"].split("\n", 1)[0] == message
                    and history[-1]["role"] == "assistant"
                ):
                    assert history[-1]["content"] == text
                    return
                time.sleep(0.02)
            pytest.fail(f"No reply in {session}")

        send("web:custom", "custom")
        send("web:other", "other")
        reply("web:custom", "custom", "session")
        reply("web:other", "other", "global")
        assert dict(requests) == {"custom": ["unavailable", "session"], "other": ["global"]}

        saved["session_routes"] = {}
        response = client.put("/api/config", json=saved)
        assert response.status_code == 200, response.text
        send("web:custom", "reset")
        reply("web:custom", "reset", "global")
        assert requests["reset"] == ["global"]
