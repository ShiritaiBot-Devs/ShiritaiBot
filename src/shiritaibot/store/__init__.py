from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import aiosqlite

from shiritaibot.core import EventBus, PublicError, canonical, now, uid
from shiritaibot.core.logs import get_logger

# Call sites log a readable line themselves; this keeps the raw activity kind for debugging.
log = get_logger("活动")
GROUP_MESSAGES_KEPT = 2000
MEMORY_PROFILE_FILTER = "COALESCE(json_extract(data,'$._memory_profile'),'default')=?"


def memory_context_key(session: str, profile: str) -> str:
    return session if profile == "default" else f"memory:{profile}:{session}"


def owner_memory_scope(profile: str) -> str:
    return "owner" if profile == "default" else f"memory:{profile}"


class Store:
    def __init__(self, home: Path, bus: EventBus):
        self.home, self.bus = home, bus
        self.lock = asyncio.Lock()
        self.db: aiosqlite.Connection

    async def open(self) -> None:
        await asyncio.to_thread(self.home.mkdir, parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.home / "state.sqlite3")
        self.db.row_factory = aiosqlite.Row
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA busy_timeout=5000")
        # Every migration is idempotent (IF NOT EXISTS) and runs in file order.
        for path in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
            await self.db.executescript(await asyncio.to_thread(path.read_text, encoding="utf-8"))
        await self.db.commit()
        for kind in ["turn", "subagent_run", "approval"]:
            while rows := await self.with_status(
                kind, ("running", "waiting_approval", "pending", "queued")
            ):
                for row in rows:
                    row["status"] = "interrupted"
                    log.debug(f"上次未完成的 {kind} {row['id'][:8]} 标记为已中断")
                    await self.put(kind, row["id"], row, row.get("scope", ""))

    async def close(self) -> None:
        await self.db.close()

    async def execute(self, sql: str, args: tuple = ()) -> int:
        async with self.lock:
            cur = await self.db.execute(sql, args)
            await self.db.commit()
            return cur.rowcount

    async def query(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        async with self.lock:
            async with self.db.execute(sql, args) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def put(self, kind: str, key: str, data: dict, scope: str = "") -> dict:
        stamp = now().timestamp()
        body = {**data, "id": key, "scope": scope}
        await self.execute(
            "INSERT INTO records VALUES(?,?,?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data,scope=excluded.scope,updated=excluded.updated",
            (kind, key, scope, canonical(body), stamp, stamp),
        )
        self.bus.emit({"kind": kind, "id": key})
        return body

    async def insert(self, kind: str, key: str, data: dict, scope: str = "") -> bool:
        stamp = now().timestamp()
        return bool(
            await self.execute(
                "INSERT OR IGNORE INTO records VALUES(?,?,?,?,?,?)",
                (kind, key, scope, canonical({**data, "id": key, "scope": scope}), stamp, stamp),
            )
        )

    async def get(self, kind: str, key: str) -> dict | None:
        rows = await self.query("SELECT data FROM records WHERE kind=? AND id=?", (kind, key))
        return json.loads(rows[0]["data"]) if rows else None

    async def list(self, kind: str, scope: str | None = None, limit: int = 500) -> list[dict]:
        sql, args = "SELECT data FROM records WHERE kind=?", (kind,)
        if scope is not None:
            sql += " AND scope=?"
            args += (scope,)
        rows = await self.query(sql + " ORDER BY updated DESC LIMIT ?", (*args, limit))
        return [json.loads(row["data"]) for row in rows]

    async def delete(self, kind: str, key: str) -> None:
        await self.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, key))

    async def page(self, kind: str, page: int, page_size: int, approval_filter: str = "") -> dict:
        where = "kind=?"
        if kind == "approval" and approval_filter == "pending":
            where += " AND json_extract(data,'$.status')='pending' AND COALESCE(json_extract(data,'$.reviewer'),'')!='model'"
        elif kind == "approval" and approval_filter == "history":
            where += " AND (COALESCE(json_extract(data,'$.status'),'')!='pending' OR json_extract(data,'$.reviewer')='model')"
        # Count and read under one lock so a concurrent approval cannot empty the last page.
        async with self.lock:
            async with self.db.execute(
                f"SELECT COUNT(*) FROM records WHERE {where}", (kind,)
            ) as cursor:
                count_row = await cursor.fetchone()
                assert count_row is not None
                total = count_row[0]
            page = min(page, max(1, (total + page_size - 1) // page_size))
            async with self.db.execute(
                f"SELECT data FROM records WHERE {where} ORDER BY updated DESC,id DESC LIMIT ? OFFSET ?",
                (kind, page_size, (page - 1) * page_size),
            ) as cursor:
                items = [json.loads(row[0]) for row in await cursor.fetchall()]
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    async def with_status(
        self, kind: str, statuses: tuple[str, ...], limit: int = 1000, *, scope: str | None = None
    ) -> list[dict]:
        marks = ",".join("?" for _ in statuses)
        sql = (
            f"SELECT data FROM records WHERE kind=? AND json_extract(data,'$.status') IN ({marks})"
        )
        args = (kind, *statuses)
        if scope is not None:
            sql += " AND scope=?"
            args += (scope,)
        rows = await self.query(sql + " ORDER BY created LIMIT ?", (*args, limit))
        return [json.loads(row["data"]) for row in rows]

    async def append(
        self,
        session: str,
        turn: str,
        message: dict,
        *,
        memory_profile: str | None = None,
        require_turn: bool = False,
    ) -> None:
        if memory_profile is not None:
            message = {**message, "_memory_profile": memory_profile}
        sql = "INSERT INTO messages(session,turn,data,created) SELECT ?,?,?,?"
        args = (session, turn, canonical(message), now().timestamp())
        if require_turn:
            # A delayed plugin reply must not recreate a chat deleted since dispatch.
            sql += " WHERE EXISTS (SELECT 1 FROM messages WHERE session=? AND turn=?)"
            args += (session, turn)
        await self.execute(sql, args)

    async def history(
        self,
        session: str,
        limit: int = 200,
        *,
        include_commands: bool = True,
        memory_profile: str = "default",
        timeline: bool = False,
    ) -> list[dict]:
        epoch = await self.get("session_epoch", memory_context_key(session, memory_profile))
        condition = "" if include_commands else " AND COALESCE(json_extract(data,'$._command'),0)=0"
        recent = (
            "SELECT seq,turn,data FROM messages WHERE session=? AND seq>?"
            f" AND {MEMORY_PROFILE_FILTER}" + condition + " ORDER BY seq DESC LIMIT ?"
        )
        args = (session, epoch["seq"] if epoch else 0, memory_profile, limit)
        if not timeline:
            rows = await self.query(f"SELECT data FROM ({recent}) ORDER BY seq", args)
            return [json.loads(row["data"]) for row in rows]

        rows = await self.query(
            "SELECT m.seq,m.turn,m.data,d.data AS diff,json_extract(t.data,'$.status') AS status,"
            "(julianday(json_extract(t.data,'$.finished')) - "
            "julianday(json_extract(t.data,'$.started'))) * 86400 AS elapsed "
            f"FROM ({recent}) m LEFT JOIN records t ON t.kind='turn' AND t.id=m.turn "
            "AND t.scope=? LEFT JOIN records d ON d.kind='tool_diff' AND d.scope=? "
            "AND d.id=m.turn||':'||json_extract(m.data,'$.tool_call_id') ORDER BY m.seq DESC",
            (*args, session, session),
        )
        messages, finished = [], set()
        for row in rows:
            message = {**json.loads(row["data"]), "_seq": row["seq"]}
            if row["diff"]:
                message["_file_diffs"] = json.loads(row["diff"])["files"]
            if (
                row["turn"] not in finished
                and message.get("role") == "assistant"
                and message.get("content")
                and row["elapsed"] is not None
            ):
                message["_elapsed_seconds"] = max(0, round(row["elapsed"]))
                message["_turn_status"] = row["status"]
                finished.add(row["turn"])
            messages.append(message)
        timeline_messages = messages[::-1]
        shell_calls = set()
        artifacts = {}
        for message in timeline_messages:
            for call in message.get("tool_calls") or ():
                if call.get("function", {}).get("name") == "shell":
                    shell_calls.add(call.get("id"))
            if message.get("role") == "tool" and message.get("tool_call_id") in shell_calls:
                try:
                    value = json.loads(message.get("content") or "{}")
                except ValueError:
                    continue
                if (
                    isinstance(value, dict)
                    and isinstance(value.get("artifact"), str)
                    and value.get("truncated")
                ):
                    artifacts[value["artifact"]] = message
        if artifacts:
            marks = ",".join("?" for _ in artifacts)
            for row in await self.query(
                f"SELECT id,json_extract(data,'$.content') AS content FROM records "
                f"WHERE kind='artifact' AND scope=? AND id IN ({marks})",
                (session, *artifacts),
            ):
                try:
                    output = json.loads(row["content"])
                except (ValueError, TypeError):
                    continue
                if isinstance(output, dict) and isinstance(output.get("exit_code"), int):
                    out, err = output.get("stdout", ""), output.get("stderr", "")
                    if isinstance(out, str) and isinstance(err, str):
                        artifacts[row["id"]]["_shell_output"] = {
                            "exit_code": output["exit_code"],
                            "stdout": out[:16000],
                            "stderr": err[:16000],
                            "truncated": bool(output.get("truncated"))
                            or len(out) > 16000
                            or len(err) > 16000,
                        }
        return timeline_messages

    async def reset_history(self, session: str, *, memory_profile: str = "default") -> None:
        key = memory_context_key(session, memory_profile)
        rows = await self.query(
            f"SELECT COALESCE(MAX(seq),0) AS seq FROM messages WHERE session=? AND {MEMORY_PROFILE_FILTER}",
            (session, memory_profile),
        )
        await self.put("session_epoch", key, {"seq": rows[0]["seq"]})
        await self.delete("context_state", key)

    async def web_chats(self) -> list[dict]:
        rows = await self.query("""
            WITH ids(id) AS (
                SELECT 'web:owner' UNION SELECT id FROM web_chat
                UNION SELECT id FROM records WHERE kind='session' AND id GLOB 'web:*'
                UNION SELECT DISTINCT session FROM messages WHERE session GLOB 'web:*'
            )
            SELECT i.id, c.title, coalesce(c.pinned,0) AS pinned,
                (SELECT substr(coalesce(json_extract(data,'$._original_content'),
                                        json_extract(data,'$.content')),1,120)
                 FROM messages WHERE session=i.id AND json_extract(data,'$.role')='user'
                 ORDER BY seq LIMIT 1) AS opening
            FROM ids i LEFT JOIN web_chat c ON c.id=i.id
            WHERE coalesce(c.deleted,0)=0 OR EXISTS (SELECT 1 FROM messages WHERE session=i.id)
            ORDER BY i.id
        """)
        for row in rows:
            opening = " ".join((row.pop("opening") or "").split("\n<context", 1)[0].split())
            row["title"] = row["title"] or (
                "默认对话" if row["id"] == "web:owner" else opening[:48] or "新对话"
            )
            row["pinned"] = bool(row["pinned"])
        return rows

    async def update_web_chat(self, session: str, title: str | None, pinned: bool | None) -> None:
        changed = await self.execute(
            "INSERT INTO web_chat(id,title,pinned) VALUES(?,?,coalesce(?,0)) "
            "ON CONFLICT(id) DO UPDATE SET title=coalesce(excluded.title,web_chat.title),"
            "pinned=coalesce(?,web_chat.pinned),deleted=0 "
            "WHERE web_chat.deleted=0 OR EXISTS (SELECT 1 FROM messages WHERE session=?)",
            (session, title, pinned, pinned, session),
        )
        if not changed:
            raise PublicError("对话已删除")

    async def delete_web_chat(self, session: str) -> None:
        async with self.lock:
            try:
                await self.db.execute("DELETE FROM messages WHERE session=?", (session,))
                await self.db.execute(
                    "DELETE FROM records WHERE kind IN ('tool_diff','artifact','help_image') AND scope=?",
                    (session,),
                )
                await self.db.execute(
                    "DELETE FROM records WHERE (id=? AND kind IN ('session','session_model','session_persona')) "
                    "OR (kind IN ('session_epoch','context_state') AND (id=? OR id GLOB ?))",
                    (session, session, "memory:*:" + session),
                )
                await self.db.execute(
                    "INSERT INTO web_chat(id,deleted) VALUES(?,1) ON CONFLICT(id) "
                    "DO UPDATE SET title=NULL,pinned=0,deleted=1",
                    (session,),
                )
                await self.db.commit()
            except BaseException:
                await self.db.rollback()
                raise

    async def record_group(
        self,
        session: str,
        message_id: str,
        sender: str,
        name: str,
        text: str,
        triggered: bool,
        *,
        memory_profile: str = "default",
    ) -> None:
        session = memory_context_key(session, memory_profile)
        await self.execute(
            "INSERT OR IGNORE INTO group_messages"
            "(session,message_id,sender,name,text,triggered,created) VALUES(?,?,?,?,?,?,?)",
            (session, message_id, sender, name, text, int(triggered), now().timestamp()),
        )
        # Bounded per-group buffer; the full chat log is not an archive target.
        await self.execute(
            "DELETE FROM group_messages WHERE session=? AND seq<=(SELECT seq FROM group_messages "
            "WHERE session=? ORDER BY seq DESC LIMIT 1 OFFSET ?)",
            (session, session, GROUP_MESSAGES_KEPT),
        )

    async def group_sender_name(
        self, session: str, sender: str, *, memory_profile: str = "default"
    ) -> str:
        rows = await self.query(
            "SELECT name FROM group_messages WHERE session=? AND sender=? "
            "ORDER BY seq DESC LIMIT 1",
            (memory_context_key(session, memory_profile), sender),
        )
        return rows[0]["name"] if rows else ""

    async def group_messages(
        self,
        session: str,
        limit: int,
        *,
        before_seq: int = 0,
        query: str = "",
        exclude: str = "",
        memory_profile: str = "default",
    ) -> list[dict]:
        rows = await self.query(
            "SELECT seq,sender,name,text,triggered,created FROM group_messages WHERE session=? "
            "AND (?=0 OR seq<?) AND message_id!=? AND instr(lower(text),lower(?))>0 "
            "ORDER BY seq DESC LIMIT ?",
            (
                memory_context_key(session, memory_profile),
                before_seq,
                before_seq,
                exclude,
                query,
                limit,
            ),
        )
        return rows[::-1]

    async def activity(self, kind: str, detail: str, scope: str = "") -> None:
        log.debug(f"{kind} {detail}", scope)
        await self.put(
            "activity", uid(), {"kind": kind, "detail": detail, "at": now().isoformat()}, scope
        )
