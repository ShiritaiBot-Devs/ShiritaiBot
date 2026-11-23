"""Linux filesystem isolation for the built-in shell tool."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from shiritaibot.core import PublicError
from shiritaibot.core.process import resolve_command

SYSTEM_PATHS = (
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/etc/alternatives",
    "/etc/ld.so.cache",
    "/etc/locale.conf",
    "/etc/nsswitch.conf",
    "/etc/hosts",
    "/etc/resolv.conf",
    "/etc/ssl/certs",
)


def isolated_shell(command: list[str], workspace: Path) -> list[str]:
    if sys.platform != "linux" or not (bwrap := shutil.which("bwrap")):
        raise PublicError("tools.shell.needsBwrap")
    if any(workspace.is_relative_to(path) for path in SYSTEM_PATHS):
        raise PublicError("tools.shell.systemFolder")

    # Mount only system tooling and this workspace. In particular the host home,
    # other sessions, the bot's store and the parent's environment stay outside.
    args = [
        bwrap,
        "--unshare-all",
        "--share-net",
        "--die-with-parent",
        "--cap-drop",
        "ALL",
        "--clearenv",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
    ]
    for path in SYSTEM_PATHS:
        if Path(path).exists():
            args.extend(["--ro-bind", path, path])
    workdir = os.fspath(workspace)
    args.extend(
        [
            "--bind",
            workdir,
            workdir,
            "--chdir",
            workdir,
            "--setenv",
            "HOME",
            workdir,
            "--setenv",
            "PATH",
            "/usr/local/bin:/usr/bin:/bin",
            "--setenv",
            "LANG",
            "C.UTF-8",
            "--setenv",
            "TMPDIR",
            "/tmp",
            "--",
            *resolve_command(command),
        ]
    )
    return args
