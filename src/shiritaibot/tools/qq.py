"""Owner-only QQ actions over OneBot 11 / NapCat: chat, friends and group management."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from shiritaibot.adapters.onebot11 import (
    OneBot,
    describe,
    file_ref,
    forward_node,
    qq_download_url,
    segments,
)
from shiritaibot.config import is_feishu_session
from shiritaibot.core import Context, PublicError, Tool
from shiritaibot.core import session_dir as session_dir
from shiritaibot.i18n import t
from shiritaibot.tools import TEXT, schema

ADAPTER = {
    "type": "string",
    "maxLength": 100,
    "description": "tools.qq.param.adapter",
}
ID = {"type": "integer", "minimum": 1}
MESSAGE_ID = {"type": "integer", "description": "tools.qq.param.messageId"}
CHAT = {
    "target": {
        "type": "string",
        "enum": ["current", "private", "group"],
        "description": "tools.qq.param.chatTarget",
    },
    "id": {**ID, "description": "tools.qq.param.target"},
}
AT = {"anyOf": [ID, {"type": "string", "enum": ["all"]}]}
PATH = {
    "type": "string",
    "minLength": 1,
    "maxLength": 2000,
    "description": (
        "tools.qq.param.file"
    ),
}
IMAGE = {
    **PATH,
    "description": "tools.qq.param.image",
}
MAX_UPLOAD = 30 * 1024 * 1024
MAX_DOWNLOAD = 50 * 1024 * 1024
# Framework-internal files under the data home that must never leave the machine.
SENSITIVE_FILES = frozenset(
    {
        "config.toml",
        "master.key",
        "web.token",
        "state.sqlite3",
        "state.sqlite3-shm",
        "state.sqlite3-wal",
    }
)
SENSITIVE_DIRS = frozenset({"logs", "memory-profiles"})


def _decode_file(encoded: str) -> bytes:
    encoded = encoded.removeprefix("base64://")
    if len(encoded) > 4 * ((MAX_DOWNLOAD + 2) // 3):
        raise PublicError("tools.qq.fileTooLarge50")
    try:
        data = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise PublicError("tools.qq.invalidBase64") from None
    if len(data) > MAX_DOWNLOAD:
        raise PublicError("tools.qq.fileTooLarge50")
    return data


async def qq_file_source(bot: OneBot, group_id: str, data: dict) -> str | bytes:
    """Resolve an allowlisted CDN link or fetch bytes from the configured OneBot."""
    if qq_download_url(str(data.get("url") or "")):
        return data["url"]
    file_id = data.get("file_id") or data.get("id")
    if not file_id:
        raise PublicError("tools.qq.fileNoSource")
    try:
        declared_size = int(data.get("file_size") or 0)
    except (ValueError, TypeError):
        declared_size = 0
    if declared_size > MAX_DOWNLOAD:
        raise PublicError("tools.qq.fileTooLarge50")

    action = "get_group_file_url" if group_id else "get_private_file_url"
    params = {"file_id": file_id}
    if group_id:
        params.update(group_id=int(group_id), busid=data.get("busid", 0))
    try:
        result = await bot.call(action, params)
        url = result.get("url") if isinstance(result, dict) else None
        if isinstance(url, str) and qq_download_url(url):
            return url
    except (PublicError, TimeoutError):
        pass

    result = await bot.get_file(str(file_id), MAX_DOWNLOAD)
    url = result.get("url")
    if isinstance(url, str) and qq_download_url(url):
        return url
    # NapCat can return the bytes even when its URL is empty and "file" is a local path.
    encoded = result.get("base64")
    if isinstance(encoded, str) and encoded:
        return await asyncio.to_thread(_decode_file, encoded)
    if encoded == "" and str(result.get("file_size")) == "0" and result.get("file"):
        return b""
    raise PublicError("tools.qq.noDownload")


async def download_qq_file(app, session: str, ref: str) -> dict:
    """Fetch a session-bound QQ file reference into the session's download folder."""
    ref = ref.strip()
    # The model can copy the filename along with the ID from the attachment marker.
    ref_with_name = re.match(r"([0-9a-f]{32})\s*:", ref)
    if ref_with_name:
        ref = ref_with_name[1]
    row = await app.store.get("qq_file", ref)
    if not row:
        raise PublicError("tools.qq.fileRefMissing")
    if row["scope"] != session:
        raise PublicError("tools.qq.fileRefOtherSession")
    bot = app.adapters.instances.get(row["adapter"])
    if not isinstance(bot, OneBot):
        raise PublicError("tools.qq.adapterUnavailable")
    data = row["data"]
    source = await qq_file_source(bot, row["group_id"], data)
    # Land downloads inside the session workspace so sandboxed main-loop
    # sessions (which mount only their workspace) can read them directly.
    folder = session_dir(app.home, "workspaces", session) / "downloads"
    await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
    stream = await asyncio.to_thread(
        tempfile.NamedTemporaryFile, dir=folder, suffix=".part", delete=False
    )
    temp = Path(stream.name)
    digest, size = hashlib.sha256(), 0
    try:
        try:
            if isinstance(source, bytes):
                size = len(source)
                digest.update(source)
                await asyncio.to_thread(stream.write, source)
            else:
                async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
                    async with client.stream("GET", source) as response:
                        response.raise_for_status()
                        async for chunk in response.aiter_bytes(65536):
                            size += len(chunk)
                            if size > MAX_DOWNLOAD:
                                raise PublicError("tools.qq.fileTooLarge50")
                            digest.update(chunk)
                            await asyncio.to_thread(stream.write, chunk)
        finally:
            await asyncio.to_thread(stream.close)
        target = folder / digest.hexdigest()
        await asyncio.to_thread(temp.replace, target)
        return {
            "path": str(target),
            "bytes": size,
            "sha256": digest.hexdigest(),
            "name": data.get("name") or data.get("file") or "file",
        }
    finally:
        await asyncio.to_thread(temp.unlink, missing_ok=True)


