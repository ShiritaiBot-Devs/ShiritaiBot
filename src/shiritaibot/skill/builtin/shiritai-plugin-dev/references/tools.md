指令需要用户自己输入，工具则是交给模型的：用户说"明天北京会下雨吗"，模型会自己决定调用你的天气工具，拿到结果后再组织语言回复。

ShiritaiBot的设计哲学之一是“一切皆工具”。工具可以赋予LLM更多的自由度，让ShiritaiBot用自然语言处理更多的工作。

## 注册工具 {#register}

用 `@hook.llm_tool` 装饰一个方法：

```python main.py
from shiritaibot.api import Plugin, ToolContext, hook


class Weather(Plugin):
    @hook.llm_tool("weather_query", risk="safe", chat=True)
    async def weather_query(self, ctx: ToolContext, city: str, days: int = 1):
        """查询城市未来几天的天气预报。

        Args:
            city(string): 城市名，例如"北京"
            days(integer): 查询天数，1 到 7
        """
        forecast = await self.fetch(city, days)
        return {"city": city, "forecast": forecast}
```

- 第一个参数是工具名，模型通过它调用工具。工具名在所有插件中不能重复，建议加上插件名作为前缀。不写的话默认使用方法名。
- 三引号中 `Args:` 之前的部分是工具说明，模型靠它判断什么时候该调用这个工具，请写清楚工具能做什么。三引号后面的部分是使用这个工具的参数（见下文）。
- `risk` 是风险等级，必须填写，见下文。
- `ctx` 是本次调用的上下文，之后的参数由模型填写。

## 参数定义 {#parameters}

ShiritaiBot 按以下顺序确定工具的参数：

1. **docstring 的 `Args:`**（推荐）。每行写成 `参数名(类型): 说明`，类型可以是 `string`、`number`、`integer`、`boolean`、`object`、`array`，或者 `array[string]` 这样带元素类型的数组。
2. **类型注解**。没有写 `Args:` 时，会根据 `str`、`int`、`float`、`bool`、`list`、`dict` 注解生成参数，但没有说明文字，模型不容易用对。
3. **手动指定**。需要枚举、长度限制等更复杂的约束时，直接传一个 JSON Schema：

```python main.py
@hook.llm_tool(
    "note_save",
    risk="normal",
    description="保存一条笔记",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "maxLength": 100},
            "tag": {"type": "string", "enum": ["工作", "生活", "学习"]},
        },
        "required": ["title"],
        "additionalProperties": False,
    },
)
async def note_save(self, ctx: ToolContext, title: str, tag: str = "生活"):
    ...
```

没有默认值的参数会被标记为必填。模型传来的参数会先按定义校验，不符合时工具不会被调用，模型会收到一条错误信息。

## 风险等级 {#risk}

每个工具都必须声明风险等级，ShiritaiBot 根据它决定工具的默认权限：

| 等级 | 适用于 | 例子 |
| --- | --- | --- |
| `safe` | 只读、没有副作用 | 查天气、搜索、读取文件列表 |
| `normal` | 会写入数据，但影响范围小、可以恢复 | 保存笔记、创建 Issue |
| `sensitive` | 会对外产生影响，或者难以撤回 | 发邮件、合并 PR、推送代码 |
| `dangerous` | 可能造成严重后果 | 修改仓库设置、删除数据 |

风险等级决定了工具在权限管理预设中的默认权限。用户可以在管理后台为每个工具单独设置禁止、人工审批、模型自动审批或无需审批，风险等级只决定默认值。

请如实声明你注册工具的风险等级。如果一个会发邮件的工具标成 `safe`，就可能在用户不知情的情况下被调用。如果你把握不准注册工具的风险等级，建议设成 `sensitive`，默认需要用户批准，并由用户来自定义其权限范围。

## 在 Chat 模式中使用 {#chat}

插件工具默认只在 Agent 模式下提供给模型。如果你的工具是只读的，而且属于日常聊天中会用到的功能（比如查天气），可以加上 `chat=True`，让它在 Chat 模式（例如群聊）中也能使用。

## 调用上下文 {#context}

`ctx` 是一个 `ToolContext`，可以用来判断是谁、在哪里调用了工具：

| 字段 | 说明 |
| --- | --- |
| `session` | 会话 ID |
| `actor` | 发起这轮对话的用户 |
| `owner` | 是否为主人 |
| `group` | 是否在群聊中 |
| `background` | 是否为后台任务，例如主动事件处理 |
| `mode` | `chat` 或 `agent` |

比如只允许主人在私聊中使用的工具，可以这样检查：

```python main.py
if not ctx.owner or ctx.group:
    raise PublicError("这个工具只能由主人在私聊中使用")
```

## 返回值和错误 {#result}

工具可以返回字符串、数字、字典或列表，ShiritaiBot 会把它转换成 JSON 交给模型。返回的内容会进入模型上下文，所以请只返回模型需要的信息，不要把整个 API 响应原样塞进去，这会造成严重的token浪费并且造成日志基本不可读。

出错时抛出 `PublicError`，错误信息会原样告诉模型，模型可以据此调整参数或者向用户解释。其他异常只会告诉模型"工具失败"，具体原因写进日志，这样设计是为了避免内部信息泄露。因此设计工具时，请考虑使报错信息足够让模型意识到问题所在，并且不会造成过量的上下文污染。

工具默认最多执行 60 秒，可以用 `timeout` 参数调整，例如 `@hook.llm_tool("slow_job", risk="normal", timeout=300)`。

## 动态注册 {#dynamic}

工具要根据配置决定是否注册时，可以在 `initialize()` 里调用 `register_tool()`：

```python main.py
from shiritaibot.api import Plugin, TEXT, schema


class Notes(Plugin):
    async def initialize(self):
        if self.config["allow_write"]:
            self.context.register_tool(
                "note_save",
                "保存一条笔记",
                schema({"text": TEXT}, required=["text"]),
                self.save,
                risk="normal",
            )

    async def save(self, ctx, args):
        ...
        return {"saved": True}
```
