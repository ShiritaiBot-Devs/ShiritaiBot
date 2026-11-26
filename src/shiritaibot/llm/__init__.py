from __future__ import annotations

import asyncio
import json
import math
import time
from collections import defaultdict, deque
from copy import deepcopy
from typing import Any

import httpx

from shiritaibot.agent.context import context_chars
from shiritaibot.agent.goals import GOAL_USAGE
from shiritaibot.core import (
    PublicError,
    Reply,
    canonical,
    model_usage_session,
    record_usage_for,
    uid,
)
from shiritaibot.core.logs import get_logger
from shiritaibot.i18n import Text, t
from shiritaibot.llm.media import ImageResolver
from shiritaibot.llm.streaming import ResponseStreamError, TextDelta, collect_stream
from shiritaibot.llm.subscription import (
    LIMIT_EXCEEDED,
    PROTOCOL,
    RESOURCE,
    UNSUPPORTED_FIELDS,
    SubscriptionAccounts,
    SubscriptionError,
)
from shiritaibot.store.statistics import tokens

log = get_logger("llm.log.source")


def plain(message: dict) -> dict:
    return {k: v for k, v in message.items() if not k.startswith("_")}


def merge_body(base: dict, extra: dict) -> dict:
    """Merge nested protocol parameters without changing defaults or configuration."""
    result = deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_body(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


# Providers bill an image by its pixels, not its base64 length; this is a typical cost.
IMAGE_TOKENS = 1600


def listed_pricing(pricing) -> dict | None:
    """Per-million USD prices from an OpenRouter-style `pricing` object (USD per token).

    Official OpenAI, Anthropic and Gemini model lists carry no prices; None means unknown.
    """
    if not isinstance(pricing, dict):
        return None

    def per_million(key):
        try:
            value = float(pricing.get(key) or 0)
        except (TypeError, ValueError):
            return None
        # OpenRouter marks variable-price routers with -1.
        return round(value * 1_000_000, 6) if math.isfinite(value) and value >= 0 else None

    prices = {
        "price_input": per_million("prompt"),
        "price_cached": per_million("input_cache_read"),
        "price_output": per_million("completion"),
    }
    if None in prices.values() or not any(prices.values()):
        return None
    return {"currency": "USD", **prices}


def estimated_tokens(body: dict) -> int:
    # A shared heuristic, not a provider tokenizer: ASCII ~4 chars/token,
    # other characters ~1 token. Include system text, tool schemas and opaque state.
    images = 0

    def strip(value: Any) -> Any:
        nonlocal images
        if isinstance(value, str) and value.startswith("data:image/"):
            images += 1
            return ""
        if isinstance(value, dict):
            if "data" in value and value.keys() & {"media_type", "mimeType"}:
                images += 1
                return {}
            return {k: strip(v) for k, v in value.items()}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value

    text = json.dumps(strip(body), ensure_ascii=False)
    ascii_chars = sum(character.isascii() for character in text)
    return math.ceil(ascii_chars / 4) + len(text) - ascii_chars + images * IMAGE_TOKENS


def content_parts(protocol: str, content: str | list[dict]) -> list[dict]:
    """Protocol blocks for message content; lists use Chat Completions text/image_url parts."""
    if isinstance(content, str):
        content = [{"type": "text", "text": content}] if content else []
    result = []
    for part in content:
        if part["type"] == "text":
            if protocol == "responses":
                result.append({"type": "input_text", "text": part["text"]})
            elif protocol == "gemini":
                result.append({"text": part["text"]})
            else:
                result.append(part)
            continue
        url = part["image_url"]["url"]
        if protocol == "responses":
            result.append({"type": "input_image", "image_url": url})
            continue
        if protocol == "chat_completions":
            result.append(part)
            continue
        mime, data = url.removeprefix("data:").split(";base64,", 1)
        if protocol == "anthropic":
            result.append(
                {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}
            )
        else:
            result.append({"inlineData": {"mimeType": mime, "data": data}})
    return result


def request_payload(
    protocol: str,
    model: str,
    system: str,
    messages: list[dict],
    tools: list[dict],
    max_output: int,
    *,
    opaque_key: str | None = None,
) -> tuple[str, dict]:
    key = opaque_key or f"{protocol}:{model}"
    if protocol == "chat_completions":
        history = []
        for message in messages:
            data = plain(message)
            opaque = message.get("_opaque", {})
            if opaque.get("key") == key and "reasoning_content" in opaque.get("data", {}):
                data["reasoning_content"] = opaque["data"]["reasoning_content"]
            history.append(data)
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *history],
            "max_completion_tokens": max_output,
            "stream": False,
        }
        if tools:
            body["tools"] = tools
        return "/chat/completions", body
    if protocol == "responses":
        items = []
        for message in messages:
            opaque = message.get("_opaque", {})
            if opaque.get("key") == key:
                items.extend(opaque["data"])
                continue
            if message["role"] == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message["tool_call_id"],
                        "output": message["content"],
                    }
                )
            else:
                content = message.get("content")
                if isinstance(content, list):
                    content = content_parts(protocol, content)
                if content:
                    items.append({"role": message["role"], "content": content})
                for call in message.get("tool_calls", []):
                    items.append(
                        {"type": "function_call", "call_id": call["id"], **call["function"]}
                    )
        return "/responses", {
            "model": model,
            "instructions": system,
            "input": items,
            "tools": [{"type": "function", **t["function"], "strict": False} for t in tools],
            "store": False,
            "include": ["reasoning.encrypted_content"],
            "max_output_tokens": max_output,
        }
    if protocol == "anthropic":
        history = []
        for message in messages:
            opaque = message.get("_opaque", {})
            role = "user" if message["role"] == "tool" else message["role"]
            if opaque.get("key") == key:
                blocks = opaque["data"]
            elif message["role"] == "tool":
                blocks = [
                    {
                        "type": "tool_result",
                        "tool_use_id": message["tool_call_id"],
                        "content": message["content"],
                    }
                ]
            else:
                blocks = content_parts(protocol, message.get("content") or "")
                blocks += [
                    {
                        "type": "tool_use",
                        "id": c["id"],
                        "name": c["function"]["name"],
                        "input": json.loads(c["function"]["arguments"]),
                    }
                    for c in message.get("tool_calls", [])
                ]
            if not blocks:
                continue
            if history and history[-1]["role"] == role:
                history[-1]["content"].extend(blocks)
            else:
                history.append({"role": role, "content": list(blocks)})
        return "/messages", {
            "model": model,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": history,
            "max_tokens": max_output,
            "tools": [
                {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "input_schema": t["function"]["parameters"],
                }
                for t in tools
            ],
        }
    if protocol == "gemini":
        history, names = [], {}
        for message in messages:
            for call in message.get("tool_calls", []):
                names[call["id"]] = call["function"]["name"]
            opaque = message.get("_opaque", {})
            if opaque.get("key") == key:
                parts = opaque["data"]
            elif message["role"] == "tool":
                parts = [
                    {
                        "functionResponse": {
                            "name": names.get(message["tool_call_id"], "unknown"),
                            "response": {"result": message["content"]},
                        }
                    }
                ]
            else:
                parts = content_parts(protocol, message.get("content") or "")
                parts += [
                    {
                        "functionCall": {
                            "name": c["function"]["name"],
                            "args": json.loads(c["function"]["arguments"]),
                        },
                        # Cross-model and legacy history has no Gemini signature.
                        # Google's documented sentinel permits replaying those calls;
                        # same-model opaque parts above keep their original signatures.
                        "thoughtSignature": "skip_thought_signature_validator",
                    }
                    for c in message.get("tool_calls", [])
                ]
            role = "model" if message["role"] == "assistant" else "user"
            if not parts:
                continue
            if history and history[-1]["role"] == role:
                history[-1]["parts"].extend(parts)
            else:
                history.append({"role": role, "parts": list(parts)})

        def clean(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    k: clean(v)
                    for k, v in value.items()
                    if k not in {"additionalProperties", "$schema"}
                }
            if isinstance(value, list):
                return [clean(v) for v in value]
            return value

        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": history,
            "generationConfig": {"maxOutputTokens": max_output},
        }
        if tools:
            body["tools"] = [{"functionDeclarations": [clean(t["function"]) for t in tools]}]
        return f"/models/{model}:generateContent", body
    raise PublicError("llm.protocolUnsupported")


