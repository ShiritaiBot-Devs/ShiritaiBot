import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from shiritaibot.plugin.email.main import EmailPlugin


@pytest.mark.parametrize(
    ("overrides", "polling", "sending"),
    [
        ({}, True, True),  # Existing settings without an SMTP switch.
        ({"smtp_enabled": False}, True, False),
        ({"poll": False}, False, True),
        ({"smtp_host": ""}, True, False),
        ({"notify_to": []}, True, False),
    ],
)
def test_email_channels_are_independent(tmp_path, overrides, polling, sending):
    async def run():
        context = SimpleNamespace(data_dir=tmp_path, register_notifier=Mock())
        settings = {
            "imap_host": "imap.example.org",
            "username": "bot@example.org",
            "smtp_host": "smtp.example.org",
            "notify_to": ["owner@example.org"],
            **overrides,
        }
        plugin = EmailPlugin(context, settings)
        plugin.poll = AsyncMock()
        await plugin.initialize()
        try:
            assert (plugin.task is not None) == polling
            if sending:
                context.register_notifier.assert_called_once_with(plugin.notify)
            else:
                context.register_notifier.assert_not_called()
        finally:
            await plugin.terminate()

    asyncio.run(run())
