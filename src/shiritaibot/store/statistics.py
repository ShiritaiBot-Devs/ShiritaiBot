from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from shiritaibot.core import now

TOKEN_FIELDS = ("input_tokens", "output_tokens", "cached_tokens", "total_tokens")
MAX_STATS_HOURS = 30 * 24


async def statistics_sessions(store) -> list[str]:
    end = now().timestamp()
    rows = await store.query(
        "SELECT id AS session FROM records WHERE kind='session' AND id NOT GLOB 'background:*' "
        "UNION SELECT scope FROM records WHERE kind='usage' AND scope!='' AND created BETWEEN ? AND ? "
        "ORDER BY session",
        (end - MAX_STATS_HOURS * 3600, end),
    )
    return [row["session"] for row in rows]


@dataclass
class ModelPerformance:
    calls: int = 0
    failures: int = 0
    timed_calls: int = 0
    ttft_ms: float = 0
    generation_tokens: int = 0
    generation_seconds: float = 0

    def add(self, data: dict, output_tokens: int):
        self.calls += 1
        if data["status"] == "failed":
            self.failures += 1
            return
        if data.get("ttft_ms") is not None:
            self.timed_calls += 1
            self.ttft_ms += data["ttft_ms"]
        if data.get("generation_seconds", 0) > 0 and output_tokens > 1:
            self.generation_seconds += data["generation_seconds"]
            self.generation_tokens += output_tokens - 1

    def metrics(self):
        return {
            "error_rate": self.failures / self.calls,
            "ttft_ms": self.ttft_ms / self.timed_calls if self.timed_calls else None,
            "tps": self.generation_tokens / self.generation_seconds
            if self.generation_seconds
            else None,
        }


def tokens(usage: dict, protocol: str = "") -> dict:
    def number(key, source=None):
        value = (usage if source is None else source).get(key, 0)
        return max(0, int(value or 0))

    if "promptTokenCount" in usage or protocol == "gemini":
        incoming = number("promptTokenCount")
        outgoing = number("candidatesTokenCount") + number("thoughtsTokenCount")
        cached = number("cachedContentTokenCount")
        total = number("totalTokenCount") or incoming + outgoing
    elif (
        protocol == "anthropic"
        or "cache_read_input_tokens" in usage
        or "cache_creation_input_tokens" in usage
    ):
        cached = number("cache_read_input_tokens")
        incoming = number("input_tokens") + cached + number("cache_creation_input_tokens")
        outgoing = number("output_tokens")
        total = incoming + outgoing
    else:
        incoming = number("input_tokens") if "input_tokens" in usage else number("prompt_tokens")
        outgoing = (
            number("output_tokens") if "output_tokens" in usage else number("completion_tokens")
        )
        details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
        cached = number("cached_tokens", details)
        total = number("total_tokens") or incoming + outgoing
    return {
        "input_tokens": incoming,
        "output_tokens": outgoing,
        "cached_tokens": cached,
        "total_tokens": total,
    }


def cost(counts: dict, model) -> float | None:
    """Estimated spend from per-million prices; None when the model has no prices set."""
    if model is None or not (model.price_input or model.price_cached or model.price_output):
        return None
    cached = min(counts["cached_tokens"], counts["input_tokens"])
    return (
        (counts["input_tokens"] - cached) * model.price_input
        + cached * (model.price_cached or model.price_input)
        + counts["output_tokens"] * model.price_output
    ) / 1_000_000


