"""Resolve Core and plugin declarations into an independently managed runtime.

Each candidate has its own project, lockfile and venv. Publishing current.json
never mutates an environment that a running Bot may still be using.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import re
import shutil
import sys
import time
import tomllib
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

import tomli_w
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import parse_sdist_filename, parse_wheel_filename

from shiritaibot.core import PublicError
from shiritaibot.core.process import run_process

FORMAT = 1
MANIFESTS = ("metadata.yaml", "plugin.toml")


def plugin_paths(core: Path, home: Path) -> dict[str, Path]:
    """Mirror the host's source-first discovery without importing plugin code."""
    result = {}
    for root in (core / "src/shiritaibot/plugin", home / "plugins"):
        if not root.is_dir():
            continue
        for directory in sorted(root.iterdir()):
            if (
                not directory.is_symlink()
                and directory.is_dir()
                and re.fullmatch(r"[a-z0-9_]{1,64}", directory.name)
                and directory.name not in {"core", "__pycache__"}
                and any((directory / name).is_file() for name in MANIFESTS)
            ):
                result.setdefault(directory.name, directory.resolve())
    return result


def source_root() -> Path:
    root = Path(__file__).resolve().parents[2]
    if not (root / "uv.lock").is_file() or not (root / "pyproject.toml").is_file():
        raise PublicError("找不到 Core 的 pyproject.toml / uv.lock，请从完整源码目录启动")
    return root


def private_write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def safe_output(value: str) -> str:
    """Resolver diagnostics are useful; credentials and URL queries are not."""

    def redact(match):
        try:
            url = urlsplit(match[0])
            return f"{url.scheme}://{url.hostname or 'source'}/[source]"
        except ValueError:
            return "[source]"

    return re.sub(r"(?:[a-zA-Z][\w+.-]*://)[^\s\"'<>]+", redact, value)[-5000:]


@dataclass
class Declaration:
    dependencies: list[str]
    constraints: list[str]
    requires_python: str = ""


