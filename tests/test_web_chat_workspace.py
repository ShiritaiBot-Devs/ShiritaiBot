import time
from unittest.mock import AsyncMock

import httpx
from fastapi.testclient import TestClient

from shiritaibot.dependencies import RuntimeEnvironment
from shiritaibot.web import create_app


def test_workspace_suggestions_and_attachments(tmp_path, monkeypatch):
    monkeypatch.setattr(RuntimeEnvironment, "prepare", AsyncMock())
    existing = tmp_path / "project"
    existing.mkdir()
    custom = tmp_path / "projet"
    app = create_app(tmp_path / "data", token="test")
    with TestClient(app, headers={"Authorization": "Bearer test"}) as client:
        url = "/api/web-chats/web:owner"
        response = client.put(url + "/workspace", json={"path": str(custom)})
        assert response.json() == {
            "path": str(custom), "missing": True, "suggestions": [str(existing)],
        }
        assert not custom.exists()
        assert client.put(url + "/workspace", json={"path": str(existing)}).json()["path"] == str(existing)
        assert client.put(url + "/workspace", json={"path": str(custom), "create": True}).json()["custom"]
        assert custom.is_dir()
        attachment = client.post(url + "/attachments", params={"name": "测试.txt"}, content=b"test")
        assert attachment.status_code == 200, attachment.text
        info = attachment.json()
        assert next(custom.glob(".shiritaibot-uploads/*/测试.txt")).read_bytes() == b"test"
        body = {"text": "", "attachments": [info["id"]]}
        assert client.post("/api/messages", json={**body, "session": "web:other"}).status_code == 422
        assert client.post("/api/messages", json=body).status_code == 200
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            history = client.get("/api/history").json()
            if history:
                break
            time.sleep(0.05)
        assert "测试.txt" in history[0]["content"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            reset = client.put(url + "/workspace", json={"path": ""})
            if reset.status_code != 409:
                break
            time.sleep(0.05)
        assert reset.json()["custom"] is False


def test_context_reports_provider_usage_for_the_selected_web_session(tmp_path, monkeypatch):
    monkeypatch.setattr(RuntimeEnvironment, "prepare", AsyncMock())
    app = create_app(tmp_path, token="test")
    with TestClient(app, headers={"Authorization": "Bearer test"}) as client:
        config = {
            "providers": [{"id": "local", "base_url": "https://provider.example.test"}],
            "models": [{"id": "chat", "provider": "local", "model": "chat", "max_context": 100000}],
            "routes": {"chat": ["chat"]},
            "policy": {"owner_default": {"mode": "chat"}},
            "memory": {"inject": False, "extract": False},
            "emotion": {"enabled": False},
        }
        assert client.put("/api/config", json=config).status_code == 200
        app.state.runtime.llm._generate = AsyncMock(return_value=httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1234, "completion_tokens": 10},
        }))
        assert client.post("/api/messages", json={"text": "Hello"}).status_code == 200
        endpoint = "/api/web-chats/web:owner/context"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if context := client.get(endpoint).json():
                break
            time.sleep(0.05)
        assert context["used"] == 1234
        assert context["limit"] == 100000
        assert not context["estimated"]
        assert client.get("/api/web-chats/web:other/context").json() is None
