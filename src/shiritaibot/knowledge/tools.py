"""Model tools for the knowledge base (docs/KNOWLEDGE.md §4)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from shiritaibot.core import PublicError, Tool
from shiritaibot.i18n import Text, t
from shiritaibot.knowledge.core import REF_PATTERN
from shiritaibot.knowledge.extract import FORMATS
from shiritaibot.tools import schema
from shiritaibot.tools.qq import download_qq_file, is_sensitive, session_dir

DOCUMENT = {
    "type": "string",
    "pattern": REF_PATTERN,
    "description": "knowledge.tools.param.document",
}
FORMAT_NAMES = "、".join(sorted(FORMATS))


def knowledge_tools(app) -> list[Tool]:
    def readable(ctx) -> bool:
        config = app.config.value.knowledge
        return config.library_for(ctx.session) is not None and (
            ctx.background
            or app.permissions.session_policy(ctx).mode == "agent"
            or ctx.session in config.sessions
            or config.chat
        )

    def writable(ctx) -> bool:
        return (
            ctx.owner
            and not ctx.group
            and app.config.value.knowledge.library_for(ctx.session) is not None
        )

    def selected(ctx):
        library = app.config.value.knowledge.library_for(ctx.session)
        if library is None:
            raise PublicError("knowledge.tools.disabled")
        return app.knowledge_for(library)

    async def kb_search(ctx, args):
        results = await selected(ctx).search(
            args["query"], args.get("limit", 5), args.get("document", "")
        )
        return {
            "results": results,
            "note": t("knowledge.tools.note"),
        }

    async def kb_read(ctx, args):
        kb = selected(ctx)
        ref = args["document"]
        document = await kb.get(ref)
        if document is None:
            raise PublicError("knowledge.tools.documentMissing")
        start = args.get("start", 0)
        rows = await kb.chunks(ref, start, args.get("count", 3))
        last = rows[-1]["seq"] + 1 if rows else start
        return {
            "title": document["title"],
            "total_chunks": document["chunks"],
            "chunks": rows,
            **({"next_start": last} if last < document["chunks"] else {}),
        }

    async def kb_list(ctx, args):
        result = await selected(ctx).listing(args.get("query", ""), args.get("page", 1), 20)
        result["items"] = [
            {key: row[key] for key in ("id", "title", "filename", "format", "chunks")}
            for row in result["items"]
        ]
        return result

    async def kb_import(ctx, args):
        if not ctx.owner or ctx.group:
            raise PublicError("knowledge.tools.writeOwnerOnly")
        kb = selected(ctx)
        given = [name for name in ("ref", "path", "content") if args.get(name)]
        if len(given) != 1:
            raise PublicError("knowledge.tools.oneSource")
        title, filename = args.get("title", ""), args.get("filename", "")
        if args.get("content"):
            if not title:
                raise PublicError("knowledge.tools.titleRequired")
            data = args["content"].encode("utf-8")
            filename, source = filename or "note.md", "agent"
        else:
            if args.get("ref"):
                found = await download_qq_file(app, ctx.session, args["ref"])
                path, filename = Path(found["path"]), filename or found["name"]
                source = "qq"
            else:
                path = await asyncio.to_thread(locate, ctx, args["path"])
                filename, source = filename or path.name, "agent"
            limit = app.config.value.knowledge.max_file_mb * 1024 * 1024
            if await asyncio.to_thread(lambda: path.stat().st_size) > limit:
                raise PublicError("knowledge.tools.fileTooLarge", value=limit // 1024 // 1024)
            data = await asyncio.to_thread(path.read_bytes)
        row = await kb.add(data, filename, title=title, source=source, origin=ctx.session)
        return {key: row[key] for key in ("id", "title", "format", "chars", "chunks", "duplicate")}

    def locate(ctx, value: str) -> Path:
        """Only the caller's own workspace and downloads; this tool is not a file reader."""
        workspace = session_dir(app.home, "workspaces", ctx.session).resolve()
        downloads = session_dir(app.home, "files", ctx.session).resolve()
        path = Path(value).expanduser()
        path = (path if path.is_absolute() else workspace / path).resolve()
        if is_sensitive(app.home, path) or not (
            path.is_relative_to(workspace) or path.is_relative_to(downloads)
        ):
            raise PublicError("knowledge.tools.importPath")
        if not path.is_file():
            raise PublicError("knowledge.tools.fileMissing")
        return path

    async def kb_delete(ctx, args):
        if not ctx.owner or ctx.group:
            raise PublicError("knowledge.tools.editOwnerOnly")
        await selected(ctx).delete(args["document"])
        return {"deleted": args["document"]}

    return [
        Tool(
            "kb_search",
            "knowledge.tools.search",
            schema(
                {
                    "query": {"type": "string", "minLength": 1, "maxLength": 500},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    "document": {**DOCUMENT, "description": "knowledge.tools.param.onlyDocument"},
                },
                ["query"],
            ),
            kb_search,
            risk="safe",
            chat=True,
            available=readable,
        ),
        Tool(
            "kb_read",
            "knowledge.tools.read",
            schema(
                {
                    "document": DOCUMENT,
                    "start": {"type": "integer", "minimum": 0},
                    "count": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                ["document"],
            ),
            kb_read,
            risk="safe",
            chat=True,
            available=readable,
        ),
        Tool(
            "kb_list",
            "knowledge.tools.list",
            schema(
                {
                    "query": {"type": "string", "maxLength": 200},
                    "page": {"type": "integer", "minimum": 1},
                }
            ),
            kb_list,
            risk="safe",
            chat=True,
            available=readable,
        ),
        Tool(
            "kb_import",
            Text("knowledge.tools.save", FORMAT_NAMES=FORMAT_NAMES),
            schema(
                {
                    "ref": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                    "path": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "content": {"type": "string", "minLength": 1, "maxLength": 200000},
                    "title": {"type": "string", "maxLength": 200},
                    "filename": {"type": "string", "maxLength": 200},
                }
            ),
            kb_import,
            risk="normal",
            available=writable,
            timeout=300,
        ),
        Tool(
            "kb_delete",
            "knowledge.tools.delete",
            schema({"document": DOCUMENT}, ["document"]),
            kb_delete,
            risk="sensitive",
            available=writable,
        ),
    ]
