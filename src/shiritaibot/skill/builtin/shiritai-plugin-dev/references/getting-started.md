插件可以给 ShiritaiBot 增加指令、给模型增加工具、把邮件和通知等外部事件送进主动性引擎，也可以在模型请求和消息发送的各个环节进行处理。

本章会教你从零写出一个插件。开始之前，你需要：

- 会写 Python，了解 `async` / `await` 的基本用法
- 已经按照[快速开始](/docs/quickstart)把 ShiritaiBot 跑起来，并配置好了至少一个模型

ShiritaiBot的插件API参考了[AstrBot](https://github.com/AstrBotDevs/AstrBot)的插件API，因此基本结构是相通的。如果你曾经有AstrBot的插件开发经历，迁移到ShiritaiBot会更简单。

## 插件目录 {#location}

ShiritaiBot 启动时会扫描 `<数据目录>/plugins/` 下的每个子目录，一个子目录就是一个插件。数据目录默认是启动 ShiritaiBot 时所在目录下的 `data`，启动时用 `--home` 指定过的话以指定的目录为准。

目录名就是插件 ID，只能用小写字母、数字和下划线，例如 `hello_world`、`weather_cn`。插件 ID 会出现在配置文件、日志和数据目录里，定下来之后最好不要更改。

我们推荐大家使用 `srtb_plugin_<插件名>`的格式来命名自己的插件，例如 `srtb_plugin_wechat`。

一个插件最少需要两个文件：

```text 目录结构
data/plugins/hello_world/
├── metadata.yaml       # 插件信息（必需）
├── main.py             # 插件入口（必需）
└── _conf_schema.json   # 配置项定义（可选）
```

## 编写 metadata.yaml {#metadata}

`metadata.yaml` 告诉 ShiritaiBot 这个插件的元数据，包括作者、名称等。

```yaml metadata.yaml
name: hello_world
display_name: Hello World
desc: 我的第一个 ShiritaiBot 插件
short_desc: 打个招呼
version: 1.0.0
author: your-name
repo: https://github.com/your-name/hello_world
```

| 字段 | 说明 |
| --- | --- |
| `name` | 插件名。插件 ID 始终是目录名，这个字段只在没有 `display_name` 时用来展示 |
| `display_name` | 插件列表中显示的名称 |
| `desc` | 插件说明 |
| `short_desc` | 一句话简介 |
| `version` / `author` / `repo` | 版本、作者、仓库地址，会显示在插件详情里 |
| `support_platforms` | 可选，支持的平台，只能写 `qq`、`web`，仅用于展示 |
| `shiritaibot_version` | 可选，要求的 ShiritaiBot 版本范围，例如 `">=0.2,<1"`。版本不满足时插件会显示为加载失败 |

## 编写 main.py {#main}

在 `main.py` 里定义一个继承 `Plugin` 的类，类名随意，但一个 `main.py` 里只能有一个插件类：

```python main.py
from shiritaibot.api import MessageEvent, Plugin, hook


class HelloWorld(Plugin):
    async def initialize(self):
        self.logger.log("Hello World 插件已启动")

    @hook.command("hello")
    async def hello(self, event: MessageEvent, name: str = ""):
        """打个招呼"""
        yield event.plain_result(f"你好，{name or event.get_sender_name()}！")
```

这段代码做了两件事：

- `initialize()` 会在插件加载时被调用，这里只打了一行日志。
- `@hook.command("hello")` 注册了一个 `/hello` 指令。用户发送 `/hello` 或 `/hello 小明` 时，ShiritaiBot 会调用 `hello()`，`name` 参数会自动从消息里取出来。函数的 docstring 会显示在 `/help` 里。

插件需要的所有东西都从 `shiritaibot.api` 导入。ShiritaiBot 的其他模块属于内部实现，随时可能调整，请不要直接导入。

## 加载和调试 {#debug}

1. 把插件目录放进 `data/plugins/`。
2. 打开管理后台的「插件」页面，点击「重载插件」。ShiritaiBot 会重新扫描插件目录，你的插件应该出现在列表里。
3. 新插件默认是停用的。点开插件，勾选「启用插件」并保存。
4. 在对话页或 QQ 里发送 `/hello`，看看机器人有没有回复你。

修改代码之后不需要重启 ShiritaiBot，在插件设置里点「重载当前插件」就能用上新代码。如果新代码有错误，旧版本会继续运行，错误信息显示在插件列表中，修好后再重载即可。

插件的日志（`self.logger`）会显示在终端，也能在管理后台「日志 → 运行日志」里看到，排查问题时先看这里。

插件目录下的其他 `.py` 文件可以用相对导入引用，例如 `from .utils import parse_city`，重载时它们也会一起更新。

## 生命周期 {#lifecycle}

插件类有两个可以重写的生命周期方法：

```python main.py
import asyncio

from shiritaibot.api import Context, Plugin, PluginSettings


class MyPlugin(Plugin):
    def __init__(self, context: Context, config: PluginSettings):
        super().__init__(context, config)
        self.task = None

    async def initialize(self):
        # 插件加载时调用，适合建立连接、启动后台任务
        self.task = asyncio.create_task(self.poll())

    async def terminate(self):
        # 插件停用、重载、移除或 ShiritaiBot 退出时调用
        if self.task:
            self.task.cancel()

    async def poll(self):
        ...
```

保存配置、点击重载都会让插件重新加载：旧实例的 `terminate()` 先被调用，然后创建一个新实例。所以你在 `initialize()` 里启动的后台任务、打开的连接，一定要在 `terminate()` 里关掉，否则每重载一次就会多出一份。

`__init__` 可以不写。如果写了，记得调用 `super().__init__()`。插件不需要配置时，`__init__` 也可以只接收 `context` 一个参数。

插件实例上常用的属性：

| 属性 | 说明 |
| --- | --- |
| `self.config` | 插件配置，见[插件配置](/docs/plugins/config) |
| `self.context` | 宿主提供的各种服务，见[宿主服务](/docs/plugins/context) |
| `self.logger` | 日志记录器 |
| `self.data_dir` | 插件专属的数据目录 `data/plugin_data/<插件 ID>/`，移除插件时不会被删除 |
| `self.name` | 插件 ID |

## 第三方依赖 {#dependencies}

ShiritaiBot 不会自动安装插件的依赖。如果插件用到了第三方库，请在插件目录放一个 `requirements.txt`，并在 README 里写清楚安装方法，例如在 ShiritaiBot 目录下执行：

```bash
uv pip install -r data/plugins/hello_world/requirements.txt
```

通过管理后台更新 ShiritaiBot 时，这样装进去的依赖会保留下来；但如果你在命令行手动执行 `uv sync`，它们会被清掉，需要加上 `--inexact` 参数。

ShiritaiBot 自带了 `httpx`、`pydantic`、`pyyaml` 等常用库，能用自带的就尽量不要再引入新依赖。未来ShiritaiBot可能会自动检查插件依赖，但短期内请大家暂时这么写。

## 分发插件 {#publish}

写好的插件可以通过以下几种方式安装到其他人的 ShiritaiBot：

- 上传到公开的 GitHub 仓库。用户在「插件 → 安装插件」中填写仓库地址，ShiritaiBot 会下载默认分支。
- 打包成 ZIP，用户在同一个位置上传。ZIP 里可以直接是插件文件，也可以多套一层目录。
- 手动把目录复制到 `data/plugins/`，然后点击「重载插件」。

安装时用户需要填写插件 ID，也就是安装后的目录名。安装后的插件同样默认停用，需要用户填写配置并启用。

## 开发原则 {#principles}

插件和 ShiritaiBot 运行在同一个进程里，拥有和 ShiritaiBot 一样的系统权限，并没有沙箱。写插件时请注意：

- **不要阻塞事件循环。** 网络请求请用 `httpx.AsyncClient` 等异步库，无法避免的同步调用放进 `asyncio.to_thread()`，一定不要使用 `time.sleep()` 等同步调用，阻塞整个Bot的运行。你的 `time.sleep()` 卡住的是整个Bot而不仅仅是插件进程。
- **收拾好自己的东西。** `initialize()` 里启动的任务和连接，在 `terminate()` 里关闭。
- **保护好用户的凭据。** 密码、token 用配置项的 `secret` 字段保存，不要写进日志，也不要原样返回给模型。
- **如实声明工具的风险等级。** 会写入数据或产生外部影响的工具，不要标成 `safe`，详见 [LLM 工具](/docs/plugins/tools#risk)。
- **把外部内容当作不可信资料。** 邮件、网页、Issue 里的文字可能包含针对模型的指令，交给模型前要想清楚。

同样，ShiritaiBot Devs也不保证从网络上安装的插件的安全性。

## 接下来 {#next}

- [指令与消息](/docs/plugins/messages)：注册指令、监听群聊消息
- [LLM 工具](/docs/plugins/tools)：给模型一件新工具
- [插件配置](/docs/plugins/config)：让用户在管理后台填写配置
- [Agent 与发送 Hook](/docs/plugins/hooks)：修改模型请求和发出的消息
- [主动事件](/docs/plugins/proactive)：把外部变化送进主动性引擎
- [宿主服务](/docs/plugins/context)：发送消息、调用模型、存储数据

完整的示例插件在 [examples/plugins/hello_world](https://github.com/ShiritaiBot-Devs/ShiritaiBot/tree/main/examples/plugins/hello_world)，用到了本章介绍的大部分功能，可以复制下来参考。