def is_sensitive(home: Path, path: Path) -> bool:
    """True for top-level secrets/state in the data home or anything under its logs/."""
    home = home.resolve()
    if not path.is_relative_to(home):
        return False
    parts = path.relative_to(home).parts
    if not parts:
        return False
    if len(parts) == 1 and parts[0] in SENSITIVE_FILES:
        return True
    return parts[0] in SENSITIVE_DIRS


def owner_only(ctx: Context) -> None:
    # Acting as the bot account is the owner's capability, never a chat member's. Groups
    # qualify only once their session policy runs in Agent mode; per-tool policy still applies.
    if not ctx.owner or ctx.mode != "agent":
        raise PublicError("tools.qq.ownerAgentOnly")


def qq_tools(app) -> list[Tool]:
    def qq_context(ctx: Context) -> bool:
        return not is_feishu_session(ctx.session)

    def connected(ctx: Context) -> bool:
        return qq_context(ctx) and any(
            isinstance(bot, OneBot) and bot.status == "connected"
            for bot in app.adapters.instances.values()
        )

    def owner_agent(ctx: Context) -> bool:
        return ctx.owner and ctx.mode == "agent"

    def own_chat(ctx: Context) -> tuple[str, str] | None:
        """The chat this session belongs to, as (private|group, id)."""
        parts = ctx.session.split(":")
        if len(parts) == 3 and parts[0] in {"private", "group"} and re.fullmatch(r"[1-9][0-9]*", parts[2]):
            return parts[0], parts[2]
        return None

    def own_private(ctx: Context) -> tuple[str, str] | None:
        scope = own_chat(ctx)
        if not ctx.group and not ctx.background and scope == ("private", ctx.actor):
            return scope
        return None

    def session_bot(ctx: Context) -> OneBot | None:
        if own_chat(ctx) is None:
            return None
        bot = app.adapters.instances.get(ctx.session.split(":")[1])
        return bot if isinstance(bot, OneBot) and bot.status == "connected" else None

    def chat_bot_for(ctx: Context) -> OneBot:
        bot = session_bot(ctx)
        if bot is None:
            raise PublicError("tools.qq.sessionAdapterUnavailable")
        return bot

    def can_operate_account(ctx: Context) -> bool:
        return owner_agent(ctx) and connected(ctx)

    def can_send_to_chat(ctx: Context) -> bool:
        return can_operate_account(ctx) or (
            own_private(ctx) is not None and session_bot(ctx) is not None
        )

    def can_read_chat(ctx: Context) -> bool:
        return can_send_to_chat(ctx) or (ctx.mode == "agent" and session_bot(ctx) is not None)

    def bot_for(ctx: Context, args: dict):
        owner_only(ctx)
        instances = {
            key: bot for key, bot in app.adapters.instances.items() if isinstance(bot, OneBot)
        }
        adapter = args.get("adapter", "")
        if not adapter:
            parts = ctx.session.split(":")
            if len(parts) == 3 and parts[0] in {"private", "group"} and parts[1] in instances:
                adapter = parts[1]
            else:
                ready = [key for key, bot in instances.items() if bot.status == "connected"]
                if not ready:
                    raise PublicError("tools.qq.noAdapter")
                if len(ready) > 1:
                    raise PublicError("tools.qq.chooseAdapter", options=', '.join(ready))
                adapter = ready[0]
        bot = instances.get(adapter)
        if not bot or bot.status != "connected":
            raise PublicError("tools.qq.adapterMissing", adapter=adapter)
        return bot

    def chat_destination(ctx: Context, args: dict, *, sending: bool) -> tuple[OneBot, str, int]:
        target = args.get("target", "current")
        if owner_agent(ctx) and target != "current":
            if "id" not in args:
                raise PublicError("tools.qq.targetIdRequired")
            return bot_for(ctx, args), target, args["id"]

        scope = own_private(ctx) if sending and not owner_agent(ctx) else own_chat(ctx)
        if scope is None or (
            target not in ("current", scope[0])
            or ("id" in args and str(args["id"]) != scope[1])
            or ("adapter" in args and args["adapter"] != ctx.session.split(":")[1])
        ):
            raise PublicError("tools.qq.sendToOwnPrivate" if sending else "tools.qq.readOwnChat")
        return chat_bot_for(ctx), scope[0], int(scope[1])

    def stamp(value) -> str | None:
        if not value:
            return None
        zone = ZoneInfo(app.config.value.proactive.timezone)
        return datetime.fromtimestamp(int(value), zone).isoformat(timespec="seconds")

    async def local_file(ctx: Context, value: str) -> tuple[Path, bytes]:
        roots = [
            session_dir(app.home, "workspaces", ctx.session),
            session_dir(app.home, "files", ctx.session),  # legacy downloads
        ]
        if owner_agent(ctx):
            roots.append(app.home / "plugin_data")
        path = Path(value)
        path = (path if path.is_absolute() else roots[0] / path).resolve()
        if not any(path.is_relative_to(root.resolve()) for root in roots):
            raise PublicError("tools.qq.sendPathDenied" if owner_agent(ctx) else "tools.qq.sendSessionPathDenied")
        if is_sensitive(app.home, path):
            raise PublicError("tools.qq.sensitiveFile")
        if not await asyncio.to_thread(path.is_file):
            raise PublicError("tools.qq.fileMissing")
        if (await asyncio.to_thread(path.stat)).st_size > MAX_UPLOAD:
            raise PublicError("tools.qq.fileTooLarge30")
        return path, await asyncio.to_thread(path.read_bytes)

    async def media(ctx: Context, value: str) -> str:
        if value.startswith("https://"):
            if owner_agent(ctx):
                return value
            raise PublicError("tools.qq.sendSessionPathDenied")
        _, content = await local_file(ctx, value)
        return "base64://" + base64.b64encode(content).decode()

    async def render(ctx: Context, bot, message: dict, group_id: str = "") -> dict:
        """Normalize a OneBot message; files become session-bound download refs."""
        parts = segments(message.get("message", message.get("content", [])))
        sender = message.get("sender") or {}
        group_id = str(message.get("group_id") or group_id or "")
        files, reply = [], None
        for part in parts:
            data, kind = part.get("data", {}), part.get("type")
            if kind == "file" or (kind in {"image", "video"} and data.get("url")):
                ref = await file_ref(app.store, bot.config.id, group_id, data, ctx.session)
                files.append(
                    {"ref": ref, "kind": kind, "name": data.get("name") or data.get("file")}
                )
            elif kind == "reply":
                reply = data.get("id")
        return {
            "message_id": message.get("message_id"),
            "message_seq": message.get("message_seq"),
            "time": stamp(message.get("time")),
            "sender": {
                "user_id": sender.get("user_id", message.get("user_id")),
                "name": sender.get("card") or sender.get("nickname", ""),
            },
            "text": describe(parts),
            "reply_to": reply,
            "files": files,
        }

    async def record_sent(bot, kind: str, target: int, receipt: dict, text: str) -> None:
        if kind == "group" and app.config.value.group_context.enabled and text:
            session = bot.config.session(str(target), group=True)
            await app.store.record_group(
                session,
                f"{bot.config.id}:{receipt.get('message_id')}",
                bot.self_id,
                app.config.value.assistant_name,
                text,
                False,
                memory_profile=app.config.value.session_memory(session),
            )

    # --- chat ---

    async def private_message(bot: OneBot, peer: str, message_id: int) -> dict:
        raw = await bot.call("get_msg", {"message_id": message_id})
        sender = str((raw.get("sender") or {}).get("user_id") or "")
        is_private = raw.get("message_type") == "private" and raw.get("group_id") in (None, "", 0, "0")
        if is_private and sender == peer:
            return raw
        if is_private and bot.self_id and sender == str(bot.self_id):
            # The sender alone does not identify a bot reply's conversation.
            data = await bot.call(
                "get_friend_msg_history", {"user_id": int(peer), "count": 50, "message_seq": 0}
            )
            if any(str(message.get("message_id")) == str(message_id) for message in data.get("messages", [])):
                return raw
        raise PublicError("tools.qq.readOwnChat")

    async def message_send(ctx, args):
        bot, target, recipient = chat_destination(ctx, args, sending=True)
        message = []
        if "reply_to" in args:
            if not owner_agent(ctx):
                await private_message(bot, str(recipient), args["reply_to"])
            message.append({"type": "reply", "data": {"id": str(args["reply_to"])}})
        for qq in args.get("at", []) if target == "group" else []:
            message.append({"type": "at", "data": {"qq": str(qq)}})
        if args.get("text"):
            message.append({"type": "text", "data": {"text": args["text"]}})
        for image in args.get("images", []):
            message.append({"type": "image", "data": {"file": await media(ctx, image)}})
        if not any(part["type"] in {"text", "image", "at"} for part in message):
            raise PublicError("tools.qq.emptyMessage")
        group = target == "group"
        receipt = await bot.call(
            "send_group_msg" if group else "send_private_msg",
            {"group_id" if group else "user_id": recipient, "message": message},
        )
        await record_sent(bot, target, recipient, receipt, describe(message))
        return {"message_id": receipt.get("message_id")}

    async def file_send(ctx, args):
        bot, target, recipient = chat_destination(ctx, args, sending=True)
        path, content = await local_file(ctx, args["path"])
        params = {
            "file": "base64://" + base64.b64encode(content).decode(),
            "name": args.get("name") or path.name,
        }
        if target == "group":
            if args.get("folder"):
                params["folder"] = args["folder"]
            await bot.call("upload_group_file", {"group_id": recipient, **params})
        else:
            await bot.call("upload_private_file", {"user_id": recipient, **params})
        return {"sent": True, "name": params["name"], "bytes": len(content)}

    async def message_get(ctx, args):
        scope = None if owner_agent(ctx) else own_chat(ctx)
        if scope is None:
            bot = bot_for(ctx, args)
            return await render(ctx, bot, await bot.call("get_msg", {"message_id": args["message_id"]}))
        bot = chat_bot_for(ctx)
        if scope[0] == "private":
            return await render(ctx, bot, await private_message(bot, scope[1], args["message_id"]))
        raw = await bot.call("get_msg", {"message_id": args["message_id"]})
        if str(raw.get("group_id") or "") != scope[1]:
            raise PublicError("tools.qq.readOwnChat")
        return await render(ctx, bot, raw)

    async def message_history(ctx, args):
        bot, target, recipient = chat_destination(ctx, args, sending=False)
        count = args.get("count", 20)
        params = {"count": count, "message_seq": args.get("before_seq", 0)}
        if target == "group":
            data = await bot.call("get_group_msg_history", {"group_id": recipient, **params})
        else:
            data = await bot.call("get_friend_msg_history", {"user_id": recipient, **params})
        group_id = str(recipient) if target == "group" else ""
        messages = [await render(ctx, bot, m, group_id) for m in data.get("messages", [])]
        oldest = messages[0] if messages else {}
        return {
            "messages": messages,
            "next_before_seq": oldest.get("message_seq") or oldest.get("message_id")
            if len(messages) >= count
            else None,
            "note": t("tools.qq.note.message"),
        }

    async def message_recall(ctx, args):
        await bot_for(ctx, args).call("delete_msg", {"message_id": args["message_id"]})
        return {"recalled": True}

    async def forward_send(ctx, args):
        bot, target, recipient = chat_destination(ctx, args, sending=True)
        privileged = owner_agent(ctx)
        nodes = []
        for node in args["nodes"]:
            if "message_id" in node:
                if not privileged:
                    await private_message(bot, str(recipient), node["message_id"])
                nodes.append({"type": "node", "data": {"id": str(node["message_id"])}})
            elif node.get("text"):
                if not privileged and ("name" in node or "user_id" in node):
                    raise PublicError("tools.qq.forwardOwnIdentity")
                nodes.append(
                    forward_node(
                        [{"type": "text", "data": {"text": node["text"]}}],
                        str(node.get("user_id") or bot.self_id),
                        node.get("name") or app.config.value.assistant_name,
                    )
                )
            else:
                raise PublicError("tools.qq.forwardNode")
        group = target == "group"
        receipt = await bot.call(
            "send_group_forward_msg" if group else "send_private_forward_msg",
            {"group_id" if group else "user_id": recipient, "messages": nodes},
        )
        return {"message_id": receipt.get("message_id"), "forward_id": receipt.get("res_id")}

    async def forward_get(ctx, args):
        scope = None if owner_agent(ctx) else own_chat(ctx)
        bot = chat_bot_for(ctx) if scope is not None else bot_for(ctx, args)
        data = await bot.call("get_forward_msg", {"message_id": args["id"], "id": args["id"]})
        return {
            "messages": [await render(ctx, bot, m) for m in data.get("messages", [])],
            "note": t("tools.qq.note.forward"),
        }

    # --- friends ---

    async def friend_list(ctx, args):
        friends = await bot_for(ctx, args).call("get_friend_list", {})
        return [
            {"user_id": f.get("user_id"), "nickname": f.get("nickname"), "remark": f.get("remark")}
            for f in friends
        ]

    async def friend_delete(ctx, args):
        bot = bot_for(ctx, args)
        await bot.call("delete_friend", {"user_id": args["user_id"]})
        if hasattr(bot, "invalidate_friends"):
            bot.invalidate_friends()
        return {"deleted": True}

    async def request_list(ctx, args):
        owner_only(ctx)
        status = args.get("status", "pending")
        rows = await app.store.list("qq_request", limit=200)
        return [
            {k: v for k, v in row.items() if k not in {"flag", "scope"}}
            for row in rows
            if status == "all" or row["status"] == status
        ]

    async def request_handle(ctx, args):
        owner_only(ctx)
        row = await app.store.get("qq_request", args["request"])
        if not row:
            raise PublicError("tools.qq.requestMissing")
        if row["status"] != "pending":
            raise PublicError("tools.qq.requestHandled", status=row['status'])
        action = args["action"]
        if action != "ignore":
            bot = bot_for(ctx, {"adapter": row["adapter"]})
            approve = action == "approve"
            if row["kind"] == "friend":
                await bot.call(
                    "set_friend_add_request",
                    {"flag": row["flag"], "approve": approve, "remark": args.get("remark", "")},
                )
                if approve and hasattr(bot, "invalidate_friends"):
                    bot.invalidate_friends()
            else:
                await bot.call(
                    "set_group_add_request",
                    {
                        "flag": row["flag"],
                        "sub_type": row["sub_type"] or "add",
                        "approve": approve,
                        "reason": args.get("reason", ""),
                    },
                )
        row["status"] = {"approve": "approved", "reject": "rejected"}.get(action, "ignored")
        await app.store.put("qq_request", row["id"], row)
        return {"status": row["status"]}

    # --- groups ---

    async def group_list(ctx, args):
        groups = await bot_for(ctx, args).call("get_group_list", {})
        return [
            {
                "group_id": g.get("group_id"),
                "name": g.get("group_name"),
                "members": g.get("member_count"),
            }
            for g in groups
        ]

    async def group_members(ctx, args):
        bot = bot_for(ctx, args)
        members = await bot.call(
            "get_group_member_list",
            {"group_id": args["group_id"], "no_cache": args.get("refresh", False)},
        )
        query = args.get("query", "").lower()
        rank = {"owner": 0, "admin": 1}
        matched = sorted(
            (
                m
                for m in members
                if not query
                or any(query in str(m.get(k, "")).lower() for k in ("user_id", "nickname", "card"))
            ),
            key=lambda m: (rank.get(m.get("role"), 2), m.get("join_time") or 0),
        )
        offset, limit = args.get("offset", 0), args.get("limit", 50)
        return {
            "total": len(matched),
            "members": [
                {
                    "user_id": m.get("user_id"),
                    "nickname": m.get("nickname"),
                    "card": m.get("card"),
                    "role": m.get("role"),
                    "title": m.get("title"),
                    "join_time": stamp(m.get("join_time")),
                    "last_sent_time": stamp(m.get("last_sent_time")),
                    "muted_until": stamp(m.get("shut_up_timestamp")),
                }
                for m in matched[offset : offset + limit]
            ],
            "next_offset": offset + limit if offset + limit < len(matched) else None,
        }

    async def essence_list(ctx, args):
        bot = bot_for(ctx, args)
        rows = await bot.call("get_essence_msg_list", {"group_id": args["group_id"]})
        return [
            {
                "message_id": r.get("message_id"),
                "sender": {"user_id": r.get("sender_id"), "name": r.get("sender_nick")},
                "time": stamp(r.get("sender_time")),
                "operator": {"user_id": r.get("operator_id"), "name": r.get("operator_nick")},
                "text": describe(segments(r.get("content", []))),
            }
            for r in rows
        ]

    async def essence_add(ctx, args):
        await bot_for(ctx, args).call("set_essence_msg", {"message_id": args["message_id"]})
        return {"ok": True}

    async def essence_delete(ctx, args):
        await bot_for(ctx, args).call("delete_essence_msg", {"message_id": args["message_id"]})
        return {"ok": True}

    async def notice_list(ctx, args):
        rows = await bot_for(ctx, args).call("_get_group_notice", {"group_id": args["group_id"]})
        return [
            {
                "notice_id": r.get("notice_id"),
                "sender_id": r.get("sender_id"),
                "time": stamp(r.get("publish_time")),
                "text": (r.get("message") or {}).get("text", ""),
            }
            for r in rows
        ]

    async def notice_send(ctx, args):
        bot = bot_for(ctx, args)
        params = {"group_id": args["group_id"], "content": args["content"]}
        if args.get("image"):
            params["image"] = await media(ctx, args["image"])
        await bot.call("_send_group_notice", params)
        return {"ok": True}

    async def notice_delete(ctx, args):
        await bot_for(ctx, args).call(
            "_del_group_notice", {"group_id": args["group_id"], "notice_id": args["notice_id"]}
        )
        return {"ok": True}

    async def group_ban(ctx, args):
        await bot_for(ctx, args).call(
            "set_group_ban",
            {
                "group_id": args["group_id"],
                "user_id": args["user_id"],
                "duration": args["duration"],
            },
        )
        return {"ok": True, "unbanned": args["duration"] == 0}

    async def group_kick(ctx, args):
        await bot_for(ctx, args).call(
            "set_group_kick",
            {
                "group_id": args["group_id"],
                "user_id": args["user_id"],
                "reject_add_request": args.get("reject_add_request", False),
            },
        )
        return {"ok": True}

    async def group_invite(ctx, args):
        bot = bot_for(ctx, args)
        card = await bot.call("ArkShareGroup", {"group_id": str(args["group_id"])})
        card = card if isinstance(card, str) else card.get("arkJson", "")
        if not card:
            raise PublicError("tools.qq.noRecommendCard")
        receipt = await bot.call(
            "send_private_msg",
            {"user_id": args["user_id"], "message": [{"type": "json", "data": {"data": card}}]},
        )
        return {"message_id": receipt.get("message_id")}

    async def file_download(ctx, args):
        return await download_qq_file(app, ctx.session, args["ref"])

    group_id = {"group_id": ID}
    member = {"group_id": ID, "user_id": ID}
    tools = [
        Tool(
            "qq_message_send",
            "tools.qq.send",
            schema(
                {
                    **CHAT,
                    "text": TEXT,
                    "at": {"type": "array", "items": AT, "maxItems": 20, "description": "tools.qq.param.groupOnly"},
                    "reply_to": MESSAGE_ID,
                    "images": {
                        "type": "array",
                        "items": IMAGE,
                        "maxItems": 9,
                    },
                    "adapter": ADAPTER,
                },
            ),
            message_send,
            risk="sensitive",
            chat=True,
            available=can_send_to_chat,
        ),
        Tool(
            "qq_file_send",
            "tools.qq.fileUpload",
            schema(
                {
                    **CHAT,
                    "path": PATH,
                    "name": {"type": "string", "minLength": 1, "maxLength": 200},
                    "folder": {"type": "string", "maxLength": 200, "description": "tools.qq.param.folderId"},
                    "adapter": ADAPTER,
                },
                ["path"],
            ),
            file_send,
            risk="sensitive",
            chat=True,
            timeout=180,
            available=can_send_to_chat,
        ),
        Tool(
            "qq_message_get",
            "tools.qq.messageGet",
            schema({"message_id": MESSAGE_ID, "adapter": ADAPTER}, ["message_id"]),
            message_get,
            risk="safe",
            chat=True,
            available=can_read_chat,
        ),
        Tool(
            "qq_message_history",
            "tools.qq.messageHistory",
            schema(
                {
                    **CHAT,
                    "count": {"type": "integer", "minimum": 1, "maximum": 50},
                    "before_seq": {"type": "integer", "minimum": 0},
                    "adapter": ADAPTER,
                },
            ),
            message_history,
            risk="safe",
            chat=True,
            available=can_read_chat,
        ),
        Tool(
            "qq_message_recall",
            "tools.qq.messageRecall",
            schema({"message_id": MESSAGE_ID, "adapter": ADAPTER}, ["message_id"]),
            message_recall,
            risk="normal",
        ),
        Tool(
            "qq_forward_send",
            "tools.qq.forwardSend",
            schema(
                {
                    **CHAT,
                    "nodes": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 100,
                        "items": schema(
                            {
                                "message_id": MESSAGE_ID,
                                "text": {**TEXT, "maxLength": 5000},
                                "name": {"type": "string", "minLength": 1, "maxLength": 50},
                                "user_id": ID,
                            }
                        ),
                    },
                    "adapter": ADAPTER,
                },
                ["nodes"],
            ),
            forward_send,
            risk="sensitive",
            chat=True,
            available=can_send_to_chat,
        ),
        Tool(
            "qq_forward_get",
            "tools.qq.forwardGet",
            schema(
                {"id": {"type": "string", "minLength": 1, "maxLength": 200}, "adapter": ADAPTER},
                ["id"],
            ),
            forward_get,
            risk="safe",
            available=can_read_chat,
        ),
        Tool(
            "qq_friend_list",
            "tools.qq.friendList",
            schema({"adapter": ADAPTER}),
            friend_list,
            risk="safe",
        ),
        Tool(
            "qq_friend_delete",
            "tools.qq.friendDelete",
            schema({"user_id": ID, "adapter": ADAPTER}, ["user_id"]),
            friend_delete,
            risk="dangerous",
        ),
        Tool(
            "qq_request_list",
            "tools.qq.requestList",
            schema(
                {
                    "status": {
                        "type": "string",
                        "enum": ["pending", "approved", "rejected", "ignored", "all"],
                    }
                }
            ),
            request_list,
            risk="safe",
        ),
        Tool(
            "qq_request_handle",
            "tools.qq.requestHandle",
            schema(
                {
                    "request": {"type": "string", "minLength": 1, "maxLength": 100},
                    "action": {"type": "string", "enum": ["approve", "reject", "ignore"]},
                    "remark": {
                        "type": "string",
                        "maxLength": 50,
                        "description": "tools.qq.param.remark",
                    },
                    "reason": {"type": "string", "maxLength": 200, "description": "tools.qq.param.reason"},
                },
                ["request", "action"],
            ),
            request_handle,
            risk="sensitive",
        ),
        Tool(
            "qq_group_list",
            "tools.qq.groupList",
            schema({"adapter": ADAPTER}),
            group_list,
            risk="safe",
        ),
        Tool(
            "qq_group_members",
            "tools.qq.groupMembers",
            schema(
                {
                    **group_id,
                    "query": {"type": "string", "maxLength": 100},
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    "refresh": {"type": "boolean", "description": "tools.qq.param.noCache"},
                    "adapter": ADAPTER,
                },
                ["group_id"],
            ),
            group_members,
            risk="safe",
        ),
        Tool(
            "qq_essence_list",
            "tools.qq.essenceList",
            schema({**group_id, "adapter": ADAPTER}, ["group_id"]),
            essence_list,
            risk="safe",
        ),
        Tool(
            "qq_essence_add",
            "tools.qq.essenceSet",
            schema({"message_id": MESSAGE_ID, "adapter": ADAPTER}, ["message_id"]),
            essence_add,
            risk="normal",
        ),
        Tool(
            "qq_essence_delete",
            "tools.qq.essenceDelete",
            schema({"message_id": MESSAGE_ID, "adapter": ADAPTER}, ["message_id"]),
            essence_delete,
            risk="normal",
        ),
        Tool(
            "qq_notice_list",
            "tools.qq.noticeList",
            schema({**group_id, "adapter": ADAPTER}, ["group_id"]),
            notice_list,
            risk="safe",
        ),
        Tool(
            "qq_notice_send",
            "tools.qq.noticeSend",
            schema(
                {
                    **group_id,
                    "content": {**TEXT, "maxLength": 4000},
                    "image": IMAGE,
                    "adapter": ADAPTER,
                },
                ["group_id", "content"],
            ),
            notice_send,
            risk="sensitive",
        ),
        Tool(
            "qq_notice_delete",
            "tools.qq.noticeDelete",
            schema(
                {
                    **group_id,
                    "notice_id": {"type": "string", "minLength": 1, "maxLength": 200},
                    "adapter": ADAPTER,
                },
                ["group_id", "notice_id"],
            ),
            notice_delete,
            risk="normal",
        ),
        Tool(
            "qq_group_ban",
            "tools.qq.groupBan",
            schema(
                {
                    **member,
                    "duration": {"type": "integer", "minimum": 0, "maximum": 30 * 86400},
                    "adapter": ADAPTER,
                },
                ["group_id", "user_id", "duration"],
            ),
            group_ban,
            risk="sensitive",
        ),
        Tool(
            "qq_group_kick",
            "tools.qq.groupKick",
            schema(
                {**member, "reject_add_request": {"type": "boolean"}, "adapter": ADAPTER},
                ["group_id", "user_id"],
            ),
            group_kick,
            risk="dangerous",
        ),
        Tool(
            "qq_group_invite",
            "tools.qq.groupInvite",
            schema({**member, "adapter": ADAPTER}, ["group_id", "user_id"]),
            group_invite,
            risk="sensitive",
        ),
    ]
    for tool in tools:
        if tool.available is None:
            tool.available = can_operate_account
    tools.append(
        Tool(
            "qq_file_download",
            "tools.qq.fileDownload",
            schema({"ref": {**TEXT, "description": "tools.qq.param.fileRef"}}, ["ref"]),
            file_download,
            risk="normal",
            chat=True,
            timeout=180,
            available=lambda ctx: qq_context(ctx) and (
                ctx.mode == "agent" or (own_private(ctx) is not None and session_bot(ctx) is not None)
            ),
        )
    )
    return tools
