from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from croniter import croniter
from pydantic import AwareDatetime, Field, model_validator

from shiritaibot.core import EVENT_TITLE_LIMIT, ExternalEvent, Model, PublicError, now, uid
from shiritaibot.core.logs import brief, get_logger
from shiritaibot.i18n import Text, join_list, t

log = get_logger("routine.log.source")

KINDS = {"todo": "routine.kinds.todo", "event": "routine.kinds.event", "course": "routine.kinds.course", "reminder": "routine.kinds.reminder", "job": "routine.kinds.job"}
FINISHED = ("completed", "cancelled", "fired")
DAY = 86400
# Finished items and past events stay visible this long, then are deleted automatically.
KEEP_FINISHED = 30 * DAY


class RoutineItem(Model):
    kind: str = Field(pattern=r"^(todo|event|course|reminder|job)$")
    title: str = Field(min_length=1, max_length=2000)
    due: AwareDatetime | None = None
    end: AwareDatetime | None = None
    cron: str = ""
    timezone: str = "Asia/Shanghai"

    @model_validator(mode="after")
    def valid(self):
        ZoneInfo(self.timezone)
        if self.kind == "job" and (not croniter.is_valid(self.cron) or len(self.cron.split()) != 5):
            raise ValueError("job requires a five-field cron expression")
        if self.kind in {"event", "course", "reminder"} and not self.due:
            raise ValueError("due time with timezone required")
        if self.end and (not self.due or self.end <= self.due):
            raise ValueError("end must follow due")
        return self


# Fields a routine_update call may change; kind is fixed once created.
EDITABLE = ("title", "due", "end", "cron", "timezone")


def schedule(item: RoutineItem) -> float | None:
    """Next fire time: reminders at their due time, jobs at the next cron match."""
    if item.kind == "reminder" and item.due:
        return item.due.timestamp()
    if item.kind == "job":
        return (
            croniter(item.cron, now().astimezone(ZoneInfo(item.timezone)))
            .get_next(datetime)
            .timestamp()
        )
    return None


