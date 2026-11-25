"""Plugin market: fetch, validate and cache plugin indexes (docs/PLUGINS.md "Plugin market").

The index format is defined by the store (ShiritaiBot plugin store, ``docs/INDEX.md``). Each
source is fetched, checked entry by entry (bad entries are dropped), and cached under
``<home>/plugin_market/``. When a source fails, its last good copy is used. Entries only name a
GitHub repository and a commit; installing downloads that commit from GitHub and nothing else.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .manifest import ManifestError, version_matches

INDEX_FORMAT = "shiritaibot-plugin-index"
INDEX_VERSION = 1
MAX_INDEX = 5 * 1024 * 1024
MAX_ENTRIES = 5000
FRESH_SECONDS = 600
RETRY_SECONDS = 60

ID = re.compile(r"[a-z0-9_]{1,64}")
REPO = re.compile(r"https://github\.com/([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,100})")
COMMIT = re.compile(r"[0-9a-f]{40}")
LANGUAGE = re.compile(r"[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8}){0,2}")
PLATFORMS = {"qq", "web"}
DISCLOSURE_KINDS = {
    "data_sharing",
    "telemetry",
    "external_account",
    "paid_service",
    "file_access",
    "network",
    "system",
    "other",
}

# Error codes a source can report, and their catalog keys.
MARKET_ERRORS = {
    "network": "plugins.market.network",
    "http": "plugins.market.http",
    "json": "plugins.market.json",
    "cache": "plugins.market.cache",
    "index_format": "plugins.market.index_format",
    "index_version": "plugins.market.index_version",
    "index_size": "plugins.market.index_size",
}


def _string(value: Any, limit: int) -> str | None:
    return value.strip()[:limit] if isinstance(value, str) else None


def _text(value: Any, limit: int, required: bool = False) -> str | dict[str, str] | None:
    """Index text: a string, or a map of language tag → string (the format says so)."""
    if isinstance(value, str):
        value = value.strip()[:limit]
        return None if required and not value else value
    if isinstance(value, dict) and value and len(value) <= 16:
        result = {}
        for key, item in value.items():
            if not isinstance(key, str) or not LANGUAGE.fullmatch(key) or not isinstance(item, str):
                return None
            result[key] = item.strip()[:limit]
        return None if required and not any(result.values()) else result
    return None if required or value is not None else ""


def _strings(value: Any, limit: int, count: int) -> list[str] | None:
    if not isinstance(value, list) or len(value) > count:
        return None
    if not all(isinstance(item, str) for item in value):
        return None
    return [item.strip()[:limit] for item in value if item.strip()]


def _disclosure(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    text = _text(value.get("text"), 600, required=True)
    if text is None:
        return None
    kind = value.get("kind")
    return {
        # Kinds added in a later index version are shown as "other".
        "kind": kind if kind in DISCLOSURE_KINDS else "other",
        "text": text,
        "location": _string(value.get("location"), 200) or "",
    }


def compatible(spec: str) -> bool:
    try:
        return version_matches(spec) if spec else True
    except ManifestError:
        return False


def validate_entry(value: Any) -> dict | None:
    """One index entry, cleaned up, or None when it can't be used."""
    if not isinstance(value, dict):
        return None
    plugin_id = value.get("id")
    repo = value.get("repo")
    commit = value.get("commit")
    if not (isinstance(plugin_id, str) and ID.fullmatch(plugin_id)) or plugin_id in {"core", "__pycache__"}:
        return None
    if not (isinstance(repo, str) and (match := REPO.fullmatch(repo))):
        return None
    if any(part in {".", ".."} for part in match.groups()):
        return None
    if not (isinstance(commit, str) and COMMIT.fullmatch(commit)):
        return None
    name = _text(value.get("name"), 200, required=True)
    description = _text(value.get("description", ""), 4000)
    short_description = _text(value.get("short_description", ""), 400)
    version = _string(value.get("version"), 64)
    tag = _string(value.get("tag"), 200)
    tags = _strings(value.get("tags", []), 40, 20)
    platforms = _strings(value.get("support_platforms", []), 20, 10)
    disclosures = value.get("disclosures", [])
    required = _string(value.get("shiritaibot_version", ""), 100)
    if (
        name is None
        or description is None
        or short_description is None
        or not version
        or tag is None
        or tags is None
        or platforms is None
        or not set(platforms) <= PLATFORMS
        or required is None
        or not isinstance(disclosures, list)
        or len(disclosures) > 50
    ):
        return None
    checked = [_disclosure(item) for item in disclosures]
    if any(item is None for item in checked):
        return None
    page = _string(value.get("page"), 500) or ""
    return {
        "id": plugin_id,
        "name": name,
        "description": description,
        "short_description": short_description,
        "author": _string(value.get("author"), 100) or match.group(1),
        "owner": match.group(1),
        "repo": repo,
        "version": version,
        "tag": tag,
        "commit": commit,
        "api": value.get("api") if value.get("api") in (1, 2) else 2,
        "tags": tags,
        "support_platforms": platforms,
        "shiritaibot_version": required,
        "compatible": compatible(required),
        "updated_at": _string(value.get("updated_at"), 40) or "",
        "disclosures": checked,
        "page": page if page.startswith("https://") else "",
    }


