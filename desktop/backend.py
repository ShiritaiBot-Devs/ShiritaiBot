"""Private process entry point for the Electron host."""

import asyncio
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

PYPI_INDEXES = (
    "https://pypi.org/simple",
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
    "https://mirrors.ustc.edu.cn/pypi/simple",
)


def report(**fields):
    print("SHIRITAI_DESKTOP " + json.dumps(fields), flush=True)


def bootstrap_environment():
    root = Path(__file__).resolve().parent
    bootstrap = root / "bootstrap"
    if bootstrap.is_dir():
        sys.path[0:0] = [str(bootstrap), str(root / "core" / "src")]
    from shiritaibot.dependencies import RuntimeEnvironment

    return RuntimeEnvironment


def probe_index(index):
    start = time.monotonic()
    request = Request(index + "/packaging/", method="HEAD")
    try:
        with urlopen(request, timeout=3) as response:
            if response.status == 200:
                return time.monotonic() - start
    except OSError:
        pass
    return float("inf")


async def prepare(home, environment):
    from shiritaibot.core import PublicError

    explicit_index = any(
        os.environ.get(name) for name in ("UV_DEFAULT_INDEX", "UV_INDEX_URL", "UV_INDEX", "UV_OFFLINE")
    )
    if explicit_index:
        indexes = [""]
    else:
        report(startupStage="probing")
        scores = await asyncio.gather(
            *(asyncio.to_thread(probe_index, index) for index in PYPI_INDEXES)
        )
        indexes = sorted(PYPI_INDEXES, key=dict(zip(PYPI_INDEXES, scores)).__getitem__)
        previous = environment(home).current()
        preferred = previous.state.get("desktop_index") if previous else None
        if preferred in indexes:
            indexes.remove(preferred)
            indexes.insert(0, preferred)

    errors = []
    for i, index in enumerate(indexes):
        os.environ["SHIRITAI_DESKTOP_INDEX"] = index
        host = urlsplit(index).hostname
        if i:
            report(startupStage="retrying", indexHost=host)
        try:
            plan = await environment(home).prepare(
                repair=True, on_progress=lambda stage: report(startupStage=stage, indexHost=host)
            )
        except PublicError as exc:
            if len(indexes) == 1:
                raise
            errors.append(f"{host}: {exc}")
        else:
            report(python=str(plan.python), index=index)
            return
    raise PublicError("\n".join(errors))


async def prepare_until_closed(home, environment):
    from shiritaibot.core import PublicError

    task = asyncio.create_task(prepare(home, environment))
    loop = asyncio.get_running_loop()

    def watch_host():
        while os.read(sys.stdin.fileno(), 4096):
            pass
        try:
            loop.call_soon_threadsafe(task.cancel)
        except RuntimeError:
            pass

    if os.environ.get("SHIRITAI_DESKTOP_PARENT"):
        threading.Thread(target=watch_host, daemon=True).start()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except PublicError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    return 0


def serve(home, port):
    import uvicorn

    from shiritaibot.web import create_app

    restart = False

    def request_restart():
        nonlocal restart
        restart = True
        server.should_exit = True

    class DesktopServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started:
                report(port=listener.getsockname()[1])

        async def serve(self, sockets=None):
            loop = asyncio.get_running_loop()

            def watch_host():
                while command := os.read(sys.stdin.fileno(), 4096):
                    if b"r" in command:
                        loop.call_soon_threadsafe(request_restart)
                try:
                    loop.call_soon_threadsafe(setattr, self, "should_exit", True)
                except RuntimeError:
                    pass

            threading.Thread(target=watch_host, daemon=True).start()
            bus = api.state.runtime.bus
            events = asyncio.Queue(maxsize=64)
            bus.subscribers.add(events)

            async def forward_notifications():
                while True:
                    event = await events.get()
                    if event["kind"] == "web_task_finished":
                        report(taskFinished=event["status"])

            notifications = asyncio.create_task(forward_notifications())
            try:
                await super().serve(sockets=sockets)
            finally:
                bus.subscribers.discard(events)
                notifications.cancel()
                await asyncio.gather(notifications, return_exceptions=True)

    api = create_app(home, request_restart=request_restart, desktop=True)
    config = uvicorn.Config(
        api,
        host="127.0.0.1",
        port=port,
        access_log=False,
        timeout_graceful_shutdown=10,
    )
    server = DesktopServer(config)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        server.run(sockets=[listener])
    return 75 if restart else (0 if server.started else 1)


if __name__ == "__main__":
    command, *args = sys.argv[1:]
    if command == "check":
        bootstrap_environment()
    elif command == "prepare":
        sys.exit(asyncio.run(prepare_until_closed(Path(args[0]), bootstrap_environment())))
    elif command == "serve":
        sys.exit(serve(Path(args[0]), int(args[1])))