def parse_reply(protocol: str, model: str, data: dict, *, opaque_key: str | None = None) -> Reply:
    if data.get("error") or data.get("status") in ("failed", "cancelled"):
        raise ValueError("provider returned an unsuccessful completion")
    key = opaque_key or f"{protocol}:{model}"
    calls, text = [], ""
    if protocol == "chat_completions":
        msg = data["choices"][0]["message"]
        return Reply(
            msg.get("content") or "",
            msg.get("tool_calls") or [],
            data.get("usage", {}),
            {"key": key, "data": msg},
        )
    if protocol == "responses":
        raw = data.get("output", [])
        for item in raw:
            if item["type"] == "message":
                text += "".join(
                    x.get("text", x.get("refusal", "")) for x in item.get("content", [])
                )
            elif item["type"] == "function_call":
                calls.append(
                    {
                        "id": item["call_id"],
                        "type": "function",
                        "function": {"name": item["name"], "arguments": item["arguments"]},
                    }
                )
        return Reply(text, calls, data.get("usage", {}), {"key": key, "data": raw})
    if protocol == "anthropic":
        raw = data.get("content", [])
        for block in raw:
            if block["type"] == "text":
                text += block["text"]
            elif block["type"] == "tool_use":
                calls.append(
                    {
                        "id": block["id"],
                        "type": "function",
                        "function": {"name": block["name"], "arguments": canonical(block["input"])},
                    }
                )
        return Reply(text, calls, data.get("usage", {}), {"key": key, "data": raw})
    raw = data["candidates"][0]["content"]["parts"]
    for part in raw:
        if "text" in part and not part.get("thought"):
            text += part["text"]
        if "functionCall" in part:
            call = part["functionCall"]
            calls.append(
                {
                    "id": call.get("id", uid()),
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": canonical(call.get("args", {})),
                    },
                }
            )
    return Reply(text, calls, data.get("usageMetadata", {}), {"key": key, "data": raw})


