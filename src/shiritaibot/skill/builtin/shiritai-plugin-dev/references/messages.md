插件处理消息有两种方式：

- **指令**：用户发送 `/指令名 参数` 时触发，适合明确的操作，例如 `/weather 北京`。
- **消息监听**：每条消息都会经过，适合关键词回复、统计之类的功能。

两者都在消息交给模型之前执行。被插件处理掉的消息可以不再交给模型。

## 注册指令 {#command}

```python main.py
from shiritaibot.api import MessageEvent, Plugin, hook


class Weather(Plugin):
    @hook.command("weather", aliases={"天气"})
    async def weather(self, event: MessageEvent, city: str):
        """查询天气：/weather 城市"""
        yield event.plain_result(f"{city}今天晴。")
```

`aliases` 用来设置别名，上面的指令用 `/weather 北京` 和 `/天气 北京` 都能触发。

三引号包裹的的第一行会显示在 `/help` 中，建议在此处写明指令的用法。

## 指令参数 {#arguments}

指令后面的文字按空格切分，依次填进处理函数的参数（`self` 和 `event` 之后的那些）。参数的类型注解决定了怎么转换：

| 注解 | 转换方式 |
| --- | --- |
| `str` 或不写 | 原样传入 |
| `int` / `float` | 转换为数字 |
| `bool` | `true`、`yes`、`on`、`1`、`是`、`开` 为真；`false`、`no`、`off`、`0`、`否`、`关` 为假 |

- 没有默认值的参数是必填的。用户少填参数或者类型对不上时，ShiritaiBot 会自动回复用法，例如 `用法：/weather <city>`。
- 最后一个 `str` 参数会接收剩下的全部文字，所以 `/note 明天 记得 交作业` 里，`async def note(self, event, text: str)` 收到的 `text` 是 `明天 记得 交作业`。想把剩下的参数都收成列表，可以用 `*args`。
- 参数里有空格时可以用引号括起来：`/remind "下午 3 点" 开会`。

```python main.py
@hook.command("add")
async def add(self, event: MessageEvent, a: int, b: int):
    """两数相加：/add 1 2"""
    return event.plain_result(str(a + b))

@hook.command("sum")
async def total(self, event: MessageEvent, *numbers: float):
    """求和：/sum 1 2 3 ..."""
    return event.plain_result(str(sum(numbers)))
```

## 指令组 {#group}

功能比较多时，可以把相关的指令放进一个指令组，用 `/组名 子指令 参数` 调用：

```python main.py
@hook.command_group("todo")
def todo(self):
    """待办事项"""

@todo.command("add")
async def todo_add(self, event: MessageEvent, text: str):
    """添加待办：/todo add 内容"""
    ...

@todo.command("list")
async def todo_list(self, event: MessageEvent):
    """列出待办：/todo list"""
    ...
```

用户只发送 `/todo` 时，会收到这个组下所有子指令的列表。指令组还可以继续嵌套，用 `todo.group("sub")` 创建子组即可。

## 回复消息 {#reply}

处理函数有三种回复方式，效果是一样的，都是在触发指令的会话中发送一条纯文本消息。

```python main.py
# 1. yield，可以多次 yield 发多条消息
yield event.plain_result("第一条")
yield event.plain_result("第二条")

# 2. return 一条结果，也可以直接 return 字符串
return "处理完成"

# 3. 在任意位置主动发送
await event.send("正在查询，请稍候…")
```

指令在后台执行，最长 10 分钟，执行期间机器人照常处理其他消息。指令和回复会记在会话历史里，但不会进入模型的上下文。

处理函数抛出 `PublicError` 时，异常信息会直接回复给用户，适合用来提示"城市不存在"这类预期内的错误。其他异常只会回复一句"插件指令执行失败"，详细堆栈写进日志。

```python main.py
from shiritaibot.api import PublicError

if city not in CITIES:
    raise PublicError(f"没有找到城市「{city}」")
```

目前只支持回复文本，暂不支持图片等富消息。

## 限制使用范围 {#filter}

以下装饰器可以和 `@hook.command` 叠加使用，限定指令在什么情况下生效：

```python main.py
from shiritaibot.api import MessageType, Permission, Platform, hook

@hook.permission(Permission.OWNER)          # 只有主人能用，其他人会收到提示
@hook.message_type(MessageType.PRIVATE)     # 只在私聊中生效
@hook.platform(Platform.QQ)                 # 只在 QQ 上生效
@hook.command("reset")
async def reset(self, event: MessageEvent):
    ...
```

| 装饰器 | 可选值 |
| --- | --- |
| `@hook.permission(...)` | `Permission.OWNER`、`Permission.EVERYONE` |
| `@hook.message_type(...)` | `MessageType.PRIVATE`、`MessageType.GROUP`、`MessageType.ALL` |
| `@hook.platform(...)` | `Platform.QQ`、`Platform.WEB`、`Platform.ALL` |

## 监听消息 {#listener}

不带 `/` 的普通消息可以用 `@hook.message_type` 或 `@hook.regex` 监听：

```python main.py
@hook.regex(r"^ping$")
async def ping(self, event: MessageEvent):
    await event.send("pong")
    event.stop_event()   # 不再交给其他插件和模型

@hook.message_type(MessageType.GROUP)
async def count(self, event: MessageEvent):
    total = await self.get_kv_data(event.group_id, 0)
    await self.put_kv_data(event.group_id, total + 1)
```

- `@hook.message_type(...)` 会收到对应类型的每一条消息。
- `@hook.regex(pattern)` 只在消息文本匹配正则时调用。

调用 `event.stop_event()` 之后，后面的监听器不会再执行，这条消息也不会交给模型。不调用的话，消息会照常交给模型回复。

群里那些既没有 @ 机器人、也不含唤醒词的消息，同样会送到监听器，这时 `event.is_wake` 为 `False`。这类消息本来就不会交给模型，适合拿来做统计或者关键词触发。

多个监听器按 `priority` 从高到低执行（默认 0），可以在装饰器里设置，例如 `@hook.regex(r"^ping$", priority=10)`。单个监听器最多执行 30 秒，处理时间长的工作请放到后台任务里。

## 消息事件 {#event}

处理函数收到的 `event` 是一个 `MessageEvent`，常用字段如下：

| 字段 | 说明 |
| --- | --- |
| `text` | 用户输入的纯文本 |
| `session` | 会话 ID，例如 `private:qq:12345`，可以传给 `self.context.send_message()` 主动发消息 |
| `sender_id` | 发送者 ID |
| `get_sender_name()` | 发送者昵称，没有昵称时返回 ID |
| `platform` | `qq` 或 `web` |
| `group_id` | 群号，私聊时为空字符串 |
| `is_group` / `is_private` | 是否群聊 / 私聊 |
| `is_owner` | 发送者是不是主人 |
| `is_wake` | 这条消息是否会唤醒机器人 |
| `message_id` | 消息 ID |
| `segments` | OneBot 消息段，可以从中取出图片、@ 等内容 |
| `raw` | 协议端发来的原始事件 |

修改这些字段不会影响模型看到的内容。想往模型的输入里加东西，请用 [`on_llm_request`](/docs/plugins/hooks#llm-request)。

## 需要注意的地方 {#notes}

- 插件的指令不能和内置指令（`/help`、`/status`、`/stop` 等）重名，也不能和其他插件的指令重名，否则插件会加载失败。
- 内置指令、"停"、"取消"和工具审批的回复始终由 ShiritaiBot 自己处理，不会发送给插件。
