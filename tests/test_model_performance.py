import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from shiritaibot.config import ModelConfig, ProviderConfig
from shiritaibot.core import EventBus, PublicError
from shiritaibot.llm import ProviderManager, parse_reply
from shiritaibot.llm.streaming import collect_stream
from shiritaibot.store import Store
from shiritaibot.store.statistics import statistics


class EventStream(httpx.AsyncByteStream):
    def __init__(self, *events):
        self.events = events

    async def __aiter__(self):
        for event in self.events:
            yield f"data: {json.dumps(event)}\r\n\r\n".encode()


def test_route_failure_and_buffered_fallback(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        provider = ProviderConfig(id="p", api_key="", max_retries=0)
        models = [ModelConfig(id=name, model=name, provider="p") for name in ("a", "b")]
        settings = SimpleNamespace(
            concurrency=1, providers=[provider], models=models, routes={"chat": ["a", "b"]}
        )
        manager = ProviderManager(SimpleNamespace(value=settings), None, store)
        await manager.client.aclose()
        requests = []

        def handler(request):
            body = json.loads(request.content)
            requests.append((body["model"], body["stream"]))
            if body["model"] == "a":
                return httpx.Response(500)
            if body["stream"]:
                return httpx.Response(400, text="stream unsupported")
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"completion_tokens": 12},
                },
            )

        manager.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            assert (await manager.complete("chat", "", [], [])).text == "ok"
            assert requests == [("a", True), ("b", True), ("b", False)]
            await store.put("usage", "old", {"model": "legacy", "usage": {"completion_tokens": 2}})
            for i, (seconds, count) in enumerate([(1, 5), (3, 17)]):
                await store.put(
                    "usage",
                    f"s{i}",
                    {
                        "model": "a",
                        "status": "ok",
                        "ttft_ms": 100 + 200 * i,
                        "generation_seconds": seconds,
                        "usage": {"completion_tokens": count},
                    },
                )
            data = await statistics(store, 2)
            measured = next(
                b["model_performance"] for b in data["buckets"] if "a" in b["model_performance"]
            )
            assert measured == {
                "a": {"error_rate": 1 / 3, "tps": 5, "ttft_ms": 200},
                "b": {"error_rate": 0, "tps": None, "ttft_ms": None},
            }
            assert data["totals"]["output_tokens"] == 36
            assert data["totals"]["llm_calls"] == 5
        finally:
            await manager.close()
            await store.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    ("protocol", "events", "usage"),
    [
        (
            "chat_completions",
            [
                {"choices": [{"delta": {"role": "assistant"}}]},
                {"choices": [{"delta": {"content": "ab"}}]},
                {
                    "choices": [
                        {
                            "delta": {
                                "content": "c",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "c",
                                        "function": {"name": "f", "arguments": "{"},
                                    }
                                ],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "}"}}]},
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
                {"choices": [], "usage": {"completion_tokens": 12}},
            ],
            {"completion_tokens": 12},
        ),
        (
            "responses",
            [
                {"type": "response.created"},
                {"type": "response.output_text.delta", "delta": "ab"},
                {"type": "response.output_text.delta", "delta": "c"},
                {
                    "type": "response.completed",
                    "response": {
                        "output": [
                            {"type": "message", "content": [{"text": "abc"}]},
                            {
                                "type": "function_call",
                                "name": "f",
                                "call_id": "c",
                                "arguments": "{}",
                            },
                        ],
                        "usage": {"output_tokens": 12},
                    },
                },
            ],
            {"output_tokens": 12},
        ),
        (
            "anthropic",
            [
                {"type": "message_start", "message": {"usage": {"input_tokens": 4}}},
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "abc"},
                },
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {"type": "tool_use", "name": "f", "id": "c", "input": {}},
                },
                {"type": "content_block_delta", "index": 1, "delta": {"partial_json": "{}"}},
                {"type": "message_delta", "usage": {"output_tokens": 12}},
                {"type": "message_stop"},
            ],
            {"input_tokens": 4, "output_tokens": 12},
        ),
        (
            "gemini",
            [
                {"candidates": [{"content": {"parts": [{"text": "ab"}]}}]},
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"text": "c"},
                                    {
                                        "functionCall": {"id": "c", "name": "f", "args": {}},
                                        "thoughtSignature": "sig",
                                    },
                                ]
                            },
                            "finishReason": "STOP",
                        }
                    ],
                    "usageMetadata": {"candidatesTokenCount": 12},
                },
            ],
            {"candidatesTokenCount": 12},
        ),
    ],
)
def test_streamed_reply_and_timing(monkeypatch, protocol, events, usage):
    from shiritaibot.llm import streaming

    async def run():
        ticks = iter(range(1, len(events) + 1))
        monkeypatch.setattr(streaming, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
        response = httpx.Response(200, stream=EventStream(*events))
        data, timing = await collect_stream(protocol, response, 0)
        reply = parse_reply(protocol, "m", data)
        assert reply.text == "abc"
        assert reply.calls[0]["function"] == {"name": "f", "arguments": "{}"}
        assert reply.usage == usage
        assert timing["ttft_ms"] == {"gemini": 1000, "anthropic": 3000}.get(protocol, 2000)
        assert timing["generation_seconds"] == {"chat_completions": 2, "anthropic": 2}.get(
            protocol, 1
        )
        if protocol == "gemini":
            assert reply.opaque["data"][-1]["thoughtSignature"] == "sig"

    asyncio.run(run())


def test_truncated_stream_is_a_failure():
    async def run():
        with pytest.raises(ValueError, match="incomplete"):
            await collect_stream(
                "responses",
                httpx.Response(
                    200,
                    stream=EventStream({"type": "response.output_text.delta", "delta": "partial"}),
                ),
                0,
            )

    asyncio.run(run())


def test_streamed_completion_and_non_text_failures(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        providers = [
            ProviderConfig(id="p", api_key="", max_retries=1),
            ProviderConfig(id="s", api_key="", protocol="systemone", max_retries=0),
        ]
        models = [
            ModelConfig(id="chat", model="chat", provider="p"),
            ModelConfig(id="embed", model="embed", provider="p", kind="embedding"),
            ModelConfig(id="decision", model="decision", provider="s"),
        ]
        settings = SimpleNamespace(
            concurrency=1,
            providers=providers,
            models=models,
            routes={"chat": ["chat"], "embedding": ["embed"], "decision": ["decision"]},
        )
        manager = ProviderManager(SimpleNamespace(value=settings), None, store)
        await manager.client.aclose()
        attempts = 0

        def handler(request):
            nonlocal attempts
            if not request.url.path.endswith("/chat/completions"):
                return httpx.Response(403)
            attempts += 1
            if attempts == 1:
                return httpx.Response(429, headers={"retry-after": "0"})
            assert json.loads(request.content)["stream_options"]["include_usage"]
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=EventStream(
                    {"choices": [{"delta": {"content": "hi"}}]},
                    {"choices": [{"delta": {"content": "!"}, "finish_reason": "stop"}]},
                    {"choices": [], "usage": {"completion_tokens": 2}},
                ),
            )

        manager.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            assert (await manager.complete("chat", "", [], [])).text == "hi!"
            with pytest.raises(PublicError, match="Embedding"):
                await manager.embed(["hello"])
            assert await manager.decide("decision", "state", {"x": "?"}) is None
            records = {r["model"]: r for r in await store.list("usage")}
            assert attempts == 2 and len(records) == 3
            assert records["chat"]["status"] == "ok"
            assert records["chat"]["ttft_ms"] >= 0
            assert records["chat"]["generation_seconds"] > 0
            assert records["embed"]["status"] == records["decision"]["status"] == "failed"
        finally:
            await manager.close()
            await store.close()

    asyncio.run(run())
