import json
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from shiritaibot.core import Context
from shiritaibot.plugin.core import PluginManager
from shiritaibot.web import create_app

AUTH = {"Authorization": "Bearer test-auth"}


def test_web_document_operations_use_selected_library(tmp_path, monkeypatch):
    monkeypatch.setattr(PluginManager, "reload_all", AsyncMock(return_value=[]))
    with TestClient(create_app(tmp_path, token="test-auth"), headers=AUTH) as client:
        path = "/api/knowledge?filename=old.txt"
        response = client.post(path, content=b"bluebeacon documentation")
        assert response.status_code == 200
        assert response.json()["id"] == "d1"

        settings = client.get("/api/config").json()
        knowledge = settings["knowledge"]
        knowledge["libraries"] = {"club": {"name": "Club"}}
        assert client.put("/api/config", json=settings).status_code == 200
        assert {x["id"] for x in client.get("/api/knowledge/libraries").json()} == {
            "default",
            "club",
        }

        shared_path = path + "&library=club"
        same = client.post(shared_path, content=b"bluebeacon documentation").json()
        assert same["id"] == "club:d1" and not same["duplicate"]
        duplicate = client.post(shared_path, content=b"bluebeacon documentation").json()
        assert duplicate["id"] == same["id"] and duplicate["duplicate"]
        other = client.post(shared_path, content=b"yellowbeacon documentation").json()
        listing = client.get("/api/knowledge?library=club").json()
        assert listing["documents"] == 2
        assert [x["id"] for x in client.get("/api/knowledge").json()["items"]] == ["d1"]
        search = "/api/knowledge/search?q=yellowbeacon"
        assert client.get(search).json() == []
        assert [x["document"] for x in client.get(search + "&library=club").json()] == [other["id"]]

        document = "/api/knowledge/club:d1"
        assert client.get(document).status_code == 400
        assert client.get(document + "?library=club").json()["items"][0]["seq"] == 0
        assert client.get(document + "/file?library=club").content == b"bluebeacon documentation"
        assert client.delete("/api/knowledge/d1?library=club").status_code == 400
        assert client.delete(document + "?library=club").status_code == 200
        assert client.get(document + "/file?library=club").status_code == 404
        assert client.get("/api/knowledge/d1/file").content == b"bluebeacon documentation"

        knowledge["libraries"].clear()
        assert client.put("/api/config", json=settings).status_code == 200
        assert client.get("/api/knowledge?library=club").status_code == 400

    with TestClient(create_app(tmp_path, token="test-auth"), headers=AUTH) as client:
        settings = client.get("/api/config").json()
        settings["knowledge"]["libraries"]["club"] = {"name": "Club recovered"}
        assert client.put("/api/config", json=settings).status_code == 200
        assert client.get("/api/knowledge?library=club").json()["items"][0]["id"] == other["id"]
        assert client.get("/api/knowledge/d1/file").content == b"bluebeacon documentation"


def test_session_tool_access_follows_binding_and_legacy_chat_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(PluginManager, "reload_all", AsyncMock(return_value=[]))
    app = create_app(tmp_path, token="test-auth")
    owner = Context("web:owner", "owner", "agent", owner=True)
    club = Context("group:qq:10", "guest", group=True)
    visitor = Context("private:qq:11", "guest")
    disabled = Context("group:qq:12", "guest", group=True)

    with TestClient(app, headers=AUTH) as client:
        registry = app.state.runtime.registry

        def call(ctx, tool, args=None):
            assert client.portal is not None
            return json.loads(client.portal.call(registry.execute, ctx, tool, args or {}))

        settings = client.get("/api/config").json()
        config = settings["knowledge"]
        config["libraries"] = {"club": {"name": "Club"}, "work": {"name": "Work"}}
        config["sessions"] = {
            owner.session: "work",
            club.session: "club",
            disabled.session: "disabled",
        }
        settings["policy"]["owner_default"]["tools"]["kb_import"] = "allow"
        assert client.put("/api/config", json=settings).status_code == 200
        for library in ("default", "club"):
            response = client.post(
                "/api/knowledge",
                params={"filename": f"{library}.txt", "library": library},
                content=f"{library} document".encode(),
            )
            assert response.status_code == 200

        imported = call(owner, "kb_import", {"content": "work document", "title": "Work"})
        assert imported["id"] == "work:d1"
        assert call(owner, "kb_list")["items"][0]["id"] == "work:d1"
        assert call(club, "kb_list")["items"][0]["id"] == "club:d1"
        assert "error" in call(club, "kb_read", {"document": "work:d1"})
        assert "error" in call(club, "kb_import", {"content": "wrong", "title": "Wrong"})
        assert "error" in call(visitor, "kb_list")
        assert "error" in call(disabled, "kb_list")

        config["chat"] = True
        assert client.put("/api/config", json=settings).status_code == 200
        assert call(visitor, "kb_list")["items"][0]["id"] == "d1"
        assert "error" in call(disabled, "kb_list")

        config["sessions"][owner.session] = "club"
        assert client.put("/api/config", json=settings).status_code == 200
        assert call(owner, "kb_list")["items"][0]["id"] == "club:d1"
        assert "error" in call(owner, "kb_read", {"document": "work:d1"})
        del config["libraries"]["club"]
        assert client.put("/api/config", json=settings).status_code == 422
