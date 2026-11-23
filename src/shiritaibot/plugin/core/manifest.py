"""Plugin manifests: v2 ``metadata.yaml`` + ``_conf_schema.json`` and legacy ``plugin.toml``.

Both formats are normalized to one listing entry whose ``settings_schema`` is standard JSON
Schema. v2 field options without a JSON Schema equivalent travel as ``x-`` keys, which the
WebUI form reads:

=====================  ==========================================================
``_conf_schema.json``  normalized
=====================  ==========================================================
description            title
hint                   description
obvious_hint           x-obvious-hint
secret                 writeOnly (encrypted into the Vault)
options / labels       enum / x-labels
subtitles              x-subtitles
slider                 minimum / maximum / x-step, x-widget = "slider"
invisible              x-invisible
editor_mode            x-widget = "editor", x-editor = {language, theme}
type = text            type = string, x-widget = "textarea"
type = dict            type = object, x-widget = "dict", x-template-schema
type = template_list   type = array, x-widget = "template_list", x-templates
_special               x-special (rendered as the base type)
=====================  ==========================================================
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from shiritaibot import __version__
from shiritaibot.i18n import DEFAULT_LOCALE, is_locale_map, pick, t

TYPES = {
    "string": "string",
    "text": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "object": "object",
    "list": "array",
    "dict": "object",
    "template_list": "array",
}
TYPE_DEFAULTS: dict[str, Any] = {
    "string": "",
    "text": "",
    "int": 0,
    "float": 0.0,
    "bool": False,
    "object": {},
    "list": [],
    "dict": {},
    "template_list": [],
}
# Plain JSON Schema constraints accepted as ShiritaiBot extensions of the v2 format.
PASSTHROUGH = ("minimum", "maximum", "pattern", "format", "minLength", "maxLength")
PLATFORMS = {"qq", "web"}


class ManifestError(ValueError):
    pass


def localized(target: dict, field: str, value: Any) -> Any:
    """Plain default text for ``field``; a per-language map (``{"zh-CN": ..., "en": ...}``)
    is also kept under ``x-l10n`` so the API can show the viewer's language."""
    if is_locale_map(value):
        target.setdefault("x-l10n", {})[field] = dict(value)
        value = pick(value, DEFAULT_LOCALE)
    return [str(item) for item in value] if isinstance(value, list) else str(value)


