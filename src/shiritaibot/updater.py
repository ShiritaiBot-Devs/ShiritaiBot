"""Self-update installer deployments from archives and manual deployments through Git.

The installer lays out ``<root>/app`` (source), ``<root>/data`` and ``<root>/runtime``. An
update downloads the chosen source archive into ``<root>/.update``, builds the WebUI there,
then swaps the top-level entries of ``app`` (keeping ``.venv`` and concrete plugins),
syncs Python dependencies in place and finally asks ``shiritaibot run`` to re-execute.
Any failure before the restart moves the previous source back.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import httpx

from shiritaibot.core import PublicError
from shiritaibot.core.logs import get_logger
from shiritaibot.core.process import run_process

REPOSITORY = "ShiritaiBot-Devs/ShiritaiBot"
BRANCH = "main"
API = f"https://api.github.com/repos/{REPOSITORY}"
ARCHIVE = f"https://codeload.github.com/{REPOSITORY}/tar.gz/"
MARKER = ".shiritaibot-release.json"
MAX_ARCHIVE = 200 * 1024 * 1024
CACHE_SECONDS = 300
MAX_RELEASE_PAGES = 10
REQUIRED = ("src/shiritaibot/cli.py", "pyproject.toml", "uv.lock", "web/package-lock.json")
# Entries of the deployed tree that survive an update untouched.
KEEP = {".venv"}
# Not part of a release, or generated locally: never reported as local edits.
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
SKIP_PATHS = {".venv", "web/dist"}
PLUGIN_DIR = Path("src/shiritaibot/plugin")
TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,99}$")
SHA = re.compile(r"^[0-9a-f]{40}$")

log = get_logger("更新")


def package_version() -> str:
    try:
        return metadata.version("shiritaibot")
    except metadata.PackageNotFoundError:
        from shiritaibot import __version__

        return __version__


def first_line(text: str) -> str:
    return (text or "").strip().splitlines()[0] if (text or "").strip() else ""


class Updater:
    def __init__(
        self,
        app_dir: Path,
        restart: Callable[[], object] | None,
        transport: httpx.AsyncBaseTransport | None = None,
        home: Path | None = None,
    ):
        self.app_dir = app_dir.resolve()
        self.root = self.app_dir.parent
        self.work = self.root / ".update"
        if self.mode == "git":
            self.work = self.root / (".update-" + self.app_dir.name)
        self.home = (home or self.root / "data").resolve()
        self.restart = restart
        self.transport = transport
        self.cache: dict[str, tuple[float, Any]] = {}
        self.task: asyncio.Task | None = None
        # Source downloaded by inspect(), reused by the following apply().
        self.staged: dict | None = None
        self.inspecting = False
        self.output: deque[str] = deque(maxlen=60)
        self.state: dict = {"phase": "idle", "message": "", "target": None}

    # ----- availability and current version -------------------------------------------------

    @property
    def mode(self) -> str:
        if (self.root / ".shiritaibot-installer").is_file():
            return "installer"
        return "git"

    def unavailable(self) -> str | None:
        if self.restart is None:
            return "当前启动方式不支持自动更新，请使用 shiritaibot run 启动"
        if self.mode == "git":
            if not (self.app_dir / ".git").exists():
                return "当前目录不是 Git 仓库，请使用 git clone 或一键部署脚本安装"
            if not shutil.which("git"):
                return "找不到 git，无法更新源码部署"
        return None

    def current(self) -> dict:
        if self.mode == "git" and (self.app_dir / ".git").exists():
            try:
                result = subprocess.run(
                    ["git", "log", "-1", "--format=%H"],
                    cwd=self.app_dir,
                    capture_output=True,
                    text=True,
                    timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                sha = result.stdout.strip() if result.returncode == 0 else ""
            except (OSError, subprocess.TimeoutExpired):
                sha = ""
            return {
                "version": package_version(),
                "kind": "commit",
                "ref": sha or None,
                "sha": sha or None,
                "deployed_at": None,
            }
        try:
            marker = json.loads((self.app_dir / MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            marker = {}
        return {
            "version": package_version(),
            "kind": marker.get("kind"),
            "ref": marker.get("ref"),
            "sha": marker.get("sha"),
            "deployed_at": marker.get("deployed_at"),
        }

    def status(self) -> dict:
        return {
            **self.state,
            "output": list(self.output),
            "available": self.unavailable() is None,
            "reason": self.unavailable(),
            "current": self.current(),
            "repository": REPOSITORY,
            "mode": self.mode,
        }

    # ----- GitHub queries --------------------------------------------------------------------

    def client(self, timeout: float = 20) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            transport=self.transport,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "ShiritaiBot-Updater",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    async def get(self, client: httpx.AsyncClient, path: str, **params):
        try:
            response = await client.get(API + path, params=params or None)
        except httpx.HTTPError:
            raise PublicError("无法连接 GitHub，请检查网络或代理设置") from None
        if (
            response.status_code in {403, 429}
            and response.headers.get("x-ratelimit-remaining") == "0"
        ):
            reset = response.headers.get("x-ratelimit-reset", "")
            when = (
                datetime.fromtimestamp(int(reset)).strftime("%H:%M") if reset.isdigit() else "稍后"
            )
            raise PublicError(f"GitHub API 访问次数已用完，请在 {when} 后重试")
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise PublicError(f"GitHub 返回错误（HTTP {response.status_code}）")
        return response.json()

    async def cached(self, key: str, load: Callable[[], Awaitable[Any]], refresh: bool) -> Any:
        hit = self.cache.get(key)
        if hit and not refresh and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        value = await load()
        self.cache[key] = (time.monotonic(), value)
        return value

    async def all_releases(self, refresh: bool = False) -> list[dict]:
        async def load():
            rows: list[dict] = []
            async with self.client() as client:
                for page in range(1, MAX_RELEASE_PAGES + 1):
                    batch = await self.get(client, "/releases", per_page=100, page=page) or []
                    rows.extend(batch)
                    if len(batch) < 100:
                        break
            return [
                {
                    "tag": row["tag_name"],
                    "name": row.get("name") or row["tag_name"],
                    "body": row.get("body") or "",
                    "prerelease": bool(row.get("prerelease")),
                    "published_at": row.get("published_at"),
                    "url": row.get("html_url"),
                }
                for row in rows
                if not row.get("draft") and TAG.match(row.get("tag_name") or "")
            ]

        return await self.cached("releases", load, refresh)

    async def releases(self, page: int, size: int, prerelease: bool, refresh: bool) -> dict:
        rows = await self.all_releases(refresh)
        if not prerelease:
            rows = [row for row in rows if not row["prerelease"]]
        start = (page - 1) * size
        return {"items": rows[start : start + size], "total": len(rows), "page": page}

    async def preview(self, refresh: bool = False) -> dict | None:
        """The head of the default branch when it has commits no release contains yet."""

        if self.mode == "git" and (self.app_dir / ".git").exists():
            return await self.git_preview()

        async def load():
            releases = await self.all_releases(refresh)
            async with self.client() as client:
                head = await self.get(client, f"/commits/{BRANCH}")
                if head is None:
                    return None
                base = releases[0]["tag"] if releases else None
                if base:
                    compare = await self.get(client, f"/compare/{base}...{head['sha']}")
                    if compare is None or compare.get("ahead_by", 0) == 0:
                        return None
                    ahead = compare["ahead_by"]
                    commits = list(reversed(compare.get("commits") or []))
                else:
                    commits = await self.get(client, "/commits", sha=head["sha"], per_page=30) or []
                    ahead = len(commits)
            return {
                "sha": head["sha"],
                "message": first_line(head["commit"]["message"]),
                "date": head["commit"]["committer"]["date"],
                "url": head.get("html_url"),
                "base": base,
                "ahead_by": ahead,
                "commits": [
                    {
                        "sha": row["sha"],
                        "message": first_line(row["commit"]["message"]),
                        "author": (row["commit"].get("author") or {}).get("name", ""),
                        "date": (row["commit"].get("committer") or {}).get("date"),
                    }
                    for row in commits[:100]
                ],
            }

        return await self.cached("preview", load, refresh)

    # ----- deployment ------------------------------------------------------------------------

    async def archive(self, kind: str, ref: str) -> str:
        if kind == "release":
            if not TAG.match(ref) or ref not in {r["tag"] for r in await self.all_releases()}:
                raise PublicError("找不到该版本，请刷新版本列表")
            return ARCHIVE + "refs/tags/" + ref
        if kind == "commit":
            if not SHA.match(ref):
                raise PublicError("提交编号无效")
            return ARCHIVE + ref
        raise PublicError("未知的更新类型")

    async def apply(self, kind: str, ref: str) -> dict:
        if reason := self.unavailable():
            raise PublicError(reason)
        if self.inspecting or (self.task and not self.task.done()):
            raise PublicError("已有更新正在进行")
        if self.mode == "git":
            self.check_git_target(kind, ref)
            self.output.clear()
            self.state = {
                "phase": "downloading",
                "message": "正在检查 Git 更新",
                "target": {"kind": kind, "ref": ref},
            }
            self.task = asyncio.create_task(self.run_git(ref))
            return self.status()
        archive = await self.archive(kind, ref)
        staged, self.staged = self.staged, None
        if staged and (staged["kind"], staged["ref"]) != (kind, ref):
            await asyncio.to_thread(shutil.rmtree, staged["stage"], True)
            staged = None
        self.output.clear()
        self.state = {
            "phase": "downloading",
            "message": "正在下载源码",
            "target": {"kind": kind, "ref": ref},
        }
        self.task = asyncio.create_task(self.run(kind, ref, archive, staged))
        return self.status()

    async def inspect(self, kind: str, ref: str) -> dict:
        """Download the target source and report the local edits an update would erase."""
        if reason := self.unavailable():
            raise PublicError(reason)
        if self.inspecting or (self.task and not self.task.done()):
            raise PublicError("已有更新正在进行")
        if self.mode == "git":
            self.check_git_target(kind, ref)
            self.inspecting = True
            try:
                info = await self.git_ready()
                if info["sha"] != ref:
                    raise PublicError("远程最新提交已变化，请重新检查更新")
                return {"files": [], "patch": None, "sha": ref}
            finally:
                self.inspecting = False
        archive = await self.archive(kind, ref)
        self.inspecting, self.staged = True, None
        stage = self.work / ("stage-" + secrets.token_hex(4))
        try:
            await asyncio.to_thread(self.work.mkdir, exist_ok=True)
            await asyncio.to_thread(self.cleanup_stages)
            source, sha = await self.download(archive, stage)
            changes = await asyncio.to_thread(self.compare, source)
            patch = await asyncio.to_thread(self.save_patch, source, changes)
            self.staged = {
                "kind": kind,
                "ref": ref,
                "stage": stage,
                "source": source,
                "sha": sha,
            }
            return {"files": changes, "patch": patch, "sha": sha}
        except BaseException:
            await asyncio.to_thread(shutil.rmtree, stage, True)
            raise
        finally:
            self.inspecting = False

    def step(self, phase: str, message: str) -> None:
        self.state = {**self.state, "phase": phase, "message": message}
        self.output.append(f"» {message}")
        log.log(message)

    # ----- manual Git deployments -----------------------------------------------------------

    @staticmethod
    def check_git_target(kind: str, ref: str) -> None:
        if kind != "commit" or not SHA.fullmatch(ref):
            raise PublicError("Git 部署仅支持更新到跟踪分支的最新提交，请重新检查更新")

    async def git(self, *args: str) -> str:
        result = await run_process(
            ["git", *args],
            self.app_dir,
            timeout=120,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"},
        )
        if result["exit_code"] != 0 or result["truncated"]:
            if args[0] == "merge":
                raise PublicError("Git 无法快进更新，可能有本地文件会被覆盖，请手动检查仓库")
            raise PublicError(f"git {args[0]} 执行失败，请检查仓库、跟踪分支和网络连接")
        return result["stdout"].strip()

    async def git_info(self) -> dict:
        root = await self.git("rev-parse", "--show-toplevel")
        if await asyncio.to_thread(Path(root).resolve) != self.app_dir.resolve():
            raise PublicError("程序目录不是 Git 仓库根目录，无法自动更新")
        branch = await self.git("branch", "--show-current")
        if not branch:
            raise PublicError("当前处于 detached HEAD，请先切换到有远程跟踪分支的本地分支")
        upstream = await self.git(
            "for-each-ref",
            "--format=%(upstream:remotename)%09%(upstream:remoteref)",
            f"refs/heads/{branch}",
        )
        parts = upstream.split("\t")
        if len(parts) != 2 or not parts[0] or parts[0] == "." or not parts[1]:
            raise PublicError("当前分支没有远程跟踪分支，请先设置 upstream 后重试")
        head = await self.git("rev-parse", "HEAD")
        await self.git("fetch", "--no-tags", "--", *parts)
        sha = await self.git("rev-parse", "FETCH_HEAD")
        return {"branch": branch, "head": head, "sha": sha}

    async def git_clean(self) -> None:
        if await self.git(
            "status", "--porcelain", "--untracked-files=all", "--ignore-submodules=none"
        ):
            raise PublicError("工作区存在未提交的修改或未跟踪文件，请先提交或移走后再更新")

    async def git_ready(self) -> dict:
        await self.git_clean()
        info = await self.git_info()
        ahead, _ = (
            await self.git("rev-list", "--left-right", "--count", f"{info['head']}...{info['sha']}")
        ).split()
        if int(ahead):
            raise PublicError("当前分支有本地独有提交或已与远程分叉，请手动处理后再更新")
        if info["head"] == info["sha"]:
            raise PublicError("已是跟踪分支的最新提交")
        if await self.git("ls-files", "--", "web/dist"):
            raise PublicError("web/dist 包含 Git 跟踪文件，请手动更新此部署")
        return info

    async def git_preview(self) -> dict:
        info = await self.git_info()
        rows = await self.git(
            "log",
            "-100",
            "--format=%H%x09%s%x09%an%x09%cI",
            f"{info['head']}..{info['sha']}",
        )
        commits = []
        for row in rows.splitlines():
            sha, message, author, date = row.split("\t", 3)
            commits.append({"sha": sha, "message": message, "author": author, "date": date})
        title = await self.git("log", "-1", "--format=%s", info["sha"])
        return {
            "sha": info["sha"],
            "message": title,
            "date": None,
            "url": None,
            "base": info["head"][:7],
            "branch": info["branch"],
            "ahead_by": int(
                await self.git("rev-list", "--count", f"{info['head']}..{info['sha']}")
            ),
            "commits": commits,
        }

    async def run_git(self, ref: str) -> None:
        stage = self.work / ("stage-" + secrets.token_hex(4))
        source = stage / "source"
        old_dist = stage / "previous-dist"
        dist = self.app_dir / "web" / "dist"
        info: dict = {}
        merged = False
        dist_replaced = False
        worktree = False
        cleanup_stage = True
        try:
            self.step("downloading", "正在核实当前提交和远程跟踪分支")
            info = await self.git_ready()
            if info["sha"] != ref:
                raise PublicError("远程最新提交已变化，请重新检查更新")
            await asyncio.to_thread(stage.mkdir, parents=True)
            await self.git("worktree", "add", "--detach", str(source), ref)
            worktree = True
            self.step("building", "正在安装 WebUI 依赖")
            await self.command(self.npm("--prefix", "web", "ci", "--no-audit", "--no-fund"), source)
            self.step("building", "正在构建 WebUI")
            await self.command(self.npm("--prefix", "web", "run", "build"), source)
            if not (source / "web/dist/index.html").is_file():
                raise PublicError("WebUI 构建没有生成页面")
            # Recheck after the build; a user may have edited or switched branches meanwhile.
            await self.git_clean()
            if (
                await self.git("rev-parse", "HEAD") != info["head"]
                or await self.git("branch", "--show-current") != info["branch"]
            ):
                raise PublicError("构建期间当前分支或提交发生变化，已取消更新")
            self.step("deploying", "正在快进更新 Git 提交")
            # Split pull into fetch + merge to deploy exactly the inspected commit.
            await self.git("merge", "--ff-only", "--no-autostash", "--no-overwrite-ignore", ref)
            merged = True
            if dist.exists():
                await asyncio.to_thread(dist.rename, old_dist)
            dist_replaced = True
            await asyncio.to_thread(shutil.copytree, source / "web/dist", dist)
            self.step("deploying", "正在同步 Python 依赖")
            await self.sync()
            self.step("deploying", "正在检查新版配置兼容性")
            await self.validate_config(self.app_dir)
            self.step("restarting", "更新完成，正在重启")
            assert self.restart is not None
            self.restart()
        except BaseException as error:
            cancelled = isinstance(error, asyncio.CancelledError)
            if merged:
                try:
                    # Never move a branch another process has since changed.
                    if (
                        await self.git("rev-parse", "HEAD") != ref
                        or await self.git("branch", "--show-current") != info["branch"]
                    ):
                        raise PublicError("分支已被其他操作改变")
                    await self.git("reset", "--keep", info["head"])
                    if dist_replaced:
                        await asyncio.to_thread(shutil.rmtree, dist, True)
                        if old_dist.exists():
                            await asyncio.to_thread(old_dist.rename, dist)
                    await self.sync()
                except Exception:
                    cleanup_stage = False
                    error = PublicError(
                        f"{error}；自动恢复失败，请手动检查 Git 和依赖状态；备份保留于 {stage}"
                    )
            message = str(error) if isinstance(error, PublicError) else f"更新失败：{error!r}"
            self.state = {**self.state, "phase": "failed", "message": message}
            self.output.append("✗ " + message)
            log.log(message)
            if cancelled:
                raise
        finally:
            if worktree:
                try:
                    await self.git("worktree", "remove", "--force", str(source))
                except Exception:
                    cleanup_stage = False
                    log.log(f"临时 Git worktree 清理失败：{source}")
            if cleanup_stage:
                await asyncio.to_thread(shutil.rmtree, stage, True)

    async def validate_config(self, source: Path) -> None:
        """Import the new Settings in a fresh interpreter, without starting the application."""
        script = (
            "import asyncio, sys\n"
            "from pathlib import Path\n"
            "from pydantic import ValidationError\n"
            "from tomllib import TOMLDecodeError\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from shiritaibot.config import ConfigService\n"
            "from shiritaibot.core import PublicError\n"
            "try:\n"
            "    asyncio.run(ConfigService(Path(sys.argv[2])).load())\n"
            "except PublicError as error:\n"
            "    print(str(error), file=sys.stderr)\n"
            "    sys.exit(1)\n"
            "except ValidationError as error:\n"
            "    print('配置校验失败：' + ', '.join('.'.join(map(str, e['loc'])) "
            "for e in error.errors(include_input=False)), file=sys.stderr)\n"
            "    sys.exit(1)\n"
            "except TOMLDecodeError:\n"
            "    print('配置文件 TOML 格式无效', file=sys.stderr)\n"
            "    sys.exit(1)\n"
        )
        if not (self.home / "config.toml").exists():
            return
        try:
            await self.command(
                [sys.executable, "-c", script, str(source / "src"), str(self.home)],
                source,
            )
        except PublicError:
            raise PublicError(
                f"新版本无法加载配置文件 {self.home / 'config.toml'}，已取消重启，请查看更新日志"
            ) from None

    async def run(self, kind: str, ref: str, archive: str, staged: dict | None = None) -> None:
        stage = staged["stage"] if staged else self.work / ("stage-" + secrets.token_hex(4))
        try:
            await asyncio.to_thread(self.work.mkdir, exist_ok=True)
            if staged:
                source, sha = staged["source"], staged["sha"]
            else:
                await asyncio.to_thread(self.cleanup_stages)
                self.step("downloading", f"正在下载 {ref[:12] if kind == 'commit' else ref}")
                source, sha = await self.download(archive, stage)
            self.step("building", "正在安装 WebUI 依赖")
            await self.command(self.npm("--prefix", "web", "ci", "--no-audit", "--no-fund"), source)
            self.step("building", "正在构建 WebUI")
            await self.command(self.npm("--prefix", "web", "run", "build"), source)
            if not (source / "web" / "dist" / "index.html").is_file():
                raise PublicError("WebUI 构建没有生成页面")
            marker = {
                "kind": kind,
                "ref": ref,
                "sha": sha if kind == "release" else ref,
                "deployed_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            (source / MARKER).write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")
            self.step("deploying", "正在替换程序文件")
            backup = stage / "previous"
            await asyncio.to_thread(self.swap, source, backup)
            try:
                self.step("deploying", "正在同步 Python 依赖")
                await self.sync()
                self.step("deploying", "正在检查新版配置兼容性")
                await self.validate_config(self.app_dir)
            except BaseException:
                self.step("deploying", "部署失败，正在恢复原版本")
                await asyncio.to_thread(self.restore, backup, stage / "failed")
                await asyncio.shield(self.sync(quiet=True))
                raise
            await asyncio.to_thread(self.keep_previous, backup)
            self.step("restarting", "部署完成，正在重启")
            assert self.restart is not None
            self.restart()
        except Exception as error:
            message = str(error) if isinstance(error, PublicError) else f"更新失败：{error!r}"
            self.state = {**self.state, "phase": "failed", "message": message}
            self.output.append("✗ " + message)
            log.log(message)
        finally:
            await asyncio.to_thread(shutil.rmtree, stage, True)

    def cleanup_stages(self) -> None:
        for path in self.work.glob("stage-*"):
            shutil.rmtree(path, ignore_errors=True)

    async def download(self, url: str, stage: Path) -> tuple[Path, str]:
        await asyncio.to_thread(stage.mkdir, parents=True)
        path = stage / "source.tar.gz"
        size = 0
        try:
            async with self.client(timeout=120) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code >= 400:
                        raise PublicError(f"源码下载失败（HTTP {response.status_code}）")
                    with path.open("wb") as stream:
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > MAX_ARCHIVE:
                                raise PublicError("源码包过大，已取消下载")
                            stream.write(chunk)
        except httpx.HTTPError:
            raise PublicError("源码下载失败，请检查网络连接") from None
        return await asyncio.to_thread(self.extract, path, stage / "source")

    @staticmethod
    def extract(path: Path, target: Path) -> tuple[Path, str]:
        try:
            with tarfile.open(path, "r:gz") as archive:
                archive.extractall(target, filter="data")
                # git archive records the commit in the global pax header.
                sha = archive.pax_headers.get("comment", "")
        except (tarfile.TarError, OSError) as error:
            raise PublicError(f"源码包无法解压：{error}") from None
        entries = list(target.iterdir())
        if len(entries) != 1 or not entries[0].is_dir():
            raise PublicError("源码包结构异常")
        source = entries[0]
        if not all((source / name).is_file() for name in REQUIRED):
            raise PublicError("下载的源码不完整")
        return source, sha if SHA.match(sha) else ""

    def snapshot(self, root: Path) -> dict[str, Path]:
        files: dict[str, Path] = {}
        for base, dirs, names in os.walk(root):
            here = Path(base)
            dirs[:] = sorted(
                name
                for name in dirs
                if name not in SKIP_DIRS
                and (here / name).relative_to(root).as_posix() not in SKIP_PATHS
            )
            for name in names:
                if name == MARKER:
                    continue
                path = here / name
                files[path.relative_to(root).as_posix()] = path
        return files

    def compare(self, source: Path) -> list[dict]:
        """Local files the release would overwrite (modified) or drop (removed)."""
        local, remote = self.snapshot(self.app_dir), self.snapshot(source)
        rows = []
        for rel, path in local.items():
            other = remote.get(rel)
            if other is None:
                rows.append({"path": rel, "kind": "removed"})
            elif self.digest(path) != self.digest(other):
                rows.append({"path": rel, "kind": "modified"})
        return sorted(rows, key=lambda row: row["path"])

    @staticmethod
    def digest(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 16), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    def text(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def save_patch(self, source: Path, changes: list[dict]) -> str | None:
        """Keep the local edits an update would erase, as a unified diff under data/patches."""
        if not changes:
            return None
        folder = self.root / "data" / "patches"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / ("local-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".patch")
        chunks: list[str] = []
        for row in changes:
            rel = row["path"]
            before_path = None if row["kind"] == "removed" else source / rel
            before = "" if before_path is None else self.text(before_path)
            after = self.text(self.app_dir / rel)
            if after is None or before is None:
                old = "/dev/null" if before_path is None else f"release/{rel}"
                chunks.append(f"Binary files {old} and local/{rel} differ\n")
                continue
            chunks.extend(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    fromfile="/dev/null" if before_path is None else f"release/{rel}",
                    tofile=f"local/{rel}",
                )
            )
            if chunks and not chunks[-1].endswith("\n"):
                chunks.append("\n")
        target.write_text("".join(chunks), encoding="utf-8")
        log.log(f"已保存本地修改补丁：{target}")
        return target.relative_to(self.root).as_posix()

    def swap(self, source: Path, backup: Path) -> None:
        """Move current entries into backup, then the new source into place."""
        backup.mkdir()
        for entry in list(self.app_dir.iterdir()):
            if entry.name not in KEEP:
                entry.rename(backup / entry.name)
        try:
            for entry in list(source.iterdir()):
                if entry.name not in KEEP:
                    entry.rename(self.app_dir / entry.name)
            # Concrete plugins live beside the host core; carry over those the release lacks.
            old_plugins = backup / PLUGIN_DIR
            new_plugins = self.app_dir / PLUGIN_DIR
            if old_plugins.is_dir() and new_plugins.is_dir():
                for plugin in old_plugins.iterdir():
                    if plugin.is_dir() and not (new_plugins / plugin.name).exists():
                        shutil.copytree(plugin, new_plugins / plugin.name, symlinks=True)
        except BaseException:
            self.restore(backup, source.parent / "failed")
            raise

    def restore(self, backup: Path, discard: Path) -> None:
        discard.mkdir(exist_ok=True)
        for entry in list(self.app_dir.iterdir()):
            if entry.name not in KEEP:
                entry.rename(discard / entry.name)
        for entry in list(backup.iterdir()):
            entry.rename(self.app_dir / entry.name)

    def keep_previous(self, backup: Path) -> None:
        """Retain one previous deployment (without node_modules) for manual rollback."""
        previous = self.work / "previous"
        shutil.rmtree(previous, ignore_errors=True)
        shutil.rmtree(backup / "web" / "node_modules", ignore_errors=True)
        backup.rename(previous)

    def npm(self, *args: str) -> list[str]:
        if os.name == "nt":
            node = shutil.which("node")
            cli = (
                Path(node).parent / "node_modules" / "npm" / "bin" / "npm-cli.js" if node else None
            )
            if not node or not cli or not cli.is_file():
                raise PublicError("找不到 Node.js/npm，无法构建 WebUI")
            return [node, str(cli), *args]
        if not shutil.which("npm"):
            raise PublicError("找不到 npm，无法构建 WebUI")
        return ["npm", *args]

    async def sync(self, quiet: bool = False) -> None:
        uv = os.environ.get("UV") or shutil.which("uv")
        if not uv:
            raise PublicError("找不到 uv，无法安装 Python 依赖")
        # --inexact keeps pip and packages that plugins installed into the environment.
        # --no-install-project skips rewriting the project and its console script: on Windows
        # the running shiritaibot.exe is locked, so reinstalling it fails (WinError 32) and can
        # uninstall the package mid-update. The existing editable install already points at
        # this source tree, so new code loads on restart.
        try:
            await self.command(
                [uv, "sync", "--frozen", "--no-dev", "--inexact", "--no-install-project"],
                self.app_dir,
            )
        except Exception:
            if not quiet:
                raise

    async def command(self, argv: list[str], cwd: Path) -> None:
        def line(raw: bytes) -> None:
            text = raw.decode("utf-8", errors="replace").rstrip()
            if text:
                self.output.append(text[:300])

        env = {**os.environ, "CI": "1", "NO_COLOR": "1"}
        result = await run_process(argv, cwd, timeout=1200, env=env, on_line=line)
        for text in result["stderr"].splitlines()[-20:]:
            if text.strip():
                self.output.append(text[:300])
        if result["exit_code"] != 0:
            raise PublicError(f"{Path(argv[0]).name} 执行失败（退出码 {result['exit_code']}）")
