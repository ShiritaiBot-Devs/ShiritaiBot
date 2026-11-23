---
name: shiritai-plugin-dev
description: Develop ShiritaiBot plugins (API v2): / commands, message listeners, LLM tools, Agent and send hooks, proactive event sources, settings forms, and plugin debugging and distribution. Use when the owner asks to write, change, or debug a ShiritaiBot plugin, asks how the plugin API works, or wants to migrate an old (plugin.toml / Entry) plugin to the new API.
license: AGPL-3.0-or-later
---

# ShiritaiBot plugin development

ShiritaiBot is an event-driven personal agent framework, and plugins run in its process (trusted code, no sandbox).
The plugin API is modeled on AstrBot and is currently API v2: import everything from `shiritaibot.api`; the framework's other modules are internal, so don't import them directly.

## Read the reference for the task first, then write

references/ holds the official documentation (source: shiritaibot.dev/docs/plugins; written in Chinese). Read what you need instead of writing the API from memory:

| Task | Read first |
| --- | --- |
| Writing a plugin from scratch, folder layout, metadata.yaml, loading and debugging, distribution | getting-started.md |
| / commands, command groups, argument injection, message listeners, MessageEvent fields | messages.md |
| Giving the model tools (@hook.llm_tool, risk levels, ToolContext) | tools.md |
| Declaring settings in _conf_schema.json, handling secrets | config.md |
| Changing model requests and replies, intercepting sends, lifecycle hooks | hooks.md |
| Sending external events to the proactive engine (ExternalEvent, polling sources) | proactive.md |
| The full self.context API (sending messages, calling models, KV, protocol clients, shell and subagent injection) | context.md |

## Minimal skeleton

Copy assets/hello_world/ (metadata.yaml + main.py + _conf_schema.json) and change it. Quick notes:

- The folder name is the plugin ID (`[a-z0-9_]`, `srtb_plugin_<name>` recommended); one main.py holds exactly one `Plugin` subclass.
- Lifecycle: read settings and start tasks in `initialize()`; cancel them in `terminate()`. A reload is: the old instance terminates, then the new one initializes.
- Commands: `@hook.command("name", aliases={"alias"})`; the first docstring line goes into /help; reply with `yield event.plain_result(...)`; raise expected errors with `raise PublicError("message")`.
- Tools: `@hook.llm_tool("tool_name", risk="safe", chat=True)`, with parameters in the docstring's `Args:` (`name(type): description`); tool names are global, so prefix them with the plugin name.
- Settings: declare fields in `_conf_schema.json` and read `self.config["key"]` in `initialize()`; saving settings reloads the plugin automatically.
- Languages: put user-facing text in `locales/zh-CN.json` and `locales/en.json` and use `self.t("key", name=value)`; metadata.yaml and _conf_schema.json text may be a `{"zh-CN": ..., "en": ...}` map (see docs/I18N.md).

## Hard rules (breaking them makes loading fail or behavior go wrong)

1. `risk` is required on `@hook.llm_tool`: safe (read-only) / normal (small, reversible writes) / sensitive (outside effects or hard to undo) / dangerous. Anything with side effects can't be safe; if unsure, use sensitive.
2. Commands can't reuse built-in names (/help, /status, /stop, ...) or other plugins' commands; tool names can't repeat.
3. Time limits: hooks 10 s, message listeners 30 s, proactive event hooks 30 s, tools 60 s by default (adjust with `timeout=`), commands 10 minutes. Put long work in `asyncio.create_task`.
4. Never block the event loop: no time.sleep and no synchronous HTTP; wrap synchronous calls in `asyncio.to_thread()`; use `httpx.AsyncClient` for the network.
5. Tasks and connections started in initialize() must be closed in terminate(); polling loop bodies need try/except (a single exception silently ends a background task).
6. Secret settings are stored encrypted, and code receives a `secret:...` reference; exchange it for the plain value with `await self.context.resolve_secret(...)`. Never log the plain value, return it to the model, or put it in child process environment variables.
7. Keep event payloads sent to the proactive engine to what's necessary: external content such as emails and web pages can carry prompt injection aimed at the model.

## Debugging and deployment

- Plugins are deployed in `data/plugins/<id>/`; `src/shiritaibot/plugin/` in the main repository holds the v2 host plugins shipped with it. When both search paths have the same name, data/plugins wins.
- After changing code, click "Reload this plugin" on the WebUI's Plugins page; no bot restart needed. If loading fails, the old instance keeps running and the error shows in the plugin list.
- Logs: `self.logger.log/warning/error(..., exc=exc)` → terminal + data/logs/shiritaibot.log + the WebUI log page; shorten long content with `brief(value, limit)`.
- Declare dependencies in the plugin folder's requirements.txt or in pyproject.toml's [project] dependencies; from 0.3 on, the runtime resolves them together and installs them into a separate runtime environment. Packages installed by hand with pip but not declared are removed at the next exact sync.
- Check syntax with the runtime venv's python: `python -m py_compile main.py`; the runtime venv is `data/runtime/projects/<generation>/.venv`, not the .venv at the repository root.
- When the deployment has global proxy variables, call mainland China APIs with `httpx.AsyncClient(trust_env=False, headers={browser UA})`: going through a proxy easily gets connections cut by the other side's risk controls. Also mind rate limits, and back off exponentially when polling fails (a real case: a stock quote API cut programmatic requests by their TLS fingerprint).
- KV stores small JSON data (self.get_kv_data / put_kv_data / delete_kv_data); put files and large data in `self.data_dir` (= data/plugin_data/<id>/, kept when the plugin is removed); settings the user should see or change belong in _conf_schema, not KV.

## Distribution

A public GitHub repository (enter the URL under "Install plugin" in the WebUI; the default branch is downloaded) or a ZIP (one extra folder level is fine); it must contain metadata.yaml and main.py. Plugins start disabled after installing; fill in the settings, then enable them.
