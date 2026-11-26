"""Private process entry point for the Electron host."""

import asyncio
import json
import os
import socket
import sys
import threading
from pathlib import Path


def report(**fields):
    print("SHIRITAI_DESKTOP " + json.dumps(fields), flush=True)


def bootstrap_environment():
    root = Path(__file__).resolve().parent
    sys.path[0:0] = [str(root / "bootstrap"), str(root / "core" / "src")]
    from shiritaibot.dependencies import RuntimeEnvironment

    return RuntimeEnvironment


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
                while os.read(sys.stdin.fileno(), 4096):
                    pass
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
        plan = asyncio.run(bootstrap_environment()(Path(args[0])).prepare(repair=True))
        report(python=str(plan.python))
    elif command == "serve":
        sys.exit(serve(Path(args[0]), int(args[1])))
