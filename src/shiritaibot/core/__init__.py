from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from shiritaibot.i18n import Locale, Text, localize, t

if TYPE_CHECKING:
    from shiritaibot.auth.menu import ApprovalMenu


def now() -> datetime:
    return datetime.now(UTC)


def uid() -> str:
    return uuid.uuid4().hex


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


delegated_workspace: ContextVar[tuple[str, Path] | None] = ContextVar(
    "delegated_workspace", default=None
)


def session_dir(home: Path, kind: str, session: str) -> Path:
    delegated = delegated_workspace.get()
    if delegated and kind == "workspaces" and delegated[0] == session:
        return delegated[1]
    return home / kind / hashlib.sha256(session.encode()).hexdigest()[:24]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Actor(Model):
    """Someone an event involves, as raw identity data; the core decides what to remember."""

    # Platform identity such as email:someone@example.edu or qq:12345.
    identity: str = Field(pattern=r"^[a-z][a-z0-9_]*:\S+$", max_length=320)
    name: str = Field(default="", max_length=200)
    role: str = Field(default="", max_length=50)  # sender | recipient | author | ...


EVENT_TITLE_LIMIT = 1000


class ExternalEvent(Model):
    id: str = Field(default_factory=uid, min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=200)
    dedup_key: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=EVENT_TITLE_LIMIT)
    payload: dict[str, Any] = Field(default_factory=dict)
    urgency: Literal["low", "normal", "high", "critical"] = "normal"
    occurred_at: AwareDatetime = Field(default_factory=now)
    actors: list[Actor] = Field(default_factory=list, max_length=50)

    @field_validator("id", "source", "kind", "dedup_key", "title", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value


@dataclass(frozen=True)
class ReplyAnchor:
    session: str
    message_id: str
    expires: float


current_reply: ContextVar[ReplyAnchor | None] = ContextVar("current_reply", default=None)
model_usage_session: ContextVar[str] = ContextVar("model_usage_session", default="")


@contextmanager
def record_usage_for(session: str):
    token = model_usage_session.set(session)
    try:
        yield
    finally:
        model_usage_session.reset(token)


@dataclass(frozen=True, slots=True)
class Context:
    session: str
    actor: str
    mode: str = "chat"
    owner: bool = False
    group: bool = False
    background: bool = False
    turn: str = ""
    allowed_tools: tuple[str, ...] | None = None
    # Proactive loops carry a creation-time snapshot, independent of Main sessions.
    tool_policy: dict[str, Any] | None = None
    # Group chatter the Bot may answer or pass on; a pass is not delivered.
    interject: bool = False
    memory_profile: str = "default"
    reply_anchor: ReplyAnchor | None = None
    web_message_ids: tuple[str, ...] = ()
    stream_reply: bool = False
    tool_call_id: str = ""
    subagent_run: str = ""
    allowed_skills: tuple[str, ...] | None = None
    require_manual_approval: bool = False
    group_wake: str = ""
    routine_event: str = ""
    goal_id: str = ""

    @property
    def summary_eligible(self) -> bool:
        return (
            self.owner and not self.group and not self.interject
            and not self.session.startswith("group:")
        )


@dataclass(slots=True)
class Tool:
    name: str
    description: str | Text  # plain text, a catalog key, or lazy Text
    schema: dict[str, Any]
    handler: Callable[[Context, dict[str, Any]], Awaitable[Any]]
    risk: str
    # Chat sessions only offer basic conversation tools; extensions stay Agent-only.
    chat: bool = False
    timeout: float = 60
    plugin: str = "builtin"
    # Runtime availability (for example a disabled feature); None means always.
    available: Callable[[Context], bool] | None = None
    interactive: bool = False

    @property
    def safe(self) -> bool:
        return self.risk == "safe"

    def spec(self) -> dict[str, Any]:
        """The model-facing definition, in the active locale (descriptions may be keys)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": t(self.description),
                "parameters": localize(self.schema),
            },
        }


@dataclass(slots=True)
class Reply:
    text: str = ""
    calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    opaque: dict[str, Any] = field(default_factory=dict)


NEEDS_INPUT = "NEEDS_INPUT:"


class Provider(Protocol):
    async def complete(
        self, route: str, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> Reply: ...


class RuntimePort(Protocol):
    async def submit(self, ctx: Context, text: str) -> str: ...

    async def session_locale(self, session: str) -> tuple[Locale, bool]: ...

    async def handoff(self, session: str, text: str, send) -> None: ...


class ApprovalPort(Protocol):
    menu: ApprovalMenu

    async def resolve(self, key: str, allow: bool, *, approver: Context | None = None) -> None: ...

    async def resolve_latest(self, approver: Context, allow: bool) -> dict: ...


class EventBus:
    """Bounded UI fanout. Slow subscribers lose old activity, never block agents."""

    def __init__(self) -> None:
        self.subscribers: set[asyncio.Queue] = set()

    def emit(self, event: dict[str, Any]) -> None:
        for queue in tuple(self.subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)


class InteractionRequired(Exception):
    """A plugin tool needs the owner to complete an interaction in private."""

    def __init__(self, key: str, reason: str, ttl: int = 86400):
        super().__init__(reason)
        if not key.strip() or not reason.strip() or ttl < 0:
            raise ValueError("interaction requires a key, reason and non-negative TTL")
        self.key, self.reason, self.ttl = key, reason, ttl


class PublicError(Exception):
    """A deliberately sanitized error suitable for the owner or model.

    The message may be a catalog key with ``{name}`` params; it is rendered in the locale
    active when the error is shown (``str(exc)``), not when it is raised. Plain text that
    is not a key is shown unchanged.
    """

    def __init__(self, *args: Any, **params: Any):
        super().__init__(*args)
        self.params = params

    def __str__(self) -> str:
        if len(self.args) == 1 and isinstance(self.args[0], str):
            return t(self.args[0], **self.params)
        return super().__str__()