def convert_field(name: str, spec: Any, path: str = "") -> dict:
    where = f"{path}{name}"
    if not isinstance(spec, dict):
        raise ManifestError(t("plugins.manifest.objectRequired", where=where))
    kind = spec.get("type")
    if kind not in TYPES:
        raise ManifestError(t("plugins.manifest.typeUnsupported", where=where, kind=kind))
    result: dict[str, Any] = {"type": TYPES[kind]}
    if spec.get("description"):
        result["title"] = localized(result, "title", spec["description"])
    if spec.get("hint"):
        result["description"] = localized(result, "description", spec["hint"])
    if spec.get("obvious_hint"):
        result["x-obvious-hint"] = True
    if spec.get("invisible"):
        result["x-invisible"] = True
    if spec.get("_special"):
        result["x-special"] = str(spec["_special"])
    for key in PASSTHROUGH:
        if key in spec:
            result[key] = spec[key]
    if kind == "text":
        result["x-widget"] = "textarea"
    if spec.get("editor_mode"):
        result["x-widget"] = "editor"
        result["x-editor"] = {
            "language": spec.get("editor_language", "json"),
            "theme": spec.get("editor_theme", "vs-light"),
        }
    if isinstance(spec.get("slider"), dict) and kind in {"int", "float"}:
        slider = spec["slider"]
        result["x-widget"] = "slider"
        if "min" in slider:
            result["minimum"] = slider["min"]
        if "max" in slider:
            result["maximum"] = slider["max"]
        if "step" in slider:
            result["x-step"] = slider["step"]
    options = spec.get("options")
    if options is not None:
        if not isinstance(options, list) or not options:
            raise ManifestError(t("plugins.manifest.optionsRequired", where=where))
        target = result
        if kind == "list":
            result["items"] = {"type": "string"}
            target = result["items"]
        target["enum"] = list(options)
        for key, extension in (("labels", "x-labels"), ("subtitles", "x-subtitles")):
            value = spec.get(key)
            if isinstance(value, list) or (is_locale_map(value) and isinstance(pick(value), list)):
                result[extension] = localized(result, extension, value)
    if spec.get("secret"):
        if kind == "string":
            result["writeOnly"] = True
        elif kind == "list":
            result["items"] = {**result.get("items", {"type": "string"}), "writeOnly": True}
        else:
            raise ManifestError(t("plugins.manifest.secretTypes", where=where))
    if kind == "object":
        items = spec.get("items")
        if not isinstance(items, dict):
            raise ManifestError(t("plugins.manifest.objectItems", where=where))
        result["properties"] = {
            key: convert_field(key, value, where + ".") for key, value in items.items()
        }
    elif kind == "list" and "items" not in result:
        result["items"] = {"type": "string"}
    elif kind == "dict":
        result["x-widget"] = "dict"
        result["additionalProperties"] = True
        if isinstance(spec.get("template_schema"), dict):
            result["x-template-schema"] = {
                key: {**convert_field(key, value, where + "."), "x-name": value.get("name", key)}
                for key, value in spec["template_schema"].items()
            }
    elif kind == "template_list":
        templates = spec.get("templates")
        if not isinstance(templates, dict) or not templates:
            raise ManifestError(t("plugins.manifest.templatesRequired", where=where))
        result["x-widget"] = "template_list"
        result["x-templates"] = {}
        for key, template in templates.items():
            if not isinstance(template, dict) or not isinstance(template.get("items"), dict):
                raise ManifestError(t("plugins.manifest.templateItems", where=where, key=key))
            result["x-templates"][key] = {
                "name": str(template.get("name", key)),
                "hint": str(template.get("hint", "")),
                "display_item": str(template.get("display_item", "")),
                "hide_hint_in_list": bool(template.get("hide_hint_in_list", False)),
                "properties": {
                    name: convert_field(name, value, f"{where}.{key}.")
                    for name, value in template["items"].items()
                },
            }
        result["items"] = {
            "type": "object",
            "required": ["__template_key"],
            "properties": {"__template_key": {"type": "string", "enum": list(templates)}},
        }
    result["default"] = field_default(kind, spec, result)
    return result


def field_default(kind: str, spec: dict, converted: dict) -> Any:
    if "default" in spec:
        return spec["default"]
    if kind == "object":
        return {key: value["default"] for key, value in converted["properties"].items()}
    return TYPE_DEFAULTS[kind]


def convert_schema(conf: Any) -> dict:
    """``_conf_schema.json`` (a mapping of field name → spec) as a JSON Schema object."""
    if not isinstance(conf, dict):
        raise ManifestError(t("plugins.manifest.confSchemaObject"))
    schema = {
        "type": "object",
        "properties": {name: convert_field(name, spec) for name, spec in conf.items()},
    }
    Draft202012Validator.check_schema(schema)
    return schema


def schema_defaults(schema: dict) -> dict:
    return {
        key: value["default"]
        for key, value in schema.get("properties", {}).items()
        if isinstance(value, dict) and "default" in value
    }


