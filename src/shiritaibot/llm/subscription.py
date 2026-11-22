from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import math
import re
import secrets
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from shiritaibot.core import PublicError

PROTOCOL = "openai_subscription"
RESOURCE = "https://api.openai.com/v1"
ISSUER = "https://auth.openai.com"
AUTHORIZE = ISSUER + "/api/accounts/authorize"
TOKEN = ISSUER + "/api/accounts/oauth/token"
REVOKE = ISSUER + "/api/accounts/oauth/revoke"
JWKS = ISSUER + "/.well-known/jwks.json"
DIRECT_SCOPE = "chatgpt.tokens.use.direct"
DYNAMIC_CLIENT = "dynamic_agent_client"
LIMIT_EXCEEDED = "subscription_sharing_usage_limit_exceeded"
SCOPE = f"openid profile email offline_access resource.invoke {DIRECT_SCOPE}"
LOGIN_SECONDS = 600
LIMIT_COOLDOWN = 60
SESSION_KIND = "openai_subscription"
TERMINAL_REFRESH_ERRORS = {
    "invalid_grant",
    "token_expired",
    "refresh_token_expired",
    "refresh_token_invalidated",
    "refresh_token_reused",
}
UNSUPPORTED_FIELDS = frozenset(
    """
    background conversation max_output_tokens max_tool_calls metadata moderation multi_agent
    previous_response_id prompt prompt_cache_retention safety_identifier temperature top_logprobs
    top_p truncation user
    """.split()
)


class SubscriptionError(PublicError):
    pass


@dataclass(frozen=True)
class Login:
    state: str
    nonce: str
    verifier: str
    redirect_uri: str
    created: float
    client_id: str
    subject: str


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def decode_b64(value: str) -> bytes:
    return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)


