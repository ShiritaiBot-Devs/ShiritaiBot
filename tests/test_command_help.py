import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from shiritaibot.agent.commands import command
from shiritaibot.api import hook
from shiritaibot.api.event import MessageEvent
from shiritaibot.auth import Permissions
from shiritaibot.config import Settings
from shiritaibot.core import Context, Tool
from shiritaibot.plugin.core.hooks import HookBus, collect


def test_help_filters_unavailable_commands_for_each_session():
    class Plugin:
        @hook.command_group("sample")
        async def sample(self, event): ...

        @sample.command("qq")
        @hook.platform(hook.Platform.QQ)
        @hook.message_type(hook.MessageType.GROUP)
        async def group_qq(self, event): ...

        @sample.command("private")
        @hook.message_type(hook.MessageType.PRIVATE)
        @hook.permission(hook.Permission.OWNER)
        async def owner_private(self, event): ...

    async def run():
        cfg = SimpleNamespace(
            value=Settings.model_validate(
                {
                    "policy": {"sessions": {"web:limited": {"tools": {"memory_search": "deny"}}}},
                }
            )
        )
        store = SimpleNamespace(append=AsyncMock())
        plugin = Plugin()
        hooks = HookBus(store, lambda h: h.owner is plugin)
        hooks.set("example", collect("example", plugin, hooks.counter))
        tool = Tool("memory_search", "", {}, AsyncMock(), "safe", chat=True)
        runtime = SimpleNamespace(
            config=cfg,
            store=store,
            hooks=hooks,
            actors={},
            deliver=AsyncMock(),
            registry=SimpleNamespace(permissions=Permissions(cfg, store), tools={tool.name: tool}),
        )

        async def menu(ctx):
            await command(runtime, ctx, "/help")
            return runtime.deliver.await_args.args[1]

        qq = await menu(Context("group:qq:123", "5", group=True))
        assert "/sample qq" in qq
        assert all(name not in qq for name in ("/sample private", "/new", "/clear", "/memory"))
        web = await menu(Context("web:limited", "owner", owner=True))
        assert "/sample private" in web and "/new" in web
        assert "/sample qq" not in web and "/memory" not in web
        assert "/memory" in await menu(Context("web:owner", "owner", owner=True))

        event = MessageEvent("group:qq:123", "/sample", "5", group_id="123")
        assert hooks.group_help(event.text, event).splitlines()[1:] == ["/sample qq"]

    asyncio.run(run())