def _version(text: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", text)
    return tuple(int(part) for part in parts[:3]) or (0,)


def version_matches(spec: str, current: str = __version__) -> bool:
    """Minimal PEP 440-style range check: comma-separated ``>=, <=, >, <, ==, !=`` clauses."""
    have = _version(current)
    for clause in filter(None, (part.strip() for part in spec.split(","))):
        match = re.fullmatch(r"(>=|<=|==|!=|>|<)\s*v?([0-9][0-9.]*)", clause)
        if not match:
            raise ManifestError(t("plugins.manifest.versionRange", clause=clause))
        operator, raw = match.groups()
        want = _version(raw)
        width = max(len(have), len(want))
        left, right = have + (0,) * (width - len(have)), want + (0,) * (width - len(want))
        ok = {
            ">=": left >= right,
            "<=": left <= right,
            ">": left > right,
            "<": left < right,
            "==": left == right,
            "!=": left != right,
        }[operator]
        if not ok:
            return False
    return True


def read_v2(directory: Path) -> dict:
    meta = yaml.safe_load((directory / "metadata.yaml").read_text(encoding="utf-8")) or {}
    if not isinstance(meta, dict):
        raise ManifestError(t("plugins.manifest.metadataMapping"))
    if not (directory / "main.py").is_file():
        raise ManifestError(t("plugins.manifest.noMain"))
    required = meta.get("shiritaibot_version")
    if required and not version_matches(str(required)):
        raise ManifestError(t("plugins.manifest.versionRequired", required=required, version=__version__))
    platforms = meta.get("support_platforms") or []
    if not isinstance(platforms, list) or not set(map(str, platforms)) <= PLATFORMS:
        raise ManifestError(t("plugins.manifest.platforms"))
    conf_path = directory / "_conf_schema.json"
    conf = json.loads(conf_path.read_text(encoding="utf-8")) if conf_path.is_file() else {}
    schema = convert_schema(conf)
    result: dict[str, Any] = {}
    result.update(
        {
            "api": 2,
            "name": localized(result, "name", meta.get("display_name") or meta.get("name") or directory.name),
            "description": localized(result, "description", meta.get("desc") or meta.get("short_desc") or ""),
            "short_description": localized(result, "short_description", meta.get("short_desc") or ""),
            "version": str(meta.get("version") or ""),
            "author": str(meta.get("author") or ""),
            "repo": str(meta.get("repo") or ""),
            "support_platforms": [str(item) for item in platforms],
            "settings_schema": schema,
            "defaults": schema_defaults(schema),
        }
    )
    return result


def read_v1(directory: Path) -> dict:
    meta = tomllib.loads((directory / "plugin.toml").read_text(encoding="utf-8"))
    if meta.get("api") != 1:
        raise ManifestError(t("plugins.manifest.apiVersion"))
    if not (directory / "main.py").is_file():
        raise ManifestError(t("plugins.manifest.noMain"))
    if not isinstance(meta.get("settings", {}), dict):
        raise ManifestError(t("plugins.manifest.settingsTable"))
    schema = meta.get("settings_schema", {"type": "object"})
    Draft202012Validator.check_schema(schema)
    if schema.get("type") != "object":
        raise ManifestError(t("plugins.manifest.settingsSchema"))
    return {
        "api": 1,
        "name": str(meta.get("name", directory.name)),
        "description": str(meta.get("description", "")),
        "short_description": "",
        "version": str(meta.get("version", "")),
        "author": str(meta.get("author", "")),
        "repo": str(meta.get("repo", "")),
        "support_platforms": [],
        "settings_schema": meta.get("settings_schema", {}),
        "defaults": {**schema_defaults(schema), **meta.get("settings", {})},
    }


MANIFESTS = ("metadata.yaml", "plugin.toml")  # v2 first: it wins when both exist.


def manifest_file(directory: Path) -> Path | None:
    for name in MANIFESTS:
        if (directory / name).is_file():
            return directory / name
    return None


def read_manifest(directory: Path) -> dict:
    """Listing entry for a plugin directory; manifest problems become ``error``, never raise."""
    entry = {
        "id": directory.name,
        "path": str(directory),
        "api": 2 if (directory / "metadata.yaml").is_file() else 1,
        "name": directory.name,
        "description": "",
        "short_description": "",
        "version": "",
        "author": "",
        "repo": "",
        "support_platforms": [],
        "settings_schema": {},
        "defaults": {},
        "error": "",
    }
    try:
        entry.update(read_v2(directory) if entry["api"] == 2 else read_v1(directory))
    except ManifestError as exc:
        entry["error"] = t("plugins.manifest.invalid", exc=exc)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        entry["error"] = t("plugins.manifest.invalidNamed", name=type(exc).__name__)
    except Exception:
        entry["error"] = t("plugins.manifest.schemaInvalid")
    return entry
