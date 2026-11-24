"""Hello World: the plugin API v2 example.

User-facing text lives in locales/<locale>.json and is read with self.t("key", param=value)
(docs/I18N.md). Docstrings stay in one language: they become /help text and tool descriptions.
"""

from shiritaibot.api import (
    Context,
    LLMRequest,
    MessageEvent,
    OutgoingMessage,
    Permission,
    Plugin,
    PluginSettings,
    ProactiveEvent,
    ToolContext,
    Triage,
    Turn,
    hook,
)


class HelloWorld(Plugin):
    def __init__(self, context: Context, config: PluginSettings):
        super().__init__(context, config)

    async def initialize(self):
        self.logger.log(self.t("started"))

    # 指令 -----------------------------------------------------------------------------

    @hook.command("hello", aliases={"你好"})
    async def hello(self, event: MessageEvent, name: str = ""):
        """打个招呼：/hello [名字]"""
        count = await self.get_kv_data("count", 0) + 1
        await self.put_kv_data("count", count)
        yield event.plain_result(
            self.t(
                "hello",
                greeting=self.config["greeting"],
                name=name or event.get_sender_name(),
                count=count,
            )
        )

    @hook.command_group("calc")
    def calc(self):
        """简单计算"""

    @calc.command("add")
    async def add(self, event: MessageEvent, a: int, b: int):
        """两数相加：/calc add 1 2"""
        return event.plain_result(str(a + b))

    @hook.permission(Permission.OWNER)
    @hook.command("hello_reset")
    async def reset(self, event: MessageEvent):
        """清零招呼计数（仅主人）"""
        await self.delete_kv_data("count")
        return self.t("reset")

    # 消息监听 -------------------------------------------------------------------------

    @hook.regex(r"^ping$")
    async def ping(self, event: MessageEvent):
        """收到 ping 直接回复 pong，不交给模型"""
        await event.send("pong")
        event.stop_event()

    # LLM 工具 -------------------------------------------------------------------------

    @hook.llm_tool("hello_world", risk="safe", chat=True)
    async def hello_tool(self, ctx: ToolContext, name: str = ""):
        """返回欢迎消息。

        Args:
            name(string): 要问候的人，可省略
        """
        self.logger.log(self.t("greetingLog", session=ctx.session))
        return {"message": f"{self.config['greeting']} {name}".strip(), "session": ctx.session}

    # Agent 生命周期 -------------------------------------------------------------------

    @hook.on_llm_request()
    async def add_hint(self, turn: Turn, request: LLMRequest):
        if request.step == 0 and not turn.background:
            request.add_context(self.t("hint"))

    @hook.on_decorating_result()
    async def sign(self, message: OutgoingMessage):
        if self.config["signature"] and message.session.startswith("web:"):
            message.text += "\n\n" + self.config["signature"]

    # 主动性引擎 -----------------------------------------------------------------------

    @hook.on_proactive_event()
    async def drop_sources(self, event: ProactiveEvent):
        if event.source in self.config["drop_sources"]:
            event.drop(self.t("dropped", source=event.source))

    @hook.on_proactive_judged()
    async def floor(self, event: ProactiveEvent, triage: Triage):
        minimum = self.config["advanced"]["min_importance"]
        if 0 < triage.importance < minimum:
            triage.importance = 0
            triage.reason += self.t("belowMinimum", minimum=minimum)
