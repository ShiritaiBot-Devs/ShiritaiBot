from __future__ import annotations

import re
import tomllib
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import tomli_w
from cryptography.fernet import InvalidToken
from pydantic import ValidationError

from shiritaibot.config import Settings, settings_toml_data
from shiritaibot.core import PublicError, uid
from shiritaibot.i18n import join_list

if TYPE_CHECKING:
    from shiritaibot.auth import Vault

CONFIG_FILENAME = "shiritaibot-config.toml"
MAX_CONFIG_BYTES = 16 * 1024 * 1024
SECRET_REF = re.compile(r"secret:([A-Za-z0-9_-]{1,80})")


def _secret_fields(data: dict | list) -> Iterator[tuple[Any, Any, str]]:
    items = data.items() if isinstance(data, dict) else enumerate(data)
    for key, value in items:
        if isinstance(value, str) and (match := SECRET_REF.fullmatch(value)):
            yield data, key, match[1]
        elif isinstance(value, (dict, list)):
            yield from _secret_fields(value)


async def export_settings(settings: Settings, vault: Vault | None) -> bytes:
    names = {name for _, _, name in _secret_fields(settings.model_dump())}
    secrets = {}
    for name in sorted(names):
        if vault is None:
            break
        try:
            value = await vault.resolve("secret:" + name)
        except InvalidToken:
            raise PublicError("config.transferCredentialUnreadable") from None
        if value:
            secrets[name] = value
    document = {
        "shiritaibot_config_version": 1,
        "settings": settings_toml_data(settings),
        "secrets": secrets,
    }
    content = tomli_w.dumps(document).encode()
    if len(content) > MAX_CONFIG_BYTES:
        raise PublicError("config.transferTooLarge")
    return content


def parse_settings(content: bytes) -> tuple[Settings, dict[str, str]]:
    if len(content) > MAX_CONFIG_BYTES:
        raise PublicError("config.transferTooLarge")
    try:
        document = tomllib.loads(content.decode("utf-8-sig"))
    except (UnicodeError, tomllib.TOMLDecodeError, RecursionError):
        raise PublicError("config.transferToml") from None

    secrets: dict[str, str] = {}
    if "shiritaibot_config_version" in document:
        version = document.get("shiritaibot_config_version")
        if type(version) is not int or version != 1:
            raise PublicError("config.transferVersion")
        data = document.get("settings")
        secrets = document.get("secrets", {})
        if not isinstance(data, dict) or not isinstance(secrets, dict) or not all(
            isinstance(value, str) for value in secrets.values()
        ):
            raise PublicError("config.transferInvalid")
    else:
        data = document
    if not Settings.model_fields.keys() & data.keys():
        raise PublicError("config.transferInvalid")
    try:
        return Settings.model_validate(data), secrets
    except ValidationError as error:
        fields = join_list(
            ".".join(map(str, row["loc"])) for row in error.errors(include_input=False)
        )
        raise PublicError("config.transferFields", fields=fields) from None


async def restore_secrets(settings: Settings, secrets: dict[str, str], vault: Vault) -> Settings:
    if not secrets:
        return settings
    data = settings.model_dump()
    replacements: dict[str, str] = {}
    for container, key, name in _secret_fields(data):
        if name not in secrets:
            continue
        if name not in replacements:
            replacements[name] = "transfer-" + uid()
        container[key] = "secret:" + replacements[name]
    restored = Settings.model_validate(data)
    for original, target in replacements.items():
        await vault.set(target, secrets[original])
    return restored