def failure(provider_id: str, exc: Exception) -> str:
    """Activity detail for a failed call; the status code tells a bad path from a bad key."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{provider_id}: HTTP {exc.response.status_code}"
    return f"{provider_id}: {type(exc).__name__}"


def response_error_code(response: httpx.Response) -> str | None:
    try:
        return response.json()["error"]["code"]
    except (KeyError, TypeError, ValueError):
        return None


def subscription_body(body: dict, model: str) -> dict:
    for field in UNSUPPORTED_FIELDS:
        body.pop(field, None)
    body["model"] = model
    if body.get("tools"):
        functions = []
        supported = []
        for tool in body["tools"]:
            if tool.get("type") in {"function", "custom"}:
                functions.append(tool)
            elif tool.get("type") in {"namespace", "web_search", "web_search_preview"}:
                supported.append(tool)
        if functions:
            supported.append(
                {
                    "type": "namespace",
                    "name": "shiritaibot",
                    "description": "Tools available in ShiritaiBot.",
                    "tools": functions,
                }
            )
            choice = body.get("tool_choice")
            if isinstance(choice, dict) and choice.get("type") in {"function", "custom"}:
                body["tool_choice"] = {**choice, "namespace": "shiritaibot"}
        body["tools"] = supported
    return body


class ProviderManager:
    def __init__(self, config, vault, store):
        self.config, self.vault, self.store = config, vault, store
        self.client = httpx.AsyncClient(follow_redirects=False, timeout=120)
        self.semaphore = asyncio.Semaphore(config.value.concurrency)
        self.subscriptions = SubscriptionAccounts(vault, store)
        # Recent call failures per provider, so the WebUI can warn before extraction
        # quietly backs off for half an hour with no visible sign.
        self.failures: dict[str, deque[float]] = defaultdict(deque)

    def record_failure(self, provider_id: str) -> None:
        now_ = time.monotonic()
        stamps = self.failures[provider_id]
        stamps.append(now_)
        while stamps and now_ - stamps[0] > 900:
            stamps.popleft()

    def failing(self, threshold: int = 3) -> dict[str, int]:
        now_ = time.monotonic()
        result = {}
        for provider_id, stamps in self.failures.items():
            while stamps and now_ - stamps[0] > 900:
                stamps.popleft()
            if len(stamps) >= threshold:
                result[provider_id] = len(stamps)
        return result

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(
        self, provider, method: str, url: str, *, stream_protocol=None, on_text=None, **kwargs
    ) -> httpx.Response:
        async def send(client):
            for attempt in range(provider.max_retries + 1):
                delay = min(20, 2**attempt)
                try:
                    started = time.monotonic()
                    async with client.stream(
                        method, url, timeout=provider.timeout, **kwargs
                    ) as response:
                        content_type = response.headers.get("content-type", "").strip().lower()
                        # Subscription requests always use SSE, including when the header is absent.
                        if (
                            stream_protocol
                            and response.is_success
                            and (
                                "text/event-stream" in content_type
                                or (stream_protocol == PROTOCOL and not content_type)
                            )
                        ):
                            data, timing = await collect_stream(stream_protocol, response, started, on_text)
                            return httpx.Response(
                                response.status_code,
                                json=data,
                                request=response.request,
                                extensions={"model_timing": timing},
                            )
                        if stream_protocol == PROTOCOL and response.is_success:
                            raise ResponseStreamError(None)
                        await response.aread()
                except httpx.TimeoutException:
                    if attempt == provider.max_retries:
                        raise
                    if on_text:
                        on_text("")
                else:
                    if (
                        response.status_code not in {429, 500, 502, 503, 504}
                        or attempt == provider.max_retries
                        or (
                            provider.protocol == PROTOCOL
                            and response_error_code(response) == LIMIT_EXCEEDED
                        )
                    ):
                        response.raise_for_status()
                        return response
                    try:
                        delay = min(20, max(0, float(response.headers.get("retry-after", delay))))
                    except ValueError:
                        pass
                await asyncio.sleep(delay)
            raise AssertionError("unreachable retry state")

        if provider.proxy:
            # Scoped clients avoid stale proxy settings after saving or testing a draft.
            async with httpx.AsyncClient(
                proxy=provider.proxy, follow_redirects=False, trust_env=False
            ) as client:
                return await send(client)
        return await send(self.client)

    async def _generate(self, provider, path, body, headers, stream, on_text=None):
        if provider.protocol == PROTOCOL:
            return await self._request(
                provider,
                "POST",
                RESOURCE + path,
                json={**body, "store": False, "stream": True},
                headers={**headers, "Accept": "text/event-stream"},
                stream_protocol=PROTOCOL,
                on_text=on_text,
            )
        base = provider.base_url.rstrip("/")
        if provider.protocol == "gemini":
            body.pop("stream", None)  # Gemini selects the mode in the URL.
        if stream:
            protocol = provider.protocol
            if protocol == "gemini":
                stream_path = (
                    path.replace(":generateContent", ":streamGenerateContent") + "?alt=sse"
                )
                stream_body = body
            else:
                stream_path = path
                stream_body = {**body, "stream": True}
                if protocol == "chat_completions":
                    stream_body["stream_options"] = {
                        "include_usage": True,
                        **(body.get("stream_options") or {}),
                    }
            try:
                return await self._request(
                    provider,
                    "POST",
                    base + stream_path,
                    json=stream_body,
                    headers=headers,
                    stream_protocol=protocol,
                    on_text=on_text,
                )
            except httpx.HTTPStatusError as exc:
                # Compatibility gateways may implement only the buffered endpoint.
                status = exc.response.status_code
                if not (
                    (status in {400, 422, 501} and "stream" in exc.response.text.lower())
                    or (protocol == "gemini" and status in {404, 405, 501})
                ):
                    raise
                if protocol != "gemini":
                    body = {**body, "stream": False}
                    body.pop("stream_options", None)
        return await self._request(provider, "POST", base + path, json=body, headers=headers)

    async def _record_call(self, model, protocol, status, usage=None, timing=None, *, scope=None):
        charge = GOAL_USAGE.get()
        if charge is not None and usage:
            await charge(tokens(usage, protocol)["total_tokens"])
        await self.store.put(
            "usage",
            uid(),
            {
                "model": model.id,
                "model_label": model.display_name,
                "provider": model.provider,
                "protocol": protocol,
                "status": status,
                "usage": usage or {},
                **(timing or {}),
            },
            model_usage_session.get() if scope is None else scope,
        )

    async def discover(self, provider) -> list[dict]:
        if provider.protocol == PROTOCOL:
            token = await self.subscriptions.access_token(provider)
            try:
                response = await self._request(
                    provider,
                    "GET",
                    RESOURCE + "/models",
                    headers={"Authorization": f"Bearer {token}"},
                )
                rows = response.json()["models"]
                if not isinstance(rows, list):
                    raise TypeError
                return [
                    {
                        "id": row["slug"],
                        "name": row.get("display_name") or row["slug"],
                        "max_output": 4096,
                    }
                    for row in rows
                    if isinstance(row, dict)
                    and row.get("visibility") == "list"
                    and isinstance(row.get("slug"), str)
                    and row["slug"]
                ]
            except (httpx.HTTPError, KeyError, TypeError, ValueError):
                raise PublicError("llm.subscriptionModels") from None
        ref = provider.api_key
        token = await self.vault.resolve(ref) if ref.startswith(("env:", "secret:")) else ref
        headers = {}
        if provider.protocol == "anthropic":
            headers = {"x-api-key": token, "anthropic-version": "2023-06-01"}
        elif provider.protocol == "gemini":
            headers = {"x-goog-api-key": token}
        elif token:
            headers = {"Authorization": f"Bearer {token}"}
        if provider.protocol == "systemone":
            # TypeSafe publishes model routes instead of a listing endpoint, and OpenRouter's
            # /models omits decision models, so offer each host's own names.
            host = httpx.URL(provider.base_url).host
            keys = (
                ("~typesafe/jev-latest",)
                if host == "openrouter.ai" or host.endswith(".openrouter.ai")
                else ("jev-latest", "jev-preview")
            )
            return [{"id": key, "name": key, "max_output": 4096} for key in keys]
        base = provider.base_url.rstrip("/")
        params, found, cursors = {}, {}, set()
        try:
            for _ in range(100):
                response = await self._request(
                    provider, "GET", base + "/models", headers=headers, params=params
                )
                body = response.json()
                if not isinstance(body, dict):
                    raise PublicError("llm.modelsInvalid")
                rows = body.get("models" if provider.protocol == "gemini" else "data")
                if not isinstance(rows, list):
                    raise PublicError("llm.modelsInvalidHint")
                for row in rows:
                    if not isinstance(row, dict):
                        raise PublicError("llm.modelEntryInvalid")
                    if provider.protocol == "gemini":
                        methods = row.get("supportedGenerationMethods", [])
                        if methods and "generateContent" not in methods:
                            continue
                        key = row["name"].removeprefix("models/")
                    else:
                        key = row["id"]
                    if not isinstance(key, str) or not key:
                        raise PublicError("llm.modelNameInvalid")
                    found[key] = {
                        "id": key,
                        "name": row.get("displayName", row.get("display_name", key)),
                        "max_output": row.get("outputTokenLimit", 4096),
                        "pricing": listed_pricing(row.get("pricing")),
                    }
                cursor = (
                    body.get("nextPageToken")
                    if provider.protocol == "gemini"
                    else (body.get("last_id") if body.get("has_more") else None)
                )
                if not cursor:
                    return sorted(found.values(), key=lambda row: row["id"])
                if cursor in cursors:
                    raise PublicError("llm.cursorRepeated")
                cursors.add(cursor)
                params = {"pageToken" if provider.protocol == "gemini" else "after_id": cursor}
            raise PublicError("llm.tooManyPages")
        except httpx.HTTPStatusError as exc:
            raise PublicError(
                "llm.modelsHttp", status_code=exc.response.status_code
            ) from None
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            raise PublicError("llm.modelsUnreadable") from None

    async def complete(
        self,
        route: str,
        system: str,
        messages: list[dict],
        tools: list[dict],
        settings=None,
        scope: str | None = None,
        *,
        tool_free: bool = False,
        track_web_context: bool = False,
        on_text: TextDelta | None = None,
    ) -> Reply:
        """Use scope for attribution and image resolution. An empty delta discards a preview."""
        cfg = settings or self.config.value
        candidates = cfg.routes.get(route, [])
        if not candidates:
            raise PublicError("llm.routeMissing", route=route)
        images = ImageResolver(self, scope, cfg) if scope else None
        if images and not images.present(messages):
            images = None
        context_skipped = decision_only = False
        subscription_error = ""
        for model_id in candidates:
            model = next((m for m in cfg.models if m.id == model_id and m.enabled), None)
            provider = next(
                (p for p in cfg.providers if model and p.id == model.provider and p.enabled),
                None,
            )
            if not model or not provider or model.kind == "embedding":
                continue
            if provider.protocol == "systemone":
                decision_only = True
                continue
            # Captioning calls complete() itself, so it must run outside the semaphore.
            sent = messages
            if images:
                with record_usage_for(images.scope):
                    sent = await images.render(messages, model)
            subscription = provider.protocol == PROTOCOL
            protocol = "responses" if subscription else provider.protocol
            opaque_key = f"{PROTOCOL}:{provider.id}:{model.model}" if subscription else None
            path, body = request_payload(
                protocol, model.model, system, sent, tools, model.max_output, opaque_key=opaque_key
            )
            body = merge_body(body, model.extra_body)
            if tool_free:
                for field in (
                    "tools",
                    "tool_choice",
                    "parallel_tool_calls",
                    "toolConfig",
                    "tool_config",
                ):
                    body.pop(field, None)
            if provider.protocol == "chat_completions" and "max_tokens" in model.extra_body:
                body.pop("max_completion_tokens", None)
            if subscription:
                body = subscription_body(body, model.model)
            generation = body.get("generationConfig")
            output_limit = {
                "chat_completions": body.get("max_tokens", body.get("max_completion_tokens")),
                "responses": body.get("max_output_tokens"),
                "anthropic": body.get("max_tokens"),
                "gemini": generation.get("maxOutputTokens")
                if isinstance(generation, dict)
                else None,
            }[protocol]
            reserved_output = (
                output_limit
                if isinstance(output_limit, int) and output_limit > 0
                else model.max_output
            )
            estimate = estimated_tokens(body) if model.max_context or track_web_context else 0
            if model.max_context and estimate + reserved_output > model.max_context:
                context_skipped = True
                log.warning(
                    Text("llm.log.contextTooLong", display_name=model.display_name, max_context=model.max_context), route
                )
                await self.store.activity("provider.context_limit", model.display_name)
                continue
            started = time.monotonic()
            try:
                if subscription:
                    token = await self.subscriptions.access_token(provider, inference=True)
                else:
                    ref = provider.api_key
                    token = (
                        await self.vault.resolve(ref)
                        if ref.startswith(("env:", "secret:"))
                        else ref
                    )
                headers = {"Content-Type": "application/json"}
                if protocol == "anthropic":
                    headers.update({"x-api-key": token, "anthropic-version": "2023-06-01"})
                elif protocol == "gemini":
                    headers["x-goog-api-key"] = token
                elif token:
                    headers["Authorization"] = f"Bearer {token}"
                async with self.semaphore:
                    response = await self._generate(
                        provider,
                        path,
                        body,
                        headers,
                        model.extra_body.get("stream") is not False,
                        **({"on_text": on_text} if on_text else {}),
                    )
                reply = parse_reply(protocol, model.model, response.json(), opaque_key=opaque_key)
                if subscription:
                    self.subscriptions.clear_limit(provider.id, started)
                await self._record_call(
                    model,
                    provider.protocol,
                    "ok",
                    reply.usage,
                    response.extensions.get("model_timing"),
                    scope=scope,
                )
                if track_web_context and scope and scope.startswith("web:"):
                    used = tokens(reply.usage or {}, protocol)["input_tokens"]
                    await self.store.put("web_context", scope, {
                        "model": model.display_name or model.model,
                        "used": used or estimate,
                        "limit": model.max_context or None,
                        "estimated": not used,
                        "context_chars": context_chars(messages) if not model.max_context else None,
                    }, scope)
                log.debug(
                    t("llm.replied", display_name=model.display_name, value=time.monotonic() - started)
                    + (t("llm.toolCalls", count=len(reply.calls)) if reply.calls else ""),
                    route,
                )
                return reply
            except SubscriptionError as exc:
                subscription_error = str(exc)
            except (
                httpx.HTTPError,
                ValueError,
                KeyError,
                IndexError,
                TypeError,
                AttributeError,
            ) as exc:
                if on_text:
                    on_text("")
                if subscription and (
                    (isinstance(exc, ResponseStreamError) and exc.code == LIMIT_EXCEEDED)
                    or (
                        isinstance(exc, httpx.HTTPStatusError)
                        and response_error_code(exc.response) == LIMIT_EXCEEDED
                    )
                ):
                    self.subscriptions.mark_limit(provider.id)
                    subscription_error = t("llm.subscriptionLimit")
                await self._record_call(model, provider.protocol, "failed", scope=scope)
                log.warning(
                    Text("llm.log.candidateFailed", display_name=model.display_name, failure=failure(provider.id, exc)),
                    route,
                )
                await self.store.activity("provider.failed", failure(provider.id, exc))
                self.record_failure(provider.id)
        if subscription_error:
            raise SubscriptionError(subscription_error)
        if context_skipped:
            raise PublicError(
                "llm.contextTooLong"
            )
        if decision_only:
            raise PublicError(
                "llm.decisionOnly", route=route
            )
        raise PublicError("llm.allFailed")

    def embedding_model(self, settings=None, route: str = "embedding"):
        """(model, provider) serving the route, or None; only the first usable one counts."""
        cfg = settings or self.config.value
        for model_id in cfg.routes.get(route, []):
            model = next(
                (m for m in cfg.models if m.id == model_id and m.enabled and m.kind == "embedding"),
                None,
            )
            provider = next(
                (p for p in cfg.providers if model and p.id == model.provider and p.enabled), None
            )
            if model and provider:
                if provider.protocol != PROTOCOL:
                    return model, provider
        return None

    @staticmethod
    def embedding_key(model) -> str:
        # Vectors from different models or dimensions never share an index.
        return f"{model.provider}:{model.model}" + (f"@{model.dims}" if model.dims else "")

    async def embed(
        self, texts: list[str], *, query: bool = False, settings=None, route: str = "embedding"
    ) -> tuple[str, list[list[float]]]:
        """Vectors from the embedding route's model, keyed by `embedding_key`."""
        selected = self.embedding_model(settings, route)
        if selected is None:
            raise PublicError("llm.noEmbedding")
        model, provider = selected
        ref = provider.api_key
        token = await self.vault.resolve(ref) if ref.startswith(("env:", "secret:")) else ref
        headers = {"Content-Type": "application/json"}
        base = provider.base_url.rstrip("/")
        if provider.protocol == "gemini":
            headers["x-goog-api-key"] = token
            task = "RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT"
            url = f"{base}/models/{model.model}:batchEmbedContents"
            body: dict[str, Any] = {
                "requests": [
                    {
                        "model": f"models/{model.model}",
                        "content": {"parts": [{"text": text}]},
                        "taskType": task,
                        **({"outputDimensionality": model.dims} if model.dims else {}),
                    }
                    for text in texts
                ]
            }
        else:
            if token:
                headers["Authorization"] = f"Bearer {token}"
            url = base + "/embeddings"
            body = {"model": model.model, "input": texts, **model.extra_body}
            if model.dims:
                body["dimensions"] = model.dims
        try:
            async with self.semaphore:
                response = await self._request(provider, "POST", url, json=body, headers=headers)
            data = response.json()
            if provider.protocol == "gemini":
                vectors = [row["values"] for row in data["embeddings"]]
            else:
                rows = sorted(data["data"], key=lambda row: row.get("index", 0))
                vectors = [row["embedding"] for row in rows]
            if len(vectors) != len(texts) or any(
                not v or len(v) != len(vectors[0]) for v in vectors
            ):
                raise ValueError("embedding count or dimensions mismatch")
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            await self._record_call(model, "embedding", "failed")
            log.warning(
                Text("llm.log.embeddingFailed", display_name=model.display_name, failure=failure(provider.id, exc)),
                route,
            )
            await self.store.activity("provider.failed", failure(provider.id, exc))
            self.record_failure(provider.id)
            raise PublicError("llm.embeddingFailed") from None
        await self._record_call(model, "embedding", "ok", data.get("usage"))
        return self.embedding_key(model), vectors

    async def decide(
        self, route: str, state: str | dict, questions: dict, settings=None
    ) -> dict | None:
        """Ask the route's System One decision models; None lets callers fall back to text."""
        cfg = settings or self.config.value
        for model_id in cfg.routes.get(route, []):
            model = next((m for m in cfg.models if m.id == model_id and m.enabled), None)
            provider = next(
                (
                    p
                    for p in cfg.providers
                    if model and p.id == model.provider and p.enabled and p.protocol == "systemone"
                ),
                None,
            )
            if not model or not provider:
                continue
            ref = provider.api_key
            token = await self.vault.resolve(ref) if ref.startswith(("env:", "secret:")) else ref
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            try:
                async with self.semaphore:
                    response = await self._request(
                        provider,
                        "POST",
                        provider.base_url.rstrip("/") + "/systemone",
                        json={"model": model.model, "state": state, "questions": questions},
                        headers=headers,
                    )
                data = response.json()
                answers = data["answers"]
                if not isinstance(answers, dict) or answers.keys() != questions.keys():
                    raise ValueError("incomplete answers")
                await self._record_call(model, "systemone", "ok", data.get("usage"))
                return answers
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                await self._record_call(model, "systemone", "failed")
                log.warning(
                    Text("llm.log.decisionFailed", display_name=model.display_name, failure=failure(provider.id, exc)), route
                )
                await self.store.activity("provider.failed", failure(provider.id, exc))
                self.record_failure(provider.id)
        return None
