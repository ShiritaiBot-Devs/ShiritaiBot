import socket
import time
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from threading import Event, Thread
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

from shiritaibot.core import canonical
from shiritaibot.dependencies import RuntimeEnvironment
from shiritaibot.llm.subscription import (
    DIRECT_SCOPE,
    ISSUER,
    JWKS,
    LIMIT_EXCEEDED,
    RESOURCE,
    REVOKE,
    TOKEN,
    SubscriptionAccounts,
    b64url,
)
from shiritaibot.web import chatgpt_login_helper, create_app


@pytest.mark.parametrize(
    "content_type", ["text/event-stream", None], ids=["sse-header", "no-header"]
)
def test_subscription_web_flow_rotates_credentials_and_tracks_inference(
    tmp_path, monkeypatch, content_type
):
    monkeypatch.setattr(RuntimeEnvironment, "prepare", AsyncMock())
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = key.public_key().public_numbers()
    jwk = {
        "kid": "fixture",
        "kty": "RSA",
        "n": b64url(public.n.to_bytes(256, "big")),
        "e": b64url(public.e.to_bytes(3, "big")),
    }
    params = {}
    refreshes = 0
    calls = 0
    event_type = "response.completed"

    def make_id_token():
        header = b64url(canonical({"alg": "RS256", "kid": "fixture"}).encode())
        claims = b64url(
            canonical(
                {
                    "iss": ISSUER,
                    "sub": "fixture-subject",
                    "aud": "oaiapp_fixture",
                    "nonce": params["nonce"][0],
                    "exp": int(time.time()) + 3600,
                    "email": "owner@example.test",
                }
            ).encode()
        )
        message = f"{header}.{claims}"
        signature = key.sign(message.encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{message}.{b64url(signature)}"

    def oauth(request):
        nonlocal refreshes
        if str(request.url) == JWKS:
            return httpx.Response(200, json={"keys": [jwk]})
        data = parse_qs(request.content.decode())
        assert data["client_id"] == ["oaiapp_fixture"]
        if str(request.url) == REVOKE:
            assert data["token"] == ["refresh-2"]
            return httpx.Response(200)
        assert str(request.url) == TOKEN
        assert data["resource"] == [RESOURCE]
        if data["grant_type"] == ["authorization_code"]:
            assert data["redirect_uri"] == params["redirect_uri"]
            assert (
                b64url(sha256(data["code_verifier"][0].encode()).digest())
                == (params["code_challenge"][0])
            )
            return httpx.Response(
                200,
                json={
                    "id_token": make_id_token(),
                    "access_token": "access-1",
                    "refresh_token": "refresh-1",
                    "scope": DIRECT_SCOPE,
                    "expires_in": 1,
                },
            )
        refreshes += 1
        assert data["grant_type"] == ["refresh_token"]
        assert data["refresh_token"] == ["refresh-1"]
        return httpx.Response(
            200, json={"access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 3600}
        )

    def inference(request):
        nonlocal calls
        assert request.headers["authorization"] == "Bearer access-2"
        if request.method == "GET":
            assert str(request.url) == RESOURCE + "/models"
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"slug": "fixture", "display_name": "Fixture", "visibility": "list"},
                        {"slug": "hidden", "visibility": "hidden"},
                    ]
                },
            )
        calls += 1
        assert str(request.url) == RESOURCE + "/responses"
        assert request.headers["accept"] == "text/event-stream"
        assert b'"stream":true' in request.content and b'"store":false' in request.content
        assert (
            b'"max_output_tokens"' not in request.content
            and b'"temperature"' not in request.content
        )
        response = {
            "output": [{"type": "message", "content": [{"text": "OK"}]}],
            "usage": {"input_tokens": 11, "output_tokens": 2},
        }
        if event_type == "response.failed":
            response = {"error": {"code": LIMIT_EXCEEDED}}
        event = {"type": event_type, "response": response}
        headers = {"content-type": content_type} if content_type else {}
        return httpx.Response(
            200, headers=headers, content=f"data: {canonical(event)}\n\n".encode()
        )

    monkeypatch.setattr(
        SubscriptionAccounts,
        "client",
        staticmethod(lambda _provider: httpx.AsyncClient(transport=httpx.MockTransport(oauth))),
    )
    app = create_app(tmp_path, token="fixture-web-token")
    headers = {"Authorization": "Bearer fixture-web-token"}
    provider = {"id": "subscription", "protocol": "openai_subscription"}
    path = "/api/providers/subscriptions/subscription"
    with TestClient(app, headers=headers) as client:
        runtime = app.state.runtime

        async def install_transport():
            await runtime.llm.client.aclose()
            runtime.llm.client = httpx.AsyncClient(transport=httpx.MockTransport(inference))

        assert client.portal is not None
        client.portal.call(install_transport)
        assert client.get(path).json() == {"saved": False}
        saved = client.put("/api/config", json={"providers": [provider]})
        assert saved.is_success
        assert not any("API Key" in warning for warning in saved.json()["warnings"])
        assert client.get(path, headers={"Authorization": ""}).status_code == 401
        script = client.post(path + "/start").json()["script"]
        run = script.rsplit("    main(", 1)[1]
        auth_url = run[1 : run.rfind("'")]
        params.update(parse_qs(urlsplit(auth_url).query))
        assert params["client_id"] == ["dynamic_agent_client"]
        assert params["ext_agent_host_id"][0].startswith("urn:uuid:")
        callback = (
            params["redirect_uri"][0]
            + "?"
            + urlencode(
                {"code": "fixture-code", "state": params["state"][0], "client_id": "oaiapp_fixture"}
            )
        )
        assert (
            client.post(path + "/finish", json={"callback": callback + "&state=bad"}).status_code
            == 400
        )
        assert client.post(path + "/finish", json={"callback": callback}).is_success
        assert client.post(path + "/finish", json={"callback": callback}).status_code == 400
        status = client.get(path).json()
        assert status["account"] == "owner@example.test" and status["status"] == "connected"
        assert "refresh-1" not in canonical(status)
        assert "access-1" not in client.get("/api/config").text
        stored = client.portal.call(runtime.store.get, "openai_subscription", "subscription")
        assert set(stored) == {"id", "scope", "cipher"}
        assert "refresh-1" not in canonical(stored)

        def discover():
            return client.post("/api/providers/discover", json=provider).json()

        with ThreadPoolExecutor(max_workers=2) as threads:
            fetched = list(threads.map(lambda _: discover(), range(2)))
        assert [rows[0]["id"] for rows in fetched] == ["fixture", "fixture"]
        assert refreshes == 1

        model = {
            "id": "fixture",
            "model": "fixture",
            "provider": "subscription",
            "extra_body": {"stream": False, "store": True, "temperature": 1},
        }
        payload = {"provider": provider, "model": model}
        result = client.post("/api/models/test", json=payload)
        assert result.status_code == 200, result.text
        assert result.json()["text"] == "OK"
        event_type = "response.incomplete"
        assert client.post("/api/models/test", json=payload).status_code == 400
        event_type = "response.failed"
        assert client.post("/api/models/test", json=payload).status_code == 400
        assert client.post("/api/models/test", json=payload).status_code == 400
        status = client.get(path).json()
        assert status["limit_reached"]
        assert status["usage"]["today"] == {"calls": 3, "input_tokens": 11, "output_tokens": 2}
        assert calls == 3

        assert client.delete(path).json() == {"revoked": True}
        assert client.get(path).json()["status"] == "disconnected"
        reconnect = client.post(path + "/start").json()["script"]
        assert "client_id=oaiapp_fixture" in reconnect


def test_login_helper_handles_callbacks_while_a_browser_connection_is_idle(monkeypatch):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    redirect = f"http://127.0.0.1:{port}/auth/callback"
    browser_opened = Event()
    monkeypatch.setattr(
        chatgpt_login_helper, "open_browser", lambda _: browser_opened.set() or True
    )
    authorization_url = (
        ISSUER
        + "/api/accounts/authorize?"
        + urlencode({"redirect_uri": redirect, "state": "fixture-state"})
    )
    helper = Thread(target=chatgpt_login_helper.main, args=(authorization_url,), daemon=True)
    helper.start()
    assert browser_opened.wait(3)
    # Browsers can connect before sending anything. Keep that connection open even
    # while the valid callback finishes and the helper shuts down.
    with (
        socket.create_connection(("127.0.0.1", port), timeout=2),
        httpx.Client(trust_env=False, timeout=2) as browser,
    ):
        assert browser.get(redirect, params={"code": "code", "state": "other"}).status_code == 400
        response = browser.get(redirect, params={"code": "fixture-code", "state": "fixture-state"})
        assert response.status_code == 200
        assert "fixture-code" in response.text and "复制回调地址" in response.text
        helper.join(timeout=3)
        assert not helper.is_alive()
