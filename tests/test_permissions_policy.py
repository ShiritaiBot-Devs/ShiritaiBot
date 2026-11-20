from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shiritaibot.agent.commands import command
from shiritaibot.auth import Permissions
from shiritaibot.config import RiskPolicy, SessionPolicy, Settings, ToolPolicy
from shiritaibot.core import Context, EventBus, Tool
from shiritaibot.store import Store
from shiritaibot.tools import ToolRegistry, schema


def _tool(name: str, risk: str) -> Tool:
    async def noop(ctx, args):
        return "ok"

    return Tool(name, f"{name} tool", schema({}), noop, risk=risk)


def test_default_balanced_risk_matrix_and_tool_override(tmp_path):
    store = Store(tmp_path, EventBus())
    cfg = SimpleNamespace(value=Settings())
    perms = Permissions(cfg, store)
    ctx = Context("web:owner", "owner", mode="agent", owner=True)

    assert perms.configured(ctx, _tool("t_safe", "safe")) == "allow"
    assert perms.configured(ctx, _tool("t_normal", "normal")) == "allow"
    assert perms.configured(ctx, _tool("t_sensitive", "sensitive")) == "auto"
    assert perms.configured(ctx, _tool("t_dangerous", "dangerous")) == "manual"

    # Per-tool override takes precedence over the risk matrix.
    cfg.value.policy.owner_default.tools["t_dangerous"] = "auto"
    cfg.value.policy.owner_default.tools["t_normal"] = "deny"
    assert perms.configured(ctx, _tool("t_dangerous", "dangerous")) == "auto"
    assert perms.configured(ctx, _tool("t_normal", "normal")) == "deny"

    # Session-level custom risk matrix overrides the default policy.
    cfg.value.policy.sessions["web:owner"] = SessionPolicy(
        mode="agent",
        risks=RiskPolicy(safe="allow", normal="manual", sensitive="deny", dangerous="deny"),
        tools={"t_sensitive": "manual"},
    )
    assert perms.configured(ctx, _tool("t_normal", "normal")) == "manual"
    assert perms.configured(ctx, _tool("t_sensitive", "sensitive")) == "manual"
    assert perms.configured(ctx, _tool("t_dangerous", "dangerous")) == "deny"


@pytest.mark.parametrize(
    ("legacy", "expected"),
    [
        ("all_manual", {"safe": "manual", "normal": "manual", "sensitive": "manual", "dangerous": "manual"}),
        ("safe_manual", {"safe": "allow", "normal": "manual", "sensitive": "manual", "dangerous": "manual"}),
        ("safe_auto", {"safe": "allow", "normal": "auto", "sensitive": "auto", "dangerous": "auto"}),
        ("full_access", {"safe": "allow", "normal": "allow", "sensitive": "allow", "dangerous": "allow"}),
    ],
)
def test_legacy_preset_migrates_to_risk_matrix(legacy, expected):
    policy = ToolPolicy.model_validate({"preset": legacy, "tools": {"shell": "deny"}})
    assert policy.risks.model_dump() == expected
    assert policy.tools == {"shell": "deny"}


def test_tools_command_risk_and_preset():
    import asyncio

    async def run():
        settings = Settings()

        class FakeConfig:
            def __init__(self, value):
                self.value = value
                self.version = 1

            async def save(self, new_value, *, expected_version=None):
                self.value = new_value
                self.version += 1

        cfg = FakeConfig(settings)
        store = SimpleNamespace(append=AsyncMock())
        perms = Permissions(cfg, store)
        registry = ToolRegistry(perms, store)
        registry.add(_tool("shell", "dangerous"))
        registry.add(_tool("kb_import", "normal"))
        delivered: list[str] = []

        async def deliver(session, answer):
            delivered.append(answer)

        runtime = SimpleNamespace(
            config=cfg,
            store=store,
            registry=registry,
            actors={},
            deliver=deliver,
        )
        ctx = Context("web:owner", "owner", mode="agent", owner=True)

        # Set a per-tool override, then adjust one risk level: override is preserved.
        await command(runtime, ctx, "/tools shell auto")
        assert cfg.value.policy.sessions["web:owner"].tools == {"shell": "auto"}

        await command(runtime, ctx, "/tools risk sensitive manual")
        assert "custom" in delivered[-1]
        session_policy = cfg.value.policy.sessions["web:owner"]
        assert session_policy.risks.sensitive == "manual"
        assert session_policy.tools == {"shell": "auto"}

        # Switching preset fills the risk matrix and clears per-tool overrides.
        await command(runtime, ctx, "/tools preset strict")
        assert "strict" in delivered[-1]
        session_policy = cfg.value.policy.sessions["web:owner"]
        assert session_policy.risks.model_dump() == {
            "safe": "allow",
            "normal": "manual",
            "sensitive": "manual",
            "dangerous": "manual",
        }
        assert session_policy.tools == {}

        # Legacy preset name still works as an alias.
        await command(runtime, ctx, "/tools preset safe_auto")
        assert cfg.value.policy.sessions["web:owner"].risks.model_dump() == {
            "safe": "allow",
            "normal": "auto",
            "sensitive": "auto",
            "dangerous": "auto",
        }

    asyncio.run(run())