class RoutineManager:
    def __init__(self, store, emit):
        self.store, self.emit = store, emit
        self.lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.pruned = 0.0
        self.retries: dict[tuple[str, float], tuple[float, float]] = {}

    async def create(self, item: RoutineItem, *, session: str, actor: str = "") -> dict:
        key = uid()
        next_fire = schedule(item)
        body = {
            **item.model_dump(mode="json"),
            "status": "scheduled" if next_fire else "open",
            "next_fire": next_fire,
        }
        if actor and session.startswith("group:"):
            body["actor"] = actor
        result = await self.store.put("routine", key, body, session)
        log.log(
            t("routine.created", kind=Text(KINDS[item.kind]), brief=brief(item.title, 60))
            + (f"，cron {item.cron}" if item.cron else "")
            + (
                t("routine.createdAt", when=item.due.astimezone(ZoneInfo(item.timezone)))
                if item.due
                else ""
            )
        )
        self.wake.set()
        return result

    async def _get(self, key: str, session: str | None) -> dict:
        row = await self.store.get("routine", key)
        if not row or (session is not None and row.get("scope") != session):
            raise PublicError("routine.missing")
        return row

    async def update_status(self, key: str, status: str) -> dict:
        return await self.update(key, {}, session=None, status=status)

    async def update(
        self, key: str, changes: dict, *, session: str | None, status: str | None = None
    ) -> dict:
        """Edit an item in place. Changing the time of a reminder or job reschedules it,
        unless it was completed or cancelled."""
        if (not changes and status is None) or set(changes) - set(EDITABLE):
            raise PublicError("routine.onlyUpdate", fields=t("common.separator").join(EDITABLE))
        if status is not None and status not in {"completed", "cancelled", "open"}:
            raise PublicError("routine.statusInvalid")
        async with self.lock:
            row = await self._get(key, session)
            if changes:
                self._edit(row, changes)
            if status is not None:
                row.update(status=status, next_fire=None)
                log.log(
                    Text("routine.log.status", kind=Text(KINDS.get(row['kind'], row['kind'])), brief=brief(row['title'], 60), status=status)
                )
            result = await self.store.put("routine", key, row, row["scope"])
        self.wake.set()
        return result

    def _edit(self, row: dict, changes: dict) -> None:
        current = {field: row.get(field) for field in ("kind", *EDITABLE)}
        try:
            item = RoutineItem.model_validate({**current, **changes})
        except ValueError:
            raise PublicError(
                "routine.invalidUpdate"
            ) from None
        row.update({k: v for k, v in item.model_dump(mode="json").items() if k in EDITABLE})
        if {"due", "cron", "timezone"} & set(changes) and row["status"] not in {
            "completed",
            "cancelled",
        }:
            next_fire = schedule(item)
            row.update(status="scheduled" if next_fire else "open", next_fire=next_fire)
        log.log(
            t("routine.updated", kind=Text(KINDS.get(row['kind'], row['kind'])), brief=brief(row['title'], 60))
            + join_list(sorted(changes))
        )

    async def delete(self, key: str, *, session: str) -> dict:
        async with self.lock:
            row = await self._get(key, session)
            await self.store.delete("routine", key)
        log.log(Text("routine.log.deleted", kind=Text(KINDS.get(row['kind'], row['kind'])), brief=brief(row['title'], 60)))
        self.wake.set()
        return {"deleted": key, "kind": row["kind"], "title": row["title"]}

    async def prune(self, stamp: float) -> int:
        """Delete items finished (or, for events, ended) more than KEEP_FINISHED ago.

        Open todos are never pruned, however overdue: they still need doing.
        """
        async with self.lock:
            stale = []
            for record in await self.store.query(
                "SELECT id, data, updated FROM records WHERE kind='routine'"
            ):
                row = json.loads(record["data"])
                if row["status"] in FINISHED:
                    finished = record["updated"]
                elif row["kind"] == "event" and row.get("due"):
                    finished = datetime.fromisoformat(row.get("end") or row["due"]).timestamp()
                else:
                    continue
                if finished < stamp - KEEP_FINISHED:
                    stale.append(record["id"])
            if stale:
                marks = ",".join("?" * len(stale))
                await self.store.execute(
                    f"DELETE FROM records WHERE kind='routine' AND id IN ({marks})", tuple(stale)
                )
        if stale:
            log.log(Text("routine.log.cleaned", count=len(stale), value=KEEP_FINISHED // DAY))
        return len(stale)

    async def _fire(self, row: dict, stamp: float) -> None:
        key, fire = row["id"], row["next_fire"]
        delay = stamp - fire
        title = (t("routine.lateTitle") if delay > 60 else "") + row["title"]
        payload = {
            "routine_id": key,
            "scheduled_at": datetime.fromtimestamp(fire, UTC).isoformat(),
            "late_seconds": delay,
        }
        if len(title) > EVENT_TITLE_LIMIT:
            payload["routine_title"] = row["title"]
            title = title[: EVENT_TITLE_LIMIT - 1] + "…"
        event = ExternalEvent(
            source="routine",
            kind=f"routine.{row['kind']}",
            dedup_key=f"{key}:{fire}",
            title=title,
            payload=payload,
        )
        if row["kind"] == "job":
            next_fire = (
                croniter(row["cron"], now().astimezone(ZoneInfo(row["timezone"])))
                .get_next(datetime)
                .timestamp()
            )
            changes = {"next_fire": next_fire}
        else:
            changes = {"status": "fired", "next_fire": None}
        await self.emit(event, routine_session=row["scope"], routine_actor=row.get("actor", ""))
        await self.store.put("routine", key, {**row, **changes}, row["scope"])
        log.log(
            Text(
                "routine.log.firedLate" if delay > 60 else "routine.log.fired",
                kind=Text(KINDS.get(row["kind"], row["kind"])),
                brief=brief(row["title"], 80),
                delay=delay,
            )
        )

    async def tick(self) -> float:
        rows = await self.store.with_status("routine", ("scheduled",), limit=10000)
        scheduled = sorted(
            (row["next_fire"], row["id"])
            for row in rows
            if row.get("next_fire") is not None and row["status"] == "scheduled"
        )
        stamp = now().timestamp()
        wake_at = stamp + 30
        retries = {}
        for fire, key in scheduled:
            if fire > stamp:
                wake_at = min(wake_at, fire)
                break
            occurrence = (key, fire)
            retry_at, backoff = self.retries.get(occurrence, (0.0, 0.0))
            if retry_at <= stamp:
                try:
                    async with self.lock:
                        row = await self.store.get("routine", key)
                        if row and row["status"] == "scheduled" and row["next_fire"] == fire:
                            await self._fire(row, stamp)
                    continue
                except Exception as exc:
                    backoff = min(300, max(5, backoff * 2))
                    retry_at = now().timestamp() + backoff
                    log.error(Text("routine.log.retry", key=key, backoff=backoff), exc=exc)
            retries[occurrence] = (retry_at, backoff)
            wake_at = min(wake_at, retry_at)
        self.retries = retries
        return max(0.1, wake_at - now().timestamp())

    async def run(self) -> None:
        while True:
            self.wake.clear()
            try:
                stamp = now().timestamp()
                if stamp - self.pruned >= DAY:
                    self.pruned = stamp
                    await self.prune(stamp)
                delay = await self.tick()
            except Exception as exc:
                log.error(Text("routine.log.schedulerError", name=type(exc).__name__), exc=exc)
                await self.store.activity("scheduler.failed", type(exc).__name__)
                delay = 5
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=delay)
            except TimeoutError:
                pass
