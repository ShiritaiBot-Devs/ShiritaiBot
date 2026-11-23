"""Per-OneBot-group observation. All state changes run on the adapter event loop."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from time import monotonic
from zoneinfo import ZoneInfo

from shiritaibot.config import HeartflowConfig
from shiritaibot.core import Context, canonical, now, record_usage_for, uid
from shiritaibot.core.logs import get_logger
from shiritaibot.i18n import Text
from shiritaibot.proactive import quiet_hours
from shiritaibot.prompts import prompt as prompt_text

log = get_logger("adapters.heartflow.log.source")
DIMENSIONS = ("content", "social", "timing", "coherence")


def dimensions() -> dict[str, str]:
    """What each score means, in the active language."""
    return {key: prompt_text(f"HEARTFLOW_{key.upper()}") for key in DIMENSIONS}


def questions() -> dict:
    """Decision-model questions in the active language."""
    return {key: {"type": "noul", "instructions": text} for key, text in dimensions().items()}


async def judge(llm, material: dict, settings=None) -> dict[str, float]:
    async with asyncio.timeout(8):
        answers = await llm.decide("light", material, questions(), settings=settings)
        if answers is None:
            reply = await llm.complete(
                "light",
                prompt_text("HEARTFLOW_SYSTEM") + canonical(dimensions()),
                [{"role": "user", "content": canonical(material)}],
                [],
                settings=settings,
            )
            scores = json.loads(reply.text)
        else:
            scores = {key: answers[key]["noul"] for key in DIMENSIONS}
    if (
        not isinstance(scores, dict)
        or scores.keys() != set(DIMENSIONS)
        or any(type(value) not in (int, float) or not 0 <= value <= 1 for value in scores.values())
    ):
        raise ValueError("invalid heartflow judgment")
    return scores


def cost(cfg: HeartflowConfig, streak: int) -> float:
    weights = [1.0] * cfg.max_streak
    if cfg.energy_curve == "linear":
        weights = [1 + 0.5 * i for i in range(cfg.max_streak)]
    elif cfg.energy_curve == "exponential":
        weights = [1.8**i for i in range(cfg.max_streak)]
    return 0.9 * weights[min(streak, cfg.max_streak - 1)] / sum(weights)


@dataclass
class Observation:
    sequence: int
    wake: str
    profile: str
    phase: str
    id: str = field(default_factory=uid)
    expires: float = field(default_factory=lambda: monotonic() + 60)
    rows: set[str] = field(default_factory=set)
    charged: bool = False
    interrupted: bool = False


@dataclass
class Group:
    phase: str = "LURK"
    energy: float = 1.0
    updated: float = field(default_factory=monotonic)
    until: float = 0
    streak: int = 0
    passes: int = 0
    sequence: int = 0
    wake: str = ""
    last_reply: str = ""
    last_judge: float = float("-inf")
    sent: deque[float] = field(default_factory=deque)
    batch: deque[tuple[str, str]] = field(default_factory=lambda: deque(maxlen=8))
    first: float = 0
    last: float = 0
    timer: asyncio.TimerHandle | None = None
    worker: asyncio.Task | None = None
    observation: Observation | None = None


class Heartflow:
    def __init__(self, runtime, enqueue):
        self.runtime = runtime
        self.config = runtime.config
        self.enqueue = enqueue
        self.groups: defaultdict[str, Group] = defaultdict(Group)

    @property
    def cfg(self) -> HeartflowConfig:
        return self.config.value.group_context.heartflow

    def enabled(self, session: str) -> bool:
        group = self.config.value.group_context
        kind, adapter, target = session.split(":", 2)
        return (
            group.enabled
            and group.interject_mode == "heartflow"
            and kind == "group"
            and any(
                item.id == adapter
                and item.type == "onebot11"
                and item.enabled
                and target in item.groups
                for item in self.config.value.adapters
            )
        )

    def phase(self, session: str, group: Group, phase: str, until: float = 0.0) -> None:
        if self.cfg.developer and group.phase != phase:
            log.debug(f"{group.phase} → {phase}", session)
        group.phase, group.until = phase, until
        if phase != "FOCUS":
            group.streak = 0
            group.last_reply = ""
        group.passes = 0

    @staticmethod
    def discard_batch(group: Group) -> None:
        if group.timer:
            group.timer.cancel()
            group.timer = None
        group.batch.clear()

    def refresh(self, session: str, group: Group) -> None:
        at = monotonic()
        cfg = self.cfg
        group.energy = min(
            1, group.energy + (at - group.updated) / (60 * cfg.energy_recovery_minutes)
        )
        group.updated = at
        while group.sent and group.sent[0] <= at - 3600:
            group.sent.popleft()
        quiet = self.config.value.proactive
        if quiet_hours(
            quiet.quiet_start, quiet.quiet_end, now().astimezone(ZoneInfo(quiet.timezone))
        ):
            if group.phase != "QUIET":
                self.phase(session, group, "QUIET")
                self.discard_batch(group)
                group.wake = uid()
        elif group.phase == "QUIET" or (
            group.phase in ("REST", "FOCUS")
            and at >= group.until
            and (group.phase == "FOCUS" or group.energy >= cost(cfg, 0))
        ):
            self.phase(session, group, "LURK")

    def wake(self, session: str) -> str:
        group = self.groups[session]
        self.refresh(session, group)
        self.discard_batch(group)
        group.wake = uid()
        if group.worker and not (group.observation and group.observation.rows):
            group.worker.cancel()
        return group.wake

    def passive_sent(self, session: str, token: str, text: str) -> None:
        group = self.groups.get(session)
        if not group or not self.enabled(session) or group.wake != token:
            return
        self.refresh(session, group)
        group.wake = uid()  # A split reply refreshes the focus window only once.
        if group.phase == "QUIET":
            return
        self.phase(
            session,
            group,
            "FOCUS" if self.cfg.focus_seconds else "LURK",
            monotonic() + self.cfg.focus_seconds,
        )
        group.streak = 0
        group.last_reply = text[:500]

    def arrival(self, session: str, *, wake: bool = False) -> tuple[Group, str]:
        group = self.groups[session]
        group.sequence += 1
        if wake and group.observation:
            group.observation.interrupted = True
        return group, group.wake

    def message(
        self, session: str, arrival: tuple[Group, str], user: str, speaker: str, text: str
    ) -> None:
        group, wake = arrival
        if self.groups.get(session) is not group or group.wake != wake:
            return
        self.refresh(session, group)
        if group.phase in ("REST", "QUIET") or not any(c.isalnum() for c in text):
            return
        text = text.strip()
        if text.startswith("/") or len(set(text)) == 1 and (len(text) > 1 or text in "嗯哦哈啊好"):
            return
        group.last = monotonic()
        if not group.batch:
            group.first = group.last
        group.batch.append((user, f"{speaker[:100]}：{text[:500]}"))
        self.schedule(session, group)

    def schedule(self, session: str, group: Group) -> None:
        if group.timer:
            group.timer.cancel()
            group.timer = None
        if not group.batch or group.worker or group.observation:
            return
        delay = self.cfg.debounce_seconds * (0.5 if group.phase == "FOCUS" else 1)
        due = min(group.first + 12, group.last + delay)
        wait = 0 if len(group.batch) == 8 else max(0, due - monotonic())
        group.timer = asyncio.get_running_loop().call_later(wait, self.start, session, group)

    def start(self, session: str, group: Group) -> None:
        group.timer = None
        if not self.enabled(session):
            self.discard_batch(group)
            return
        self.refresh(session, group)
        batch = list(group.batch)
        group.batch.clear()
        if not batch or group.phase in ("QUIET", "REST") or self.runtime.busy(session):
            return
        if group.energy < cost(self.cfg, group.streak):
            self.phase(session, group, "REST", monotonic() + self.cfg.rest_seconds)
            return
        interval = self.cfg.min_judge_interval * (0.3 if group.phase == "FOCUS" else 1)
        if (
            monotonic() - group.last_judge < interval
            or len(group.sent) >= self.config.value.group_context.interject_max_per_hour
        ):
            return
        group.last_judge = monotonic()
        observation = Observation(
            group.sequence, group.wake, self.config.value.session_memory(session), group.phase
        )
        group.observation = observation
        task = asyncio.create_task(self.observe(session, group, observation, batch))
        group.worker = task

        # A task cancelled before its first await may never enter its coroutine.
        def done(_):
            group.worker = None
            if not observation.rows:
                group.observation = None
            if self.groups.get(session) is group:
                self.schedule(session, group)

        task.add_done_callback(done)

    def valid(self, session: str, token: str) -> bool:
        group = self.groups.get(session)
        if not group or not self.enabled(session):
            return False
        self.refresh(session, group)
        obs = group.observation
        return bool(
            obs
            and obs.id == token
            and obs.wake == group.wake
            and not obs.interrupted
            and (obs.charged or group.phase == obs.phase)
            and (
                obs.charged
                or len(group.sent) < self.config.value.group_context.interject_max_per_hour
            )
            and obs.profile == self.config.value.session_memory(session)
            and group.sequence - obs.sequence <= 5
            and monotonic() < obs.expires
            and group.phase != "QUIET"
            and not self.runtime.busy(session)
        )

    def passed(self, session: str, group: Group, changed_topic: bool = False) -> None:
        group.passes += 1
        if group.phase == "FOCUS" and (changed_topic or group.passes >= 2):
            self.phase(session, group, "LURK")
        elif group.phase == "LURK" and group.passes >= 3:
            self.phase(session, group, "REST", monotonic() + self.cfg.min_judge_interval)

    async def observe(
        self, session: str, group: Group, obs: Observation, batch: list[tuple[str, str]]
    ) -> None:
        cfg = self.config.value
        ctx = Context(
            session,
            batch[-1][0],
            group=True,
            interject=True,
            memory_profile=obs.profile,
            turn=obs.id,
        )
        try:
            material = {
                "state": group.phase,
                "context": await self.runtime.group_context(ctx, cfg, []),
                "new_messages": [line for _, line in batch],
                "previous_reply": group.last_reply if group.phase == "FOCUS" else "",
            }
            tense = False
            if cfg.emotion.enabled and obs.profile == "default" and self.runtime.emotion:
                try:
                    tense = bool(await self.runtime.emotion.context(ctx, cfg))
                except Exception as exc:
                    log.debug(Text("adapters.heartflow.log.moodFailed", name=type(exc).__name__), session)
            if not self.valid(session, obs.id):
                return
            with record_usage_for(session):
                scores = await judge(self.runtime.llm, material, cfg.with_session_routes(session))
            if not self.valid(session, obs.id):
                return
            focused = group.phase == "FOCUS"
            threshold = 0.9 - 0.4 * self.cfg.activity - 0.2 * focused
            threshold += 0.12 * (1 - group.energy) + 0.04 * group.streak + 0.04 * tense
            score = sum(scores.values()) / len(scores)
            if self.cfg.developer:
                log.debug(Text("adapters.heartflow.log.score", phase=group.phase, score=score, threshold=threshold), session)
            changed = focused and scores["coherence"] < 0.35
            if changed or score < min(0.98, threshold):
                self.passed(session, group, changed)
                return
            prompt = prompt_text("HEARTFLOW_BATCH") + "\n".join(material["new_messages"])
            if material["previous_reply"]:
                prompt += prompt_text("HEARTFLOW_LAST") + material["previous_reply"]
            async with asyncio.timeout(max(0, obs.expires - monotonic())):
                reply = await self.runtime.interjection_candidate(ctx, prompt)
            if not self.valid(session, obs.id):
                if self.cfg.developer:
                    log.debug("adapters.heartflow.log.expired", session)
                return
            if reply == "<pass>":
                self.passed(session, group)
            elif reply:
                await self.enqueue(session, reply, observation=obs)
        except Exception as exc:
            log.debug(Text("adapters.heartflow.log.skipped", name=type(exc).__name__), session)

    def settled(self, session: str, row: dict) -> None:
        group = self.groups.get(session)
        obs = group.observation if group else None
        if not group or not obs or row["id"] not in obs.rows:
            return
        obs.rows.remove(row["id"])
        if row["status"] == "sent" and not obs.charged:
            obs.charged = True
            self.refresh(session, group)
            at = monotonic()
            group.sent.append(at)
            group.energy = max(0, group.energy - cost(self.cfg, group.streak))
            if group.phase != "QUIET" and group.wake == obs.wake:
                group.streak += 1
                if group.streak >= self.cfg.max_streak or group.energy < cost(
                    self.cfg, group.streak
                ):
                    self.phase(session, group, "REST", at + self.cfg.rest_seconds)
                elif self.cfg.focus_seconds:
                    self.phase(session, group, "FOCUS", at + self.cfg.focus_seconds)
                    group.last_reply = row["text"][:500]
                else:
                    self.phase(session, group, "LURK")
        if not obs.rows and not group.worker:
            group.observation = None
            self.schedule(session, group)

    async def reset(self, sessions: list[str] | None = None) -> None:
        tasks = []
        for session in list(self.groups) if sessions is None else sessions:
            group = self.groups.pop(session)
            self.discard_batch(group)
            if group.worker:
                group.worker.cancel()
                tasks.append(group.worker)
        await asyncio.gather(*tasks, return_exceptions=True)
