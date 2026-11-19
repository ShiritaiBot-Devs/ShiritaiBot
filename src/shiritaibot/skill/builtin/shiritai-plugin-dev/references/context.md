`self.context` 是 ShiritaiBot 提供给插件的服务入口。这一页按用途整理了常用的方法。

## 发送消息 {#send}

```python
await self.context.send_message(session, "提醒：该喝水了")
await self.context.notify_owner("备份失败，请检查磁盘空间")
```

- `send_message(session, text)` 向指定会话发送文本。`session` 可以从 `MessageEvent.session` 或 `ToolContext.session` 拿到，记下来之后随时可以用。消息会经过[发送 Hook](/docs/plugins/hooks#send)。
- `notify_owner(text)` 直接发给主人，不经过主动性引擎的分诊、免打扰和节流。

## 调用模型 {#llm}

插件可以借用 ShiritaiBot 配置好的模型做一些简单的文本处理：

```python
summary = await self.context.llm_generate(
    f"用一句话总结下面的公告：\n{notice}",
    system="你是一个简洁的摘要助手。",
)
```

| 参数 | 说明 |
| --- | --- |
| `prompt` | 用户消息 |
| `messages` | 可选，之前的对话消息，`prompt` 会追加在最后 |
| `system` | 系统提示词 |
| `route` | 使用哪个模型路由，默认 `light`，也就是「主动事件分诊与摘要模型」 |

这是一次不带工具的普通调用，返回模型输出的文本。

如果配置了判断模型，还可以用 `await self.context.decide(state, questions)` 提出结构化的选择或打分问题。没有配置判断模型时返回 `None`，这时可以退回 `llm_generate`。

## 存储数据 {#storage}

小块的数据用 KV 存储，每个插件各自独立，值需要能被 JSON 序列化：

```python
count = await self.get_kv_data("count", 0)
await self.put_kv_data("count", count + 1)
await self.delete_kv_data("count")
```

文件和较大的数据放在 `self.data_dir`，也就是 `data/plugin_data/<插件 ID>/`。这个目录在加载插件时会自动创建，移除插件时也不会被删除，重新安装后数据还在。

用户需要查看或修改的设置请放进[插件配置](/docs/plugins/config)，不要放在 KV 存储里。

## 日志 {#logging}

`self.logger` 输出的日志会显示在终端、写入 `data/logs/shiritaibot.log`，也会出现在管理后台「日志 → 运行日志」中，自动带上插件 ID：

```python
self.logger.debug("只在 --log-level debug 时显示")
self.logger.log("收到 3 封新邮件")
self.logger.warning("收件失败（TimeoutError）", "INBOX")   # 第二个参数是额外的标签
self.logger.error("同步出错", exc=exc)                     # 堆栈会写进日志文件
```

日志请尽量写成人/模型能看懂的一句话，不要直接打印整段 JSON、正文或者凭据。需要把字典、长字符串压成一行时，可以用 `brief(value, limit)`：

```python
from shiritaibot.api import brief

self.logger.log(f"收到请求 {brief(payload, 80)}")
```

## 协议端 {#platform}

需要调用 OneBot 协议端的 API（比如获取群成员列表）时：

```python
platform = self.context.get_platform(event.adapter)
if platform and platform.status == "connected":
    members = await platform.call("get_group_member_list", {"group_id": int(event.group_id)})
```

`adapter` 是协议端实例的 ID，可以从 `MessageEvent.adapter` 拿到。`platform.self_id` 是机器人自己的 QQ 号。

`self.context.get_plugins()` 返回所有已加载插件的信息。

## Shell 与子代理 {#shell}

ShiritaiBot 的 shell 工具和子代理（Codex、Claude Code）会启动子进程。插件可以影响这些子进程：

| 方法 | 说明 |
| --- | --- |
| `set_process_env(env)` | 给子进程添加环境变量，例如 git 的提交身份 |
| `add_shell_filter(rewrite)` | 在 shell 命令获批之后、执行之前改写命令 |
| `add_subagent_note(text)` | 在新启动的子代理的提示词末尾追加一段说明 |
| `session_workspace(session)` | 返回某个会话的 shell 工作目录 |

内置的 GitHub 插件就用到了这些方法：通过环境变量让模型用 bot 账号的身份提交代码，通过 shell 过滤器给提交自动加上 Co-authored-by。

使用时请注意：

- 子进程由模型控制，**不要通过 `set_process_env` 传递任何凭据，例如API Key**。不同插件不能设置同名变量。
- `rewrite(command)` 必须是同步、快速、没有副作用的函数，只做小范围、可以预期的修改。它出错时会被忽略，原命令照常执行。
- 以上设置都会在插件重载或停用时自动撤销。

## 旧版插件接口 {#legacy}

早期的插件使用 `plugin.toml` 作为清单，入口是 `main.py` 中的 `Entry` 类，从 `shiritaibot.plugin_api` 导入，生命周期方法是 `start()` / `close()`：

```python main.py
from shiritaibot.plugin_api import Plugin, schema


class Entry(Plugin):
    async def start(self):
        self.context.register_tool("my_query", "工具说明", schema({}), self.query, risk="safe")

    async def query(self, ctx, args):
        return {"answer": "hello"}
```

这种写法仍然可以正常加载，但不支持指令、消息监听和各类 hook。新插件请使用本章介绍的写法。同一个目录里两种清单都存在时，按新版处理。

未来的几个版本中我们会移除对旧版API的支持，因此请及时将插件更新到新版API。把这件事情交给你的ShiritaiBot试一下？
