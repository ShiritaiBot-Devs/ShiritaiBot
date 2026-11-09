import asyncio

import lark_oapi
import pytest
from fastapi.testclient import TestClient
from lark_oapi.scene.registration.errors import AppAccessDeniedError, AppExpiredError

from shiritaibot.web import create_app

QR_API = "/api/adapters/feishu/qr"
ADMIN = {"Authorization": "Bearer admin"}
APP_SECRET = "fixture-only-qr-secret"
SCANNER_ID = "ou_scanner"


@pytest.fixture
def registration(tmp_path, monkeypatch):
    class RegistrationSdk:
        def __init__(self):
            self.cancelled = False
            self.url = "https://accounts.feishu.cn/page/launcher?ticket=fixture"

        async def __call__(self, **options):
            self.options = options
            self.result = asyncio.get_running_loop().create_future()
            options["on_qr_code"]({"url": self.url, "expire_in": 30})
            try:
                return await self.result
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    sdk = RegistrationSdk()
    monkeypatch.setattr(lark_oapi, "aregister_app", sdk)
    with TestClient(create_app(tmp_path, token="admin")) as client:
        yield client, sdk


def authorize(client, sdk, domain="feishu"):
    if domain == "lark":
        client.portal.call(sdk.options["on_status_change"], {"status": "domain_switched"})
    client.portal.call(
        sdk.result.set_result,
        {
            "client_id": "cli_created",
            "client_secret": APP_SECRET,
            "user_info": {"tenant_brand": domain, "open_id": SCANNER_ID},
        },
    )


def test_feishu_qr_applies_once_and_protects_credentials(registration, tmp_path):
    client, sdk = registration
    assert client.post(QR_API, json={}).status_code == 401
    assert (
        client.post(QR_API, json={"domain": "https://example.com"}, headers=ADMIN).status_code
        == 422
    )
    response = client.post(QR_API, json={}, headers=ADMIN)
    start = response.json()
    assert response.status_code == 200 and start["status"] == "waiting"
    assert start["qr_url"] == sdk.url and 0 < start["expires_in"] <= 30
    assert sdk.options["create_only"] is True
    assert sdk.options["domain"] == "https://accounts.feishu.cn"
    assert sdk.options["addons"] == {
        "preset": False,
        "scopes": {"tenant": ["im:message.p2p_msg:readonly", "im:message:send_as_bot"]},
        "events": {"items": {"tenant": ["im.message.receive_v1"]}},
    }
    endpoint = f"{QR_API}/{start['id']}"
    body = {"adapter_id": "new", "enabled": False, "memory_profile": "default"}
    assert client.post(endpoint + "/apply", json=body, headers=ADMIN).status_code == 409
    authorize(client, sdk, "lark")
    status = client.get(endpoint, headers=ADMIN)
    assert status.json()["status"] == "authorized" and status.json()["domain"] == "lark"
    assert status.json()["app_id"] == "cli_created" and "qr_url" not in status.json()
    assert APP_SECRET not in status.text and SCANNER_ID not in status.text

    config = client.get("/api/config", headers=ADMIN).json()
    config["agent_split"] = True
    config["adapters"] = [{"id": "qq", "enabled": False}]
    assert client.put("/api/config", json=config, headers=ADMIN).status_code == 200
    assert client.post(endpoint + "/apply", json=body).status_code == 401
    applied = client.post(endpoint + "/apply", json=body, headers=ADMIN)
    assert applied.status_code == 200 and applied.json()["adapter_id"] == "new"
    assert client.post(endpoint + "/apply", json=body, headers=ADMIN).json() == applied.json()
    assert (
        client.post(
            endpoint + "/apply", json={**body, "adapter_id": "second"}, headers=ADMIN
        ).status_code
        == 409
    )
    status = client.get(endpoint, headers=ADMIN).json()
    assert status["status"] == "applied" and status["adapter_id"] == "new"
    saved = client.get("/api/config", headers=ADMIN)
    assert APP_SECRET not in saved.text and APP_SECRET not in (tmp_path / "config.toml").read_text()
    settings = saved.json()
    assert settings["agent_split"] is True and [x["id"] for x in settings["adapters"]] == [
        "qq",
        "new",
    ]
    adapter = settings["adapters"][1]
    assert adapter["app_secret_configured"] and adapter["app_secret"] == ""
    assert (
        adapter["owner_id"] == ""
        and adapter["domain"] == "lark"
        and adapter["memory_profile"] == "default"
    )
    runtime = client.app.state.runtime
    reference = runtime.config.value.adapters[1].app_secret
    assert client.portal.call(runtime.vault.resolve, reference) == APP_SECRET


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AppAccessDeniedError("access_denied", APP_SECRET), "denied"),
        (AppExpiredError("expired_token", APP_SECRET), "expired"),
        (ValueError(APP_SECRET), "error"),
    ],
)
def test_feishu_qr_terminal_errors_are_redacted(registration, error, expected):
    client, sdk = registration
    start = client.post(QR_API, json={}, headers=ADMIN).json()
    client.portal.call(sdk.result.set_exception, error)
    status = client.get(f"{QR_API}/{start['id']}", headers=ADMIN)
    assert status.json()["status"] == expected
    assert APP_SECRET not in status.text and "qr_url" not in status.json()


def test_feishu_qr_cancel_stops_sdk_and_rejects_untrusted_links(registration):
    client, sdk = registration
    start = client.post(QR_API, json={}, headers=ADMIN).json()
    endpoint = f"{QR_API}/{start['id']}"
    assert client.get(endpoint).status_code == 401
    assert client.delete(endpoint).status_code == 401
    assert client.delete(endpoint, headers=ADMIN).status_code == 204
    assert sdk.cancelled and client.get(endpoint, headers=ADMIN).status_code == 404
    assert client.delete(endpoint, headers=ADMIN).status_code == 204
    sdk.url = f"https://accounts.feishu.cn@evil.test/?{APP_SECRET}"
    rejected = client.post(QR_API, json={}, headers=ADMIN)
    assert rejected.status_code == 502 and APP_SECRET not in rejected.text


def test_feishu_qr_conflict_leaves_existing_connection_intact(registration, monkeypatch):
    client, sdk = registration
    starts = 0

    async def count_start():
        nonlocal starts
        starts += 1

    monkeypatch.setattr(client.app.state.runtime.adapters, "start", count_start)
    config = client.get("/api/config", headers=ADMIN).json()
    config["adapters"] = [
        {"id": "existing", "type": "feishu", "app_id": "cli_existing", "app_secret": "old-secret"}
    ]
    assert client.put("/api/config", json=config, headers=ADMIN).status_code == 200
    assert starts == 1
    session_id = client.post(QR_API, json={}, headers=ADMIN).json()["id"]
    authorize(client, sdk)
    endpoint = f"{QR_API}/{session_id}/apply"
    assert client.post(endpoint, json={"adapter_id": "new"}, headers=ADMIN).status_code == 409
    assert (
        client.post(
            endpoint, json={"adapter_id": "existing", "enabled": False}, headers=ADMIN
        ).status_code
        == 409
    )
    assert (
        client.post(
            endpoint, json={"adapter_id": "new", "enabled": False}, headers=ADMIN
        ).status_code
        == 200
    )
    assert starts == 1
    adapters = client.get("/api/config", headers=ADMIN).json()["adapters"]
    assert [(a["id"], a["enabled"]) for a in adapters] == [("existing", True), ("new", False)]
