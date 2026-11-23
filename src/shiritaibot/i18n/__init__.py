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
from functools import cache, lru_cache
from pathlib import Path
from typing import Any, Literal, get_args

Locale = Literal["zh-CN", "en"]
LOCALES: tuple[Locale, ...] = get_args(Locale)
DEFAULT_LOCALE: Locale = "zh-CN"
LOCALE_NAMES: dict[Locale, str] = {"zh-CN": "简体中文", "en": "English"}  # i18n: ok
FOLDER = Path(__file__).resolve().parent.parent / "locales"

# The locale of the current request or turn. Unset elsewhere (scheduled work, startup, logs),
# where the instance's global language applies.
current_locale: ContextVar[Locale | None] = ContextVar("shiritaibot_locale", default=None)
_instance: list[Locale] = [DEFAULT_LOCALE]


def set_instance_locale(locale: Locale) -> None:
    """The global bot language (``Settings.locale``); set whenever config loads or saves."""
    _instance[0] = locale


def instance_locale() -> Locale:
    return _instance[0]


def active_locale() -> Locale:
    """The request or turn locale if one is set, else the instance's global language."""
    return current_locale.get() or _instance[0]

_ALIASES: dict[str, Locale] = {
    "zh": "zh-CN",
    "cn": "zh-CN",
    "chinese": "zh-CN",
    "中文": "zh-CN",  # i18n: ok
    "简体中文": "zh-CN",  # i18n: ok
    "汉语": "zh-CN",  # i18n: ok
    "english": "en",
    "英文": "en",  # i18n: ok
    "英语": "en",  # i18n: ok
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


def t(key: str | Text, /, *, locale: Locale | None = None, **params: Any) -> str:
    """Render ``key`` in ``locale`` (default: the active locale) with ``{name}`` params.

    With a ``count`` param, ``key_one`` / ``key_other`` are preferred, as in i18next.
    Text that is not a catalog key is returned unchanged and is never formatted.
    """
    if isinstance(key, Text):  # stored lazy text, such as a tool description with params
        return key.render(locale)
    lang = locale or active_locale()
    text = None
    if "count" in params:
        text = lookup(f"{key}_{_plural(lang, params['count'])}", lang)
    if text is None:
        text = lookup(key, lang)
    if text is None:
        return key
    return fill(text, **params)


def join_list(items: Any) -> str:
    """Joins items with the active language's list separator (、 or a comma)."""
    return t("common.separator").join(str(item) for item in items)


class TranslatedDict(dict):
    """A dict whose values are catalog keys, translated when read (``d[k]``, ``d.get(k)``)."""

    def __getitem__(self, key: Any) -> str:
        return t(super().__getitem__(key))

    def get(self, key: Any, default: Any = None) -> Any:
        return t(super().__getitem__(key)) if key in self else default


class ListParam(list):
    """A list param that joins itself with the separator of the language it renders in."""

    def __format__(self, spec: str) -> str:
        return format(join_list(self), spec)


def fill(template: str, /, **params: Any) -> str:
    """Formats a catalog or prompt template. Templates always go through this, so literal
    braces are written ``{{ }}``; an unknown ``{name}`` is left as it is."""
    return template.format_map(_Params(params))


class _Params(dict):
    """Leaves unknown ``{name}`` placeholders as they are instead of raising."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


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

    def render(self, locale: Locale | None = None) -> str:
        """Renders in ``locale``, including any Text passed as a param."""
        params = {
            name: value.render(locale) if isinstance(value, Text) else value
            for name, value in self.params.items()
        }
        return t(self.key, locale=locale, **params)

    def __repr__(self) -> str:
        return f"Text({self.key!r})"


def is_locale_map(value: Any) -> bool:
    """``{"zh-CN": ..., "en": ...}``: text given per language, as plugin manifests allow."""
    return (
        isinstance(value, Mapping)
        and bool(value)
        and all(isinstance(key, str) and normalize(key) is not None for key in value)
    )


def pick(value: Any, locale: Locale | None = None) -> Any:
    """The active language's entry of a locale map (then zh-CN, then the first one);
    any other value is returned unchanged."""
    if not is_locale_map(value):
        return value
    entries = {normalize(key): item for key, item in value.items()}
    lang = locale or active_locale()
    return entries.get(lang, entries.get(DEFAULT_LOCALE, next(iter(entries.values()))))


def localize(value: Any) -> Any:
    """Copy of a tool schema, plugin manifest or other JSON-like value for the active locale:
    ``description`` / ``title`` keys are translated, locale maps are picked, and fields named
    in an ``x-l10n`` map are replaced by their per-language text. Plain text is unchanged."""
    if is_locale_map(value):
        return localize(pick(value))
    if isinstance(value, Mapping):
        overrides = value.get("x-l10n") if isinstance(value.get("x-l10n"), Mapping) else {}
        return {
            key: (
                localize(pick(overrides[key]))
                if key in overrides
                else t(item)
                if key in ("description", "title") and isinstance(item, str)
                else localize(item)
            )
            for key, item in value.items()
            if key != "x-l10n"
        }
    if isinstance(value, list):
        return [localize(item) for item in value]
    if isinstance(value, Text):
        return str(value)
    return value


def plugin_catalog(directory: Path, locale: Locale) -> dict[str, str]:
    """A plugin's own catalog, ``locales/<locale>.json`` next to its ``main.py``."""
    file = directory / "locales" / f"{locale}.json"
    try:
        stamp = file.stat().st_mtime_ns
    except OSError:
        return {}
    return _plugin_catalog(file, stamp)


@lru_cache(maxsize=256)
def _plugin_catalog(file: Path, stamp: int) -> dict[str, str]:
    result: dict[str, str] = {}
    data = json.loads(file.read_text(encoding="utf-8"))
    for key, value in data.items():
        if isinstance(value, Mapping):
            _flatten(value, key, result)
        else:
            result[key] = str(value)
    return result


def plugin_text(directory: Path | None, key: str, /, **params: Any) -> str:
    """Like ``t`` but from a plugin's catalog: active locale, then zh-CN, then the key."""
    if directory is None:
        return key
    lang = active_locale()
    text = None
    for locale in dict.fromkeys((lang, DEFAULT_LOCALE)):
        entries = plugin_catalog(directory, locale)
        if "count" in params:
            text = entries.get(f"{key}_{_plural(locale, params['count'])}")
        text = text or entries.get(key)
        if text is not None:
            break
    return key if text is None else fill(text, **params)
