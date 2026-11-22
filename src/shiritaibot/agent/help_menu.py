"""Render slash-command help with a system font or the bundled CJK font."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

type HelpSection = tuple[str, list[tuple[str, str]]]
WRAP_TOKENS = re.compile(r"[A-Za-z0-9_./:-]+|.")
BUNDLED_CJK_FONT = Path(__file__).parent / "fonts" / "NotoSansSC-Regular.otf"


@dataclass(frozen=True)
class Theme:
    background: str
    card: str
    edge: str
    header: str
    title: str
    accent: str
    command: str
    text: str


THEMES = {
    "light": Theme(
        "#f2f1fa",
        "#ffffff",
        "#e5e1f1",
        "#e6defa",
        "#241744",
        "#6242b5",
        "#503293",
        "#403a50",
    ),
    "dark": Theme(
        "#14121d",
        "#211e2e",
        "#373146",
        "#30254a",
        "#faf5ff",
        "#cdb7ff",
        "#decaff",
        "#dfd8ec",
    ),
}


def system_cjk_font() -> Path | None:
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for path in (
        windows / "msyh.ttc",
        windows / "NotoSansSC-VF.ttf",
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
    ):
        if path.is_file():
            return path
    return None


def wrap(text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines = []
    for paragraph in text.splitlines():
        line = ""
        for token in WRAP_TOKENS.findall(paragraph):
            parts = token if len(token) > 1 and font.getlength(token) > width else (token,)
            for part in parts:
                if line and font.getlength(line + part) > width:
                    lines.append(line.rstrip())
                    line = part.lstrip()
                else:
                    line += part
        lines.append(line.rstrip())
    return lines


def render_help_menu(name: str, sections: list[HelpSection], style: str) -> bytes:
    theme = THEMES[style]
    path = system_cjk_font() or BUNDLED_CJK_FONT
    # Noto's collection puts Simplified Chinese after Japanese and Korean.
    index = 2 if path.name == "NotoSansCJK-Regular.ttc" else 0
    title_font = ImageFont.truetype(str(path), 48, index=index)
    heading_font = ImageFont.truetype(str(path), 28, index=index)
    body_font = ImageFont.truetype(str(path), 24, index=index)
    width, margin = 1120, 28
    inner_width = width - 2 * (margin + 28)
    command_width, column_gap, line_height = 306, 42, 34
    detail_width = inner_width - command_width - column_gap
    name_lines = wrap(name, body_font, inner_width)
    header_height = 116 + line_height * len(name_lines)
    cards = []
    for title, entries in sections:
        headings = wrap(title, heading_font, inner_width - 23)
        rows = []
        for command, description in entries:
            commands = wrap(command, body_font, command_width)
            details = wrap(description, body_font, detail_width)
            row_height = line_height * max(len(commands), len(details)) + 16
            rows.append((commands, details, row_height))
        card_height = 44 + 38 * len(headings) + sum(row_height for _, _, row_height in rows)
        cards.append((headings, rows, card_height))

    height = 2 * margin + header_height + sum(height + 18 for _, _, height in cards)
    image = Image.new("RGB", (width, height), theme.background)
    draw = ImageDraw.Draw(image)
    x, right = margin + 28, width - margin
    draw.rounded_rectangle((margin, margin, right, margin + header_height), 30, fill=theme.header)
    draw.text((x, margin + 26), "命令帮助", font=title_font, fill=theme.title, anchor="lt")
    draw.rounded_rectangle((right - 128, margin + 31, right - 28, margin + 77), 23, fill=theme.card)
    draw.text((right - 78, margin + 54), "/help", font=body_font, fill=theme.command, anchor="mm")
    for index, line in enumerate(name_lines):
        draw.text(
            (x, margin + 98 + line_height * index),
            line,
            font=body_font,
            fill=theme.accent,
            anchor="lt",
        )

    y = margin + header_height + 18
    detail_x = x + command_width + column_gap
    for headings, rows, card_height in cards:
        draw.rounded_rectangle(
            (margin, y, right, y + card_height), 25, fill=theme.card, outline=theme.edge
        )
        cursor = y + 24
        draw.rounded_rectangle((x, cursor + 2, x + 6, cursor + 27), 3, fill=theme.accent)
        for heading in headings:
            draw.text((x + 22, cursor), heading, font=heading_font, fill=theme.title, anchor="lt")
            cursor += 38
        cursor += 4
        for index, (commands, details, row_height) in enumerate(rows):
            if index:
                draw.line((x, cursor, right - 28, cursor), fill=theme.edge)
            for column, lines, color in (
                (x, commands, theme.command),
                (detail_x, details, theme.text),
            ):
                for line_index, line in enumerate(lines):
                    draw.text(
                        (column, cursor + 31 + line_height * line_index),
                        line,
                        font=body_font,
                        fill=color,
                        anchor="ls",
                    )
            cursor += row_height
        y += card_height + 18

    output = BytesIO()
    image.save(output, "PNG", optimize=True)
    return output.getvalue()
