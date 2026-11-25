# Plugins and OneLearn

**English** · [简体中文](PLUGINS.md)

## Folder layout

`src/shiritaibot/plugin/core/` is the main repository's plugin host. Under `src/shiritaibot/plugin/`, every folder other than `core/`, the general email plugin `email/`, the GitHub plugin `github/`, and the developer announcement plugin `srtb_plugin_devs_notice/` is ignored by `.gitignore`. The Feishu and Web Learning plugins have to be installed separately, code and manifest, in the deployment; a clean checkout doesn't include them. The old `integrations/` and `builtin_plugins/` have been removed.

`src/shiritaibot/learn/` is core code: it defines school-independent requests and responses, stdio access, query and download tools, sync baselines, and change events. It contains no Tsinghua URLs, SSO, SM2, or school client. `plugin/thu_learn/` holds the Tsinghua-specific implementation, including the client and the single-request adapter process.

There are two plugin search paths: first `src/shiritaibot/plugin/<id>/`, then `<home>/plugins/<id>/`. When both have the same name, the first wins. `core` isn't scanned as a plugin. The folder name is the plugin ID (`[a-z0-9_]{1,64}`).

The plugin API has two versions, told apart by the manifest file in the folder:

| | API v2 (recommended) | API v1 (legacy, still supported) |
|---|---|---|
| Manifest | `metadata.yaml` | `plugin.toml` (`api = 1`) |
| Settings definition | `_conf_schema.json` (AstrBot format) | `[settings_schema]` in `plugin.toml` (JSON Schema) |
| Entry point | The only `shiritaibot.api.Plugin` subclass in `main.py` | `Entry(shiritaibot.plugin_api.Plugin)` in `main.py` |
| Lifecycle | `initialize()` / `terminate()` | `start()` / `close()` |
| Capabilities | Commands, message listeners, LLM tool decorators, Agent / send / proactive engine hooks, KV storage, plus everything v1 has | Tools, events, notification channels, child process environment variables, and more (see "Legacy API" below) |

When both files exist, the plugin is treated as v2. The built-in email and github plugins have moved to v2; the deployment's Feishu and Web Learning plugins are still v1 and keep working without changes.

On startup, these folders are scanned and settings are filled in for valid plugins, keeping existing enabled states and values. Newly found plugins start disabled; configure them, then enable and save. Invalid manifests and load failures are shown in the WebUI and don't block other plugins.

The plugin list only shows plugins actually found on disk. IDs left over in the configuration don't create plugin entries or "not installed" placeholders; their settings are kept for when the code comes back. A manual reload stops running instances whose folder or manifest has disappeared and withdraws their tools and notification channels. Each plugin's manifest must be deployed together with its actual `main.py`; creating only the manifest can't restore a missing implementation.

