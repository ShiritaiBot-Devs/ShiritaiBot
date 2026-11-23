#!/usr/bin/env python3
"""i18n guard for CI (docs/I18N.md).

1. Hardcoded Chinese: counts lines with CJK text in Python string literals (docstrings and
   comments excluded) and in TypeScript code (comment lines excluded), per file. A file may
   not exceed its count in ``scripts/i18n-baseline.json``; new files start at zero. As pages
   are migrated, run ``--update`` to lower the baseline so the gain is locked in.
2. Catalogs: every locale has the same keys and the same ``{placeholders}`` as zh-CN, for
   both ``src/shiritaibot/locales`` and ``web/src/locales``. Plural forms (``key_one``,
   ``key_other``) count as one key, since each language has its own set of forms.

Usage: python3 scripts/check_i18n.py [--update]
"""

from __future__ import annotations

import ast
import io
import json
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "scripts" / "i18n-baseline.json"
CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
PLACEHOLDER = re.compile(r"\{(\w+)\}")
PLURAL = re.compile(r"_(zero|one|two|few|many|other)$")
# Where Chinese text belongs: catalogs, per-locale prompts, and tests.
ALLOWED = (
    "src/shiritaibot/locales/",
    "src/shiritaibot/prompts/",
    "web/src/locales/",
    "tests/",
)
SCANNED = (("src", "*.py"), ("web/src", "*.ts"), ("web/src", "*.tsx"))


def docstring_lines(tree: ast.AST) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                end = body[0].end_lineno or body[0].lineno
                lines.update(range(body[0].lineno, end + 1))
    return lines


# Text that must stay Chinese (language data such as word lists, bilingual control words,
# internal IDs) is marked so the count stays honest: "i18n: ok" on the line, or
# "i18n-check: language-data" anywhere in a file made of such data.
LINE_OK = "i18n: ok"
FILE_OK = "i18n-check: language-data"


def python_lines(text: str) -> int:
    if FILE_OK in text:
        return 0
    marked = {n for n, line in enumerate(text.splitlines(), 1) if LINE_OK in line}
    skip = docstring_lines(ast.parse(text)) | marked
    found: set[int] = set()
    string_tokens = {tokenize.STRING}
    if hasattr(tokenize, "FSTRING_MIDDLE"):
        string_tokens.add(tokenize.FSTRING_MIDDLE)
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type in string_tokens and token.start[0] not in skip:
            for offset, line in enumerate(token.string.splitlines() or [""]):
                if CJK.search(line):
                    found.add(token.start[0] + offset)
    return len(found)


def typescript_lines(text: str) -> int:
    count = 0
    for line in text.splitlines():
        code = line.strip()
        if code.startswith(("//", "/*", "*", "{/*")) or LINE_OK in line:
            continue
        if CJK.search(code.split(" // ", 1)[0]):
            count += 1
    return count


def scan() -> dict[str, int]:
    counts: dict[str, int] = {}
    for folder, pattern in SCANNED:
        for path in sorted((ROOT / folder).rglob(pattern)):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith(ALLOWED) or "/node_modules/" in rel or rel.endswith(".d.ts"):
                continue
            text = path.read_text(encoding="utf-8")
            count = python_lines(text) if path.suffix == ".py" else typescript_lines(text)
            if count:
                counts[rel] = count
    return counts


def flatten(data: dict, prefix: str = ""):
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            yield from flatten(value, path)
        else:
            yield path, str(value)


def plural_merged(entries: dict[str, str]) -> dict[str, str]:
    """Folds ``key_one`` / ``key_other`` … into ``key``: languages need different plural
    forms (Chinese has one), so parity is checked per key, with placeholders combined."""
    merged: dict[str, str] = {}
    for key, value in entries.items():
        base = PLURAL.sub("", key)
        merged[base] = merged.get(base, "") + " " + value
    return merged


def catalog_problems() -> list[str]:
    problems = []
    for base in (ROOT / "src/shiritaibot/locales", ROOT / "web/src/locales"):
        locales = {
            folder.name: plural_merged(
                {
                    f"{file.stem}:{key}": value
                    for file in sorted(folder.glob("*.json"))
                    for key, value in flatten(json.loads(file.read_text(encoding="utf-8")))
                }
            )
            for folder in sorted(base.iterdir())
            if folder.is_dir()
        }
        reference = locales.get("zh-CN", {})
        for locale, entries in locales.items():
            where = f"{base.relative_to(ROOT)}/{locale}"
            for key in sorted(reference.keys() - entries.keys()):
                problems.append(f"{where}: missing {key}")
            for key in sorted(entries.keys() - reference.keys()):
                problems.append(f"{where}: {key} is not in zh-CN")
            for key in sorted(entries.keys() & reference.keys()):
                ours = set(PLACEHOLDER.findall(entries[key]))
                theirs = set(PLACEHOLDER.findall(reference[key]))
                if ours != theirs:
                    problems.append(f"{where}: {key} placeholders {ours} != zh-CN {theirs}")
    return problems


def main() -> int:
    counts = scan()
    if "--update" in sys.argv[1:]:
        BASELINE.write_text(
            json.dumps(counts, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"Baseline updated: {sum(counts.values())} lines in {len(counts)} files.")
        return 0
    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    problems = catalog_problems()
    for rel, count in sorted(counts.items()):
        allowed = baseline.get(rel, 0)
        if count > allowed:
            problems.append(
                f"{rel}: {count} lines with hardcoded Chinese (baseline {allowed}). "
                "Move the text to a catalog or prompt module (docs/I18N.md)."
            )
    lowered = [rel for rel, allowed in baseline.items() if counts.get(rel, 0) < allowed]
    for problem in problems:
        print(problem)
    if lowered and not problems:
        print(f"{len(lowered)} files have less Chinese text than the baseline; run --update.")
    print(
        f"{sum(counts.values())} lines of hardcoded Chinese left in {len(counts)} files."
        if not problems
        else f"{len(problems)} problems found."
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