class DeclarationReader:
    """Read static PEP 508 inputs, including nested requirements/constraints.

    Installer options and dynamic metadata cannot silently become dependencies.
    Local references are made absolute before moving into the runtime project.
    """

    def __init__(self):
        self.inputs: dict[str, str] = {}

    def read(self, path: Path) -> str:
        path = path.resolve()
        try:
            value = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            raise PublicError(f"无法读取依赖声明 {path.name}") from None
        self.inputs[str(path)] = value
        return value

    def requirement(self, value: str, base: Path) -> str:
        if "${" in value:
            raise PublicError("依赖声明不支持环境变量展开，请使用无凭据的依赖地址")
        # Bare local directories and archives need a distribution name for PEP 508.
        if value.startswith(("./", "../", "/", ".\\", "..\\")) or re.match(
            r"^[A-Za-z]:[\\/]", value
        ):
            path = (base / value).resolve()
            if path.is_dir():
                metadata = tomllib.loads(self.read(path / "pyproject.toml"))
                name = metadata.get("project", {}).get("name")
                if not name:
                    raise PublicError("本地依赖目录需要在 pyproject.toml 中声明 project.name")
            else:
                try:
                    name = (
                        parse_wheel_filename(path.name)[0]
                        if path.suffix == ".whl"
                        else parse_sdist_filename(path.name)[0]
                    )
                except ValueError:
                    raise PublicError("无法识别本地依赖，请使用 name @ file:///绝对路径") from None
                # Changes to an archive must invalidate the combined environment.
                try:
                    self.inputs[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
                except OSError:
                    raise PublicError("本地依赖文件不存在") from None
            value = f"{name} @ {path.as_uri()}"
        try:
            requirement = Requirement(value)
        except InvalidRequirement:
            raise PublicError("依赖格式无效，请使用 PEP 508 包名、版本范围或 name @ URL") from None
        if requirement.url:
            url = requirement.url
            parsed = urlsplit(url)
            if (
                parsed.password
                or parsed.query
                or (parsed.username and parsed.scheme not in {"ssh", "git+ssh"})
            ):
                raise PublicError("依赖地址不能包含凭据或查询参数，请通过 uv 的认证配置提供凭据")
            if not parsed.scheme or parsed.scheme == "file":
                raw_path = url2pathname(parsed.path) if parsed.scheme == "file" else parsed.path
                if parsed.netloc and parsed.netloc != "localhost":
                    raw_path = "//" + parsed.netloc + raw_path
                path = (base / raw_path).resolve()
                requirement.url = path.as_uri() + ("#" + parsed.fragment if parsed.fragment else "")
                if path.is_dir():
                    self.read(path / "pyproject.toml")
                else:
                    try:
                        self.inputs[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
                    except OSError:
                        raise PublicError("本地依赖文件不存在") from None
        return str(requirement)

    def requirements(
        self, path: Path, *, constraint: bool = False, stack: tuple[Path, ...] = ()
    ) -> Declaration:
        path = path.resolve()
        if path in stack or len(stack) >= 20:
            raise PublicError("requirements 文件递归引用或嵌套超过 20 层")
        text = re.sub(r"\\\r?\n", " ", self.read(path))
        result = Declaration([], [])
        for line in text.splitlines():
            line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
            if not line or line.startswith("#"):
                continue
            include = re.fullmatch(r"(-r|--requirement|-c|--constraint)(?:\s+|=)?(.+)", line)
            if include:
                kind, filename = include.groups()
                nested = self.requirements(
                    path.parent / filename.strip().strip("\"'"),
                    constraint=constraint or kind in {"-c", "--constraint"},
                    stack=(*stack, path),
                )
                result.dependencies.extend(nested.dependencies)
                result.constraints.extend(nested.constraints)
                continue
            if line.startswith("-") or re.search(r"\s--[a-z]", line):
                raise PublicError(
                    "requirements 仅支持依赖声明及 -r/-c 引用；安装源请配置 uv，"
                    "editable、hash 等 pip 安装选项暂不支持"
                )
            value = self.requirement(line, path.parent)
            (result.constraints if constraint else result.dependencies).append(value)
        return result

    def plugin(self, directory: Path) -> Declaration:
        project = directory / "pyproject.toml"
        if project.is_file():
            try:
                data = tomllib.loads(self.read(project))
            except tomllib.TOMLDecodeError:
                raise PublicError("插件 pyproject.toml 格式无效") from None
            metadata = data.get("project", {})
            if "project" not in data:
                requirements = directory / "requirements.txt"
                return (
                    self.requirements(requirements)
                    if requirements.is_file()
                    else Declaration([], [])
                )
            if "dependencies" in metadata.get("dynamic", []):
                raise PublicError("插件依赖必须静态声明在 project.dependencies")
            if data.get("tool", {}).get("uv", {}).get("sources"):
                raise PublicError(
                    "插件暂不支持 tool.uv.sources，请使用 project.dependencies 的 name @ URL"
                )
            dependencies = metadata.get("dependencies", [])
            if not isinstance(dependencies, list) or not all(
                isinstance(v, str) for v in dependencies
            ):
                raise PublicError("project.dependencies 必须是字符串数组")
            requires_python = metadata.get("requires-python", "")
            try:
                if not isinstance(requires_python, str):
                    raise InvalidSpecifier()
                SpecifierSet(requires_python)
            except InvalidSpecifier:
                raise PublicError("project.requires-python 版本范围无效") from None
            if requires_python and not SpecifierSet(requires_python).contains(
                platform.python_version(), prereleases=True
            ):
                raise PublicError(
                    f"插件要求 Python {requires_python}，当前版本 {platform.python_version()}"
                )
            return Declaration(
                [self.requirement(value, directory) for value in dependencies],
                [],
                requires_python,
            )
        requirements = directory / "requirements.txt"
        return self.requirements(requirements) if requirements.is_file() else Declaration([], [])


@dataclass
class RuntimePlan:
    directory: Path
    state: dict

    @property
    def python(self) -> Path:
        return self.directory / (
            ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"
        )

    def supports(self, key: str, current: dict) -> bool:
        if key in self.state["errors"]:
            return False
        wanted = self.state["plugins"].get(key)
        return not wanted or wanted == current.get("plugins", {}).get(key)


class RuntimeEnvironment:
    def __init__(self, home: Path, core: Path | None = None, extras: list[str] | None = None):
        self.home = home.resolve()
        self.core = (core or source_root()).resolve()
        self.root = self.home / "runtime"
        self.extras = extras
        self.lock = asyncio.Lock()

    def current(self) -> RuntimePlan | None:
        try:
            pointer = json.loads((self.root / "current.json").read_text("utf-8"))
            name = pointer["generation"]
            if not re.fullmatch(r"[a-f0-9]{16}-[a-f0-9]{8}", name):
                return None
            directory = self.root / "projects" / name
            state = json.loads((directory / "state.json").read_text("utf-8"))
            if (
                state.get("format") != FORMAT
                or not isinstance(state.get("signature"), str)
                or not isinstance(state.get("plugins"), dict)
                or not isinstance(state.get("errors"), dict)
                or not isinstance(state.get("extras"), list)
            ):
                return None
            return RuntimePlan(directory, state)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def running_state(self) -> dict:
        directory = Path(sys.prefix).resolve().parent
        if directory.parent == (self.root / "projects").resolve():
            try:
                state = json.loads((directory / "state.json").read_text("utf-8"))
                return state if isinstance(state, dict) else {}
            except (OSError, ValueError):
                pass
        return {}

    @asynccontextmanager
    async def exclusive(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # A kernel lock is released even if a preparation process crashes.
        with (self.root / ".prepare.lock").open("a+b") as stream:
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            start = time.monotonic()
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() - start > 1200:
                        raise PublicError("等待运行环境更新超时，请稍后重试") from None
                    await asyncio.sleep(0.2)
            try:
                yield
            finally:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    async def command(self, *args: str) -> str:
        executable = os.environ.get("UV") or shutil.which("uv")
        if not executable:
            raise PublicError("找不到 uv，请安装 uv 并将其加入 PATH")
        # Inherited environment overrides must not redirect sync into the running venv.
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"UV_PROJECT_ENVIRONMENT", "UV_PROJECT", "UV_WORKING_DIR", "VIRTUAL_ENV"}
        }
        env.update(UV_NO_PROGRESS="1", NO_COLOR="1")
        try:
            result = await run_process(
                [executable, *args], self.core, timeout=1200, limit=128 * 1024, env=env
            )
        except (OSError, TimeoutError):
            raise PublicError("uv 执行失败或超时，请检查网络和 Python 环境") from None
        if result["exit_code"] or result["truncated"]:
            raise PublicError("依赖解析或安装失败：\n" + safe_output(result["stderr"]))
        return result["stdout"]

    async def prepare(
        self,
        paths: dict[str, Path] | None = None,
        *,
        required: str | None = None,
        repair: bool = False,
        retry: bool = False,
    ) -> RuntimePlan:
        async with self.lock, self.exclusive():
            return await self._prepare(paths, required=required, repair=repair, retry=retry)

    async def _prepare(self, paths, *, required, repair, retry) -> RuntimePlan:
        previous = self.current()
        extras = sorted(
            set(
                self.extras
                if self.extras is not None
                else (previous.state.get("extras", []) if previous else [])
            )
        )
        reader = DeclarationReader()
        core_text = reader.read(self.core / "pyproject.toml")
        reader.read(self.core / "uv.lock")
        if (self.core / ".python-version").is_file():
            reader.read(self.core / ".python-version")
        try:
            metadata = tomllib.loads(core_text)["project"]
        except (ValueError, KeyError):
            raise PublicError("Core 的 pyproject.toml 格式无效") from None
        if not set(extras) <= set(metadata.get("optional-dependencies", {})):
            raise PublicError("指定的 Core extra 不存在，请检查 --extra 参数")
        declarations, errors = {}, {}
        for key, directory in (
            paths if paths is not None else plugin_paths(self.core, self.home)
        ).items():
            try:
                declaration = reader.plugin(directory)
                if (
                    declaration.dependencies
                    or declaration.constraints
                    or declaration.requires_python
                ):
                    declarations[key] = declaration
            except (PublicError, ValueError, TypeError, AttributeError) as exc:
                errors[key] = (
                    f"插件 {key} 的依赖声明无效：{exc if isinstance(exc, PublicError) else type(exc).__name__}"
                )
        signature = hashlib.sha256(
            json.dumps(
                [
                    FORMAT,
                    str(self.core),
                    sys.implementation.cache_tag,
                    platform.python_version(),
                    extras,
                    reader.inputs,
                    {key: vars(value) for key, value in declarations.items()},
                    errors,
                ],
                sort_keys=True,
            ).encode()
        ).hexdigest()
        if (
            previous
            and previous.state["signature"] == signature
            and previous.python.is_file()
            and (previous.directory / "pyproject.toml").is_file()
            and (previous.directory / "uv.lock").is_file()
            and required not in previous.state["errors"]
            and not (retry and previous.state["errors"])
        ):
            if repair:
                await self.command(
                    "sync",
                    "--project",
                    str(previous.directory),
                    "--locked",
                    "--no-dev",
                    "--python",
                    sys.executable,
                )
            return previous

        directory = self.root / "projects" / (signature[:16] + "-" + uuid.uuid4().hex[:8])
        directory.mkdir(parents=True, mode=0o700)
        published = False
        try:
            export = [
                "export",
                "--project",
                str(self.core),
                "--locked",
                "--no-dev",
                "--no-emit-project",
                "--no-hashes",
            ]
            for extra in extras:
                export.extend(["--extra", extra])
            # Export preserves environment markers and pins transitive Core packages too.
            pins = [
                line.strip()
                for line in (await self.command(*export)).splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
            project = {
                "project": {
                    "name": "shiritaibot-runtime",
                    "version": "0.0.0",
                    "requires-python": metadata["requires-python"],
                    "dependencies": [
                        "shiritaibot" + ("[" + ",".join(extras) + "]" if extras else ""),
                        "pip",
                    ],
                },
                "tool": {
                    "uv": {
                        "package": False,
                        "sources": {"shiritaibot": {"path": str(self.core), "editable": True}},
                        "constraint-dependencies": pins,
                    }
                },
            }
            if previous and (previous.directory / "uv.lock").is_file():
                preferred_lock = (previous.directory / "uv.lock").read_text("utf-8")
            else:
                preferred_lock = ""

            async def resolve():
                private_write(directory / "pyproject.toml", tomli_w.dumps(project))
                # Preserve versions of plugins not yet visited by the incremental solve.
                if preferred_lock:
                    private_write(directory / "uv.lock", preferred_lock)
                await self.command("lock", "--project", str(directory), "--python", sys.executable)

            await resolve()
            accepted = {}
            # Keep established plugins ahead of newly discovered plugins when resolving conflicts.
            prior = previous.state.get("plugins", {}) if previous else {}
            for key in sorted(
                declarations,
                key=lambda item: (
                    item == required,
                    vars(declarations[item]) != prior.get(item),
                    item not in prior,
                    item,
                ),
            ):
                declaration = declarations[key]
                before_project = json.loads(json.dumps(project))
                before_lock = (directory / "uv.lock").read_text("utf-8")
                project["project"]["dependencies"].extend(declaration.dependencies)
                project["tool"]["uv"]["constraint-dependencies"].extend(declaration.constraints)
                if declaration.requires_python:
                    project["project"]["requires-python"] += "," + declaration.requires_python
                try:
                    await resolve()
                    accepted[key] = vars(declaration)
                except PublicError as exc:
                    errors[key] = f"插件 {key} 与 Core 或其他插件的依赖不兼容：{exc}"
                    project = before_project
                    private_write(directory / "pyproject.toml", tomli_w.dumps(project))
                    private_write(directory / "uv.lock", before_lock)
            if required in errors:
                raise PublicError(errors[required])
            # These commands only touch the candidate venv, never sys.prefix.
            await self.command(
                "sync",
                "--project",
                str(directory),
                "--locked",
                "--no-dev",
                "--python",
                sys.executable,
            )
            state = {
                "format": FORMAT,
                "signature": signature,
                "extras": extras,
                "plugins": accepted,
                "errors": errors,
            }
            private_write(directory / "uv.lock", (directory / "uv.lock").read_text("utf-8"))
            private_write(directory / "state.json", json.dumps(state, ensure_ascii=False, indent=2))
            pointer = self.root / (".current-" + uuid.uuid4().hex + ".json")
            private_write(pointer, json.dumps({"generation": directory.name}))
            pointer.replace(self.root / "current.json")
            published = True
            return RuntimePlan(directory, state)
        finally:
            if not published:
                shutil.rmtree(directory, ignore_errors=True)
