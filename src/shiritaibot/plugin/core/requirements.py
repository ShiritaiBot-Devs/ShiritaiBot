"""Plugin dependency declaration checks.

Plugins may ship a requirements.txt next to main.py. The host does not install
these packages itself (that would touch the user environment), but it can detect
missing dependencies before importing the plugin and report them clearly,
instead of letting the import fail with a bare ModuleNotFoundError.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import re
from pathlib import Path

# Enough for a distribution name: stop at extras, version specifiers, and whitespace.
_REQ_SPLIT = re.compile(r"[\[<>=!~;\s]")


def declared_requirements(directory: Path) -> list[str]:
    """Distribution names declared in the plugin's requirements.txt."""
    path = directory / "requirements.txt"
    if not path.is_file():
        return []
    names: list[str] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue  # Blank lines, comments, and options such as -r/-e.
        name = _REQ_SPLIT.split(line, 1)[0].strip()
        if not name:
            continue
        key = name.lower()
        if key not in seen:
            seen.add(key)
            names.append(name)
    return names


def missing_requirements(directory: Path) -> list[str]:
    """Declared requirements that are not installed in the current environment.

    Detection goes through importlib.metadata.packages_distributions() (mapping
    of top-level module name to distribution names), so packages whose import
    name differs from the distribution name (python-docx -> docx, pyyaml ->
    yaml) are not false positives. find_spec on a few normalized candidates is
    used as a fallback for environments where metadata is incomplete.
    """
    installed: set[str] = set()
    for module, dists in importlib.metadata.packages_distributions().items():
        for dist in dists:
            installed.add(dist.lower())
    missing: list[str] = []
    for name in declared_requirements(directory):
        if name.lower() in installed:
            continue
        candidates = (name, name.replace("-", "_"))
        if any(importlib.util.find_spec(candidate) for candidate in candidates):
            continue
        missing.append(name)
    return missing
