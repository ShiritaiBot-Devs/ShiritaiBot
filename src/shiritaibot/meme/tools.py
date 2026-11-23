"""Model tools for the meme library: search, view and save (docs/MEMES.md §4)."""

from __future__ import annotations

import asyncio

from shiritaibot.config import is_feishu_session
from shiritaibot.core import PublicError, Tool
from shiritaibot.i18n import t
from shiritaibot.llm.media import view_marker
from shiritaibot.meme.core import MEME_ID, download
from shiritaibot.tools import schema


def meme_tools(app) -> list[Tool]:
    core = app.memes.core

    def available(ctx) -> bool:
        cfg = app.config.value
        return cfg.meme.enabled and not is_feishu_session(ctx.session)

    async def meme_search(ctx, args):
        results = await core.search(args["query"], args.get("category", ""), args.get("limit", 5))
        return {
            "memes": results,
            "note": t("memes.tools.sendHint"),
        }

    async def meme_view(ctx, args):
        row = core.get(args["id"])
        if row is None:
            raise PublicError("memes.tools.missing")
        # Rendering attaches the image for image-capable models and errors for others.
        return {
            "id": row["id"],
            "category": row["category"],
            "image": view_marker(row["id"]),
        }

    async def meme_save(ctx, args):
        record = await app.store.get("qq_file", args["ref"])
        if not record or record["scope"] != ctx.session:
            raise PublicError("memes.tools.refOtherSession")
        cached = app.store.home / "media" / args["ref"]
        try:
            data = await asyncio.to_thread(cached.read_bytes)
        except OSError:
            data = await download(app.llm.client, record["data"].get("url", ""))
        if data is None:
            raise PublicError("memes.tools.imageExpired")
        row = await core.ingest(
            data,
            source="agent",
            origin=ctx.session,
            category=args.get("category", ""),
            description=args.get("description", ""),
            enforce_capacity=True,
        )
        return {"id": row["id"], "category": row["category"], "description": row["description"]}

    return [
        Tool(
            "meme_search",
            "memes.tools.search",
            schema(
                {
                    "query": {"type": "string", "minLength": 1, "maxLength": 200},
                    "category": {"type": "string", "maxLength": 32},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                ["query"],
            ),
            meme_search,
            risk="safe",
            chat=True,
            available=available,
        ),
        Tool(
            "meme_view",
            "memes.tools.view",
            schema({"id": {"type": "string", "pattern": MEME_ID.pattern}}, ["id"]),
            meme_view,
            risk="safe",
            chat=True,
            available=available,
        ),
        Tool(
            "meme_save",
            "memes.tools.save",
            schema(
                {
                    "ref": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                    "category": {"type": "string", "maxLength": 32},
                    "description": {"type": "string", "maxLength": 300},
                },
                ["ref"],
            ),
            meme_save,
            risk="normal",
            chat=True,
            available=available,
            timeout=120,
        ),
    ]
