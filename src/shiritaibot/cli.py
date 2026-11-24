import logging
import os
import socket
import subprocess
import sys
from copy import copy, deepcopy
from errno import EADDRINUSE, ECONNREFUSED
from pathlib import Path
from urllib.parse import urlencode

import typer
import uvicorn
from uvicorn.config import LOGGING_CONFIG
from uvicorn.logging import DefaultFormatter

from shiritaibot.i18n import DEFAULT_LOCALE, normalize, t

# The CLI runs before any settings load, so it follows the terminal's language.
CLI_LOCALE = normalize(os.environ.get("LC_ALL") or os.environ.get("LANG") or "") or DEFAULT_LOCALE

cli = typer.Typer(help="ShiritaiBot · Personal agent runtime")


class LoginLinkFormatter(DefaultFormatter):
    """Add the sign-in link only to terminal output, not shared log records."""

    def __init__(self, *, token: str, **kwargs):
        super().__init__(**kwargs)
        self.token = token

    def formatMessage(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        prefix, suffix = "Uvicorn running on ", " (Press CTRL+C to quit)"
        if record.name == "uvicorn.error" and message.startswith(prefix + "http"):
            address = message.removeprefix(prefix).removesuffix(suffix)
            record = copy(record)
            record.msg = f"{prefix}{address}/token#{urlencode({'token': self.token})}{suffix}"
            record.args = ()
            record.message = record.getMessage()
            record.__dict__["color_message"] = record.message
        return super().formatMessage(record)


def _port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    address = (host, port)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if os.name == "posix":
            # Match Uvicorn's listener so connections in TIME_WAIT after a
            # restart do not look like another server is listening.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(address)
            return False
        except OSError as exc:
            if exc.errno != EADDRINUSE or os.name != "posix" or sys.platform == "cygwin":
                return True

        # Match the server's POSIX bind after shutdown. Check for a live listener
        # first, since macOS may let a reuse bind overlap a wildcard listener.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.2)
                if probe.connect_ex(address) != ECONNREFUSED:
                    return True
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(address)
        except OSError:
            return True
    return False


@cli.command()
def run(
    home: Path = Path("data"),
    port: int = 8765,
    allowed_host: list[str] = typer.Option(
        [], help=t("cli.allowedHost", locale=CLI_LOCALE)
    ),
    log_level: str | None = typer.Option(
        None,
        help=t("cli.logLevel", locale=CLI_LOCALE),
    ),
    extra: list[str] = typer.Option([], help=t("cli.extra", locale=CLI_LOCALE)),
):
    """Run the local control plane and configured adapters."""
    home = home.resolve()
    restart_requested = False

    def request_restart() -> None:
        nonlocal restart_requested
        restart_requested = True
        server.should_exit = True

    if _port_in_use(port):
        typer.echo(
            t("cli.portBusy", locale=CLI_LOCALE, port=port, port2=port, value=port + 1),
            err=True,
        )
        raise typer.Exit(1)

    # Resolve and repair before importing the application or executing any plugin.
    # A fresh interpreter also ensures no modules from the bootstrap venv survive.
    import asyncio

    from shiritaibot.core import PublicError
    from shiritaibot.dependencies import RuntimeEnvironment

    try:
        environment = RuntimeEnvironment(home, extras=extra)
        typer.echo(t("cli.checkingDeps", locale=CLI_LOCALE))
        plan = asyncio.run(environment.prepare(repair=True))
    except PublicError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None

    def arguments(python: Path) -> list[str]:
        args = [
            str(python),
            "-m",
            "shiritaibot.cli",
            "run",
            "--home",
            str(home),
            "--port",
            str(port),
        ]
        for host in allowed_host:
            args.extend(["--allowed-host", host])
        for name in extra:
            args.extend(["--extra", name])
        if log_level:
            args.extend(["--log-level", log_level])
        return args

    os.environ["VIRTUAL_ENV"] = str(plan.python.parent.parent)
    os.environ["PATH"] = str(plan.python.parent) + os.pathsep + os.environ.get("PATH", "")
    if os.name == "nt":
        # CRT execv does not preserve foreground waiting or argv quoting on
        # Windows. One supervisor owns the terminal across all service restarts.
        if os.environ.get("SHIRITAI_RUNTIME_CHILD") != str(home):
            child_env = {**os.environ, "SHIRITAI_RUNTIME_CHILD": str(home)}
            while True:
                child = subprocess.Popen(
                    arguments(plan.python),
                    env=child_env,
                    creationflags=0
                    if sys.stdin and sys.stdin.isatty()
                    else subprocess.CREATE_NO_WINDOW,
                )
                try:
                    code = child.wait()
                except KeyboardInterrupt:
                    try:
                        child.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        child.terminate()
                        child.wait()
                    raise typer.Exit(130) from None
                if code != 75:
                    raise typer.Exit(code)
                plan = environment.current() or plan
        elif Path(sys.prefix).resolve() != plan.python.parent.parent.resolve():
            raise typer.Exit(75)
    elif Path(sys.prefix).resolve() != plan.python.parent.parent.resolve():
        os.execv(str(plan.python), arguments(plan.python))
        return

    from shiritaibot.web import create_app, ensure_token

    admin_token = ensure_token(home)
    log_config = deepcopy(LOGGING_CONFIG)
    log_config["formatters"]["default"].update({"()": LoginLinkFormatter, "token": admin_token})
    config = uvicorn.Config(
        create_app(
            home,
            token=admin_token,
            allowed_hosts=allowed_host,
            log_level=log_level,
            request_restart=request_restart,
        ),
        host="127.0.0.1",
        port=port,
        access_log=False,
        log_config=log_config,
    )
    server = uvicorn.Server(config)
    try:
        server.run()
    except SystemExit:
        if _port_in_use(port):
            typer.echo(
                t("cli.portBusyShort", locale=CLI_LOCALE, port=port, value=port + 1),
                err=True,
            )
            raise typer.Exit(1) from None
        raise
    if restart_requested:
        # Re-enter Python only after Uvicorn has closed connections and the
        # application lifespan has flushed data and stopped its workers.
        if os.name == "nt":
            raise typer.Exit(75)
        next_plan = environment.current()
        python = next_plan.python if next_plan else Path(sys.executable)
        os.execv(str(python), arguments(python))


@cli.command()
def sync(
    home: Path = Path("data"),
    extra: list[str] = typer.Option([], help=t("cli.extra", locale=CLI_LOCALE)),
):
    """Prepare the combined runtime without starting the Bot."""
    import asyncio

    from shiritaibot.core import PublicError
    from shiritaibot.dependencies import RuntimeEnvironment

    try:
        plan = asyncio.run(RuntimeEnvironment(home, extras=extra).prepare(repair=True, retry=True))
    except PublicError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(1) from None
    typer.echo(t("cli.runtimeReady", locale=CLI_LOCALE, directory=plan.directory))
    for message in plan.state["errors"].values():
        typer.echo(message, err=True)


@cli.command()
def token(
    home: Path = Path("data"),
    create: bool = typer.Option(False, help=t("cli.newToken", locale=CLI_LOCALE)),
):
    """Display the local admin token for signing into WebUI."""
    from shiritaibot.web import ensure_token

    home = home.resolve()
    if create:
        typer.echo(ensure_token(home))
        return
    path = home / "web.token"
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise typer.BadParameter(
            t("cli.tokenUnreadable", locale=CLI_LOCALE, path=path, strerror=error.strerror)
        ) from error
    if not value:
        raise typer.BadParameter(t("cli.tokenEmpty", locale=CLI_LOCALE, path=path))
    typer.echo(value)


@cli.command()
def doctor():
    """Check executable availability without contacting providers."""
    import shutil
    import sqlite3
    import sys

    typer.echo(f"Python {sys.version.split()[0]} · SQLite {sqlite3.sqlite_version}")
    for name in ["bash", "codex", "claude", "dsh", "lark-cli", "node"]:
        typer.echo(f"{name}: {shutil.which(name) or 'not found'}")
    typer.echo(t("cli.windowsEntry", locale=CLI_LOCALE))


if __name__ == "__main__":
    cli()
