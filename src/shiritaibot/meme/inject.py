"""Meme injectors (docs/MEMES.md §3).

- prompt(): usage instructions appended to the system prompt of QQ sessions. It lists
  only configured categories, never library contents, so it stays byte-stable and
  keeps provider prompt caches warm while memes are collected.
- split(): runs between the send function and the protocol side. <meme>tag</meme>
  markers become separate sticker messages; a marker inside a sentence splits one
  reply into text + sticker + text.
"""

from __future__ import annotations

import re

from shiritaibot.config import meme_description
from shiritaibot.prompts import prompt as prompt_text

# Chat models sometimes drift on the tag name ("<mememe>"), so accept any run of
# "me" and let the opening and closing tag differ; a stray tag is stripped below.
MARKER = re.compile(r"<(?:me){2,}>\s*([A-Za-z0-9_]{1,40})\s*</(?:me){2,}>", re.IGNORECASE)
STRAY = re.compile(r"</?(?:me){2,}>", re.IGNORECASE)


def meme_prompt(cfg, *, tool_guidance: bool = True) -> str:
    meme = cfg.meme
    if not (meme.enabled and meme.inject):
        return ""
    categories = "\n".join(
        f"- {c.name}：{meme_description(c)}" if c.description else f"- {c.name}" for c in meme.categories
    )
    return (
        prompt_text("MEME_INJECT_HEAD")
        + (
            prompt_text("MEME_INJECT_BY_ID")
            if tool_guidance
            else "。"
        )
        + prompt_text("MEME_INJECT_RULES", categories=categories, max_per_reply=meme.max_per_reply)
        + (
            prompt_text("MEME_INJECT_TOOLS")
            if tool_guidance
            else ""
        )
        + "\n</memes>"
    )


class MemeInjector:
    def __init__(self, core, config):
        self.core, self.config = core, config

    def prompt(self, cfg=None) -> str:
        return meme_prompt(cfg or self.config.value)

    def split(self, text: str) -> list[str | dict]:
        """Text pieces and meme records in order; unknown or excess markers are dropped."""
        cfg = self.config.value.meme
        if not cfg.enabled or not STRAY.search(text):
            return [text]
        pieces: list[str | dict] = []
        buffer, end, used = "", 0, 0
        for match in MARKER.finditer(text):
            buffer += text[end : match.start()]
            end = match.end()
            row = self.core.resolve(match[1]) if used < cfg.max_per_reply else None
            if row is None:
                continue
            used += 1
            if buffer.strip():
                pieces.append(STRAY.sub("", buffer).strip())
            pieces.append(row)
            buffer = ""
        buffer += text[end:]
        if buffer.strip():
            pieces.append(STRAY.sub("", buffer).strip())
        return [piece for piece in pieces if piece]

    @staticmethod
    def label(row: dict) -> str:
        """Readable stand-in for a sent sticker in group context and WebUI replies."""
        description = row["description"]
        return prompt_text("MEME_SENT", value=description[:40] + '…' if len(description) > 40 else description)

    def plain(self, text: str) -> str:
        """Markers rendered as readable labels, for surfaces that cannot show stickers."""
        return "\n".join(
            piece if isinstance(piece, str) else self.label(piece) for piece in self.split(text)
        )
