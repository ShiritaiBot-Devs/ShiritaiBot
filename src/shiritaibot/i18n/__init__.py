"""Message catalogs and the active locale (docs/I18N.md).

Catalogs live in ``shiritaibot/locales/<locale>/<namespace>.json``. A key is the namespace
followed by the dotted path inside the file, so ``{"lang": {"current": "..."}}`` in
``commands.json`` is ``commands.lang.current``. ``zh-CN`` is the reference catalog: a key
missing from another locale falls back to it, and text that is not a key at all is returned
unchanged, so untranslated literals keep working during the migration.

The active locale is a context variable. The Web API sets it per request and the agent
runtime per turn, so code that renders text never has to pass a locale around.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cache
from pathlib import Path
from typing import Any, Literal, get_args

Locale = Literal["zh-CN", "en"]
LOCALES: tuple[Locale, ...] = get_args(Locale)
DEFAULT_LOCALE: Locale = "zh-CN"
LOCALE_NAMES: dict[Locale, str] = {"zh-CN": "简体中文", "en": "English"}
FOLDER = Path(__file__).resolve().parent.parent / "locales"

current_locale: ContextVar[Locale] = ContextVar("shiritaibot_locale", default=DEFAULT_LOCALE)

_ALIASES: dict[str, Locale] = {
    "zh": "zh-CN",
    "cn": "zh-CN",
    "chinese": "zh-CN",
    "中文": "zh-CN",
    "简体中文": "zh-CN",
    "汉语": "zh-CN",
    "english": "en",
    "英文": "en",
    "英语": "en",
}


def normalize(value: str | None) -> Locale | None:
    """A supported locale for a tag or alias ("en-US", "zh", "中文"), or None."""
    if not value:
        return None
    value = value.strip()
    if value in LOCALES:
        return value  # type: ignore[return-value]
    folded = value.casefold().replace("_", "-")
    if folded in _ALIASES:
        return _ALIASES[folded]
    for locale in LOCALES:
        if folded == locale.casefold():
            return locale
    primary = folded.split("-", 1)[0]
    if primary == "zh":
        # Only Simplified Chinese ships; it is closer for zh-TW readers than English.
        return "zh-CN"
    return next((locale for locale in LOCALES if locale.casefold() == primary), None)


def _flatten(data: Mapping[str, Any], prefix: str, into: dict[str, str]) -> None:
    for key, value in data.items():
        path = f"{prefix}.{key}"
        if isinstance(value, Mapping):
            _flatten(value, path, into)
        else:
            into[path] = str(value)


@cache
def catalog(locale: Locale) -> dict[str, str]:
    result: dict[str, str] = {}
    for file in sorted((FOLDER / locale).glob("*.json")):
        _flatten(json.loads(file.read_text(encoding="utf-8")), file.stem, result)
    return result


def _plural(locale: Locale, count: Any) -> str:
    if locale == "en":
        return "one" if count == 1 else "other"
    return "other"


def lookup(key: str, locale: Locale) -> str | None:
    return catalog(locale).get(key) or catalog(DEFAULT_LOCALE).get(key)


def t(key: str, /, *, locale: Locale | None = None, **params: Any) -> str:
    """Render ``key`` in ``locale`` (default: the active locale) with ``{name}`` params.

    With a ``count`` param, ``key_one`` / ``key_other`` are preferred, as in i18next.
    Text that is not a catalog key is returned unchanged and is never formatted.
    """
    lang = locale or current_locale.get()
    text = None
    if "count" in params:
        text = lookup(f"{key}_{_plural(lang, params['count'])}", lang)
    if text is None:
        text = lookup(key, lang)
    if text is None:
        return key
    return text.format_map(params) if params else text


@contextmanager
def use_locale(locale: Locale) -> Iterator[None]:
    token = current_locale.set(locale)
    try:
        yield
    finally:
        current_locale.reset(token)


class Text:
    """Text rendered in whichever locale is active when it is shown, not when created."""

    __slots__ = ("key", "params")

    def __init__(self, key: str, /, **params: Any):
        self.key, self.params = key, params

    def __str__(self) -> str:
        return t(self.key, **self.params)

    def __repr__(self) -> str:
        return f"Text({self.key!r})"
