"""Prompt fragment for the current emotion value.

Rendered from a state snapshot at the start of a turn and injected into the user
message's `<context>`. The tone tier decides how far the wording may go; a group
value is atmosphere only and never names or cares for anyone publicly.
"""

from __future__ import annotations

from shiritaibot.i18n import TranslatedDict, t
from shiritaibot.prompts import prompt as prompt_text

EMOTIONS = TranslatedDict({
    "frustrated": "emotion.moods.frustrated",
    "anxious": "emotion.moods.anxious",
    "sad": "emotion.moods.sad",
    "angry": "emotion.moods.angry",
    "tired": "emotion.moods.tired",
})
CALM_FLOOR = 0.25


def _strength(value: float) -> str:
    if value >= 0.85:
        return t("emotion.strength.very")
    return "" if value >= 0.6 else t("emotion.strength.somewhat")


def _age(at: float, updated: float) -> str:
    delta = max(0.0, at - updated)
    if delta < 60:
        return t("emotion.age.justNow")
    if delta < 3600:
        return t("emotion.age.minutes", count=int(delta // 60))
    if delta < 86400:
        return t("emotion.age.hours", count=int(delta // 3600))
    return t("emotion.age.days", count=int(delta // 86400))


def render(
    *,
    group: bool,
    value: float,
    affect: str,
    situation: str,
    updated: float,
    tone: float,
    at: float,
) -> str:
    if value < CALM_FLOOR:
        return ""
    mood = _strength(value) + EMOTIONS.get(affect, t("emotion.moods.unsettled"))
    if group:
        if tone < 0.2 and value < 0.6:
            return ""
        lines = [
            "<emotion>",
            prompt_text("EMOTION_GROUP_MOOD", mood=mood),
            prompt_text("EMOTION_GROUP_TONE"),
        ]
        if tone > 0.6:
            lines.append(prompt_text("EMOTION_GROUP_LIGHT"))
        lines.append("</emotion>")
        return "\n".join(lines)
    if tone < 0.2:
        if value < 0.6:
            return ""
        return "\n".join(["<emotion>", prompt_text("EMOTION_USER_MOOD", mood=mood), prompt_text("EMOTION_TONE_MILD"), "</emotion>"])
    lines = [
        "<emotion>",
        prompt_text("EMOTION_USER_MOOD", mood=mood),
        prompt_text("EMOTION_TONE_STEADY"),
        prompt_text("EMOTION_ACKNOWLEDGE"),
    ]
    if tone > 0.6:
        lines.append(
            prompt_text("EMOTION_CARE")
        )
    if situation and value >= 0.4:
        lines.append(
            prompt_text("EMOTION_SITUATION_NOTE", situation=situation, age=_age(at, updated))
        )
    lines.append("</emotion>")
    return "\n".join(lines)
