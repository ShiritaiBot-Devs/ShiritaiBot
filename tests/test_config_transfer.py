import tomllib
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from shiritaibot.cli import cli
from shiritaibot.dependencies import RuntimeEnvironment
from shiritaibot.web import create_app


def test_cli_export_and_desktop_import(tmp_path, monkeypatch):
    monkeypatch.setattr(RuntimeEnvironment, "prepare", AsyncMock())
    runner = CliRunner()
    cli_home = tmp_path / "cli"
    original = tmp_path / "config.toml"
    original.write_text(
        """
        [[providers]]
        id = "local"
        api_key = "test-api-key"
        base_url = "https://provider.example.test"

        [[models]]
        id = "chat"
        provider = "local"
        model = "example"
        extra_body = '{"nullable": null}'
        """,
        encoding="utf-8",
    )
    result = runner.invoke(cli, ["import-config", str(original), "--home", str(cli_home)])
    assert result.exit_code == 0, result.output
    assert runner.invoke(cli, ["import-config", str(original), "--home", str(cli_home)]).exit_code == 1
    cli_config = (cli_home / "config.toml").read_text(encoding="utf-8")
    assert "test-api-key" not in cli_config
    exported = tmp_path / "export.toml"
    result = runner.invoke(cli, ["export-config", str(exported), "--home", str(cli_home)])
    assert result.exit_code == 0, result.output
    content = exported.read_bytes()
    document = tomllib.loads(content.decode())
    source_ref = document["settings"]["providers"][0]["api_key"]
    assert document["secrets"][source_ref.removeprefix("secret:")] == "test-api-key"

    desktop_home = tmp_path / "desktop"
    app = create_app(desktop_home, token="test", desktop=True)
    with TestClient(app) as client:
        assert client.get("/api/config/export").status_code == 401
        assert client.post("/api/config/import", content=content).status_code == 401
        client.headers["Authorization"] = "Bearer test"
        before = client.get("/api/config")
        revision = {"X-Config-Revision": before.headers["X-Config-Revision"]}
        assert client.post("/api/config/import", content=b"broken = [").status_code == 400
        assert client.get("/api/config").json() == before.json()
        response = client.post("/api/config/import", content=content, headers=revision)
        assert response.status_code == 200, response.text
        after = client.get("/api/config").json()
        target_ref = after["providers"][0]["api_key"]
        assert target_ref.startswith("secret:") and target_ref != source_ref
        assert after["models"][0]["extra_body"] == {"nullable": None}
        assert "test-api-key" not in (desktop_home / "config.toml").read_text(encoding="utf-8")
        assert client.post("/api/config/import", content=content, headers=revision).status_code == 412
        response = client.get("/api/config/export")
        assert response.headers["Cache-Control"] == "no-store"
        assert response.headers["Content-Disposition"].endswith('"shiritaibot-config.toml"')
        assert tomllib.loads(response.text)["secrets"] == {
            target_ref.removeprefix("secret:"): "test-api-key"
        }
        exported.write_bytes(response.content)

    result = runner.invoke(cli, ["import-config", str(exported), "--home", str(cli_home), "--replace"])
    assert result.exit_code == 0, result.output
    response = runner.invoke(cli, ["export-config", str(exported), "--home", str(cli_home)])
    assert response.exit_code == 1
    web_home = tmp_path / "web"
    web_app = create_app(web_home, token="test")
    with TestClient(web_app, headers={"Authorization": "Bearer test"}) as client:
        response = client.post("/api/config/import", content=exported.read_bytes())
        assert response.status_code == 200, response.text
        response = client.get("/api/config/export")
        assert list(tomllib.loads(response.text)["secrets"].values()) == ["test-api-key"]
