Hook 让插件能在 ShiritaiBot 处理流程的特定环节被调用。

## Agent 生命周期 {#agent}

ShiritaiBot 每处理一轮对话（一个 Turn），会经过下面这些环节：

| 装饰器 | 处理函数参数 | 时机与用途 |
| --- | --- | --- |
| `@hook.on_agent_begin()` | `(turn)` | 一轮对话开始 |
| `@hook.on_llm_request()` | `(turn, request)` | 每次请求模型之前，可以修改请求 |
| `@hook.on_llm_response()` | `(turn, response)` | 模型返回之后，可以改写回复文本 |
| `@hook.on_tool_call()` | `(turn, name, arguments)` | 模型调用工具之前，只能观察 |
| `@hook.on_tool_result()` | `(turn, name, arguments, result)` | 工具返回之后，只能观察 |
| `@hook.on_agent_done()` | `(turn, result)` | 一轮对话结束 |

一轮对话里模型可能会调用好几次工具，所以 `on_llm_request` 和 `on_llm_response` 可能被调用多次。

这些 hook 对用户对话和[主动事件](/docs/plugins/proactive)的处理都生效，但不会作用于子代理、事件分诊和记忆整理这些 ShiritaiBot 内部的模型调用。

## 修改模型请求 {#llm-request}

`on_llm_request` 是最常用的 hook。下面的例子在每轮对话的第一次请求中，给用户消息附上当前天气：

```python main.py
from shiritaibot.api import LLMRequest, Plugin, Turn, hook


class WeatherHint(Plugin):
    @hook.on_llm_request()
    async def add_weather(self, turn: Turn, request: LLMRequest):
        if request.step != 0 or turn.background:
            return
        weather = await self.current_weather()
        request.add_context(f"<weather>当前天气：{weather}</weather>")
```

`request` 上可以修改的内容：

| 字段 / 方法 | 说明 |
| --- | --- |
| `system_prompt` | 系统提示词 |
| `messages` | 发给模型的消息列表 |
| `tools` | 提供给模型的工具列表 |
| `add_context(text)` | 在最新一条用户消息后追加一段文字，最常用 |
| `step` | 本轮第几次请求模型，从 0 开始，只读 |
| `route` | 本次请求使用的模型路由，只读 |

这些修改只对这一次请求生效，不会写进对话历史。也就是说，每次请求你都需要重新添加。

## 改写模型回复 {#llm-response}

```python main.py
@hook.on_llm_response()
async def polish(self, turn: Turn, response: LLMResponse):
    response.text = response.text.replace("我是 AI", "我是 ShiritaiBot")
```

改写后的 `response.text` 会写入历史并发送给用户。`response.tool_calls` 只读。

## Turn 和 AgentResult {#turn}

`turn` 描述了当前这轮对话：

| 字段 | 说明 |
| --- | --- |
| `id` | 本轮 ID |
| `session` | 会话 ID |
| `actor` | 发起这轮对话的用户 |
| `owner` / `group` | 是否为主人 / 是否在群聊中 |
| `background` | 是否为后台任务 |
| `mode` | `chat` 或 `agent` |
| `text` | 触发本轮对话的用户输入 |
| `proactive` | 是否为主动事件的处理 |
| `event_id` | 主动事件的接收记录 ID，其他情况为空 |

`on_agent_done` 收到的 `result` 包含 `status`（`completed`、`failed` 或 `cancelled`）、`steps`（请求模型的次数）和 `text`（最终回复）。

## 发送 Hook {#send}

ShiritaiBot 发往 QQ 和管理后台的所有文本，包括模型回复、指令输出和主动通知，都会经过这两个 hook：

| 装饰器 | 处理函数参数 | 时机与用途 |
| --- | --- | --- |
| `@hook.on_decorating_result()` | `(message)` | 消息进入发送队列之前，可以改写或取消 |
| `@hook.after_message_sent()` | `(message)` | 消息进入发送队列之后，在后台调用 |

```python main.py
from shiritaibot.api import OutgoingMessage

@hook.on_decorating_result()
async def sign(self, message: OutgoingMessage):
    if message.session.startswith("web:"):
        message.text += "\n\n—— 来自 ShiritaiBot"

@hook.on_decorating_result()
async def block_words(self, message: OutgoingMessage):
    if "不该说的话" in message.text:
        message.cancel()
```

工具审批的提示消息不经过这两个 hook，插件既不能改写也不能取消。

## 插件加载完成 {#loaded}

`@hook.on_loaded()` 的处理函数只接收 `self`，在 ShiritaiBot 启动完成后，或者插件之后被重载时调用。需要等其他组件（比如 QQ 协议端）都准备好再做的事情，可以放在这里。

## 执行顺序与超时 {#order}

- 多个插件注册了同一个 hook 时，按 `priority` 从高到低执行，例如 `@hook.on_llm_request(priority=10)`。优先级相同时按插件加载顺序执行，后面的插件看到的是前面修改过的对象。
- hook 最多执行 10 秒。hook 出错或超时只会记录一条警告，不会影响正常的处理流程。
- hook 会被频繁调用，请保持轻量。耗时的查询最好提前缓存。
