from __future__ import annotations

import asyncio
import base64
from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING

from shiritaibot.agent.help_menu import HelpSection, render_help_menu
from shiritaibot.auth.menu import RISK_LABELS
from shiritaibot.config import SessionPolicy, preset_risks
from shiritaibot.core import PublicError, uid
from shiritaibot.core.logs import get_logger
from shiritaibot.i18n import LOCALE_NAMES, LOCALES, Text, normalize, t, use_locale

if TYPE_CHECKING:
    from shiritaibot.plugin.core.hooks import Handler

COMMANDS = {
    "help": "commands.help.description",
    "status": "commands.status.description",
    "models": "commands.models.description",
    "model": "commands.model.description",
    "personas": "commands.personas.description",
    "persona": "commands.persona.description",
    # Values may be catalog keys; visible_builtin_commands renders them (docs/I18N.md).
    "lang": "commands.lang.description",
    "new": "commands.new.description",
    "clear": "commands.clear.description",
    "stop": "commands.stop.description",
    "tools": "commands.tools.description",
    "permissions": "commands.permissions.description",
    "mode": "commands.mode.description",
    "tasks": "commands.tasks.description",
    "memory": "commands.memory.description",
    "approve": "commands.approve.description",
    "deny": "commands.deny.description",
    "grants": "commands.grants.description",
}


BUILTIN_COMMANDS = frozenset({*COMMANDS, "cancel", "reset"})
PRIVATE_COMMANDS = {"tasks", "memory", "approve", "deny", "grants"}
OWNER_COMMANDS = {"new", "clear"}
COMMAND_TOOLS = {"memory": "memory_search", "tasks": "subagent_list"}
COMMAND_GROUPS = {
    "builtin:session": ("commands.sections.session", ("help", "status", "new", "clear", "stop", "mode", "lang")),
    "builtin:models": ("commands.sections.models", ("models", "model", "personas", "persona")),
    "builtin:tools": ("commands.sections.tools", ("tools", "permissions", "approve", "deny", "grants")),
    "builtin:memory": ("commands.sections.memory", ("memory", "tasks")),
}
log = get_logger("commands.log.source")


def builtin_slash(text: str) -> bool:
    if not text.startswith("/"):
        return False
    parts = text[1:].split(maxsplit=1)
    return bool(parts) and parts[0].lower() in BUILTIN_COMMANDS


def visible_builtin_commands(ctx, registry) -> dict[str, str]:
    private = ctx.owner and not ctx.group and not ctx.background
    visible = {}
    for name, description in COMMANDS.items():
        if name in PRIVATE_COMMANDS and not private:
            continue
        if name in OWNER_COMMANDS and (not ctx.owner or ctx.background):
            continue
        if tool_name := COMMAND_TOOLS.get(name):
            tool = registry.tools.get(tool_name)
            if tool is None or registry.permissions.decision(ctx, tool) == "deny":
                continue
        visible[name] = t(description)
    return visible


def help_section_titles(handlers: Iterable[Handler]) -> dict[str, str]:
    return {
        **{key: t(title) for key, (title, _) in COMMAND_GROUPS.items()},
        **{f"plugin:{handler.plugin}": t("commands.sections.plugin", plugin=handler.plugin) for handler in handlers},
    }


def help_sections(runtime, ctx) -> list[HelpSection]:
    from shiritaibot.plugin.core.hooks import usage

    commands = visible_builtin_commands(ctx, runtime.registry)
    entries = {
        key: [(f"/{name}", commands[name]) for name in names if name in commands]
        for key, (_, names) in COMMAND_GROUPS.items()
    }
    hooks = getattr(runtime, "hooks", None)
    handlers = hooks.commands_for_context(ctx) if hooks is not None else []
    for handler in handlers:
        entries.setdefault(f"plugin:{handler.plugin}", []).append((usage(handler), t(handler.description)))
    titles = help_section_titles(handlers)
    configured = {section.id: section.visible for section in runtime.config.value.help_menu_sections}
    return [
        (titles[key], entries[key])
        for key in dict.fromkeys((*configured, *titles))
        if configured.get(key, True) and entries.get(key)
    ]


