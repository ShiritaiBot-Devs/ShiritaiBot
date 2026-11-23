"""Agent tools over long-term memory (docs/MEMORY.md §5)."""

from __future__ import annotations

from shiritaibot.core import Context, PublicError, Tool
from shiritaibot.tools import TEXT, schema

SHORT = {"type": "string", "minLength": 1, "maxLength": 200}
NAMES = {"type": "array", "items": SHORT, "maxItems": 20}
PARTICIPANTS = {
    **NAMES,
    "description": "memory.tools.param.participants",
}
DATE = {
    "type": "string",
    "maxLength": 40,
    "description": "memory.tools.param.time",
}


def private_agent(ctx: Context) -> bool:
    return (
        ctx.owner and ctx.mode == "agent" and not ctx.group and not ctx.session.startswith("group:")
    )


def memory_tools(app) -> list[Tool]:
    def writable(ctx: Context) -> bool:
        return private_agent(ctx) and (
            ctx.background
            or (
                app.config.value.session_memory(ctx.session) == ctx.memory_profile
                and app.permissions.session_policy(ctx).mode == "agent"
            )
        )

    def readable(ctx: Context) -> bool:
        if ctx.group:
            cfg = app.config.value
            return (
                ctx.session.startswith("group:")
                and not ctx.interject
                and cfg.shared_memory(ctx.memory_profile)
                and cfg.session_memory(ctx.session) == ctx.memory_profile
            )
        return writable(ctx)

    def selected(ctx: Context, *, write: bool = False):
        if not (writable(ctx) if write else readable(ctx)):
            raise PublicError("memory.tools.noAccess")
        return app.memory_for(ctx.memory_profile)

    async def search(ctx, args):
        return await selected(ctx).search(
            query=args.get("query", ""),
            entity=args.get("entity", ""),
            kind=args.get("kind", ""),
            since=args.get("since", ""),
            until=args.get("until", ""),
            include_history=args.get("include_history", False),
            limit=args.get("limit", 10),
        )

    async def get(ctx, args):
        return await selected(ctx).get(args["id"])

    async def save(ctx, args):
        memory = selected(ctx, write=True)
        kind = args["kind"]
        if kind == "entity":
            if not args.get("name") and not args.get("entity"):
                raise PublicError("memory.tools.entityName")
            return await memory.save_entity(
                ctx,
                args.get("name", ""),
                args.get("entity_kind", ""),
                args.get("aliases"),
                args.get("summary"),
                args.get("entity", ""),
                args.get("merge"),
            )
        if not args.get("content") or not args.get("evidence"):
            raise PublicError("memory.tools.factEvidence")
        if kind == "fact":
            return await memory.save_fact(
                ctx,
                args["content"],
                args["evidence"],
                args.get("subject", ""),
                args.get("valid_from", ""),
                args.get("replaces", ""),
            )
        return await memory.save_event(
            ctx,
            args["content"],
            args["evidence"],
            args.get("time", ""),
            args.get("time_text", ""),
            args.get("phase", "happened"),
            args.get("participants"),
            args.get("replaces", ""),
        )

    async def forget(ctx, args):
        return await selected(ctx, write=True).forget(args["id"], args["reason"])

    async def resolve(ctx, args):
        return await selected(ctx, write=True).resolve_conflict(
            ctx, args["conflict"], args["decision"], args["owner_quote"], args.get("note", "")
        )

    return [
        Tool(
            "memory_search",
            "memory.tools.search",
            schema(
                {
                    "query": {"type": "string", "maxLength": 500},
                    "entity": SHORT,
                    "kind": {"type": "string", "enum": ["entity", "fact", "event"]},
                    "since": DATE,
                    "until": DATE,
                    "include_history": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                }
            ),
            search,
            risk="safe",
            chat=True,
            available=readable,
        ),
        Tool(
            "memory_get",
            "memory.tools.get",
            schema({"id": {"type": "string", "pattern": "^[efvc][0-9]+$"}}, ["id"]),
            get,
            risk="safe",
            chat=True,
            available=readable,
        ),
        Tool(
            "memory_save",
            "memory.tools.save",
            schema(
                {
                    "kind": {"type": "string", "enum": ["entity", "fact", "event"]},
                    "name": SHORT,
                    "entity": SHORT,
                    "entity_kind": {
                        "type": "string",
                        "enum": ["person", "thing"],
                        "description": "memory.tools.param.entityKind",
                    },
                    "aliases": NAMES,
                    "summary": {"type": "string", "maxLength": 500},
                    "merge": NAMES,
                    "subject": SHORT,
                    "content": {"type": "string", "minLength": 1, "maxLength": 1000},
                    "valid_from": DATE,
                    "time": DATE,
                    "time_text": {"type": "string", "maxLength": 200},
                    "phase": {"type": "string", "enum": ["happened", "planned", "cancelled"]},
                    "participants": PARTICIPANTS,
                    "evidence": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "replaces": {"type": "string", "pattern": "^[fv][0-9]+$"},
                },
                ["kind"],
            ),
            save,
            risk="normal",
            available=writable,
        ),
        Tool(
            "memory_forget",
            "memory.tools.forget",
            schema(
                {"id": {"type": "string", "pattern": "^[efv][0-9]+$"}, "reason": TEXT},
                ["id", "reason"],
            ),
            forget,
            risk="normal",
            available=writable,
        ),
        Tool(
            "memory_resolve",
            "memory.tools.resolve",
            schema(
                {
                    "conflict": {"type": "string", "pattern": "^c[0-9]+$"},
                    "decision": {"type": "string", "maxLength": 40},
                    "owner_quote": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "note": {"type": "string", "maxLength": 500},
                },
                ["conflict", "decision", "owner_quote"],
            ),
            resolve,
            risk="normal",
            available=writable,
        ),
    ]
