"""Model-facing text, one module per locale (docs/I18N.md).

``zh.py`` is the reference: its constants must stay byte-identical to the prompts the
Chinese build has always sent, so existing behaviour and prompt caches are unaffected.
``en.py`` defines the same names; a name missing there falls back to ``zh.py``.
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType

from shiritaibot.i18n import DEFAULT_LOCALE, Locale, current_locale

_MODULES: dict[Locale, str] = {"zh-CN": "zh", "en": "en"}


def _module(locale: Locale) -> ModuleType:
    return import_module(f"{__name__}.{_MODULES[locale]}")


def prompt(name: str, locale: Locale | None = None) -> str:
    """The prompt constant ``name`` for ``locale`` (default: the active locale)."""
    value = getattr(_module(locale or current_locale.get()), name, None)
    if value is None:
        value = getattr(_module(DEFAULT_LOCALE), name)
    return value
