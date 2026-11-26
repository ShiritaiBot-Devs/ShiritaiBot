from __future__ import annotations

import asyncio
import difflib
import heapq
import re
import shutil
from pathlib import Path, PureWindowsPath

from fastapi import HTTPException, Request

from shiritaibot.core import session_dir, uid
from shiritaibot.i18n import t

MAX_UPLOAD = 250 * 1024 * 1024


def workspace_suggestions(path: Path) -> list[str]:
    for missing in (path, *path.parents):
        parent = missing.parent
        if parent == missing:
            break
        if not parent.is_dir():
            continue
        try:
            choices = (
                (difflib.SequenceMatcher(None, missing.name.casefold(), entry.name.casefold()).ratio(), entry)
                for entry in parent.iterdir() if entry.is_dir()
            )
            nearest = heapq.nlargest(5, choices, key=lambda choice: choice[0])
            suffix = path.relative_to(missing)
            return [str(entry / suffix) for score, entry in nearest
                    if score >= 0.4 and (entry / suffix).is_dir()]
        except OSError:
            return []
    return []


async def workspace_for(store, home: Path, session: str) -> Path:
    selection = await store.get("web_workspace", session)
    return Path(selection["path"]) if selection else session_dir(home, "workspaces", session)


async def upload_attachment(request: Request, workspace: Path, name: str) -> dict:
    key = uid()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:120].rstrip(" .")
    if not name or PureWindowsPath(name).is_reserved():
        name = "attachment-" + name
    parent = workspace / ".shiritaibot-uploads"
    directory = parent / key
    target = directory / name

    def create_directory():
        if workspace.resolve() != workspace:
            raise HTTPException(409, t("web.workspaceUnavailable"))
        parent.mkdir(parents=True, exist_ok=True)
        if parent.resolve() != parent:
            raise HTTPException(409, t("web.workspaceUnavailable"))
        directory.mkdir()

    owned = False
    size = 0
    try:
        await asyncio.to_thread(create_directory)
        owned = True
        stream = await asyncio.to_thread(target.open, "xb")
        try:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, t("web.chatUploadTooLarge"))
                await asyncio.to_thread(stream.write, chunk)
        finally:
            await asyncio.to_thread(stream.close)
    except BaseException as exc:
        if owned:
            await asyncio.to_thread(shutil.rmtree, directory, True)
        if isinstance(exc, OSError):
            raise HTTPException(422, t("web.workspaceUnavailable")) from exc
        raise
    return {"id": key, "name": name, "size": size, "path": str(target), "workspace": str(workspace)}