async def statistics(
    store, hours: int, end: datetime | None = None, models=(), *, session: str | None = None
):
    """None includes all sessions, and the empty scope selects unattributed calls."""
    end_stamp = (end or now()).timestamp()
    start = end_stamp - hours * 3600
    bucket_hours = 24 if hours > 7 * 24 else 1
    seconds = bucket_hours * 3600
    # Daily buckets start at midnight in Asia/Shanghai.
    offset = 8 * 3600 if bucket_hours == 24 else 0
    first = int((start + offset) // seconds)
    last = int((end_stamp + offset) // seconds)
    fields = [
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "total_tokens",
        "llm_calls",
        "incoming_messages",
        "outgoing_messages",
        "messages",
        "failed_turns",
    ]
    buckets = [
        {
            "hour": datetime.fromtimestamp(bucket * seconds - offset, timezone.utc).isoformat(),
            **dict.fromkeys(fields, 0),
            "models": {},
            "model_performance": {},
        }
        for bucket in range(first, last + 1)
    ]
    args = (start, end_stamp) if session is None else (start, end_stamp, session)
    rows = await store.query(
        "SELECT kind,data,created FROM records WHERE kind IN ('usage','turn') AND created BETWEEN ? AND ?"
        + (" AND scope=?" if session is not None else ""),
        args,
    )
    per_model: dict[str, dict] = {}
    for row in rows:
        bucket = buckets[int((row["created"] + offset) // seconds) - first]
        data = json.loads(row["data"])
        if row["kind"] == "usage":
            bucket["llm_calls"] += 1
            name = str(data.get("model") or "")
            entry = per_model.setdefault(
                name,
                {"model": name, "llm_calls": 0, **dict.fromkeys(TOKEN_FIELDS, 0)},
            )
            entry["llm_calls"] += 1
            if data.get("model_label"):
                entry["model_label"] = data["model_label"]
            counted = tokens(data.get("usage") or {}, data.get("protocol", ""))
            # Legacy success-only records cannot establish the historical error rate.
            if data.get("status") in {"ok", "failed"}:
                performance = bucket["model_performance"].setdefault(name, ModelPerformance())
                performance.add(data, counted["output_tokens"])
            for key, value in counted.items():
                bucket[key] += value
                entry[key] += value
            bucket["models"][name] = bucket["models"].get(name, 0) + counted["total_tokens"]
        elif data.get("status") == "failed":
            bucket["failed_turns"] += 1
    for bucket in buckets:
        bucket["model_performance"] = {
            name: performance.metrics() for name, performance in bucket["model_performance"].items()
        }
    rows = await store.query(
        "SELECT CAST((created + ?) / ? AS INTEGER) AS bucket, "
        "json_extract(data,'$.role') AS role, count(*) AS count "
        "FROM messages WHERE created BETWEEN ? AND ? AND turn!='handoff' "
        "AND json_extract(data,'$.role') IN ('user','assistant') "
        "AND coalesce(json_array_length(data,'$.tool_calls'),0)=0"
        + (" AND session=?" if session is not None else "")
        + " GROUP BY bucket,role",
        (offset, seconds, *args),
    )
    for row in rows:
        bucket = buckets[row["bucket"] - first]
        bucket["incoming_messages" if row["role"] == "user" else "outgoing_messages"] += row[
            "count"
        ]
        bucket["messages"] += row["count"]
    configured = {model.id: model for model in models}
    costs: dict[str, float] = {}
    for entry in per_model.values():
        model = configured.get(entry["model"])
        price = cost(entry, model)
        entry["cost"] = price
        currency = model.currency if model is not None and price is not None else None
        entry["currency"] = currency
        entry["cache_hit_rate"] = (
            entry["cached_tokens"] / entry["input_tokens"] if entry["input_tokens"] else None
        )
        if price is not None and currency is not None:
            costs[currency] = costs.get(currency, 0) + price
    return {
        "hours": hours,
        "bucket_hours": bucket_hours,
        "timezone": "Asia/Shanghai",
        "buckets": buckets,
        "start": datetime.fromtimestamp(start, timezone.utc).isoformat(),
        "end": datetime.fromtimestamp(end_stamp, timezone.utc).isoformat(),
        "totals": {key: sum(bucket[key] for bucket in buckets) for key in fields},
        "models": sorted(per_model.values(), key=lambda row: (-row["total_tokens"], row["model"])),
        "costs": costs,
    }