class MarketIndexError(ValueError):
    """The document as a whole isn't a plugin index this version can read."""


def validate_index(document: Any) -> tuple[list[dict], int]:
    """Usable entries and the number dropped. Raises ``MarketIndexError`` for a bad document."""
    if not isinstance(document, dict) or document.get("format") != INDEX_FORMAT:
        raise MarketIndexError("format")
    if document.get("version") != INDEX_VERSION:
        raise MarketIndexError("version")
    plugins = document.get("plugins")
    if not isinstance(plugins, list) or len(plugins) > MAX_ENTRIES:
        raise MarketIndexError("format")
    entries: list[dict] = []
    seen: set[str] = set()
    dropped = 0
    for raw in plugins:
        entry = validate_entry(raw)
        if entry is None or entry["id"] in seen:
            dropped += 1
            continue
        seen.add(entry["id"])
        entries.append(entry)
    return entries, dropped


class PluginMarket:
    """Fetches the configured sources, keeping a validated copy of each on disk."""

    def __init__(self, home: Path):
        self.folder = home / "plugin_market"
        self._memory: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    def _path(self, url: str) -> Path:
        return self.folder / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".json")

    def _read_cache(self, url: str) -> dict | None:
        try:
            data = json.loads(self._path(url).read_text("utf-8"))
            if data.get("url") != url:
                return None
            entries, dropped = validate_index(data.get("index"))
            return {"entries": entries, "dropped": dropped, "fetched_at": data.get("fetched_at", "")}
        except (OSError, ValueError, AttributeError):
            return None

    def _write_cache(self, url: str, document: dict, fetched_at: str) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._path(url)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"url": url, "fetched_at": fetched_at, "index": document}, ensure_ascii=False),
            "utf-8",
        )
        os.replace(temporary, path)

    async def _download(self, url: str) -> Any:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            async with client.stream("GET", url, headers={"Accept": "application/json"}) as response:
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_INDEX:
                        raise MarketIndexError("size")
        return json.loads(bytes(data))

    async def _source(self, url: str, refresh: bool) -> dict:
        held = self._memory.get(url)
        # A good copy is reused for 10 minutes; a failed source is retried after a minute.
        if held and not refresh and time.monotonic() - held["checked"] < (RETRY_SECONDS if held["stale"] else FRESH_SECONDS):
            return held
        try:
            document = await self._download(url)
            entries, dropped = validate_index(document)
            fetched_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
            await asyncio.to_thread(self._write_cache, url, document, fetched_at)
            result = {"entries": entries, "dropped": dropped, "fetched_at": fetched_at, "stale": False, "error": ""}
        except (httpx.HTTPError, ValueError, OSError) as exc:
            if isinstance(exc, MarketIndexError):
                error = f"index_{exc.args[0]}"
            elif isinstance(exc, httpx.HTTPStatusError):
                error = "http"
            elif isinstance(exc, httpx.HTTPError):
                error = "network"
            elif isinstance(exc, OSError):
                error = "cache"
            else:
                error = "json"
            cached = held or await asyncio.to_thread(self._read_cache, url)
            result = {
                "entries": cached["entries"] if cached else [],
                "dropped": cached["dropped"] if cached else 0,
                "fetched_at": cached["fetched_at"] if cached else "",
                "stale": True,
                "error": error,
            }
        result["checked"] = time.monotonic()
        self._memory[url] = result
        return result

    async def listing(self, sources: list[str], refresh: bool = False) -> dict:
        """Entries from every source (the first source wins on duplicate IDs) and each
        source's state. ``error`` is a code; the API turns it into text."""
        async with self._lock:
            results = await asyncio.gather(*(self._source(url, refresh) for url in sources))
        plugins: list[dict] = []
        seen: set[str] = set()
        states = []
        for url, result in zip(sources, results, strict=True):
            for entry in result["entries"]:
                if entry["id"] not in seen:
                    seen.add(entry["id"])
                    plugins.append({**entry, "source": url})
            states.append(
                {
                    "url": url,
                    "count": len(result["entries"]),
                    "dropped": result["dropped"],
                    "fetched_at": result["fetched_at"],
                    "stale": result["stale"],
                    "error": result["error"],
                }
            )
        return {"plugins": plugins, "sources": states}