**Install plugin** in the WebUI accepts an uploaded ZIP or a public GitHub repository URL (the default branch's ZIP is downloaded; installing from the [plugin market](#plugin-market) downloads the approved commit). Plugins are installed into `<home>/plugins/<id>/`. A ZIP can contain the manifest (`metadata.yaml` or `plugin.toml`) and `main.py` directly, or wrapped in one repository folder. Before installing, the manifest and settings definition are checked with the same rules as loading. Installing over an existing name is refused. **Remove** asks for confirmation first; it then stops the plugin, withdraws its tools and notification channels, deletes its code folder and settings, and updates the list. Both search paths are cleaned for that name; the plugin's data folder and history are kept. **Reload plugins** rescans all folders, fills in settings, and reloads; you can also reload just the current plugin. Reloading uses the saved settings.

Plugins run in the same process and count as trusted local code; this is not a security sandbox. Tool execution is controlled by the permission layer, but the plugin code itself has the host account's permissions. Only install code you trust. The host resolves and installs the Python dependencies a plugin declares automatically.

## Plugin market

**Plugins → Market** lists plugins from plugin markets. The official market is [plugin.shiritaibot.com](https://plugin.shiritaibot.com): an AI reviews the code of every listed version and a maintainer approves it. Submitting and publishing happen on the website; the WebUI only lists and installs.

- **Browsing**: search by name, description, author and tag; installed plugins show the installed version. Plugin details follow the interface language.
- **Notices before installing**: **Install** first shows the notices from the review (for example, conversation data sent to a third-party server, or an external account required) and the reminder that plugins have no sandbox. Nothing is installed until you confirm.
- **Installing**: downloads the ZIP of the listed repository at the approved commit from GitHub, through the same flow as **Install plugin → Public GitHub repository** (`POST /api/plugins/install/github` with `commit`), under the plugin ID from the index. Code only comes from GitHub; no other URL from an index is used, and never a branch or tag. New plugins start disabled; configure and enable them under **Installed**.
- **Sources**: set under **Plugin sources** at the bottom of the page, one index URL per line, applied once saved. By default only the official source is listed (`plugin_market.sources`). URLs must be https; `http://127.0.0.1` works for local testing. When two sources list the same ID, the one higher up wins.
- **Cache and fallback**: the backend reads `GET /api/plugins/market` (`refresh=true` forces a fetch), checks the index entry by entry, and drops and reports malformed entries. Each source's last good copy is kept in `<home>/plugin_market/`; when a source fails, that copy is shown with a note.

The index format is defined by the plugin store (`docs/INDEX.en.md` in the ShiritaiBot plugin store repository).

## Managing plugin dependencies

`pyproject.toml` / `uv.lock` at the repository root remain Core's development and release baseline. Before starting the app, `shiritaibot run` scans every installed plugin (disabled ones included) and combines Core's and the plugins' dependencies into a separate project in the user data folder:

```text
<home>/runtime/
  current.json                     # the currently selected environment generation
  projects/<generation>/
    pyproject.toml                 # combined dependency declaration generated by the framework
    uv.lock                        # combined lock file resolved by uv
    state.json                     # input fingerprint, accepted plugin dependencies, and failure reasons
    .venv/                         # the actual runtime environment
```

The framework resolves with `uv lock --project <generation>`, then installs with `uv sync --project <generation> --locked --no-dev`. `current.json` is only updated after a successful install; a failure doesn't replace the existing environment. The CLI restarts the app with the selected environment's Python and sets `VIRTUAL_ENV` and `PATH`, so `python` / `pip` in the framework's shell use that environment too. The repository's `.venv` is only for development and starting, so a plain `uv sync` in the repository doesn't affect the runtime environment.

To only prepare dependencies without starting the service, run `uv run shiritaibot sync --home data`; `--extra learn` can be added here too. The one-click install script also runs this step.

Plugins can use either format:

```toml
# pyproject.toml (recommended for new plugins)
[project]
name = "srtb-plugin-example"
version = "1.0.0"
requires-python = ">=3.12"
dependencies = ["aiohttp>=3.9,<4"]
```

or write `aiohttp>=3.9,<4` in `requirements.txt`. When `[project]` exists, its static `dependencies` are used; a `pyproject.toml` with only tool settings doesn't hide `requirements.txt`. Supported: PEP 508 version ranges, extras, environment markers, `name @ URL`, relative local folders or archives, and nested `-r` / `-c` references in requirements files; relative paths are based on the declaring file's folder. Not supported yet, with a clear error: dynamic dependencies, plugin `tool.uv.sources`, `-e`, `--hash`, and index options in requirements files. Set package indexes and authentication through uv's configuration or environment variables; dependency URLs shouldn't embed passwords or query parameters. A plugin's own lock file isn't merged, and dependencies are never guessed by scanning Python imports.

The direct and indirect dependencies Core needs are exported from Core's lock file as exact constraints, keeping environment markers. Plugins can add packages but can't change Core's locked versions, and plugins are solved together. Existing plugins take priority: a newly found incompatible plugin gets its own error, and other plugins keep loading. Fix the declaration or package index and reload to retry; conflicting constraints are never forced.

Declarations are checked on startup, when installing or reloading a plugin, when removing a plugin, and when the WebUI updates Core. If the inputs haven't changed, the lock file is reused; on startup the locked environment is synced to restore packages that were removed. When the set of dependencies changes, a new environment is created, so imported packages are never replaced while running. The Plugins page says a restart is needed to enable the plugin, and plugins in the old environment keep running. Disabling only stops a plugin and doesn't uninstall its dependencies; removing a plugin resolves the remaining set again, and shared dependencies stay in the new environment. Old environments are kept for processes still using them.

Core's optional dependencies are enabled with `--extra` on the run command, for example `uv run shiritaibot run --extra learn`, not just `uv sync --extra learn` in the repository environment. The runtime environment includes pip, but packages installed by hand with pip and not declared are removed at the next exact sync; lasting dependencies belong in the plugin's declaration. Custom hosts that call `create_app` directly need to use the selected runtime environment themselves; switching interpreters automatically is done by `shiritaibot run`.

## Tool risk levels and session permissions

`risk` is required for `@hook.llm_tool(..., risk=...)` and `Context.register_tool`, and also when constructing a `Tool` directly. Valid values are `safe`, `normal`, `sensitive`, and `dangerous`; only `safe` counts as a safe tool. A missing or invalid value makes the plugin fail to load, and a failed reload keeps the old instance. Never mark a tool with side effects as safe.

```python
self.context.register_tool("read_status", "Read the status", schema({}), self.read_status, risk="safe")
self.context.register_tool("send_notice", "Send something outside", parameters, self.send_notice, risk="sensitive")
```

Plugin Manager checks every tool's risk level and schema before replacing the old instance. The plugin list API exposes `name/risk/safe` for each registered tool, and the WebUI's tool catalog shows source and safety. Each Agent session can set an individual tool to blocked, manual approval, automatic approval by the model, or no approval needed. A tool's risk level only decides the preset default permission and doesn't replace authorization at execution. Plugin tools are only available in Agent mode by default. Read-only tools that are part of basic conversation can be declared with `register_tool(..., chat=True)` so they also appear in Chat sessions, under the same permission controls.

OneLearn's `learn_query` is declared safe and `learn_download` normal. The deployment's Feishu read-only query tool is declared safe; the Tsinghua login / SMS verification tools are declared sensitive and keep their owner-private-chat business checks. The email plugin currently only registers event polling and a notification channel, with no tools the model can call, so it needs no made-up tool declarations; these lifecycle tasks aren't subject to model tool approval.

The Feishu and Tsinghua plugin sources are still maintained separately in the deployment. The compatibility change for them is in `examples/plugins/session-permissions.patch`; apply it from the repository root with `git apply examples/plugins/session-permissions.patch` (already applied in this deployment, no need to repeat). From now on, every new tool must declare risk explicitly.

## Plugin settings declared by the plugin

A v2 plugin declares its settings in `_conf_schema.json` in its folder, in the same format as AstrBot, with no changes to the frontend or host:

```json
{
  "token": { "type": "string", "description": "Access token", "hint": "Stored encrypted", "secret": true },
  "mode": {
    "type": "string",
    "description": "Mode",
    "options": ["fast", "safe"],
    "labels": ["Fast", "Safe"],
    "default": "safe"
  },
  "interval": { "type": "int", "description": "Interval (seconds)", "slider": { "min": 60, "max": 3600, "step": 60 }, "default": 300 },
  "advanced": {
    "type": "object",
    "description": "Advanced settings",
    "items": { "retries": { "type": "int", "default": 3 } }
  }
}
```

| Field | Description |
|---|---|
| `type` | Required: `string`, `text` (multi-line), `int`, `float`, `bool`, `object`, `list`, `dict`, `template_list` |
| `description` / `hint` | Form label / help text; shown prominently with `obvious_hint: true` |
| `default` | Default value; if omitted, `""`, `0`, `0.0`, `false`, `{}`, or `[]` by type |
| `items` | Children of an `object`; can be nested |
| `options` / `labels` / `subtitles` | Dropdown options, their display names, and option descriptions (matched by index); a `list` with `options` is multi-select |
| `slider` | Slider for `int` / `float`: `{"min", "max", "step"}` |
| `secret` | For `string` or a `list` of strings: a secret field, **encrypted into the Vault** (AstrBot only masks it in the UI) |
| `invisible` | Not shown in the form; the value is still kept |
| `editor_mode` / `editor_language` | Monospace code editor |
| `template_schema` | Optional parameter template for a `dict` |
| `templates` | Item templates for a `template_list`, saved as an array of objects with `__template_key`; `display_item` picks the field shown in the list summary |
| `minimum` / `maximum` / `pattern` / `format` | ShiritaiBot extensions: validated as JSON Schema |

The `file` type isn't supported yet; `_special` selectors are ignored and shown as the base type. The host turns this definition into JSON Schema (with `x-` extension fields, see `plugin/core/manifest.py`) and validates settings before loading; a validation failure only reports the field path.

Values are saved in the main `config.toml`, and credentials go in the Vault. As in AstrBot, after a plugin updates its settings definition, reloading fills in defaults for new fields and removes deleted fields. Plugin code reads settings through `self.config` (`PluginSettings`, a `dict` subclass); after changing them, `await self.config.save_config()` writes them back to the main configuration, and secret fields are encrypted automatically.

Text in `metadata.yaml` and `_conf_schema.json` can be given per language; see [Languages](#languages) below.

v1 plugins keep using JSON Schema in `[settings_schema]` in `plugin.toml` (`title`, `description`, `minimum`, `maximum`, `default`, `writeOnly`), and can define defaults with `[settings]`. After changing the manifest, click **Reload plugins** to update the form and the running instance.

## Feishu CLI

Local folder: `src/shiritaibot/plugin/feishu/`. Plugin ID: `feishu`.

On the **Plugins** page, expand the entry, click **Fill in the settings template**, and set:

```toml
[[plugins]]
id = "feishu"
enabled = true
[plugins.settings]
command = ["lark-cli"]
config_dir = "/home/your-user/.lark-cli/orgs/shiritaibot"
```

Sign in to the CLI in that separate folder and grant what's needed first. The code sets `LARKSUITE_CLI_CONFIG_DIR` explicitly every time; it never reads or migrates AstrBot's credential files.

`feishu_query` uses a fixed set of read-only operations: chat list/search, message history/search, threads, document search/read, and task queries. Parameters are allow-listed too, so arbitrary CLI flags can't be injected, and it can't send messages or change documents. What it can reach depends on that CLI profile's authorization.

The allow list comes from `build_args` / `redact` in the local AstrBot Feishu plugin, read from its current source. Past verification on a server doesn't mean ShiritaiBot has been tested end to end with real Feishu; after a CLI upgrade, check the command help and required scopes again.

## Tsinghua Web Learning

Local folder: `src/shiritaibot/plugin/thu_learn/`. Plugin ID: `thu_learn`.

```powershell
uv sync
uv run shiritaibot run --extra learn
```

Enter your student ID and password directly in the plugin settings in the WebUI and save; the password is encrypted automatically. The saved settings look like this (`secret:...` is generated by the program; don't create it by hand):

```toml
[[plugins]]
id = "thu_learn"
enabled = true
[plugins.settings]
username = "your student ID"
password = "secret:auto-generated credential ID"
poll = false
poll_seconds = 900
```

If `command` is empty, the plugin starts `bridge.py` in its own folder with the current Python interpreter. For another location, give an explicit argv, for example a Python executable plus the absolute path of `bridge.py`.

Signing in is done by the model calling plugin tools; there's no WebUI-only sign-in panel or `/api/learn/auth` endpoint anymore:

1. The owner asks to sign in or look something up in a private chat, and the model calls `thu_learn_login()`; an existing session can be reused directly.
2. When it returns a status such as `verification_required`, the model asks the user for the SMS code instead of calling login again.
3. After the user replies with the code, the model calls `thu_learn_verify_sms(code="the 6-digit code from the user")` and, on success, continues the original query. `thu_learn_login(resend_sms=true)` is only used when the user explicitly asks to resend.

These two tools are only available in the owner's private chat / the WebUI's ordinary chat entry, follow disabled tools and scope policies, and can't be called from group chats, friends, or background tasks. The password is read by the plugin from the encrypted settings and passed to the adapter over stdin, never as a model parameter. The verification code enters the current model context and session history through the normal tool flow; the activity log never records the code or password. Cookies / device fingerprints are still saved to `<home>/plugin_data/thu_learn/session.json`; existing accounts or cookies on the server are never reused.

`learn_query` supports courses, announcements, unsubmitted homework, course files, and homework attachment lists; `learn_download` downloads course files and attachments to the plugin's private folder. There's no homework submission.

When a background tool hits a condition the owner has to handle, it can raise `InteractionRequired(key, reason, ttl=86400)`. The host scopes the key by plugin ID and saves the related proactive event as `waiting`, notifying only once per waiting period. After the owner's action succeeds in the private chat, the plugin calls `await self.context.resolve_interaction(key)`, and all still-valid waiting events are rerun automatically. Expired events become `processed`. Pass `interactive=True` when registering sign-in, verification-code, and similar tools; background tasks then hide these tools automatically and refuse direct calls.

A separately maintained Tsinghua plugin can adopt this protocol by running `git apply examples/plugins/thu-learn-interaction.patch` from the repository root.

Polling is off by default. When on, the first successful sync only sets a baseline; after that, created / updated events are generated by homework ID and content fingerprint and handed to the Proactive Engine. A failed sync generates a diagnostic event. Only homework is polled for now, not announcements or course files.

`client.py` was ported from the AstrBot Web Learning plugin on the user's server and keeps the school-specific sign-in, course paging, attachment ownership checks, and download limits. This compatibility client still uses synchronous requests, but runs in a separate child process; the main program calls it through asyncio, sets time limits, and cancels the child process, so the main event loop is never blocked.

## Email (IMAP event source)

Folder: `src/shiritaibot/plugin/email/` (committed with the main repository, API v2). Plugin ID: `email`, disabled by default.

The main path is **receive → unified event API → preprocessing → importance filter → Proactive Loop → notification and short-term summary**:

1. The plugin polls the IMAP mailbox every `poll_seconds` (read-only `EXAMINE` + `BODY.PEEK`, so read status isn't changed) and merges the emails matching the time conditions into one `email.received` event, entering at most one Proactive Loop. The `payload` has `mailbox`, `count`, and a `messages` array; each email keeps its UID, sender, subject, date, server receive time, body summary, and attachment names. If no email matches, no event is sent. Each batch gets a stable dedup key from the mailbox, UIDVALIDITY, and message identifiers, and the UID cursor is saved after a successful submission. A receive failure produces an `email.sync.failed` event (once a day per kind of error).
2. The event wakes the proactive engine right away (see "Proactive engine" below). The light model rates its importance (0–4): 0 is dropped, everything else enters a Proactive Loop that can call tools; level 4 must notify.
3. The event flow is written to the proactive_trace audit. Each executed Turn is compressed by the model into Shortterm Memory, and the owner can review Main / Proactive summaries with `proactive_recall`.

On the **Plugins** page, choose the auto-discovered `email`, enter the mailbox details, enable it, and save (the password is encrypted automatically). To go back to the defaults, click **Fill in the settings template**:

```toml
[[plugins]]
id = "email"
enabled = true
[plugins.settings]
imap_host = "imap.example.com"
imap_port = 993
imap_security = "ssl"          # ssl / starttls / none (none only for localhost)
username = "you@example.com"
password = "secret:auto-generated credential ID"   # shared by IMAP and optional SMTP
mailbox = "INBOX"
poll_seconds = 300             # at least 60
max_per_poll = 20
max_body_chars = 2000
time_filter = "startup"         # startup / last_poll / none / custom
filter_before = ""              # required for custom, e.g. 2026-10-28T09:00:00+08:00
# The SMTP send switch is separate from IMAP receiving. Fill in the server and notification recipients to send email.
smtp_enabled = true
smtp_host = ""
smtp_port = 465
smtp_security = "ssl"
notify_to = []
```

Time filtering compares the server's `INTERNALDATE` exactly (boundaries included), never the Date header, which the sender can set. `startup` (the default) filters out emails from before this plugin start or reload; `last_poll` filters out emails from before the last successful poll, with the time saved, using this start time when there's no record yet; `none` doesn't filter by time, so the first poll can read existing emails; `custom` filters out emails from before `filter_before`, which must include a time zone. Processed or filtered UIDs aren't scanned again; when `UIDVALIDITY` changes, the mailbox is rescanned by the time conditions. `max_per_poll` limits how many candidate emails are read per poll; a backlog is handled over several polls, still with one merged event each. With `last_poll`, the original filter window is kept until the backlog is read, and a failure doesn't move the cursor or time forward.

Email content is treated as untrusted material: both the triage and the preparation prompts state that instructions in it are not to be followed, and background preparation uses the tool permission snapshot from when the event was created. Triage runs without a persona; once in the Proactive Loop, the configured default persona and the assistant / owner names are loaded, and the final notification follows the persona. If `imap_host` or `username` is missing, loading fails; the error only lists field names and never echoes input. IMAP / SMTP use the standard library clients, run in a thread, and never block the event loop.

The SMTP channel is a best-effort copy: the email is sent after delivery to QQ / the WebUI has been recorded as sent. A failure only records `notify.channel_failed`, doesn't affect the IM notification, and isn't retried. Other plugins can join the same channel with `PluginContext.register_notifier(handler)`, where `handler(source, text)` is an async function; the host withdraws it automatically on reload and disable.

## GitHub

Folder: `src/shiritaibot/plugin/github/` (committed with the main repository, API v2). Plugin ID: `github`, disabled by default.

Lets the Agent maintain repositories as a **dedicated bot account**. Create a fine-grained PAT under the bot account: set Repository access to *All repositories* (so repositories created later are covered too), and grant read and write for Contents, Issues, Pull requests, Workflows, Actions, and Administration as needed. Don't use your main account's token.

```toml
[[plugins]]
id = "github"
enabled = true
[plugins.settings]
token = "secret:auto-generated credential ID"   # entered in the WebUI, encrypted automatically
commit_name = ""                      # empty = the token account's login
commit_email = ""                     # empty = <id>+<login>@users.noreply.github.com
co_author = false                     # when on, commits get Co-authored-by: ShiritaiBot
allowed_owners = ["shiritai-bot"]     # empty = only the token account itself
proxy = ""                            # e.g. socks5h://127.0.0.1:1080; empty = the API uses proxy environment variables, git connects directly
timeout = 30
```

| Tool | risk | What it covers |
|---|---|---|
| `github_read` | safe | Account, repositories, branches, files, commits, issues / PRs (with comments and diffs), Actions, releases |
| `github_write` | normal | Create and edit issues and PRs, comment, review, create labels |
| `github_publish` | sensitive | Merge PRs, publish releases, rerun or trigger workflows, delete non-default branches |
| `github_admin` | dangerous | Create repositories, change repository settings and topics, set branch protection; **deleting repositories isn't offered** |
| `github_push` | sensitive | Push a local workspace branch to GitHub; `force` can't be used on the default branch |

All tools can only be called by the owner in Agent mode in a private chat / the WebUI, not in group chats, and the repository owner must be in `allowed_owners`. Issue, PR, and file contents are treated as untrusted material.

**The token never enters the shell or subagents.** The plugin only injects `GIT_AUTHOR_*` / `GIT_COMMITTER_*` into the shell tool and subagents through `PluginContext.set_process_env`, so a `git commit` the model runs in the shell uses the bot identity; pushing has to go through `github_push`. That tool doesn't run git in the model-controlled repository (its config and hooks could read credentials). Instead, it fetches the branch into a plugin-private bare mirror in `<home>/plugin_data/github/mirrors/` and pushes from the mirror with the token. The token is passed to git through an environment variable and never appears in command-line arguments or tool output. Pass `subagent_run` when pushing a subagent's changes.

With `co_author` on, commit messages get `Co-authored-by: ShiritaiBot <shiritaibot@shiritaibot.dev>`, without touching git config, hooks, or environment variables:

- **Main Agent shell**: through `PluginContext.add_shell_filter`, the plugin rewrites commands after approval and before execution, adding `-c trailer.co-authored-by.ifexists=addIfDifferent` and `--trailer '...'` to each `git [global options] commit` (no duplicate if the commit message already has that line). The rewrite finds command positions using bash syntax, so text in quotes, comments, and heredocs isn't changed; git ≥ 2.32 is required.
- **Commits made by tools**: merge / squash in `pr_merge` appends the line to the end of the commit body. Without a `commit_message`, the GitHub default body is rebuilt from the repository's merge / squash commit message settings before appending; rebase creates no new commit message and returns `co_author: false`.
- **Subagents**: through `PluginContext.add_subagent_note`, a note is added to the end of the prompt of newly started subagents (except the generic type): add this line when committing, and ignore it if the task clearly doesn't commit. Continued runs don't get it again.

Limitation: the shell tool runs under the same system account as the host, so a model that can read `<home>/master.key` and the database could in theory still decrypt the Vault. This design prevents the token from accidentally entering context, logs, and chat history. For stronger isolation, run the shell in a sandbox or a separate account, and keep the token's permissions minimal. A token referenced with `env:` is inherited by every child process, so enter it in the WebUI instead.

## ShiritaiBot Devs announcements

Local folder: `src/shiritaibot/plugin/srtb_plugin_devs_notice/`. Plugin ID: `srtb_plugin_devs_notice`. Shipped with the main repository, disabled by default.

The plugin polls `https://notice.shiritaibot.dev/api/notices?after=<cursor>` over HTTPS to receive development announcements from ShiritaiBot Devs. The announcement service gives every publish, edit, and deletion a globally increasing `seq`, and the plugin stores the largest `seq` it has read in KV as its cursor. The first poll only records the current `seq` as a baseline and doesn't replay past announcements.

```toml
[[plugins]]
id = "srtb_plugin_devs_notice"
enabled = true
[plugins.settings]
url = "https://notice.shiritaibot.dev/api"   # must be https; http://127.0.0.1 / localhost only for local debugging
poll = true
poll_seconds = 1200                           # at least 600
categories = ["release", "security", "breaking", "maintenance", "plugin", "general"]
notify_updates = true                         # also notify when an announcement is edited
min_importance = 2                            # 1–4
timeout = 30
```

| Type | Meaning |
|---|---|
| `release` | Version release |
| `security` | Security advisory |
| `breaking` | Breaking change / migration guide |
| `maintenance` | Service maintenance |
| `plugin` | Plugin news |
| `general` | General announcement |

Each new (or edited) announcement of a selected type submits one event: `source = "devs_notice"`, `kind` is `devs_notice.published` / `devs_notice.updated`, and `dedup_key = notice:<id>:r<revision>`. Announcement levels `info` / `important` / `critical` map to urgency `normal` / `high` / `critical`. Deleted announcements and unselected types only move the cursor. The plugin's `on_proactive_judged` raises the triage importance to at least `min_importance`, and `critical` announcements are fixed at level 4, so announcements are never dropped at triage and always enter the Proactive Loop. The announcement body goes into the payload as untrusted material, marked as not being instructions for the assistant.

The plugin registers no model tools and needs no credentials. Publishing announcements uses a separate internal ShiritaiBot Devs service and plugin, not distributed with the main repository.

## Proactive engine

Event sources call `PluginContext.emit(event)`, `emit_many(events)`, and `get_event(receipt_id)`. The host offers the same API; over HTTP these are the authenticated `POST /api/events`, `POST /api/events/batch`, and `GET /api/events/{id}`. An event has the source event `id`, event name `kind`, `source`, a time-zone-aware `occurred_at`, `title`, content `payload`, a stable `dedup_key`, a reference `urgency`, and optional `actors` (people involved in the event: `identity` such as `email:someone@example.edu` / `qq:12345`, `name`, and `role` such as `sender`). `actors` is only raw identity data: plugins never call memory APIs, and after the event is handled the core decides whether to write anything to long-term memory (see [MEMORY.md](MEMORY.md), in Chinese). The entry point can fill in `id` automatically. The returned receipt ID is deduplicated stably by `(source, dedup_key)`. Submitting doesn't mean processing is done; check the status to see the triage and execution results.

| Stage | Behavior |
|---|---|
| Entry / preprocessing | Trim whitespace in identifiers and titles, validate, deduplicate, persist, then wake the PE |
| Filter | Uses its own triage prompt without a persona; the light model outputs importance=0–4, a reason, and optional notification / verification suggestions. A failure is corrected once; if it fails again, a normal-level Loop checks it |
| Levels | 0 is discarded; 1–4 start background Turns in descending order of level, FIFO within a level; running tasks aren't preempted |
| Proactive Loop | `background:proactive:<receipt ID>`, reusing the Agent Runtime, loading the configured default persona and the assistant / owner names, and calling tools with the default tool permission snapshot from creation. The final output is `{notify, message, reason}`, with message following the persona |
| Notification and handoff | The final message is only sent after the Loop finishes and is appended to the target Main context. Level 4 always notifies, bypassing Do Not Disturb, summary delays, and throttling; on failure or timeout, a fallback is sent saying it's unfinished |
| Per-turn memory | After every Main / Proactive Turn succeeds, fails, or is cancelled, a summary task is persisted, and the light model compresses the input, tool results, and final message; failures are retried without blocking replies |

Explicit Routine reminders / scheduled tasks also go through the Filter, at level 3 or higher; explicit reminders can't be silenced. When the PE is off, other events are only audited. An event source's urgency can't decide importance directly.

The audit is stored in `proactive_trace` (`/api/records/proactive_trace`), model summaries in `short_term` (kept 7 days by default), and tasks waiting for retry in `memory_summary`. `proactive_recall(query?, limit?)` queries the model summaries of the owner's private chat / background, and never returns content from other Chat or group sessions. Stage logs from old versions are never recalled as new model summaries. Level 0 events never enter the Loop and only have a discard audit.

## OneLearn subset

The current convention is defined in `learn/protocol.py`: a process receives a UTF-8 JSON request on stdin, handles one operation, returns one JSON object on stdout, and exits. Credentials go through stdin, never argv.

A request has `version=1`, `operation`, `params`, `owner`, `config`, and `data_dir`. A response has `ok` and `data`, or `status` / `message`. This is a minimal working subset, **not a conforming implementation of the final OneLearn standard**; the `docs/LEARN_PROTOCOL.md` mentioned in the design isn't in this workspace. Long-lived hello/capabilities connections, WebSocket, multi-school aggregation, and write operations aren't implemented yet.

## Writing a plugin (API v2)

v2's structure follows [AstrBot plugins](https://docs.astrbot.app/dev/star/plugin-new.html): `metadata.yaml` + `_conf_schema.json` + `main.py`, with the plugin class inheriting `Plugin` and `hook` decorators registering commands, listeners, tools, and hooks. See `examples/plugins/hello_world` for a full example: copy it to `<home>/plugins/hello_world` (or zip it and install it in the WebUI), click **Reload plugins**, then enable and save.

Everything is imported from `shiritaibot.api`, the only supported import boundary for v2; the program's other modules may be refactored at any time.

### Manifest: metadata.yaml

```yaml
name: hello_world            # fallback display name; the plugin ID is always the folder name
display_name: Hello World    # name in the plugin list
desc: What the plugin does
short_desc: One-line summary # shown in the sidebar
version: 1.0.0
author: you
repo: https://github.com/you/repo
support_platforms: [qq, web] # display only
shiritaibot_version: ">=0.2,<1"   # optional; if not met, the plugin shows as failed to load
```

Declare dependencies in `requirements.txt` or `pyproject.toml` in the plugin folder; the host resolves and installs them, see [Managing plugin dependencies](#managing-plugin-dependencies).

### Plugin class

```python
from shiritaibot.api import Context, MessageEvent, Plugin, PluginSettings, hook


class MyPlugin(Plugin):
    def __init__(self, context: Context, config: PluginSettings):
        super().__init__(context, config)   # can take only context if there are no settings

    async def initialize(self):             # called on load; a good place to start background tasks
        ...

    async def terminate(self):              # called on disable, reload, remove, and exit; must cancel your own tasks
        ...

    @hook.command("hello")
    async def hello(self, event: MessageEvent, name: str = ""):
        """Say hello"""                     # the first line is the description in /help
        yield event.plain_result(f"Hello {name or event.get_sender_name()}")
```

`main.py` must define exactly one `Plugin` subclass (intermediate base classes are fine; the most derived one is used). Available on the instance: `self.config` (settings), `self.context` (host services), `self.name` (plugin ID), `self.data_dir` (`<home>/plugin_data/<id>/`, for large files), `self.logger`, `self.t()` (translated text, see [Languages](#languages)), and per-plugin KV storage with `await self.get_kv_data(key, default)` / `put_kv_data(key, value)` / `delete_kv_data(key)` (values must be JSON serializable).

### Commands

In enabled QQ groups and Feishu groups, members can send built-in commands (such as `/help`) and plugin commands (such as `/hello`) directly by default, including plugin aliases and subcommands. After turning on **Group / commands need waking** under **General → Names and wake words** in the WebUI, they need to @ the bot or include the wake word, for example `小知, /hello`.

| Decorator | Purpose |
|---|---|
| `@hook.command(name, aliases={...}, priority=0)` | `/name args…`; arguments are bound by the handler's signature, `int` / `float` / `bool` are converted automatically, the last `str` parameter takes the remaining text, and `*args` takes all remaining arguments. Missing or mistyped arguments get a usage reply |
| `grp = hook.command_group(name)` + `@grp.command(sub)` / `grp.group(sub)` | Command group `/name sub args…`, nestable; sending only the group name replies with the subcommand list |
| `@hook.permission(Permission.OWNER)` | Owner only; anyone else gets a notice |
| `@hook.message_type(MessageType.PRIVATE)` / `@hook.platform(Platform.QQ)` | Combined with a command, it only matches in those situations |

The handler's first two parameters are `self, event`. Replies work as in AstrBot: `yield event.plain_result(...)` (any number of times), `return` a result or string, or `await event.send(...)`. Commands run in the background (up to 10 minutes) without blocking later messages; their output is recorded in session history but doesn't enter the model's context.

Plugin commands can't share a name with built-in commands (`/help`, `/status`, `/stop`, and so on) or with another plugin's top-level commands; a conflict makes the plugin fail to load. Built-in commands, "stop / cancel", and approval replies are always handled by the host, and plugins can neither see nor intercept them. Only text replies are supported for now, not images, text-to-image, or other rich messages.

### Message listeners

| Decorator | Purpose |
|---|---|
| `@hook.message_type(MessageType.ALL)` | Called for every message; `GROUP` / `PRIVATE` only for group or private chats |
| `@hook.regex(pattern)` | Called when the text matches with `re.search` |

Listeners run from highest to lowest `priority`, each with a 30-second limit. After `event.stop_event()`, later listeners don't run and the message isn't passed to the model. Group messages that don't @ the bot and have no wake word still reach listeners (`event.is_wake` is False), which is useful for statistics or keyword triggers; these messages never go to the model anyway.

`MessageEvent` fields: `session` (unified session ID, can be passed to `Context.send_message`), `text` (the user's plain text), `sender_id`, `sender_name` / `get_sender_name()`, `platform` (`qq` / `web`), `adapter`, `group_id`, `is_group`, `is_private`, `is_owner`, `is_wake`, `message_id`, `segments` (OneBot message segments), and `raw` (the original event). Changing these fields doesn't change what the model sees; to change model input, use `on_llm_request`.

### LLM tools

```python
@hook.llm_tool("weather", risk="safe", chat=True, timeout=30)
async def weather(self, ctx: ToolContext, city: str, days: int = 1):
    """Look up the weather.

    Args:
        city(string): City name
        days(integer): Number of days
    """
    return {"city": city, "forecast": "sunny"}
```

The parameter schema comes from, in order: an explicit `parameters=`, the docstring's `Args:` (types can be `string`, `number`, `integer`, `boolean`, `object`, `array`, `array[string]`), and type annotations (unlike AstrBot, annotations are read here). Parameters without a default are required. `ctx` is the tool call context, with fields such as `session`, `actor`, `owner`, `group`, and `mode`. See above for the `risk` rules. You can also register tools dynamically with `self.context.register_tool(...)` or `self.context.add_tools(Tool(...))`.

### Agent lifecycle hooks

| Decorator | Handler parameters | What it can do |
|---|---|---|
| `on_agent_begin` | `(turn)` | Observe |
| `on_llm_request` | `(turn, request)` | Called before each model request: change `request.system_prompt`, `request.messages`, `request.tools`, or add to the latest user message with `request.add_context(text)`. Changes only affect this request and aren't written to history |
| `on_llm_response` | `(turn, response)` | Rewrite `response.text` (written to history and sent) |
| `on_tool_call` | `(turn, name, arguments)` | Observe; called before the permission check |
| `on_tool_result` | `(turn, name, arguments, result)` | Observe |
| `on_agent_done` | `(turn, result)` | Observe `result.status` (`completed` / `failed` / `cancelled`), `result.steps`, `result.text` |

`Turn` has `id`, `session`, `actor`, `owner`, `group`, `background`, `mode`, `route`, and `text`; `turn.proactive` and `turn.event_id` identify a Proactive Loop. These hooks apply to the main loop and the Proactive Loop, not to subagents, triage, memory extraction, or other internal model calls.

### Send hooks

| Decorator | Handler parameters | What it can do |
|---|---|---|
| `on_decorating_result` | `(message)` | Called before a message enters the send queue: rewrite `message.text`, or cancel sending with `message.cancel()` |
| `after_message_sent` | `(message)` | Called in the background after a message enters the send queue |

These cover all text sent to QQ and the WebUI, including model replies, command output, and proactive notifications. Permission approval prompts are the exception: plugins can neither rewrite nor cancel them.

### Proactive engine hooks

| Decorator | When | What it can do |
|---|---|---|
| `on_proactive_event(event)` | When an event starts processing, before triage | Change `event.title` / `payload` / `urgency`; drop it with `event.drop(reason)` |
| `on_proactive_judged(event, triage)` | After triage | Change `triage.importance` (0 drops it), `decision`, `message`, `reason`, `prepare` |
| `on_proactive_done(event, outcome)` | After the Proactive Loop ends, before the notification is queued | Rewrite `outcome.message` / `reason`; set `outcome.notify` to False to cancel the notification |
| `on_notification_sent(source, text)` | After a notification has been sent to the owner | Observe, for example to forward it to another channel (v1's `register_notifier` also works) |

The host keeps fallback rules. Reminders and scheduled tasks set by the owner, and results of subagents the owner delegated, are `event.protected`: they can't be dropped, triage still has a minimum level, and delegated results are always level 4. Importance 4 and owner-set reminders are marked `outcome.forced`; then only the content can be rewritten, not cancelled. If a change is invalid (for example importance outside 0–4, or a message that's empty or over 4000 characters), the host ignores that plugin's change and logs a warning. The read-only fields `source` and `kind` can be used to tell event types apart.

### Host services: Context

`self.context` is a `Context`, with everything v1's `PluginContext` has (see "Legacy API" below), plus:

| Method | Description |
|---|---|
| `await send_message(session, text)` | Push text to a session (goes through send hooks) |
| `await notify_owner(text)` | Message the owner directly, skipping triage, Do Not Disturb, and throttling |
| `await emit(ExternalEvent(...))` / `emit_many` / `get_event` | Submit events to the proactive engine (see "Proactive engine" above) |
| `await llm_generate(prompt, messages=None, system="", route="light")` | One model call without tools; returns text |
| `await decide(state, questions, route="light")` | Ask the decision model structured questions; returns None if no decision model is configured |
| `get_plugins()` | Metadata of loaded plugins |
| `get_platform(adapter_id)` | The OneBot protocol client instance; `await platform.call(action, params)` calls the protocol client's API directly |
| `t(key, **params)` | Text from the plugin's `locales/` in the current language (same as `self.t`) |

### Languages

Plugins can support both Chinese and English ([I18N.md](I18N.md)):

- Put user-facing text in `locales/zh-CN.json` and `locales/en.json` in the plugin folder, and use it in code with `self.t("key", param=value)`. `{param}` in the text is replaced; write literal braces as `{{ }}`. If the current language is missing, Chinese is used, and if that's missing too, the key is returned as is.
- `display_name`, `desc`, and `short_desc` in `metadata.yaml`, and `description`, `hint`, and `labels` in `_conf_schema.json`, can be per-language maps: `{"zh-CN": "默认城市", "en": "Default city"}`. The WebUI shows them in the viewer's language.
- The current language follows the session (`/lang` or the global default); logs always use the global default language.
- See `assets/hello_world/` in the `shiritai-plugin-dev` Skill for an example.

### Runtime conventions

- No handler or hook goes through the plugin's reload lock, so slow tools don't block message handling. After a plugin is reloaded or disabled, the old instance's handlers are no longer called.
- A hook that raises or times out (pipeline hooks 10 seconds, proactive engine hooks 30 seconds) only logs a warning and a `plugin.hook_failed` activity, without affecting the main flow.
- Hooks of the same kind from several plugins run from highest to lowest `priority` (default 0); with equal priority they run in load order, and each plugin sees the object as changed by the one before.
- The plugin list API and the WebUI show each plugin's version, author, commands, tools (with risk), and hooks.

## Legacy API (v1)

`plugin.toml` (`api = 1`) + `Entry(Plugin)` in `main.py`, imported from `shiritaibot.plugin_api`. This version is still supported, but new plugins should use v2.

```python
from shiritaibot.plugin_api import Plugin, schema

class Entry(Plugin):
    async def start(self):
        self.context.register_tool("my_query", "What the tool does", schema({}), self.query, risk="safe")

    async def query(self, ctx, args):
        return {"answer": "hello"}

    async def close(self):
        ...
```

v1's `PluginContext` provides the following (v2's `Context` has them too):

- A separate data folder, settings (`settings`), credential resolution (`resolve_secret`), and logging.
- Emitting events, registering tools, and registering proactive notification channels (`register_notifier`).
- `set_process_env(env)`: add **non-sensitive** environment variables to the shell tool and subagent processes. Different plugins can't set the same variable.
- `add_subagent_note(text)`: append a note to the end of the prompt of newly started non-generic subagents.
- `add_shell_filter(rewrite)`: register a synchronous, side-effect-free `rewrite(command) -> command` that rewrites shell tool commands after approval and before execution. If the filter fails, it's ignored and the original command runs as usual.
- `session_workspace(session)`: return the session's shell working folder.
- `t(key, **params)`: text from the plugin's `locales/` in the current language.

These environment variables, subagent notes, and shell filters are all withdrawn when the plugin is reloaded or disabled. v1 tool calls and plugin reloads are mutually exclusive through a lock.

School adapter plugins can inherit `OneLearnPlugin` exported by `shiritaibot.plugin_api` and only need to add their own adapter process entry point; on exit, the plugin must cancel its own tasks in `close()`.

## Logs

`self.logger` (v1: `self.context.logger`) is a `Logger` from the host's `LogManager`, with its source fixed to `Plugin:<id>` (`插件:<id>` when the global default language is Chinese). It writes to the color terminal, the rotating log file `data/logs/shiritaibot.log`, and **Activity Log → Runtime logs** in the WebUI:

```python
log = self.logger
log.debug("Only shown with --log-level debug")
log.log("Received 3 new emails")             # [LOG][time][Plugin:email]: Received 3 new emails
log.warning("Receiving failed (TimeoutError)", "INBOX")  # extra arguments are tags: [Plugin:email][INBOX]
log.error("Sync error", exc=exc)          # exc writes the stack trace to the log file
```

For more sources, use `get_logger("name")` or `logger.bind("tag")`; `brief(value, limit)` squeezes a string, dict, or list into one readable line. Write human-readable summaries; don't log raw JSON, credentials, or full message bodies.

A manual hot reload first checks the new entry point, tool schemas, and command conflicts, then replaces the old instance; on failure, the original instance is kept.
