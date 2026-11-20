"""Small, bounded text snapshots of a Web chat's Shell working directory."""

from __future__ import annotations

import codecs
import difflib
import os
import stat
from pathlib import Path

from shiritaibot.core.text import redact_secrets


def snapshot(root: Path) -> dict[str, str | None] | None:
    files: dict[str, str | None] = {}
    budget = 4 * 1024 * 1024
    scanned = 0
    folders = [root]
    try:
        if root.is_symlink() or root.is_junction():
            return None
        while folders:
            folder = folders.pop()
            with os.scandir(folder) as entries:
                for entry in entries:
                    scanned += 1
                    if scanned > 1500:
                        return None
                    if (
                        entry.name.startswith(".")
                        and entry.name
                        not in {
                            ".gitignore",
                            ".gitattributes",
                            ".editorconfig",
                        }
                    ) or entry.name in {
                        "node_modules",
                        "__pycache__",
                        "target",
                        "dist",
                        "build",
                    }:
                        continue
                    info = entry.stat(follow_symlinks=False)
                    if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                        continue
                    path = Path(entry.path)
                    if stat.S_ISDIR(info.st_mode):
                        folders.append(path)
                        continue
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    name = path.relative_to(root).as_posix()
                    files[name] = None
                    if info.st_size > 96_000 or path.suffix.lower() in {
                        ".pem",
                        ".key",
                        ".p12",
                        ".token",
                    }:
                        continue
                    with path.open("rb") as stream:
                        raw = stream.read(96_001)
                    budget -= len(raw)
                    if budget < 0:
                        return None
                    if len(raw) > 96_000:
                        continue
                    if raw.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
                        encoding = "utf-32"
                    elif raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
                        encoding = "utf-16"
                    else:
                        encoding = "utf-8-sig"
                    try:
                        text = raw.decode(encoding)
                    except UnicodeDecodeError:
                        continue
                    if "\x00" not in text:
                        files[name] = text
        return files
    except OSError:
        return None


def file_diffs(before: dict, after: dict, secrets: tuple[str, ...]) -> list[dict]:
    changes = []
    budget = 32_000
    for name in sorted(before.keys() | after.keys()):
        old, new = before.get(name, ""), after.get(name, "")
        exists_in_both = name in before and name in after
        if old is None or new is None or (exists_in_both and old == new):
            continue
        old_lines = redact_secrets(old, secrets).splitlines(keepends=True)
        new_lines = redact_secrets(new, secrets).splitlines(keepends=True)
        patch = difflib.unified_diff(old_lines, new_lines, n=3, lineterm="")
        # Keep the hunk headers and preserve newline-only edits explicitly.
        lines = []
        for line in patch:
            if not lines and not line.startswith("@@"):
                continue
            lines.append(line.rstrip("\r\n"))
            if line[:1] in {"-", "+", " "} and not line.endswith(("\r", "\n")):
                lines.append("\\ No newline at end of file")
        if not lines and exists_in_both:
            continue
        text = "\n".join(lines)
        changes.append(
            {
                "path": redact_secrets(name, secrets),
                "added": sum(line.startswith("+") for line in lines),
                "removed": sum(line.startswith("-") for line in lines),
                "patch": text[:budget],
                "truncated": len(text) > budget,
            }
        )
        budget = max(0, budget - len(text))
    return changes