def verify_identity(raw: str, keys: dict, login: Login, client_id: str) -> dict:
    try:
        header_part, payload_part, signature_part = raw.split(".")
        header = json.loads(decode_b64(header_part))
        if header["alg"] != "RS256" or not isinstance(header["kid"], str):
            raise ValueError
        jwk = next(
            key
            for key in keys["keys"]
            if key.get("kid") == header["kid"] and key.get("kty") == "RSA"
        )
        key = rsa.RSAPublicNumbers(
            e=int.from_bytes(decode_b64(jwk["e"]), "big"),
            n=int.from_bytes(decode_b64(jwk["n"]), "big"),
        ).public_key()
        key.verify(
            decode_b64(signature_part),
            f"{header_part}.{payload_part}".encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        claims = json.loads(decode_b64(payload_part))
        audience = claims["aud"]
        if isinstance(audience, str):
            audience = [audience]
        if (
            claims["iss"] != ISSUER
            or not isinstance(audience, list)
            or client_id not in audience
            or (len(audience) > 1 and claims.get("azp") != client_id)
            or claims["nonce"] != login.nonce
            or not isinstance(claims["exp"], int)
            or claims["exp"] <= time.time()
            or not isinstance(claims["sub"], str)
            or not claims["sub"]
            or (login.subject and login.subject != claims["sub"])
        ):
            raise ValueError
        return claims
    except (AttributeError, InvalidSignature, KeyError, StopIteration, TypeError, ValueError):
        raise SubscriptionError("无法验证 ChatGPT 登录身份，请重新授权。") from None


def token_fields(data: dict, previous_scope: str = "") -> dict:
    scope = data.get("scope", previous_scope)
    access, refresh = data.get("access_token"), data.get("refresh_token")
    lifetime = data.get("expires_in")
    earliest = data.get("earliest_refresh_at", 0)
    if not isinstance(scope, str) or DIRECT_SCOPE not in scope.split():
        raise SubscriptionError("未授予使用 ChatGPT 套餐的权限，请重新授权。")
    if (
        not isinstance(access, str)
        or not access
        or not isinstance(refresh, str)
        or not refresh
        or not isinstance(lifetime, int)
        or lifetime <= 0
        or str(data.get("token_type", "Bearer")).lower() != "bearer"
    ):
        raise SubscriptionError("ChatGPT 返回的凭据无效，请重新授权。")
    return {
        "access_token": access,
        "refresh_token": refresh,
        "scope": scope,
        "expires_at": time.time() + lifetime,
        "earliest_refresh_at": (
            earliest if isinstance(earliest, (int, float)) and math.isfinite(earliest) else 0
        ),
    }


class SubscriptionAccounts:
    def __init__(self, vault, store):
        self.vault, self.store = vault, store
        self.logins: dict[str, Login] = {}
        self.locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.host_lock = asyncio.Lock()
        self.limits: dict[str, float] = {}

    async def load(self, provider_id: str) -> dict:
        row = await self.store.get(SESSION_KIND, provider_id)
        return json.loads(self.vault.cipher.decrypt(row["cipher"].encode())) if row else {}

    async def save(self, provider_id: str, session: dict) -> None:
        cipher = self.vault.cipher.encrypt(json.dumps(session).encode()).decode()
        await self.store.put(SESSION_KIND, provider_id, {"cipher": cipher})

    async def host_id(self) -> str:
        async with self.host_lock:
            row = await self.store.get("openai_subscription_meta", "host")
            if row:
                return row["host_id"]
            value = uuid4().urn
            await self.store.put("openai_subscription_meta", "host", {"host_id": value})
            return value

    async def start(self, provider) -> str:
        async with self.locks[provider.id]:
            record = await self.load(provider.id)
            login = Login(
                state=secrets.token_urlsafe(32),
                nonce=secrets.token_urlsafe(32),
                verifier=secrets.token_urlsafe(48),
                redirect_uri=f"http://127.0.0.1:{49152 + secrets.randbelow(16384)}/auth/callback",
                created=time.monotonic(),
                client_id=record.get("client_id", DYNAMIC_CLIENT),
                subject=record.get("subject", ""),
            )
            params = {
                "client_id": login.client_id,
                "ext_agent_host_id": await self.host_id(),
                "response_type": "code",
                "redirect_uri": login.redirect_uri,
                "scope": SCOPE,
                "resource": RESOURCE,
                "state": login.state,
                "nonce": login.nonce,
                "code_challenge_method": "S256",
                "code_challenge": b64url(hashlib.sha256(login.verifier.encode()).digest()),
            }
            if login.client_id == DYNAMIC_CLIENT:
                params["agent_name_hint"] = "ShiritaiBot"
            else:
                params["prompt"] = "consent"
            self.logins[provider.id] = login
            return AUTHORIZE + "?" + urlencode(params)

    async def finish(self, provider, callback: str) -> None:
        async with self.locks[provider.id]:
            login = self.logins.get(provider.id)
            if login is None or login.created + LOGIN_SECONDS < time.monotonic():
                self.logins.pop(provider.id, None)
                raise SubscriptionError("授权已过期，请重新发起。")
            try:
                uri = urlsplit(callback.strip())
                query = parse_qs(uri.query, keep_blank_values=True, max_num_fields=24)
                valid = (
                    not uri.fragment
                    and uri._replace(query="").geturl() == login.redirect_uri
                    and all(len(values) == 1 for values in query.values())
                    and hmac.compare_digest(
                        query.get("state", [""])[0].encode(), login.state.encode()
                    )
                )
            except ValueError:
                valid = False
            if not valid:
                raise SubscriptionError(
                    "回调地址或授权状态不匹配，请粘贴本次授权助手显示的完整地址。"
                )
            self.logins.pop(provider.id, None)
            if "error" in query:
                raise SubscriptionError("ChatGPT 授权未完成，请重新发起。")
            code = query.get("code", [""])[0]
            returned = query.get("client_id", [""])[0]
            client_id = returned if login.client_id == DYNAMIC_CLIENT else login.client_id
            if (
                not code
                or not re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", client_id)
                or client_id == DYNAMIC_CLIENT
                or (returned and returned != client_id)
            ):
                raise SubscriptionError("ChatGPT 回调缺少有效的授权码或客户端标识。")
            async with self.client(provider) as client:
                result = await self.auth_request(
                    client,
                    "POST",
                    TOKEN,
                    data={
                        "grant_type": "authorization_code",
                        "client_id": client_id,
                        "code": code,
                        "code_verifier": login.verifier,
                        "redirect_uri": login.redirect_uri,
                        "resource": RESOURCE,
                    },
                )
                if not result.is_success:
                    raise SubscriptionError("ChatGPT 授权码换取失败，请重新发起授权。")
                data = self.response_json(result)
                token = data.get("id_token")
                if not isinstance(token, str):
                    raise SubscriptionError("ChatGPT 未返回身份令牌，请重新授权。")
                fields = token_fields(data)
                jwks = await self.auth_request(client, "GET", JWKS)
                if not jwks.is_success:
                    raise SubscriptionError("暂时无法取得 ChatGPT 的身份验证公钥，请稍后重试。")
                claims = verify_identity(token, self.response_json(jwks), login, client_id)
            account = claims.get("email") or claims.get("name")
            await self.save(
                provider.id,
                {
                    **fields,
                    "client_id": client_id,
                    "subject": claims["sub"],
                    "account": account if isinstance(account, str) else "",
                    "id_token": token,
                },
            )
            self.limits.pop(provider.id, None)

    @staticmethod
    def client(provider) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            proxy=provider.proxy or None,
            trust_env=not provider.proxy,
            timeout=20,
            follow_redirects=False,
        )

    @staticmethod
    async def auth_request(client, method, url, **kwargs) -> httpx.Response:
        try:
            return await client.request(method, url, **kwargs)
        except httpx.HTTPError:
            raise SubscriptionError("无法连接 ChatGPT 授权服务，请检查服务器网络或代理。") from None

    @staticmethod
    def response_json(response) -> dict[str, Any]:
        try:
            data = response.json()
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
        raise SubscriptionError("ChatGPT 授权服务返回了无效响应。")

    async def status(self, provider_id: str) -> dict:
        record = await self.load(provider_id)
        return {
            "account": record.get("account", ""),
            "status": "connected"
            if record.get("refresh_token")
            else ("reauthorize" if record.get("reauthorize") else "disconnected"),
            "limit_reached": provider_id in self.limits,
        }

    def mark_limit(self, provider_id: str) -> None:
        self.limits[provider_id] = time.monotonic()

    def clear_limit(self, provider_id: str, call_started: float) -> None:
        if self.limits.get(provider_id, 0) <= call_started:
            self.limits.pop(provider_id, None)

    async def access_token(self, provider, *, inference: bool = False) -> str:
        async with self.locks[provider.id]:
            limited_at = self.limits.get(provider.id)
            if (
                inference
                and limited_at is not None
                and time.monotonic() - limited_at < LIMIT_COOLDOWN
            ):
                raise SubscriptionError("ChatGPT 套餐用量已达上限，请在 ChatGPT 用量设置中查看。")
            record = await self.load(provider.id)
            if not record.get("refresh_token"):
                raise SubscriptionError("请先在提供商设置中授权使用 ChatGPT 套餐。")
            if time.time() < max(record["expires_at"] - 60, record.get("earliest_refresh_at", 0)):
                if time.time() < record["expires_at"]:
                    return record["access_token"]
                raise SubscriptionError("ChatGPT 凭据暂不能续期，请稍后重试。")
            async with self.client(provider) as client:
                result = await self.auth_request(
                    client,
                    "POST",
                    TOKEN,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": record["client_id"],
                        "refresh_token": record["refresh_token"],
                        "resource": RESOURCE,
                    },
                )
            if not result.is_success:
                if result.status_code in {400, 401} and (
                    self.response_json(result).get("error") in TERMINAL_REFRESH_ERRORS
                ):
                    await self.save(provider.id, self.without_tokens(record, reauthorize=True))
                    raise SubscriptionError("ChatGPT 授权已失效，请重新连接。")
                raise SubscriptionError("ChatGPT 凭据暂时无法续期，请稍后重试。")
            fields = token_fields(self.response_json(result), record["scope"])
            await self.save(provider.id, {**record, **fields})
            return fields["access_token"]

    @staticmethod
    def without_tokens(record: dict, *, reauthorize: bool = False) -> dict:
        return {
            key: record[key] for key in ("client_id", "subject", "account") if key in record
        } | {"reauthorize": reauthorize}

    async def disconnect(self, provider, *, remove: bool = False) -> bool:
        async with self.locks[provider.id]:
            record = await self.load(provider.id)
            self.logins.pop(provider.id, None)
            self.limits.pop(provider.id, None)
            revoked = not record.get("refresh_token")
            if not revoked:
                try:
                    async with self.client(provider) as client:
                        response = await self.auth_request(
                            client,
                            "POST",
                            REVOKE,
                            data={
                                "client_id": record["client_id"],
                                "token": record["refresh_token"],
                                "token_type_hint": "refresh_token",
                            },
                        )
                        revoked = response.status_code == 200
                except SubscriptionError:
                    pass
            if remove:
                await self.store.delete(SESSION_KIND, provider.id)
            elif record:
                await self.save(provider.id, self.without_tokens(record))
            return revoked
