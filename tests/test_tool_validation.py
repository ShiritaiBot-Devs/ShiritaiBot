import asyncio
import json
from unittest.mock import AsyncMock

from shiritaibot.core import Context, Tool
from shiritaibot.tools import ToolRegistry, schema


def test_tool_validation_reports_first_three_errors():
    permissions = AsyncMock()
    handler = AsyncMock()
    registry = ToolRegistry(permissions, AsyncMock())
    item = schema({"count": {"type": "integer"}}, required=["count"])
    registry.add(
        Tool("batch", "", schema({"items": {"type": "array", "items": item}}), handler, "safe")
    )
    arguments = '{"items": [{"count": "two"}, {}, {"count": 1, "extra": 2}, {}]}'

    result = asyncio.run(registry.execute(Context("session", "user"), "batch", arguments))

    assert json.loads(result)["error"].splitlines() == [
        "工具参数不符合 JSON Schema：arguments.items[0].count [type]: 'two' is not of type 'integer'",
        "arguments.items[1] [required]: 'count' is a required property",
        "arguments.items[2] [additionalProperties]: Additional properties are not allowed ('extra' was unexpected)",
    ]
    permissions.check.assert_not_awaited()
    handler.assert_not_awaited()


def test_tool_validation_bounds_and_redacts_root_error():
    registry = ToolRegistry(AsyncMock(), AsyncMock())
    registry.secrets = ("sample-value",)
    registry.add(Tool("empty", "", schema({}), AsyncMock(), "safe"))
    arguments = {"sample-value": 1, "z" * 1000: 2}

    result = asyncio.run(registry.execute(Context("session", "user"), "empty", arguments))

    error = json.loads(result)["error"]
    assert error.startswith("工具参数不符合 JSON Schema：arguments [additionalProperties]:")
    assert "sample-value" not in error
    assert len(error) < 600 and error.endswith("…")
