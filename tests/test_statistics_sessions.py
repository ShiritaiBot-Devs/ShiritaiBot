import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from shiritaibot.config import Settings
from shiritaibot.core import EventBus, PublicError, record_usage_for
from shiritaibot.dependencies import RuntimeEnvironment
from shiritaibot.llm import ProviderManager
from shiritaibot.store import Store
from shiritaibot.web import create_app


def test_usage_is_attributed_across_concurrent_provider_calls(tmp_path):
    async def run():
        store = Store(tmp_path, EventBus())
        await store.open()
        providers = [
            {"id": "chat", "base_url": "https://chat.example.test"},
            {
                "id": "decision",
                "base_url": "https://decision.example.test",
                "protocol": "systemone",
            },
        ]
        models = [
            {"id": "chat", "provider": "chat", "model": "chat"},
            {"id": "embedding", "provider": "chat", "model": "vec", "kind": "embedding"},
            {"id": "decision", "provider": "decision", "model": "decision"},
        ]
        config = SimpleNamespace(
            value=Settings.model_validate(
                {
                    "providers": providers,
                    "models": models,
                    "routes": {"chat": ["chat"], "embedding": ["embedding"], "light": ["decision"]},
                }
            )
        )
        vault = SimpleNamespace(resolve=AsyncMock(return_value=""))
        llm = ProviderManager(config, vault, store)

        async def generate(_provider, _path, body, *_):
            await asyncio.sleep(0)
            if body["messages"][-1]["content"] == "fail":
                raise httpx.ReadTimeout("local fixture")
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "answer"}}],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 2},
                },
            )

        async def request(_provider, _method, url, **_):
            await asyncio.sleep(0)
            payload = (
                {"data": [{"index": 0, "embedding": [0.5]}]}
                if url.endswith("/embeddings")
                else {"answers": {"test": {}}}
            )
            return httpx.Response(200, json={**payload, "usage": {"total_tokens": 3}})

        llm._generate = generate
        llm._request = request

        async def first():
            with record_usage_for("web:first"):
                await llm.complete("chat", "", [{"role": "user", "content": "one"}], [])
                await llm.decide("light", {}, {"test": {}})
                await llm.embed(["embedding input"])

        async def second():
            with record_usage_for("group:qq:42"):
                with pytest.raises(PublicError):
                    await llm.complete("chat", "", [{"role": "user", "content": "fail"}], [])
                await llm.complete(
                    "chat", "", [{"role": "user", "content": "two"}], [], scope="web:override"
                )

        try:
            await asyncio.gather(first(), second())
            await llm.complete("chat", "", [{"role": "user", "content": "standalone"}], [])
            actual = {
                (row["scope"], row["model"], row["status"]) for row in await store.list("usage")
            }
            assert actual == {
                ("web:first", "chat", "ok"),
                ("web:first", "decision", "ok"),
                ("web:first", "embedding", "ok"),
                ("group:qq:42", "chat", "failed"),
                ("web:override", "chat", "ok"),
                ("", "chat", "ok"),
            }
        finally:
            await llm.client.aclose()
            await store.close()

    asyncio.run(run())


def test_statistics_api_filters_sessions_and_limits_the_rolling_window(tmp_path, monkeypatch):
    end = datetime(2026, 11, 22, 4, 30, tzinfo=UTC)
    start = end - timedelta(days=30)
    midnight = datetime(2026, 11, 21, 16, tzinfo=UTC)
    monkeypatch.setattr("shiritaibot.store.statistics.now", lambda: end)
    monkeypatch.setattr(RuntimeEnvironment, "prepare", AsyncMock())

    async def seed():
        store = Store(tmp_path, EventBus())
        await store.open()
        try:

            async def record(key, scope, time, token_count, **details):
                await store.put(
                    "usage",
                    key,
                    {"model": "chat", "usage": {"total_tokens": token_count}, **details},
                    scope,
                )
                await store.execute(
                    "UPDATE records SET created=? WHERE kind='usage' AND id=?",
                    (time.timestamp(), key),
                )

            await record("previous", "web:old", start - timedelta(seconds=1), 100)
            await record("first", "web:focus", start, 2)
            await record("yesterday", "web:focus", midnight - timedelta(seconds=1), 4)
            await record("today", "web:focus", midnight, 8, status="ok", ttft_ms=200)
            await record("today-again", "web:focus", end, 16, status="ok", ttft_ms=400)
            await record("failed", "web:focus", end, 0, status="failed")
            await record("future", "web:focus", end + timedelta(seconds=1), 200)
            await record("group", "group:qq:32", end, 32)
            await record("legacy", "", end, 64)
            await store.put("session", "web:no-usage", {})
            await store.put("turn", "turn-1", {"status": "failed"}, "web:focus")
            await store.execute(
                "UPDATE records SET created=? WHERE kind='turn'", (midnight.timestamp(),)
            )
            for scope, turn, payload in [
                ("web:focus", "turn-1", {"role": "user"}),
                ("web:focus", "turn-1", {"role": "assistant", "tool_calls": [{"id": "tool"}]}),
                ("web:focus", "turn-1", {"role": "assistant", "tool_calls": []}),
                ("web:focus", "handoff", {"role": "assistant"}),
                ("group:qq:32", "turn-2", {"role": "user"}),
            ]:
                await store.append(scope, turn, payload)
            await store.execute("UPDATE messages SET created=?", (midnight.timestamp(),))
        finally:
            await store.close()

    asyncio.run(seed())
    headers = {"Authorization": "Bearer test-token"}
    with TestClient(create_app(tmp_path, token="test-token")) as client:
        assert client.get("/api/stats/sessions").status_code == 401
        assert set(client.get("/api/stats/sessions", headers=headers).json()) == {
            "group:qq:32",
            "web:focus",
            "web:no-usage",
        }
        assert client.get("/api/stats?hours=721", headers=headers).status_code == 422
        assert client.get("/api/stats?hours=0", headers=headers).status_code == 422

        def stats(hours, session=None):
            params = {"hours": hours}
            if session is not None:
                params["session"] = session
            response = client.get("/api/stats", params=params, headers=headers)
            assert response.status_code == 200
            return response.json()

        all_sessions = stats(720)
        assert all_sessions["totals"]["total_tokens"] == 126
        assert all_sessions["totals"]["messages"] == 3
        assert stats(720, "")["totals"]["total_tokens"] == 64
        assert stats(720, "web:no-usage")["totals"]["total_tokens"] == 0
        focus = stats(720, "web:focus")
        assert focus["bucket_hours"] == 24
        assert len(focus["buckets"]) == 31
        assert focus["totals"]["total_tokens"] == 30
        assert focus["totals"]["messages"] == 2
        assert focus["totals"]["failed_turns"] == 1
        assert focus["models"][0]["total_tokens"] == 30
        yesterday, today = focus["buckets"][-2:]
        assert yesterday["total_tokens"] == 4
        assert datetime.fromisoformat(today["hour"]) == midnight
        assert today["total_tokens"] == 24
        performance = today["model_performance"]["chat"]
        assert performance["ttft_ms"] == 300
        assert performance["error_rate"] == 1 / 3
        recent = stats(24, "web:focus")
        assert recent["bucket_hours"] == 1
        assert recent["totals"]["total_tokens"] == 28
