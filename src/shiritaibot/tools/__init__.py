from __future__ import annotations

import asyncio
import json
from itertools import islice

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from shiritaibot.core import (
    Context,
    InteractionRequired,
    PublicError,
    Tool,
    canonical,
    record_usage_for,
    uid,
)
from shiritaibot.core.logs import describe_session, get_logger
from shiritaibot.core.text import redact_secrets
from shiritaibot.i18n import Text, t

log = get_logger("tools.log.source")


class ToolRegistry:
    def __init__(self, permissions, store):
        self.permissions, self.store = permissions, store
        self.tools: dict[str, Tool] = {}
        # Credential values resolved from the vault; tool output is scrubbed of them.
        self.secrets: tuple[str, ...] = ()

    def add(self, tool: Tool) -> None:
        self.validate(tool)
        if tool.name in self.tools:
            raise ValueError(f"duplicate tool: {tool.name}")
        self.tools[tool.name] = tool

    @staticmethod
    def validate(tool: Tool) -> None:
        if tool.risk not in {"safe", "normal", "sensitive", "dangerous"}:
            raise ValueError(t("tools.riskInvalid"))
        Draft202012Validator.check_schema(tool.schema)

    def remove_plugin(self, plugin: str) -> None:
        self.tools = {k: v for k, v in self.tools.items() if v.plugin != plugin}

    def specs(self, ctx: Context) -> list[dict]:
        return [
            tool.spec()
            for _, tool in sorted(self.tools.items())
            if self.permissions.decision(ctx, tool) != "deny"
        ]

    async def execute(self, ctx: Context, name: str, arguments: str | dict) -> str:
        try:
            tool = self.tools.get(name)
            if not tool:
                raise PublicError("tools.missing")
            args = json.loads(arguments) if isinstance(arguments, str) else arguments
            errors = Draft202012Validator(tool.schema).iter_errors(args)
            details = []
            for error in islice(errors, 3):
                path = "arguments" + error.json_path.removeprefix("$")
                detail = f"{path} [{error.validator or 'false schema'}]: {error.message}"
                detail = redact_secrets(detail, self.secrets)
                details.append(detail if len(detail) <= 512 else detail[:511] + "…")
            if details:
                return canonical({"error": t("tools.schemaInvalid") + "\n".join(details)})
            with record_usage_for(ctx.session):
                await self.permissions.check(ctx, tool, args)
                async with asyncio.timeout(tool.timeout):
                    try:
                        value = await tool.handler(ctx, args)
                    except InteractionRequired as request:
                        reason = redact_secrets(request.reason, self.secrets)
                        if not (ctx.background or ctx.goal_id or ctx.subagent_run):
                            raise PublicError(reason) from None
                        raise InteractionRequired(
                            f"{tool.plugin}:{request.key}", reason, request.ttl
                        ) from None
            result = redact_secrets(canonical(value), self.secrets)
            if len(result) > 16000:
                key = uid()
                await self.store.put(
                    "artifact",
                    key,
                    {"content": result, "memory_profile": ctx.memory_profile},
                    ctx.session,
                )
                result = canonical({"preview": result[:12000], "artifact": key, "truncated": True})
            await self.store.activity("tool.completed", name, ctx.session)
            return result
        except InteractionRequired:
            raise
        except (ValidationError, json.JSONDecodeError):
            return canonical({"error": t("tools.schemaInvalidShort")})
        except PublicError as exc:
            return canonical({"error": str(exc)})
        except TimeoutError:
            return canonical({"error": t("tools.timeout")})
        except Exception as exc:
            log.error(
                Text("tools.log.raised", name=name, name2=type(exc).__name__),
                describe_session(ctx.session),
                exc=exc,
            )
            await self.store.activity("tool.failed", f"{name}: {type(exc).__name__}", ctx.session)
            return canonical({"error": t("tools.failed", name=type(exc).__name__)})


def schema(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


TEXT = {"type": "string", "minLength": 1, "maxLength": 16000}
