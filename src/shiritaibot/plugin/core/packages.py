"""Bounded ZIP installation. Plugin code is only executed by PluginManager."""

from __future__ import annotations

import io
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import httpx

from shiritaibot.core import PublicError
from shiritaibot.i18n import t

from .manifest import MANIFESTS, manifest_file, read_manifest

MAX_ARCHIVE = 20 * 1024 * 1024
MAX_UNPACKED = 100 * 1024 * 1024


def validate_id(key: str) -> None:
    if not re.fullmatch(r"[a-z0-9_]{1,64}", key) or key in {"core", "__pycache__"}:
        raise PublicError("plugins.packages.idFormat")


def check_entries(archive: zipfile.ZipFile, max_unpacked: int, label: str) -> None:
    """Reject oversized archives, absolute/parent paths, links and special files."""
    entries = archive.infolist()
    if len(entries) > 2000 or sum(i.file_size for i in entries) > max_unpacked:
        raise PublicError("plugins.packages.tooLarge", label=label)
    for item in entries:
        path = PurePosixPath(item.filename)
        mode = item.external_attr >> 16
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in item.filename
            or ":" in item.filename
            or stat.S_ISLNK(mode)
            or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)))
        ):
            raise PublicError("plugins.packages.unsafe", label=label)


def extract_entries(archive: zipfile.ZipFile, stage: Path) -> None:
    """Extract an archive already accepted by check_entries, skipping VCS and caches."""
    for item in archive.infolist():
        path = PurePosixPath(item.filename)
        if any(p in {".git", "__pycache__"} for p in path.parts):
            continue
        target = stage.joinpath(*path.parts)
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)


def plugin_root(stage: Path) -> Path:
    """The extracted plugin directory: the archive root, or its only folder with a manifest."""
    if manifest_file(stage):
        return stage
    found = {path.parent for name in MANIFESTS for path in stage.rglob(name)}
    if len(found) != 1:
        raise PublicError("plugins.packages.layout")
    return found.pop()


def install_archive(root: Path, key: str, data: bytes) -> None:
    validate_id(key)
    if len(data) > MAX_ARCHIVE:
        raise PublicError("plugins.packages.zipTooLarge")
    if root.is_symlink():
        raise PublicError("plugins.packages.targetSymlink")
    root.mkdir(parents=True, exist_ok=True)
    destination = root / key
    if destination.exists() or destination.is_symlink():
        raise PublicError("plugins.packages.exists")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            check_entries(archive, MAX_UNPACKED, t("plugins.packages.label"))
            with tempfile.TemporaryDirectory(prefix=".install-", dir=root) as staging:
                stage = Path(staging)
                extract_entries(archive, stage)
                directory = plugin_root(stage)
                error = read_manifest(directory)["error"]
                if error:
                    raise PublicError(error)
                compile((directory / "main.py").read_text("utf-8"), "main.py", "exec")
                directory.rename(destination)
    except PublicError:
        raise
    except Exception:
        raise PublicError("plugins.packages.invalid") from None


def remove_directory(root: Path, key: str) -> None:
    validate_id(key)
    directory = root / key
    if root.is_symlink() or directory.is_symlink():
        raise PublicError("plugins.packages.symlinkDelete")
    if directory.is_dir():
        shutil.rmtree(directory)


async def download_github(url: str) -> bytes:
    match = re.fullmatch(
        r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", url.strip()
    )
    if not match or any(p in {".", ".."} for p in match.groups()):
        raise PublicError("plugins.packages.githubUrl")
    owner, repo = match.groups()
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            async with client.stream(
                "GET",
                f"https://api.github.com/repos/{owner}/{repo}/zipball",
                headers={"Accept": "application/vnd.github+json"},
            ) as response:
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_ARCHIVE:
                        raise PublicError("plugins.packages.githubTooLarge")
                return bytes(data)
    except httpx.HTTPError:
        raise PublicError("plugins.packages.githubFailed") from None
