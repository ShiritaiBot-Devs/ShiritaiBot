import asyncio
import sys
from unittest.mock import AsyncMock

import pytest

from shiritaibot.core.process import run_process


@pytest.mark.parametrize("payload", ["", "input for child"])
def test_subprocess_stdin(tmp_path, monkeypatch, payload):
    spawn = AsyncMock(wraps=asyncio.create_subprocess_exec)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    command = [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"]

    result = asyncio.run(run_process(command, tmp_path, stdin=payload))

    # Fast commands may exit before uvloop can write even an empty byte string.
    expected = asyncio.subprocess.PIPE if payload else asyncio.subprocess.DEVNULL
    assert spawn.await_args.kwargs["stdin"] == expected
    assert result["exit_code"] == 0
    assert result["stdout"] == payload
