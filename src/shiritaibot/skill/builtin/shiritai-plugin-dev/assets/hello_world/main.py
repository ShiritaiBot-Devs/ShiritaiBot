"""Hello World：ShiritaiBot 插件最小示例（API v2）。"""

from shiritaibot.api import MessageEvent, Plugin, ToolContext, hook


class HelloWorld(Plugin):
    async def initialize(self):
        self.logger.log("Hello World 插件已启动")
        self.city = self.config.get("default_city", "北京")

    @hook.command("hello", aliases={"hi"})
    async def hello(self, event: MessageEvent, name: str = ""):
        """打个招呼：/hello [名字]"""
        yield event.plain_result(f"你好，{name or event.get_sender_name()}！")

    @hook.llm_tool("hello_weather", risk="safe", chat=True)
    async def hello_weather(self, ctx: ToolContext, city: str = ""):
        """查询城市天气（示例工具，返回假数据）。

        Args:
            city(string): 城市名，留空时用配置里的默认城市
        """
        return {"city": city or self.city, "weather": "晴", "temp": "23°C"}
