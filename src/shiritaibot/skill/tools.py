from __future__ import annotations

import asyncio
from pathlib import Path

from shiritaibot.core import PublicError, Tool
from shiritaibot.i18n import t
from shiritaibot.tools import TEXT, schema
from shiritaibot.tools.qq import is_sensitive, session_dir

NAME = {"type": "string", "pattern": r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"}
PATH = {"type": "string", "minLength": 1, "maxLength": 300}
CONTENT = {"type": "string", "maxLength": 200000}


def skill_tools(app) -> list[Tool]:
    skills = app.skills

    def workspace(ctx) -> Path:
        return session_dir(app.home, "workspaces", ctx.session)

    async def skill_list(ctx, args):
        rows = await skills.listing()
        return {
            "skills": [
                {
                    "name": row["name"],
                    "description": row["description"],
                    "source": row["source"],
                    "enabled": row["enabled"],
                    **({"error": row["error"]} if row["error"] else {}),
                }
                for row in rows
                if ctx.allowed_skills is None
                or (row["enabled"] and row["name"] in ctx.allowed_skills)
            ]
        }

    async def skill_read(ctx, args):
        if ctx.allowed_skills is not None and args["name"] not in ctx.allowed_skills:
            raise PublicError("skills.notAllowed")
        return await skills.read(args["name"], args.get("path", "SKILL.md"), args.get("offset", 0))

    async def skill_write(ctx, args):
        files = {item["path"]: item["content"] for item in args.get("files", [])}
        files["SKILL.md"] = args["skill_md"]
        row = await skills.create(args["name"], files, replace=args.get("replace", False))
        return {"written": row["name"], "files": row["files"], "path": row["path"]}

    async def skill_update(ctx, args):
        row = await skills.update(
            args["name"],
            args.get("path", "SKILL.md"),
            content=args.get("content"),
            old_text=args.get("old_text"),
            new_text=args.get("new_text"),
            delete=args.get("delete", False),
        )
        return {"updated": row["name"], "source": row["source"], "files": row["files"]}

    async def skill_install(ctx, args):
        def locate() -> Path:
            path = Path(args["path"]).expanduser()
            return (path if path.is_absolute() else workspace(ctx) / path).resolve()

        path = await asyncio.to_thread(locate)
        if is_sensitive(app.home, path):
            raise PublicError("skills.sensitiveFile")
        row = await skills.install_path(path, replace=args.get("replace", False))
        return {
            "installed": row["name"],
            "description": row["description"],
            "files": [f["path"] for f in row["files"]],
        }

    async def skill_remove(ctx, args):
        return await skills.remove(args["name"])

    async def skill_pack(ctx, args):
        folder = workspace(ctx)
        await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
        result = await skills.pack(args["name"], folder)
        return {**result, "note": t("skills.sendHint")}

    return [
        Tool(
            "skill_list",
            "skills.tool.list",
            schema({}),
            skill_list,
            risk="safe",
        ),
        Tool(
            "skill_read",
            "skills.tool.read",
            schema(
                {"name": NAME, "path": PATH, "offset": {"type": "integer", "minimum": 0}}, ["name"]
            ),
            skill_read,
            risk="safe",
        ),
        Tool(
            "skill_write",
            "skills.tool.create",
            schema(
                {
                    "name": NAME,
                    "skill_md": {"type": "string", "minLength": 1, "maxLength": 200000},
                    "files": {
                        "type": "array",
                        "maxItems": 49,
                        "items": schema({"path": PATH, "content": CONTENT}, ["path", "content"]),
                    },
                    "replace": {"type": "boolean"},
                },
                ["name", "skill_md"],
            ),
            skill_write,
            risk="normal",
        ),
        Tool(
            "skill_update",
            "skills.tool.edit",
            schema(
                {
                    "name": NAME,
                    "path": PATH,
                    "content": CONTENT,
                    "old_text": {"type": "string", "minLength": 1, "maxLength": 200000},
                    "new_text": CONTENT,
                    "delete": {"type": "boolean"},
                },
                ["name"],
            ),
            skill_update,
            risk="normal",
        ),
        Tool(
            "skill_install",
            "skills.tool.install",
            schema({"path": TEXT, "replace": {"type": "boolean"}}, ["path"]),
            skill_install,
            risk="sensitive",
        ),
        Tool(
            "skill_remove",
            "skills.tool.remove",
            schema({"name": NAME}, ["name"]),
            skill_remove,
            risk="dangerous",
        ),
        Tool(
            "skill_pack",
            "skills.tool.pack",
            schema({"name": NAME}, ["name"]),
            skill_pack,
            risk="normal",
        ),
    ]
