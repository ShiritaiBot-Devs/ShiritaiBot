ShiritaiBot 不只在用户说话时才工作。新邮件、作业截止、仓库有了新 Issue……这些外部变化都可以作为事件交给主动性引擎（Proactive Engine）。引擎会先判断事件有多重要，再决定是直接丢掉、整理一下，还是处理完后通知主人。

插件在这里能做两件事：作为**事件源**提交事件，以及通过 **hook** 调整引擎的判断。

## 提交事件 {#emit}

用 `self.context.emit()` 提交一个 `ExternalEvent`：

```python main.py
from shiritaibot.api import Actor, ExternalEvent

await self.context.emit(
    ExternalEvent(
        source="weather",
        kind="weather.alert",
        dedup_key=f"alert:{alert['id']}",
        title=f"{city}发布暴雨预警",
        payload={"city": city, "level": alert["level"], "text": alert["text"]},
        urgency="high",
    )
)
```

| 字段 | 说明 |
| --- | --- |
| `source` | 事件来源，一般就用插件 ID |
| `kind` | 事件类型，例如 `email.received`、`weather.alert` |
| `dedup_key` | 去重键。同一 `source` 下相同的 `dedup_key` 只会处理一次，必填 |
| `title` | 一句话标题 |
| `payload` | 事件内容，任意可以 JSON 序列化的字典 |
| `urgency` | 紧急程度：`low`、`normal`、`high`、`critical`，默认 `normal`。只作参考，最终的重要性由引擎判断 |
| `occurred_at` | 事件发生时间，需要带时区，默认为当前时间 |
| `actors` | 事件涉及的人，例如邮件的发件人 |

`dedup_key` 很重要。轮询类插件经常会重复看到同一条数据，只要用稳定的 ID 作为去重键，就不用担心同一件事通知好几遍。

`actors` 用来告诉 ShiritaiBot 事件里有谁，比如 `Actor(identity="email:someone@example.com", name="张三", role="sender")`。插件不需要自己写记忆，事件处理完之后，由 ShiritaiBot 决定要不要把这些人记下来。

`emit()` 返回一个接收记录 ID，提交成功不代表已经处理完。需要查询处理结果时，可以调用 `await self.context.get_event(receipt_id)`。一次提交多个事件可以用 `emit_many()`。

## 写一个轮询事件源 {#poll}

大部分事件源都是定时检查一次有没有新东西。在 `initialize()` 里启动一个后台任务，在 `terminate()` 里取消它：

```python main.py
import asyncio

import httpx

from shiritaibot.api import ExternalEvent, Plugin


class WeatherAlert(Plugin):
    async def initialize(self):
        self.task = asyncio.create_task(self.poll())

    async def terminate(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    async def poll(self):
        async with httpx.AsyncClient(timeout=20) as client:
            while True:
                try:
                    await self.check(client)
                except Exception as exc:
                    self.logger.warning(f"检查预警失败（{type(exc).__name__}）")
                await asyncio.sleep(self.config["interval"])

    async def check(self, client: httpx.AsyncClient):
        city = self.config["city"]
        response = await client.get("https://weather.example.com/alerts", params={"city": city})
        for alert in response.json()["alerts"]:
            await self.context.emit(
                ExternalEvent(
                    source="weather",
                    kind="weather.alert",
                    dedup_key=f"alert:{alert['id']}",
                    title=f"{city}发布{alert['title']}",
                    payload=alert,
                )
            )
```

记得在循环里捕获异常，否则一次网络错误就会让后台任务悄悄退出。

## 事件之后会发生什么 {#flow}

1. 事件先经过分诊模型打分，重要性从 0 到 4。0 分直接丢弃。
2. 1 分以上的事件会启动一个 Proactive Loop，模型可以调用工具查证、准备材料。
3. Loop 结束后，模型决定要不要通知主人。4 分的事件一定会通知。

事件内容会交给模型阅读。邮件、网页这类内容可能包含针对模型的指令，请不要在 `payload` 里放超出必要的内容。

## 调整引擎的判断 {#hooks}

除了提交事件，插件还可以介入其他事件源的处理：

| 装饰器 | 处理函数参数 | 时机与用途 |
| --- | --- | --- |
| `@hook.on_proactive_event()` | `(event)` | 事件开始处理、分诊之前。可以修改 `title`、`payload`、`urgency`，或用 `event.drop(reason)` 丢弃 |
| `@hook.on_proactive_judged()` | `(event, triage)` | 分诊完成后。可以修改 `triage.importance`（0 为丢弃）、`decision`、`message`、`reason` |
| `@hook.on_proactive_done()` | `(event, outcome)` | Loop 结束、发通知之前。可以改写 `outcome.message`，或把 `outcome.notify` 设为 `False` 取消通知 |
| `@hook.on_notification_sent()` | `(source, text)` | 通知已经发给主人之后，例如转发到其他渠道 |

比如一个"忽略某些来源"的插件：

```python main.py
from shiritaibot.api import ProactiveEvent, Triage, hook

@hook.on_proactive_event()
async def drop_sources(self, event: ProactiveEvent):
    if event.source in self.config["drop_sources"]:
        event.drop(f"配置为忽略来源 {event.source}")

@hook.on_proactive_judged()
async def raise_floor(self, event: ProactiveEvent, triage: Triage):
    if event.kind == "email.received" and 0 < triage.importance < 2:
        triage.importance = 0
```

`event.source` 和 `event.kind` 可以用来判断事件类型。这些 hook 最多执行 30 秒。

为了不让插件误伤重要的事情，ShiritaiBot 保留了一些兜底规则：

- 主人设定的提醒、定时任务，以及主人委派的子代理结果（`event.protected` 为 `True`）不能被丢弃。
- 重要性为 4 的事件和主人设定的提醒（`outcome.forced` 为 `True`）一定会通知，插件只能改写内容。
- 修改结果不合法时（比如 importance 超出 0 到 4，或者 message 为空），这次修改会被忽略，并记录一条警告。

## 主动通知主人 {#notify}

如果你只希望进行简单的通知任务，不希望经过主动性引擎的判断与分诊流程，可以用 `await self.context.notify_owner(text)`通知。这样的流程不会经过主动性引擎，也不会记入主动性引擎生成的短期记忆。
