"""Hello World：ShiritaiBot 插件最小示例（API v2）。

用户看到的文字放在 locales/<语言>.json，用 self.t("键", 参数=值) 取用（docs/I18N.md）。
"""

from shiritaibot.api import MessageEvent, Plugin, ToolContext, hook


class HelloWorld(Plugin):
    async def initialize(self):
        self.logger.log(self.t("started"))
        self.city = self.config.get("default_city", "北京")  # i18n: ok (a default value)

    @hook.command("hello", aliases={"hi"})
    async def hello(self, event: MessageEvent, name: str = ""):
        """打个招呼：/hello [名字]"""
        yield event.plain_result(self.t("greeting", name=name or event.get_sender_name()))

    @hook.llm_tool("hello_weather", risk="safe", chat=True)
    async def hello_weather(self, ctx: ToolContext, city: str = ""):
        """查询城市天气（示例工具，返回假数据）。

        Args:
            city(string): 城市名，留空时用配置里的默认城市
        """
        return {"city": city or self.city, "weather": self.t("sunny"), "temp": "23°C"}