async def command(runtime, ctx, text):
    parts = text.strip()[1:].split(maxsplit=1)
    name, argument = parts[0] if parts else "help", parts[1] if len(parts) > 1 else ""
    name, argument = name.lower(), argument.strip()
    name = {"cancel": "stop", "reset": "clear"}.get(name, name)
    cfg = runtime.config.value
    private = ctx.owner and not ctx.group and not ctx.background
    actor = runtime.actors.get(ctx.session)
    image = None
    try:
        if name in PRIVATE_COMMANDS and not private:
            raise PublicError("commands.ownerPrivateOnly")
        capability = COMMAND_TOOLS.get(name)
        if capability:
            await runtime.registry.permissions.check(ctx, runtime.registry.tools[capability], {})
        if name == "lang" and argument and (not ctx.owner or ctx.background):
            raise PublicError("commands.lang.owner_only")
        if name in OWNER_COMMANDS or (name in {"model", "persona"} and argument):
            if not ctx.owner or ctx.background:
                raise PublicError("commands.ownerOnlyReset")
        if name == "help":
            sections = help_sections(runtime, ctx)
            body = "\n\n".join(
                title + "\n" + "\n".join(
                    f"{usage} — {description}" if description else usage
                    for usage, description in entries
                )
                for title, entries in sections
            )
            answer = t("commands.help.title", assistant_name=cfg.assistant_name, body=body or t("commands.help.empty"))
            if sections and cfg.help_menu_style != "text" and not ctx.background:
                try:
                    image = await asyncio.to_thread(
                        render_help_menu, cfg.assistant_name, sections, cfg.help_menu_style
                    )
                except (OSError, ValueError) as exc:
                    log.warning(Text("commands.log.helpImageFailed", exc=exc))
        elif name in {"new", "clear"}:
            if actor:
                actor.task.cancel()
                await asyncio.gather(actor.task, return_exceptions=True)
                if runtime.actors.get(ctx.session) is actor:
                    runtime.actors.pop(ctx.session, None)
            await runtime.store.reset_history(ctx.session, memory_profile=ctx.memory_profile)
            answer = t("commands.new.done")
        elif name == "stop":
            if actor:
                await actor.submit(ctx, "/stop")
            answer = t("commands.stop.sent")
        elif name == "models":
            providers = {p.id for p in cfg.providers if p.enabled}
            answer = t("commands.models.title") + (
                "\n".join(
                    t("commands.models.item", display_name=m.display_name, id=m.id)
                    for m in cfg.models
                    if m.enabled and m.provider in providers
                )
                or t("commands.models.none")
            )
        elif name == "personas":
            answer = t("commands.personas.title") + (
                "\n".join(f"{p.id} · {p.name}" for p in cfg.personas if p.enabled)
                or t("commands.personas.none")
            )
        elif name == "persona":
            if argument:
                if argument != "default" and not any(
                    p.id == argument and p.enabled for p in cfg.personas
                ):
                    raise PublicError("commands.persona.missing")
                await runtime.store.put(
                    "session_persona",
                    ctx.session,
                    {"persona": "" if argument == "default" else argument},
                )
            selected = await runtime.store.get("session_persona", ctx.session)
            persona = (
                cfg.selected_persona(selected.get("persona", "") if selected else "")
                or cfg.selected_persona()
            )
            answer = t("commands.persona.current", value=persona.id + ' · ' + persona.name if persona else t("commands.persona.base"))
            if argument:
                answer += t("commands.persona.nextTurn")
        elif name in {"model", "status"}:
            if name == "model" and argument:
                providers = {p.id for p in cfg.providers if p.enabled}
                if argument != "default" and not any(
                    m.id == argument and m.enabled and m.provider in providers for m in cfg.models
                ):
                    raise PublicError("commands.model.missing")
                await runtime.store.put(
                    "session_model",
                    ctx.session,
                    {"model": "" if argument == "default" else argument},
                )
            selected = await runtime.store.get("session_model", ctx.session)
            model = selected.get("model") if selected else None
            model_names = {m.id: m.display_name for m in cfg.models}

            def display(ref):
                return model_names.get(ref, t("commands.model.unknownProvider", ref=ref))

            routes = cfg.with_session_routes(ctx.session).routes
            if model:
                models = display(model)
            elif ctx.mode != "agent":
                models = " → ".join(map(display, routes["chat"])) or t("commands.status.notConfigured")
            else:
                models = " → ".join(map(display, routes["agent"])) or t("commands.status.notConfigured")
                if cfg.agent_split and routes["agent_chat"] and routes["router"]:
                    models = (
                        f"Work {models}；Chat "
                        + " → ".join(map(display, routes["agent_chat"]))
                        + t("commands.status.autoSplit")
                    )
            answer = t("commands.status.summary", session=ctx.session, mode=ctx.mode, models=models)
            if argument:
                answer += t("commands.model.nextTurn")
            if name == "status":
                selected = await runtime.store.get("session_persona", ctx.session)
                persona = (
                    cfg.selected_persona(selected.get("persona", "") if selected else "")
                    or cfg.selected_persona()
                )
                answer += t("commands.status.persona", value=persona.name if persona else t("commands.persona.base"))
                locale, _ = await runtime.session_locale(ctx.session)
                answer += "\n" + t("commands.lang.status", name=LOCALE_NAMES[locale])
                answer += t("commands.status.state", state=t("commands.status.busy") if actor and actor.current else t("commands.status.idle"), queued=actor.queue.qsize() if actor else 0)
        elif name == "lang":
            options = ", ".join(f"{code} ({LOCALE_NAMES[code]})" for code in LOCALES)
            if argument:
                # Stored apart from SessionPolicy: a policy entry replaces the session's
                # defaults wholesale, and /lang must not touch mode or tool permissions.
                if argument.casefold() == "default":
                    await runtime.store.delete("session_locale", ctx.session)
                elif chosen := normalize(argument):
                    await runtime.store.put("session_locale", ctx.session, {"locale": chosen})
                else:
                    raise PublicError("commands.lang.unknown", value=argument[:40], options=options)
            locale, forced = await runtime.session_locale(ctx.session)
            # Confirm in the language just chosen.
            with use_locale(locale):
                answer = t(
                    "commands.lang.current_session" if forced else "commands.lang.current_default",
                    name=LOCALE_NAMES[locale],
                )
                if argument:
                    answer += "\n" + t("commands.lang.saved")
                answer += "\n" + t("commands.lang.usage", options=options)
        elif name in {"tools", "permissions", "mode"}:
            permissions = runtime.registry.permissions
            profile = permissions.session_policy(ctx).model_copy(deep=True)
            if argument:
                if not ctx.owner or ctx.background:
                    raise PublicError("commands.tools.ownerOnly")
                args = argument.split()
                if name == "mode":
                    args = ["mode", *args]
                if args == ["reset"]:
                    profile = await permissions.set_session(ctx, lambda _: None)
                else:
                    usage = (
                        t("commands.tools.usage")
                    )
                    if len(args) not in {2, 3}:
                        raise PublicError(usage)

                    def update(current: SessionPolicy) -> SessionPolicy:
                        data = current.model_dump()
                        if len(args) == 3 and args[0] == "risk":
                            level, perm = args[1], args[2]
                            if level in {"safe", "normal", "sensitive", "dangerous"} and perm in {
                                "deny",
                                "manual",
                                "auto",
                                "allow",
                            }:
                                data["risks"][level] = perm
                            else:
                                raise PublicError(usage)
                        elif len(args) == 2:
                            target, value = args
                            if target == "mode" and value in {"agent", "chat"}:
                                data["mode"] = value
                            elif target == "preset" and (mapped := preset_risks(value)) is not None:
                                data.update(risks=mapped, tools={})
                            elif target in runtime.registry.tools and value in {
                                "deny",
                                "manual",
                                "auto",
                                "allow",
                                "inherit",
                            }:
                                if value == "inherit":
                                    data["tools"].pop(target, None)
                                else:
                                    data["tools"][target] = value
                            else:
                                raise PublicError(
                                    "commands.tools.invalid"
                                )
                        else:
                            raise PublicError(usage)
                        return SessionPolicy.model_validate(data)

                    profile = await permissions.set_session(ctx, update)
            labels = {
                "deny": t("commands.permission.deny"),
                "manual": t("commands.permission.manual"),
                "auto": t("commands.permission.auto"),
                "allow": t("commands.permission.allow"),
            }
            preset_name = profile.matched_preset() or "custom"
            risk_summary = " / ".join(
                f"{title}:{labels[getattr(profile.risks, key)]}"
                for key, title in (
                    ("safe", t("commands.risk.safe")),
                    ("normal", t("commands.risk.normal")),
                    ("sensitive", t("commands.risk.sensitive")),
                    ("dangerous", t("commands.risk.dangerous")),
                )
            )
            answer = (
                t("commands.tools.summary", session=ctx.session, mode=profile.mode, preset_name=preset_name, risk_summary=risk_summary)
            )
            if name != "mode":
                answer += "\n" + "\n".join(
                    f"{tool.name} · {labels[permissions.configured(ctx, tool)]}"
                    + (t("commands.tools.unavailable") if permissions.decision(ctx, tool) == "deny" else "")
                    for tool in sorted(runtime.registry.tools.values(), key=lambda tool: tool.name)
                )
            if profile.mode == "chat":
                answer += t("commands.tools.chatHint")
            if argument:
                answer += t("commands.tools.saved")
        elif name == "tasks":
            rows = await runtime.store.list("subagent_run", limit=20)
            answer = t("commands.tasks.title") + (
                "\n".join(f"{row['id']} · {row.get('status', '')}" for row in rows) or t("commands.tasks.none")
            )
        elif name == "memory":
            memory = runtime.memory_for(ctx.memory_profile)
            rows = (await memory.search(query=argument, limit=10))["results"]
            answer = t("commands.memory.title") + (
                "\n".join(row["text"][:500] for row in rows) or t("commands.memory.none")
            )
        elif name == "grants":
            permissions = runtime.registry.permissions
            # The subcommand word works in either language.
            word = next((w for w in ("撤销", "revoke") if argument.startswith(w)), "")  # i18n: ok
            if word:
                parts = argument.removeprefix(word).split()
                if not parts:
                    raise PublicError("commands.grants.usage")
                answer = await permissions.revoke_grant(ctx, parts[0], parts[1] if len(parts) > 1 else "")
            else:
                rows = await permissions.grant_rows(ctx)
                answer = (
                    t("commands.grants.none")
                    if not rows
                    else t("commands.grants.title")
                    + "\n".join(
                        f"{row.get('scope', '')} · {row['tool']} · "
                        f"{RISK_LABELS.get(row.get('risk'), row.get('risk', ''))} · "
                        f"{datetime.fromtimestamp(row['created']):%Y-%m-%d} — "
                        f"{row.get('reason', '')}"
                        for row in rows
                    )
                    + t("commands.grants.revokeHint")
                )
        elif name in {"approve", "deny"}:
            if not argument:
                raise PublicError("commands.approve.usage", name=name)
            await runtime.registry.permissions.resolve(
                argument, name == "approve", approver=ctx
            )
            answer = t("commands.approve.allowed") if name == "approve" else t("commands.approve.denied")
        else:
            answer = t("commands.unknown")
    except PublicError as exc:
        answer = str(exc)
    turn = uid()
    await runtime.store.append(
        ctx.session,
        turn,
        {
            "role": "user",
            "content": text,
            "_command": True,
            **({"_client_ids": list(ctx.web_message_ids)} if ctx.web_message_ids else {}),
        },
        memory_profile=ctx.memory_profile,
    )
    reply = {"role": "assistant", "content": answer, "_command": True}
    if image is not None and ctx.session.startswith("web:"):
        await runtime.store.put(
            "help_image", turn, {"png": base64.b64encode(image).decode("ascii")}, ctx.session
        )
        reply["_help_image"] = turn
        image = None
    await runtime.store.append(
        ctx.session,
        turn,
        reply,
        memory_profile=ctx.memory_profile,
    )
    if image is None:
        await runtime.deliver(ctx.session, answer)
    else:
        await runtime.deliver(ctx.session, answer, image=image)
    return "command"
