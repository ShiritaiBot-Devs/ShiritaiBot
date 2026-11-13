import pytest

from shiritaibot.core import PublicError
from shiritaibot.learn import OneLearnPlugin
from shiritaibot.plugin_api import PluginContext


def adapter_result(exit_code=0, stdout="", stderr="", truncated=False):
    return {
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": truncated,
    }


def entry_with_adapter(app, monkeypatch, result):
    async def fake_run_process(*args, **kwargs):
        return result

    monkeypatch.setattr("shiritaibot.learn.run_process", fake_run_process)
    ctx = PluginContext(
        "learn",
        {"command": ["/bin/true"]},
        app.home,
        app.vault.resolve,
        app.proactive.emit,
    )
    return OneLearnPlugin(ctx)


async def test_invoke_returns_parsed_response(app, monkeypatch):
    entry = entry_with_adapter(
        app,
        monkeypatch,
        adapter_result(stdout='{"ok": true, "data": {"operation": "courses"}}'),
    )
    await entry.start()
    try:
        assert await entry.invoke("courses", {}) == {
            "ok": True,
            "data": {"operation": "courses"},
        }
    finally:
        await entry.close()


async def test_invoke_returns_parsed_response_despite_stderr_truncation(app, monkeypatch):
    entry = entry_with_adapter(
        app,
        monkeypatch,
        adapter_result(
            stdout='{"ok": true, "data": {"operation": "courses"}}',
            stderr="warning " * 2000,
            truncated=True,
        ),
    )
    await entry.start()
    try:
        assert await entry.invoke("courses", {}) == {
            "ok": True,
            "data": {"operation": "courses"},
        }
    finally:
        await entry.close()


async def test_invoke_returns_parsed_response_despite_nonzero_exit(app, monkeypatch):
    entry = entry_with_adapter(
        app,
        monkeypatch,
        adapter_result(exit_code=1, stdout='{"ok": true}', stderr="cleanup failed"),
    )
    await entry.start()
    try:
        assert await entry.invoke("courses", {}) == {"ok": True}
    finally:
        await entry.close()


async def test_invoke_reports_exit_code_and_stderr_tail(app, monkeypatch):
    stderr = "head-marker " + "x" * 600 + " tail-marker"
    entry = entry_with_adapter(
        app,
        monkeypatch,
        adapter_result(exit_code=1, stdout="not json", stderr=stderr),
    )
    await entry.start()
    try:
        with pytest.raises(PublicError) as raised:
            await entry.invoke("courses", {})
    finally:
        await entry.close()
    message = str(raised.value)
    assert "exit code 1" in message
    assert "tail-marker" in message
    assert "head-marker" not in message


async def test_invoke_reports_missing_stderr_when_exit_code_nonzero(app, monkeypatch):
    entry = entry_with_adapter(
        app,
        monkeypatch,
        adapter_result(exit_code=3, stdout="not json", stderr="  \n"),
    )
    await entry.start()
    try:
        with pytest.raises(PublicError) as raised:
            await entry.invoke("courses", {})
    finally:
        await entry.close()
    message = str(raised.value)
    assert "exit code 3" in message
    assert "无 stderr 输出" in message


async def test_invoke_reports_truncated_output(app, monkeypatch):
    entry = entry_with_adapter(
        app,
        monkeypatch,
        adapter_result(exit_code=0, stdout="not json", truncated=True),
    )
    await entry.start()
    try:
        with pytest.raises(PublicError) as raised:
            await entry.invoke("courses", {})
    finally:
        await entry.close()
    assert "1MB" in str(raised.value)


async def test_invoke_reports_invalid_json(app, monkeypatch):
    entry = entry_with_adapter(
        app,
        monkeypatch,
        adapter_result(exit_code=0, stdout="not json"),
    )
    await entry.start()
    try:
        with pytest.raises(PublicError) as raised:
            await entry.invoke("courses", {})
    finally:
        await entry.close()
    assert str(raised.value) == "OneLearn 适配端没有返回有效 JSON"
