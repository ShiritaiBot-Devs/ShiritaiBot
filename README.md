<div align="center">

<img src="https://shiritaibot.dev/shiritai.webp" width="180" alt="ShiritaiBot" />

# ShiritaiBot

**知りたい (*shiritai*, "I want to know"): a next-generation proactive agent framework that lives in your messaging apps**


![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![Node.js](https://img.shields.io/badge/Node.js-20.19+-339933?logo=node.js&logoColor=white)
![OneBot](https://img.shields.io/badge/OneBot-V11-black)
![Status](https://img.shields.io/badge/status-0.3.1-orange)
[![License](https://img.shields.io/badge/license-AGPL--3.0-blue)](LICENSE)

[Website](https://shiritaibot.dev/) ·
[Docs](https://shiritaibot.dev/docs/models) ·
[Plugin development](https://shiritaibot.dev/docs/plugins) ·
[Report an issue](https://github.com/ShiritaiBot-Devs/ShiritaiBot/issues)

**English** · [简体中文](README.zh-CN.md)

</div>

> **Language note:** ShiritaiBot uses Simplified Chinese by default. To switch the bot to English, set **Settings → General → Language** [设置 → 通用设定 → 语言] in the WebUI, or send `/lang en` in a chat. The WebUI has its own language switch in the top bar. English translation is still in progress, so some WebUI pages, bot messages, and most documents under [`docs/`](docs/) are still in Chinese. Where this README mentions a WebUI menu, the Chinese label is shown in brackets so you can find it.

## Why ShiritaiBot?

Most AI agents still work like this: you sit down at your computer, type a prompt, wait for the agent, then check the result. Agents save a lot of time, but you are still tied to your desk.

ShiritaiBot takes a different approach. It runs inside the chat apps you already use and works for you in the background, so you can stay productive wherever you are. Four ideas shape the project:

| Principle | What it means |
|---|---|
| **Agent everywhere** | You talk to ShiritaiBot in your everyday messaging app, not in a terminal or a web page. The WebUI is only for setup and monitoring. |
| **Proactive by design** | Alongside the normal conversation loop, an event-driven **Proactive Loop** watches for things that matter to you and acts *before* you ask. |
| **Everything is a tool** | Instead of making you learn commands or click through settings, ShiritaiBot exposes its abilities as tools (and, in the future, Skills). You ask in plain language and it does the work, including maintaining and updating itself. |
| **Grows with you** | ShiritaiBot is meant to be a companion as well as an assistant. It remembers the details you share and gradually adapts to how you work and live. |

## How it works

ShiritaiBot runs two loops side by side:

```
                        ┌──────────── Main Loop ────────────┐
 You ⇄ QQ / Feishu ⇄ Adapter ⇄ Agent (LLM + tools) ⇄ memory, schedule, skills…
                                     ▲
 Event sources ──▶ Proactive Engine ─┘
 (email, GitHub, reminders,    rates each event:
  subagents, plugins…)          low → drop · medium → archive · high → act and notify you
```

- **Main Loop**: your normal back-and-forth conversation with the agent.
- **Proactive Loop**: when an event source (a new email, a reminder coming due, a finished subagent task, …) produces something new, the **Proactive Engine** wakes up and a judge model rates how important it is. Low-importance events are dropped, medium ones are filed away, and high-importance ones are handled and then sent to you.

The goal is simple: by the time you think to ask, ShiritaiBot has already done the groundwork.

### Key terms

| Term | Meaning |
|---|---|
| **OneBot V11** | An open protocol for chat bots, widely used with QQ. You run a protocol client such as [NapCatQQ](https://github.com/NapNeko/NapCatQQ) that signs in to QQ, and ShiritaiBot connects to it. |
| **QQ** | Tencent's messaging app, popular in China. ShiritaiBot can connect through OneBot or through the [official QQ Bot platform](docs/QQ-OFFICIAL.md). |
| **Feishu / Lark** | ByteDance's workplace messaging suite (Lark is the international edition). See [Feishu setup](docs/FEISHU.md). |
| **Owner** | Each instance serves one person, the owner. Proactive features, scheduling, subagents, and local machine access are for the owner only. |
| **Chat mode / Agent mode** | *Chat* mode is conversation only, with no tools. *Agent* (Work) mode has full tool access. The owner's private chat uses Agent mode; group chats default to Chat mode. |

## Features

**Personal assistant**

- 🔔 **Proactive engine**: an event-driven *triage → rate → act* cycle with Do Not Disturb hours and configurable notification rules.
- 🧠 **Layered memory**: structured long-term memory with semantic search, links back to the original evidence, conflict confirmation, and deliberate forgetting.
- 📅 **Scheduling**: to-dos, calendar events, class timetables, reminders, and scheduled tasks, which can be organized automatically from your conversations. Classes don't send reminders or trigger the Proactive Loop by default. Click a date on the monthly calendar to see them in a pop-up.
- 💬 **Companionship**: a highly customizable persona, separate Chat and Work modes, awareness of group-chat context, and optional unprompted chiming in to group conversations. If the protocol client's process dies, ShiritaiBot can be set to restart it automatically.

**Agent capabilities**

- 🔍 **Web search**: Tavily, SerpAPI, DeepSeek Search, and other providers.
- 🤖 **Subagents**: add built-in ShiritaiBot subagents on the WebUI's **Subagents** page [子代理]. Each one can use any text model, tools, and Skills you've configured, with optional human approval. When the main agent delegates a task, the subagent runs in its own working directory and reports back through the Proactive Loop when it finishes or needs more input. Local Codex, Claude Code, DeepSeek Harness, Google Antigravity CLI (`agy`), and generic CLI tools are also supported, and each CLI instance can use its own network proxy.
- 📖 **Knowledge base**: upload files in the WebUI or send them to the bot in a private chat. Supports txt, md, pdf, doc(x), csv, xls(x), and ppt(x), with hybrid keyword and embedding search.
- 🌐 **Multi-model routing**: supports the OpenAI Chat Completions and Responses APIs, Anthropic, and Gemini. Set primary and fallback models, route different tasks to different models, and plug in lightweight decision models such as Jev.

**Extensibility**

- 🧩 **Plugins**: add new tools or event sources. Install from a ZIP file or GitHub, with hot reload.
- 📚 **Skills**: install `.skill` packages by uploading them in the WebUI or sending them in chat. The built-in `skill-creator` lets the assistant write and improve its own Skills.
- 🔌 **MCP**: connect stdio or Streamable HTTP MCP servers. Their tools follow the same permission and approval rules as built-in tools.

**Management**

- 🖥️ **WebUI console**: manage models, connections, permissions, memory, logs, and usage dashboards from your browser.
- 🌏 **Languages**: Simplified Chinese (the default) and English. Set the bot's language for every chat in the WebUI, switch a single chat with `/lang`, and switch the WebUI itself from its top bar. English translation is still in progress.

## Quick start

### Option 1: One-click install

Use the [one-click install script](scripts/README.md). On Linux with Docker, run this from the source directory instead:

```bash
bash scripts/deploy-docker.sh
```

### Option 2: Manual install

You'll need Python 3.12+, [uv](https://docs.astral.sh/uv/), and Node.js 20.19+.

```bash
git clone https://github.com/ShiritaiBot-Devs/ShiritaiBot.git
cd ShiritaiBot
uv sync
npm --prefix web ci
npm --prefix web run build
uv run shiritaibot run
```

On first launch, ShiritaiBot builds an isolated runtime environment under `<home>/runtime/`, based on the Core lock file and the dependencies declared by your installed plugins. The repository's `.venv` is only for starting and developing the app, so running `uv sync` in the repo later won't remove plugin dependencies from the runtime environment. If the set of dependencies changes, restart when prompted. For the declaration format, conflict handling, and optional dependencies, see [Plugin dependency management](docs/PLUGINS.md#插件依赖管理).

### Connect a chat platform

Once it's running, connect ShiritaiBot to OneBot (QQ), the [official QQ Bot platform](docs/QQ-OFFICIAL.md), or a [Feishu / Lark app bot](docs/FEISHU.md).

### Sign in to the WebUI

In the terminal output, click the full link after `Uvicorn running on` to sign in automatically. If you open <http://127.0.0.1:8765> directly instead, get a token from a new terminal first:

```bash
uv run shiritaibot token      # if you run from source
```

```bash
shiritaibot token             # if you used the one-click install (macOS / Linux)
```

> By default, the server only listens on localhost. For remote access, SSH port forwarding is recommended. To expose it under a public domain, add `--allowed-host <domain>` to the start command.

### Updating

Click **Check for updates** [检查更新] in the WebUI to update and restart. This only works if you started ShiritaiBot with `shiritaibot run`.

- **One-click installs** let you choose a release version or the latest development build.
- **Manual Git installs** update to the latest commit on the remote branch your current branch tracks. Your working tree must be clean, so commit or move any local changes first, including untracked files. The update stops with an explanation if you have local-only commits, a diverged branch, a detached HEAD, or no upstream branch. Git-ignored data, virtual environments, and standalone plugins are kept.

Both methods rebuild the WebUI, sync Python dependencies, and check your current configuration against the new code before restarting. If the configuration isn't compatible, the restart is cancelled and ShiritaiBot tries to roll back to the previous version. The update log explains why.

## Use your ChatGPT subscription

You can power ShiritaiBot with an eligible ChatGPT Plus or Pro subscription instead of an API key.

1. In the WebUI, go to **Models & Services → Add provider** [模型与服务 → 新增提供商], choose **OpenAI Subscription**, and save.
2. Click **Continue with ChatGPT**. This downloads a small helper script.
3. On the computer you're using to view the WebUI, run the helper:
   - Windows: `py shiritaibot-chatgpt-login.py`
   - macOS / Linux: `python3 shiritaibot-chatgpt-login.py`
4. Sign in with your ChatGPT account to authorize it, then paste the callback URL the helper shows back into the WebUI.

Good to know:

- If the bot runs in Docker or on a remote server, still run the helper on the computer with the browser.
- If your WebUI is publicly reachable, serve it over HTTPS.
- Once connected, you can fetch the account's model list. Only requests routed to this provider use the subscription's quota.
- The provider card shows this instance's request and token usage, and links to ChatGPT's usage settings for the official numbers.

See OpenAI's [guide to self-hosted integrations](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms) for details.

## Message delivery and the `/help` menu

Under **Settings → General → Message Sending** [设置 → 通用设定 → 消息发送] in the WebUI, you can:

- **Fold long messages** on OneBot.
- **Choose the `/help` style**: plain text, a light image, or a dark image. Image menus use a system CJK font when one is installed, and otherwise fall back to the bundled Noto Sans SC 2.004, so they render fully offline. To send images on Feishu, the app needs the `im:resource` permission. ([Font source](https://github.com/notofonts/noto-cjk/blob/Sans2.004/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf) · [SIL Open Font License 1.1](src/shiritaibot/agent/fonts/OFL-NotoSansSC.txt))
- **Arrange the `/help` menu sections** [`/help` 菜单分区]: show, hide, and reorder the built-in sections and the sections added by loaded plugins. The text and image menus share this setting, and commands are still filtered by the permissions of whoever opens the menu. Any section you haven't configured is added at the end in its default order.

## License

ShiritaiBot is open source under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0-or-later).

## Contributing

Contributions are welcome!

When you add text that users will read, follow the [internationalization guide](docs/I18N.md) so it can be translated.

[![Contributors](https://contrib.rocks/image?repo=ShiritaiBot-Devs/ShiritaiBot)](https://github.com/ShiritaiBot-Devs/ShiritaiBot/graphs/contributors)

To help shape what comes next, join the ShiritaiBot feedback group on QQ: **1134225815**.

## Acknowledgements and related projects

- [NapNeko/NapCatQQ](https://github.com/NapNeko/NapCatQQ): an excellent OneBot protocol client for QQ.
- [AstrBotDevs/AstrBot](https://github.com/AstrBotDevs/AstrBot): a classic, highly extensible bot framework. ShiritaiBot's plugin API is modeled on AstrBot's.
    - [lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory): a well-known memory-layer plugin for AstrBot.

> In March 2025, I tried deploying my own bot with AstrBot on my own computer, and slowly started writing AstrBot plugins. Looking back, that was where my journey as a developer began.
>
> AstrBot taught me a lot: the spirit of open source, architecture design, community collaboration, and the courage to try building an idea as soon as you have one.
>
> — M42

- [Mai-with-u/MaiBot](https://github.com/Mai-with-u/MaiBot): a bot framework for building your own digital companion. ShiritaiBot's memory layer draws on MaiBot's design.
- [Einsia/OpenChronicle](https://github.com/Einsia/OpenChronicle): a pioneering proactive-memory design.
- [XCM42-Orion/AutoWater](https://github.com/XCM42-Orion/AutoWater): a lightweight framework for goofing around in group chats. !?Lightweight thgiewthgiL?!
