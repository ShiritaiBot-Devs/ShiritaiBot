<div align="center">

<img src="https://shiritaibot.dev/shiritai.webp" width="180" alt="ShiritaiBot" />

# ShiritaiBot

**「知りたい」—— 住在消息平台里的下一代 Proactive Agent 框架**


![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white)
![Node.js](https://img.shields.io/badge/Node.js-20.19+-339933?logo=node.js&logoColor=white)
![OneBot](https://img.shields.io/badge/OneBot-V11-black)
![Status](https://img.shields.io/badge/status-0.3.1-orange)
[![License](https://img.shields.io/badge/license-AGPL--3.0-blue)](LICENSE)

[官网](https://shiritaibot.dev/) ·
[文档](https://shiritaibot.dev/docs/models) ·
[插件开发](https://shiritaibot.dev/docs/plugins) ·
[问题反馈](https://github.com/ShiritaiBot-Devs/ShiritaiBot/issues)

[English](README.md) · **简体中文**

</div>

## 设计理念

### Everywhere Agent（随身助手）

现在的 Agent 工作流是：人 → 打开电脑 → 输入 prompt → Agent 干活 → 人看结果。但尽管Agent省下了大量写代码的时间，人依旧被绑在了电脑前面，依旧没有跳开人坐在电脑前面工作这个场景。

而ShiritaiBot希望更进一步，能够把人从电脑前解放出来，随时随地用自己最熟悉的社交平台最大化利用生产力。

### Proactive Loop（主动型能力）

ShiritaiBot践行Proactive Agent的设计理念，除了与用户对话的Main Loop之外，还设计了基于事件的Proactive Loop。

事件源在进入新消息后会激活Proactive Engine，并由判断模型判断重要性。不同重要性的事件会遵循不同的处理方式：低重要性事件直接丢弃，中重要性事件整理归档，高重要性事件处理后直接通知用户。

我们希望，ShiritaiBot能够做到“行动在指令之前”：在你输入一段prompt之前，ShiritaiBot已经做好了一切先期工作。

### Tools for Everything（一切皆工具）

ShiritaiBot践行“一切皆工具(和Skill，未来方向)”的理念，希望能够尽可能减少对话中命令和WebUI的使用，用自然语言让ShiritaiBot处理尽可能多的工作，实现自我维护与更新。

### Self-improvement（自我进化）
我们希望ShiritaiBot不止是帮你工作的Agent，更是随时随地陪伴你的虚拟伴侣。随着ShiritaiBot与你的相处，它能够记住你的一点一滴，不断让自己更贴合你的工作与生活习惯。

## 功能一览

- 🔔 **主动性引擎**：事件驱动的「判断 → 分级 → 行动」循环，带免打扰和通知策略
- 🧠 **复合记忆层**：结构化的长期记忆，支持语义检索、证据回溯、冲突确认和主动遗忘
- 📅 **日程管理**：待办、日程、课程、提醒和定时任务，可以根据对话自动整理；课程默认不提醒、不触发 Proactive Loop，月历点击日期后可在浮窗查看
- 💬 **情感陪伴**：高度自定义的人格配置，Chat / Work 模式分流，群聊上下文感知与可选的被动插话（水群）；协议端进程消失时可配置自动拉起
- 🔍 **网页搜索**：支持 Tavily、SerpAPI、DeepSeek Search 等多种网页搜索提供商
- 🤖 **子代理**：在 WebUI「子代理」中添加 ShiritaiBot 内置实例，可选择已配置的文本模型、工具和 Skill，按需开启人工审批。主 Agent 委派后，子代理在独立工作目录运行，完成或需要补充输入时由主动循环汇报。也支持本机 Codex / Claude Code / DeepSeek Harness / Google Antigravity CLI（`agy`）/ 通用 CLI，并可为 CLI 实例单独指定网络代理。
- 🧩 **插件系统**：用插件添加工具或事件源，支持 ZIP / GitHub 安装和热重载
- 📖 **知识库**：在 WebUI 上传或在私聊里发给 Bot，支持 txt、md、pdf、doc(x)、csv、xls(x)、ppt(x)，关键词与 Embedding 混合检索
- 📚 **技能系统**：安装 `.skill` 技能包（WebUI 上传或直接在聊天里发给 Bot），内置 skill-creator，助手可自己编写和改进技能
- 🔌 **MCP 接入**：连接 stdio / Streamable HTTP MCP 服务器，工具统一纳入权限与审批
- 🌐 **多模型路由**：支持 OpenAI Chat Completions / Responses、Anthropic、Gemini 协议，可设置主备模型和按任务路由，也可接入 Jev 等决策模型
- 🖥️ **WebUI 控制台**：模型、连接、权限、记忆、日志、数据看板，都在浏览器里可视化管理
- 🌏 **多语言**：支持简体中文（默认）和英文。可在 WebUI 设置所有会话的机器人语言，用 `/lang` 单独切换某个会话，管理界面也能在顶栏切换语言；管理界面已全部翻译，机器人消息的英文翻译仍在进行中

## 快速部署

可以使用 [一键部署脚本](scripts/README.md)，也可以采用下方的手动部署方式。Linux 上使用 Docker 时，在源码目录运行 `bash scripts/deploy-docker.sh`。

部署后可连接 OneBot、[QQ 官方机器人](docs/QQ-OFFICIAL.md)或[飞书应用机器人](docs/FEISHU.md)。

手动部署需要 Python 3.12+、[uv](https://docs.astral.sh/uv/) 和 Node.js 20.19+。

```bash
git clone https://github.com/ShiritaiBot-Devs/ShiritaiBot.git
cd ShiritaiBot
uv sync
npm --prefix web ci
npm --prefix web run build
uv run shiritaibot run
```

首次启动会根据 Core 锁文件和已安装插件的依赖声明，自动准备 `<home>/runtime/` 下的独立运行环境。仓库的 `.venv` 用于启动和开发，后续在仓库执行 `uv sync` 不会清掉运行环境中的插件依赖。依赖组合变化后按提示重启生效；声明格式、冲突处理和可选依赖见 [插件依赖管理](docs/PLUGINS.md#插件依赖管理)。

启动后，点击终端里 `Uvicorn running on` 后面的完整链接即可自动登录。如果直接访问 <http://127.0.0.1:8765>，需要先在新终端获取令牌：

```bash
uv run shiritaibot token      # 源码启动
shiritaibot token             # 一键安装（macOS / Linux）
```

> 服务默认只监听本机。远程访问建议使用 SSH 端口转发，如果要暴露到公网，请在启动命令后加上 `--allowed-host <域名>`。

在 WebUI 点击「检查更新」即可更新并重启（需通过 `shiritaibot run` 启动）。一键部署继续支持选择发布版本或最新开发版；手动 Git 部署更新到当前分支所跟踪的远程分支最新提交。Git 更新要求工作区干净，包含未跟踪文件在内的本地修改需先提交或移走；有本地独有提交、分支分叉、detached HEAD 或未设置 upstream 时会停止并提示。被 Git 忽略的数据、虚拟环境和独立插件会保留。

两种部署都会构建 WebUI、同步 Python 依赖，并在重启前用新版代码检查当前配置；配置不兼容时取消重启并尝试恢复旧版，具体原因见更新日志。

### 使用 ChatGPT 套餐

在 WebUI 的「模型与服务 → 新增提供商」选 **OpenAI Subscription** 并保存，再点 **Continue with ChatGPT**。在打开 WebUI 的电脑运行下载的 `shiritaibot-chatgpt-login.py`（Windows 可用 `py shiritaibot-chatgpt-login.py`，macOS/Linux 可用 `python3 shiritaibot-chatgpt-login.py`）。用符合条件的 ChatGPT Plus / Pro 账号授权套餐用量，将助手显示的回调地址粘贴回 WebUI 完成连接。

Bot 跑在 Docker 或远程服务器时，仍在操作浏览器的电脑运行授权助手。公开访问 WebUI 时请使用 HTTPS。连接后可获取该账号的模型列表，只有路由到这个提供商的请求才使用部署者的套餐额度。卡片显示本实例的请求和 Token 用量，并链接到 ChatGPT 用量设置查询官方用量。 [OpenAI 自托管接入说明](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms)

## 消息发送

在 WebUI「设置 → 通用设定 → 消息发送」可以配置 OneBot 长消息折叠，以及 `/help` 的文字、亮色图片或暗色图片样式。图片菜单优先使用系统中文字体，缺少时使用随程序附带的 Noto Sans SC 2.004，可离线渲染。飞书发送图片需要应用开通 `im:resource` 权限。 [字体来源](https://github.com/notofonts/noto-cjk/blob/Sans2.004/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf) · [SIL Open Font License 1.1](src/shiritaibot/agent/fonts/OFL-NotoSansSC.txt)

同页的「`/help` 菜单分区」可设置内置功能分区和已加载插件分区的显示及顺序。文字和图片共用这份设置，仍按发起者的会话权限筛选命令。未配置的分区按默认顺序追加。

## 许可证

本项目使用 [GNU Affero General Public License v3.0](LICENSE)（AGPL-3.0-or-later）开源。

## 参与贡献

欢迎参与ShiritaiBot的开发！

新增面向用户的文字时，请遵循[国际化指南](docs/I18N.md)（英文），以便翻译。

[![贡献者](https://contrib.rocks/image?repo=ShiritaiBot-Devs/ShiritaiBot)](https://github.com/ShiritaiBot-Devs/ShiritaiBot/graphs/contributors)

加入ShiritaiBot Issue反馈群，参与ShiritaiBot的后续工作：1134225815

## Also try...

- [NapNeko/NapcatQQ](https://github.com/NapNeko/NapCatQQ) - 绝赞Onebot QQ协议端接入实现
- [AstrBotDevs/AstrBot](https://github.com/AstrBotDevs/AstrBot) - 经典的可扩展性Bot框架，ShiritaiBot参考了AstrBot的插件API设计
    - [lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory](https://github.com/lxfight-s-Astrbot-Plugins/astrbot_plugin_livingmemory) - 经典的AstrBot记忆层插件

> 应该说2025年3月，在自己的电脑上试着用AstrBot部署自己的Bot，慢慢尝试AstrBot的插件开发是我在开发路上走到现在的起点。
> 
> Astrbot教会了我很多：开源精神，架构设计，社区协作，以及有一个想法就要敢于尝试去实现的勇气。
> 
> —— M42

- [Mai-with-u/MaiBot](https://github.com/Mai-with-u/MaiBot) - 用于构建你的数字生命的Bot框架，ShiritaiBot参考了MaiBot的记忆层设计
- [Einsia/OpenChronicle](https://github.com/Einsia/OpenChronicle) - 开创性的主动记忆设计
- [XCM42-Orion/AutoWater](https://github.com/XCM42-Orion/AutoWater) - 轻量化的整活水群框架，！？轻量化的整整的化量轻？！


