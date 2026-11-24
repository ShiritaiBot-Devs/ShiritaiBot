from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

from shiritaibot.adapters.feishu import Feishu
from shiritaibot.adapters.feishu_transport import FILE_SCOPE, FeishuSendError
from shiritaibot.core import Context, PublicError, Tool, session_dir
from shiritaibot.tools import schema

MAX_FILE_SIZE = 30 * 1024 * 1024


def read_workspace_file(workspace: Path, value: str) -> tuple[str, bytes]:
    try:
        path = (workspace / value).resolve()
        if not path.is_relative_to(workspace):
            raise PublicError("tools.feishu.sendPathDenied")
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise PublicError("tools.feishu.notAFile")
            if info.st_size > MAX_FILE_SIZE:
                raise PublicError("tools.feishu.fileTooLarge30")
            data = stream.read(MAX_FILE_SIZE + 1)
    except (OSError, RuntimeError, ValueError):
        raise PublicError("tools.feishu.unreadable") from None
    if not data:
        raise PublicError("tools.feishu.emptyFile")
    if len(data) > MAX_FILE_SIZE:
        raise PublicError("tools.feishu.fileTooLarge30")
    return path.name, data


def feishu_file_tool(app) -> Tool:
    def adapter_for(ctx: Context) -> Feishu | None:
        if not ctx.owner or ctx.mode != "agent" or ctx.group or ctx.background:
            return None
        adapter = app.config.value.active_feishu_session(ctx.session)
        if adapter is None or adapter.owner_id != ctx.actor:
            return None
        bot = app.adapters.instances.get(adapter.id)
        if (
            isinstance(bot, Feishu)
            and bot.status == "connected"
            and bot.config.owner_id == ctx.actor
            and bot.config.owner_session() == ctx.session
        ):
            return bot
        return None

    async def send_file(ctx: Context, args: dict) -> dict:
        workspace = session_dir(app.home, "workspaces", ctx.session)
        name, data = await asyncio.to_thread(read_workspace_file, workspace, args["path"])
        bot = adapter_for(ctx)
        if bot is None:
            raise PublicError("tools.feishu.unavailable")
        try:
            result = await bot.send_owner_file(name, data)
        except FeishuSendError as exc:
            if exc.code in {99991672, 99991679}:
                raise PublicError(
                    "tools.feishu.scopeDenied", code=exc.code, FILE_SCOPE=FILE_SCOPE
                ) from None
            if exc.uncertain:
                raise PublicError("tools.feishu.unconfirmed") from None
            raise PublicError("tools.feishu.sendFailed", code=exc.code) from None
        return {"sent": True, "name": name, "bytes": len(data), **result}

    return Tool(
        "feishu_file_send",
        "tools.feishu.send",
        schema(
            {
                "path": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 2000,
                    "description": "tools.feishu.param.path",
                }
            },
            ["path"],
        ),
        send_file,
        risk="sensitive",
        timeout=120,
        available=lambda ctx: adapter_for(ctx) is not None,
    )
