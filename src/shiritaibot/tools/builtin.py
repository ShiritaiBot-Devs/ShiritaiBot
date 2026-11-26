from __future__ import annotations

import asyncio
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from shiritaibot.agent.plan import PLAN_SCHEMA, update_plan
from shiritaibot.core import PublicError, Tool, delegated_workspace, now, session_dir
from shiritaibot.core.process import run_process
from shiritaibot.i18n import Text, t
from shiritaibot.knowledge.tools import knowledge_tools
from shiritaibot.meme.tools import meme_tools
from shiritaibot.memory.tools import memory_tools, private_agent
from shiritaibot.routine import FINISHED, KEEP_FINISHED, RoutineItem
from shiritaibot.skill.tools import skill_tools
from shiritaibot.store import MEMORY_PROFILE_FILTER
from shiritaibot.tools import TEXT, schema
from shiritaibot.tools.feishu import feishu_file_tool
from shiritaibot.tools.qq import qq_tools
from shiritaibot.tools.shell_diff import file_diffs, snapshot
from shiritaibot.tools.shell_workspace import isolated_shell
from shiritaibot.tools.web_search import search_token, search_web


async def register_builtin(app) -> None:
    registry = app.registry
    registry.remove_plugin("builtin")

    async def current_time(ctx, args):
        zone = app.config.value.proactive.timezone
        local = now().astimezone(ZoneInfo(zone))
        return {
            "local_time": local.isoformat(timespec="seconds"),
            "weekday": t(f"tools.weekdays.{local.weekday()}"),
            "timezone": zone,
        }

    async def search(ctx, args):
        return await search_web(app.config.value.web_search, app.vault, args)

    async def shell(ctx, args):
        workspace = session_dir(app.home, "workspaces", ctx.session)
        isolate = (
            not ctx.background
            and registry.permissions.session_policy(ctx).shell_workspace_isolation
        )
        command = [
            *app.config.value.policy.shell_command,
            app.plugins.filter_shell(args["command"]),
        ]
        if isolate:
            selected = delegated_workspace.get()
            expected = (
                selected[1]
                if selected and selected[0] == ctx.session and ctx.session.startswith("web:")
                else app.home.resolve() / "workspaces" / workspace.name
            )
            if workspace.resolve() != expected:
                raise PublicError("tools.workspaceRedirected")
            command = await asyncio.to_thread(isolated_shell, command, expected)
        await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=True)
        before = (
            await asyncio.to_thread(snapshot, workspace)
            if ctx.tool_call_id and ctx.session.startswith("web:")
            else None
        )
        try:
            return await run_process(
                command,
                workspace,
                timeout=args.get("timeout", 60),
                env=app.plugins.child_env(),
            )
        finally:
            if before is not None:
                after = await asyncio.to_thread(snapshot, workspace)
                if after is not None:
                    changes = await asyncio.to_thread(file_diffs, before, after, registry.secrets)
                    if changes:
                        await app.store.put(
                            "tool_diff",
                            f"{ctx.turn}:{ctx.tool_call_id}",
                            {"files": changes},
                            ctx.session,
                        )

    async def history_query(ctx, args):
        # Ignore context epochs/checkpoints: these are the original records.
        # Session is derived from the caller, never supplied by the model.
        if not ctx.owner or ctx.mode != "agent" or ctx.group:
            raise PublicError("tools.historyOwnerOnly")
        seq = args.get("message_seq")
        before = args.get("before_seq", 0)
        query = args.get("query", "")
        limit = args.get("limit", 10)
        offset = args.get("offset", 0)
        rows = await app.store.query(
            "SELECT seq,turn,data,created FROM messages WHERE session=? "
            f"AND {MEMORY_PROFILE_FILTER} AND (?=0 OR seq<?) AND (? IS NULL OR seq=?) "
            "AND instr(lower(COALESCE(json_extract(data,'$._original_content'),json_extract(data,'$.content'),'')),lower(?))>0 "
            "AND COALESCE(json_extract(data,'$._command'),0)=0 ORDER BY seq DESC LIMIT ?",
            (ctx.session, ctx.memory_profile, before, before, seq, seq, query, limit + 1),
        )
        messages = []
        for row in reversed(rows[:limit]):
            message = json.loads(row["data"])
            content = message.get("_original_content", message.get("content", ""))
            end = offset + 4000
            messages.append(
                {
                    "seq": row["seq"],
                    "turn": row["turn"],
                    "created": row["created"],
                    "role": message["role"],
                    "speaker": message.get("_speaker", message["role"]),
                    "content": content[offset:end],
                    "total_chars": len(content),
                    "next_offset": end if len(content) > end else None,
                    "tool_calls": message.get("tool_calls", []),
                    "tool_call_id": message.get("tool_call_id"),
                }
            )
        return {
            "session": ctx.session,
            "messages": messages,
            "next_before_seq": rows[limit - 1]["seq"] if len(rows) > limit else None,
        }

    def group_context_available(ctx):
        settings = app.config.value
        return (
            settings.group_context_enabled(ctx.session)
            and settings.group_context.query_tool
            and ctx.session.startswith("group:")
        )

    async def group_context_query(ctx, args):
        # Session is derived from the caller; one group cannot read another.
        if not group_context_available(ctx):
            raise PublicError("tools.groupContextOff")
        limit = args.get("limit", 20)
        rows = await app.store.group_messages(
            ctx.session,
            limit + 1,
            before_seq=args.get("before_seq", 0),
            query=args.get("query", ""),
            memory_profile=ctx.memory_profile,
        )
        page = rows[-limit:]
        return {
            "messages": [
                {
                    "seq": row["seq"],
                    "time": datetime.fromtimestamp(
                        row["created"], ZoneInfo(app.config.value.proactive.timezone)
                    ).isoformat(timespec="minutes"),
                    "sender": row["sender"],
                    "name": row["name"],
                    "text": row["text"],
                    "mentioned_bot": bool(row["triggered"]),
                }
                for row in page
            ],
            "next_before_seq": page[0]["seq"] if len(rows) > limit else None,
            "note": t("tools.note.groupMaterial"),
        }

    async def proactive_recall(ctx, args):
        return await app.short_term.recent(
            args.get("query", ""), args.get("limit", 10), memory_profile=ctx.memory_profile
        )

    async def routine_list(ctx, args):
        statuses = ("open", "scheduled") + (FINISHED if args.get("include_finished") else ())
        return await app.store.with_status("routine", statuses, scope=ctx.session)

    async def routine_create(ctx, args):
        return await app.routine.create(
            RoutineItem.model_validate(args), session=ctx.session, actor=ctx.actor
        )

    async def routine_update(ctx, args):
        changes = {k: v for k, v in args.items() if k not in {"id", "status"}}
        return await app.routine.update(
            args["id"], changes, session=ctx.session, status=args.get("status")
        )

    async def routine_delete(ctx, args):
        return await app.routine.delete(args["id"], session=ctx.session)

    async def subagent_instances(ctx, args):
        return app.subagents.instances()

    async def subagent_start(ctx, args):
        row = await app.subagents.start(ctx, args["instance"], args["prompt"])
        return {
            "id": row["id"],
            "instance": row["instance"],
            "status": row["status"],
            # The result comes back as a high-priority Proactive turn; Main must not wait.
            "note": t("tools.note.subagentStarted"),
        }

    async def subagent_spawn(ctx, args):
        row = await app.subagents.spawn(
            ctx,
            args["prompt"],
            role=args.get("role", ""),
            instructions=args.get("instructions", ""),
            tools=args.get("tools"),
            skills=args.get("skills"),
            read_only=args.get("read_only", False),
            model=args.get("model", ""),
        )
        return {
            "id": row["id"],
            "instance": row["instance"],
            "status": row["status"],
            "note": t("tools.note.dynamicStarted"),
        }

    async def subagent_send(ctx, args):
        row = await app.subagents.send(ctx, args["id"], args["message"])
        return {
            "id": row["id"],
            "status": row["status"],
            "round": row["round"],
            "note": t("tools.note.subagentResumed"),
        }

    async def subagent_list(ctx, args):
        return await app.store.list("subagent_run", scope=ctx.session)

    async def subagent_cancel(ctx, args):
        row = await app.store.get("subagent_run", args["id"])
        if not row or row["scope"] != ctx.session:
            raise PublicError("tools.subagentTaskMissing")
        await app.subagents.cancel(args["id"])
        return {"cancelled": True}

    async def artifact_read(ctx, args):
        row = await app.store.get("artifact", args["id"])
        if (
            not row
            or row["scope"] != ctx.session
            or row.get("memory_profile", "default") != ctx.memory_profile
        ):
            raise PublicError("tools.artifactDenied")
        offset = args.get("offset", 0)
        return {"text": row["content"][offset : offset + 12000], "total": len(row["content"])}

    entries = [
        Tool(
            "update_plan",
            "tools.update_plan.description",
            schema({"steps": PLAN_SCHEMA}, ["steps"]),
            lambda ctx, args: update_plan(app.store, ctx, args["steps"]),
            risk="safe",
            available=lambda ctx: not (ctx.background or ctx.subagent_run or ctx.routine_event),
        ),
        Tool(
            "history_query",
            "tools.history_query.description",
            schema(
                {
                    "query": {"type": "string", "maxLength": 1000},
                    "before_seq": {"type": "integer", "minimum": 0},
                    "message_seq": {"type": "integer", "minimum": 1},
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                }
            ),
            history_query,
            risk="safe",
        ),
        Tool(
            "group_context_query",
            "tools.group_context_query.description",
            schema(
                {
                    "query": {"type": "string", "maxLength": 200},
                    "before_seq": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                }
            ),
            group_context_query,
            risk="safe",
            chat=True,
            available=group_context_available,
        ),
        Tool(
            "current_time", "tools.current_time.description", schema({}), current_time, risk="safe", chat=True
        ),
        Tool(
            "shell",
            "tools.shell.description",
            schema(
                {"command": TEXT, "timeout": {"type": "integer", "minimum": 1, "maximum": 120}},
                ["command"],
            ),
            shell,
            risk="dangerous",
            timeout=130,
        ),
        Tool(
            "proactive_recall",
            "tools.proactive_recall.description",
            schema(
                {
                    "query": {"type": "string", "maxLength": 200},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                }
            ),
            proactive_recall,
            risk="safe",
            available=private_agent,
        ),
        Tool(
            "routine_list",
            Text("tools.routine_list.description", value=KEEP_FINISHED // 86400),
            schema({"include_finished": {"type": "boolean"}}),
            routine_list,
            risk="safe",
        ),
        Tool(
            "routine_create",
            "tools.routine_create.description",
            RoutineItem.model_json_schema(),
            routine_create,
            risk="normal",
        ),
        Tool(
            "routine_update",
            "tools.routine_update.description",
            schema(
                {
                    "id": TEXT,
                    "title": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "due": {"type": ["string", "null"], "format": "date-time"},
                    "end": {"type": ["string", "null"], "format": "date-time"},
                    "cron": {"type": "string", "maxLength": 200},
                    "timezone": {"type": "string", "maxLength": 64},
                    "status": {"type": "string", "enum": ["completed", "cancelled"]},
                },
                ["id"],
            ),
            routine_update,
            risk="normal",
        ),
        Tool(
            "routine_delete",
            "tools.routine_delete.description",
            schema({"id": TEXT}, ["id"]),
            routine_delete,
            risk="dangerous",
        ),
        Tool(
            "subagent_instances",
            "tools.subagent_instances.description",
            schema({}),
            subagent_instances,
            risk="safe",
        ),
        Tool(
            "subagent_start",
            "tools.subagent_start.description",
            schema({"instance": TEXT, "prompt": TEXT}, ["instance", "prompt"]),
            subagent_start,
            risk="dangerous",
        ),
        Tool(
            "subagent_spawn",
            "tools.subagent_spawn.description",
            schema(
                {
                    "prompt": TEXT,
                    "role": {"type": "string", "minLength": 1, "maxLength": 80},
                    "instructions": {"type": "string", "maxLength": 16000},
                    "tools": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 100},
                        "maxItems": 100,
                    },
                    "skills": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1, "maxLength": 100},
                        "maxItems": 100,
                    },
                    "read_only": {"type": "boolean"},
                    "model": {"type": "string", "minLength": 1, "maxLength": 100},
                },
                ["prompt"],
            ),
            subagent_spawn,
            risk="normal",
            available=lambda _ctx: app.config.value.policy.dynamic_subagent,
        ),
        Tool(
            "subagent_send",
            "tools.subagent_send.description",
            schema({"id": TEXT, "message": TEXT}, ["id", "message"]),
            subagent_send,
            risk="dangerous",
        ),
        Tool(
            "subagent_list",
            "tools.subagent_list.description",
            schema({}),
            subagent_list,
            risk="safe",
        ),
        Tool(
            "subagent_cancel",
            "tools.subagent_cancel.description",
            schema({"id": TEXT}, ["id"]),
            subagent_cancel,
            risk="normal",
        ),
        Tool(
            "artifact_read",
            "tools.artifact_read.description",
            schema({"id": TEXT, "offset": {"type": "integer", "minimum": 0}}, ["id"]),
            artifact_read,
            risk="safe",
            chat=True,
        ),
        feishu_file_tool(app),
    ]
    entries += memory_tools(app)
    entries += qq_tools(app)
    entries += meme_tools(app)
    entries += knowledge_tools(app)
    if app.config.value.skills.enabled:
        entries += skill_tools(app)
    if await search_token(app.config.value.web_search, app.vault):
        entries.append(
            Tool(
                "web_search",
                "tools.web_search.description",
                schema(
                    {
                        "query": {"type": "string", "minLength": 1, "maxLength": 128},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    ["query"],
                ),
                search,
                risk="safe",
                chat=True,
            )
        )
    for tool in entries:
        registry.add(tool)
