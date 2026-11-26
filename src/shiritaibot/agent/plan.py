from shiritaibot.core import PublicError, canonical
from shiritaibot.i18n import t
from shiritaibot.store import memory_context_key

PLAN_STATUSES = {
    "pending": {"in_progress", "skipped"},
    "in_progress": {"completed", "blocked", "pending"},
    "blocked": {"in_progress", "skipped"},
    "completed": set(),
    "skipped": set(),
}
PLAN_SCHEMA = {
    "type": "array",
    "maxItems": 30,
    "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "minLength": 1, "maxLength": 64},
            "title": {"type": "string", "minLength": 1, "maxLength": 300},
            "active_form": {"type": "string", "minLength": 1, "maxLength": 300},
            "status": {"enum": list(PLAN_STATUSES)},
        },
        "required": ["id", "title", "active_form", "status"],
        "additionalProperties": False,
    },
}


async def read_plan(store, session, profile):
    row = await store.get("session_tasks", memory_context_key(session, profile))
    return row["steps"] if row else []


def plan_anchor(steps):
    if not steps:
        return ""
    snapshot = [
        [step["id"], step["status"], step["title"][:100]]
        for step in steps
    ]
    return "\n\n" + t("agent.execution.plan", steps=canonical(snapshot))


async def update_plan(store, ctx, steps):
    old = {step["id"]: step["status"] for step in await read_plan(store, ctx.session, ctx.memory_profile)}
    if len({step["id"] for step in steps}) != len(steps) or sum(
        step["status"] == "in_progress" for step in steps
    ) > 1:
        raise PublicError("agent.execution.planInvalid")
    for step in steps:
        previous = old.get(step["id"])
        if previous and step["status"] != previous and step["status"] not in PLAN_STATUSES[previous]:
            raise PublicError("agent.execution.planTransition", id=step["id"])
    key = memory_context_key(ctx.session, ctx.memory_profile)
    await store.put("session_tasks", key, {"steps": steps}, ctx.session)
    store.bus.emit({"kind": "task_plan", "session": ctx.session})
    return {"updated": True}


async def clear_plan(store, session, profile, *, archive=""):
    key = memory_context_key(session, profile)
    if archive and (row := await store.get("session_tasks", key)):
        await store.put("task_plan_archive", archive, {"steps": row["steps"]}, session)
    await store.delete("session_tasks", key)
    store.bus.emit({"kind": "task_plan", "session": session})
