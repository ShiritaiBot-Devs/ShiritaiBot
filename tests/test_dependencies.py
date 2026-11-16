"""Offline integration checks using real uv and tiny local wheels."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import tomli_w

from shiritaibot.core import PublicError
from shiritaibot.dependencies import (
    DeclarationReader,
    RuntimeEnvironment,
    plugin_paths,
    safe_output,
)


def wheel(directory, name, version, dependencies=()):
    normalized = name.replace("-", "_")
    stem = f"{normalized}-{version}"
    path = directory / f"{stem}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{normalized}/__init__.py", f"VERSION = {version!r}\n")
        archive.writestr(
            f"{stem}.dist-info/METADATA",
            f"Metadata-Version: 2.3\nName: {name}\nVersion: {version}\n"
            + "".join(f"Requires-Dist: {value}\n" for value in dependencies),
        )
        archive.writestr(
            f"{stem}.dist-info/WHEEL",
            "Wheel-Version: 1.0\nGenerator: offline-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(f"{stem}.dist-info/RECORD", "")
    return path


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    if not shutil.which("uv"):
        pytest.skip("uv is required for runtime integration tests")
    core = tmp_path / "core"
    core.mkdir()
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    wheel(wheels, "pip", "25.0")
    wheel(wheels, "core-dependency", "1.0")
    wheel(wheels, "core-dependency", "2.0")
    wheel(wheels, "plugin-dependency", "1.0", ["shared-dependency>=1"])
    wheel(wheels, "shared-dependency", "1.0")
    wheel(wheels, "shared-dependency", "2.0")
    wheel(wheels, "incompatible-plugin", "1.0", ["core-dependency>=2"])
    wheel(wheels, "second-plugin", "1.0", ["shared-dependency<2"])
    # A dependency-free build backend keeps the entire test offline, including
    # editable Core installation into candidates created by the production code.
    (core / "backend.py").write_text(
        "from pathlib import Path\n"
        "import zipfile\n"
        "def build_editable(wheel_directory, config_settings=None, metadata_directory=None):\n"
        "    name = 'shiritaibot-0.0.0-py3-none-any.whl'\n"
        "    with zipfile.ZipFile(Path(wheel_directory) / name, 'w') as z:\n"
        "        z.writestr('core_fixture.pth', str(Path(__file__).parent / 'src'))\n"
        "        z.writestr('shiritaibot-0.0.0.dist-info/METADATA', "
        "'Metadata-Version: 2.3\\nName: shiritaibot\\nVersion: 0.0.0\\nRequires-Dist: core-dependency>=1\\n')\n"
        "        z.writestr('shiritaibot-0.0.0.dist-info/WHEEL', "
        "'Wheel-Version: 1.0\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n')\n"
        "        z.writestr('shiritaibot-0.0.0.dist-info/RECORD', '')\n"
        "    return name\n"
        "build_wheel = build_editable\n",
        encoding="utf-8",
    )
    source = core / "src/shiritaibot"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("", encoding="utf-8")
    project = {
        "project": {
            "name": "shiritaibot",
            "version": "0.0.0",
            "requires-python": ">=3.12",
            "dependencies": ["core-dependency>=1"],
        },
        "build-system": {"requires": [], "build-backend": "backend", "backend-path": ["."]},
        "tool": {"uv": {"constraint-dependencies": ["core-dependency==1.0"]}},
    }
    (core / "pyproject.toml").write_text(tomli_w.dumps(project), encoding="utf-8")
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_FIND_LINKS", str(wheels))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("UV_PYTHON_DOWNLOADS", "never")
    # Ensure no parent repository venv is used, even with this override inherited.
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(tmp_path / "do-not-touch"))
    subprocess.run(
        ["uv", "lock", "--project", str(core), "--python", sys.executable],
        check=True,
        capture_output=True,
        text=True,
    )
    return RuntimeEnvironment(tmp_path / "home", core=core)


def plugin(runtime, key, requirements):
    path = runtime.home / "plugins" / key
    path.mkdir(parents=True)
    (path / "metadata.yaml").write_text(f"name: {key}\n", encoding="utf-8")
    (path / "requirements.txt").write_text(requirements, encoding="utf-8")
    return path


def versions(plan, *names):
    code = (
        "import importlib.metadata as m, json; print(json.dumps({n:m.version(n) for n in "
        + repr(names)
        + "}))"
    )
    return json.loads(subprocess.check_output([str(plan.python), "-c", code], text=True))


def test_real_uv_install_conflict_removal_and_repair(runtime):
    original_lock = (runtime.core / "uv.lock").read_bytes()
    plugin(runtime, "good", "plugin-dependency>=1\n")
    plugin(runtime, "bad", "incompatible-plugin\n")
    first = asyncio.run(runtime.prepare())
    assert "bad" in first.state["errors"]
    assert "good" in first.state["plugins"]
    assert "core-dependency" in first.state["errors"]["bad"]
    assert versions(first, "core-dependency", "plugin-dependency", "shared-dependency") == {
        "core-dependency": "1.0",
        "plugin-dependency": "1.0",
        "shared-dependency": "2.0",
    }
    assert (runtime.core / "uv.lock").read_bytes() == original_lock
    assert not (runtime.home.parent / "do-not-touch").exists()
    subprocess.run(
        [
            "uv",
            "sync",
            "--project",
            str(runtime.core),
            "--locked",
            "--no-dev",
            "--python",
            sys.executable,
        ],
        env={key: value for key, value in os.environ.items() if key != "UV_PROJECT_ENVIRONMENT"},
        check=True,
        capture_output=True,
    )
    assert versions(first, "plugin-dependency") == {"plugin-dependency": "1.0"}
    # Unrelated new plugins reuse the previously locked transitive versions.
    wheel(Path(os.environ["UV_FIND_LINKS"]), "shared-dependency", "3.0")
    plugin(runtime, "unrelated", "pip>=25\n")
    second = asyncio.run(runtime.prepare())
    assert versions(second, "shared-dependency") == {"shared-dependency": "2.0"}
    # A new combined resolution can change plugin-only transitive versions.
    plugin(runtime, "second", "second-plugin\n")
    third = asyncio.run(runtime.prepare())
    assert versions(third, "shared-dependency") == {"shared-dependency": "1.0"}
    assert versions(first, "shared-dependency") == {"shared-dependency": "2.0"}
    # Removing a plugin prunes packages from the next environment, not the live one.
    shutil.rmtree(runtime.home / "plugins/good")
    shutil.rmtree(runtime.home / "plugins/second")
    fourth = asyncio.run(runtime.prepare())
    output = subprocess.check_output(
        [
            str(fourth.python),
            "-c",
            "import importlib.util; print(importlib.util.find_spec('plugin_dependency'))",
        ],
        text=True,
    )
    assert output.strip() == "None"
    assert versions(first, "plugin-dependency")["plugin-dependency"] == "1.0"
    # An unchanged startup restores a missing dependency using the existing lock.
    dependency = next((fourth.python.parent.parent).rglob("core_dependency/__init__.py"))
    shutil.rmtree(dependency.parent)
    dist_info = next((fourth.python.parent.parent).rglob("core_dependency-1.0.dist-info"))
    shutil.rmtree(dist_info)
    repaired = asyncio.run(runtime.prepare(repair=True))
    assert repaired.directory == fourth.directory
    assert versions(repaired, "core-dependency") == {"core-dependency": "1.0"}


def test_failed_required_plugin_and_install_leave_previous_environment(runtime):
    first = asyncio.run(runtime.prepare())
    pointer = (runtime.root / "current.json").read_bytes()
    plugin(runtime, "bad", "incompatible-plugin\n")
    with pytest.raises(PublicError, match="bad"):
        asyncio.run(runtime.prepare(required="bad"))
    assert (runtime.root / "current.json").read_bytes() == pointer
    assert versions(first, "core-dependency") == {"core-dependency": "1.0"}

    original_command = runtime.command

    async def failed_sync(*args):
        if args[0] == "sync":
            raise PublicError("simulated installation failure")
        return await original_command(*args)

    runtime.command = failed_sync
    with pytest.raises(PublicError, match="installation failure"):
        asyncio.run(runtime.prepare())
    assert (runtime.root / "current.json").read_bytes() == pointer
    assert len(list((runtime.root / "projects").iterdir())) == 1


def test_core_upgrade_rechecks_plugins_against_new_core_lock(runtime):
    plugin(runtime, "old_only", "core-dependency<2\n")
    first = asyncio.run(runtime.prepare())
    assert "old_only" in first.state["plugins"]
    project_path = runtime.core / "pyproject.toml"
    project_path.write_text(
        project_path.read_text("utf-8").replace("core-dependency==1.0", "core-dependency==2.0"),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "uv",
            "lock",
            "--project",
            str(runtime.core),
            "--python",
            sys.executable,
            "--upgrade-package",
            "core-dependency",
        ],
        check=True,
        capture_output=True,
    )
    second = asyncio.run(runtime.prepare())
    assert "old_only" in second.state["errors"]
    assert versions(second, "core-dependency") == {"core-dependency": "2.0"}
    assert versions(first, "core-dependency") == {"core-dependency": "1.0"}


def test_real_uv_relative_wheel_and_python_compatibility(runtime):
    directory = plugin(runtime, "local", "./local_package-1.0-py3-none-any.whl\n")
    wheel(directory, "local-package", "1.0")
    bad = plugin(runtime, "wrong_python", "")
    (bad / "pyproject.toml").write_text(
        '[project]\nrequires-python="<3.0"\ndependencies=[]\n', encoding="utf-8"
    )
    plan = asyncio.run(runtime.prepare())
    assert versions(plan, "local-package") == {"local-package": "1.0"}
    assert "wrong_python" in plan.state["errors"]
    assert "Python" in plan.state["errors"]["wrong_python"]


def test_plugin_update_cannot_displace_existing_plugins(runtime):
    changing = plugin(runtime, "a_changing", "shared-dependency>=1\n")
    plugin(runtime, "b_existing", "second-plugin\n")
    first = asyncio.run(runtime.prepare())
    (changing / "requirements.txt").write_text("shared-dependency>=2\n", encoding="utf-8")
    with pytest.raises(PublicError, match="a_changing"):
        asyncio.run(runtime.prepare(required="a_changing"))
    assert runtime.current().directory == first.directory
    assert versions(first, "shared-dependency") == {"shared-dependency": "1.0"}


def test_reload_retries_cached_resolution_errors(runtime):
    plugin(runtime, "missing", "later-package\n")
    first = asyncio.run(runtime.prepare())
    assert "missing" in first.state["errors"]
    wheel(Path(os.environ["UV_FIND_LINKS"]), "later-package", "1.0")
    second = asyncio.run(runtime.prepare(required="missing"))
    assert "missing" not in second.state["errors"]
    assert versions(second, "later-package") == {"later-package": "1.0"}


def test_disabled_plugin_install_prepares_dependencies_before_enable(runtime, monkeypatch):
    from shiritaibot.config import Settings
    from shiritaibot.plugin.core import PluginManager

    async def save(settings):
        config.value = settings

    async def protect(settings, *args):
        return settings

    config = SimpleNamespace(value=Settings(), save=save)
    manager = PluginManager(
        runtime.home,
        config,
        SimpleNamespace(tools={}),
        SimpleNamespace(protect_settings=protect),
        SimpleNamespace(activity=AsyncMock()),
        AsyncMock(),
    )
    manager.dependencies = runtime
    manager.source_root = runtime.core / "src/shiritaibot/plugin"
    archive = runtime.home.parent / "plugin.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("plugin.toml", "api=1\n")
        stream.writestr("main.py", "class Entry: pass\n")
        stream.writestr("requirements.txt", "plugin-dependency\n")
    rows = asyncio.run(manager.install("installed", archive.read_bytes()))
    row = next(row for row in rows if row["id"] == "installed")
    assert not row["enabled"]
    assert "重启" in row["error"]
    plan = runtime.current()
    assert versions(plan, "plugin-dependency") == {"plugin-dependency": "1.0"}
    # Simulate the newly selected interpreter to check status after restart.
    monkeypatch.setattr(sys, "prefix", str(plan.python.parent.parent))
    manager.errors.clear()
    assert asyncio.run(manager.listing())[0]["error"] == ""


def test_nested_requirements_constraints_markers_and_relative_paths(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "\ufeff-r deps/extra.txt\n-c constraints.txt\n", encoding="utf-8"
    )
    (tmp_path / "deps").mkdir()
    (tmp_path / "deps/extra.txt").write_text(
        "httpx[socks]>=0.28,\\\n <1 ; python_version >= '3.12' # explanation\n"
        "local-package @ ../local\n",
        encoding="utf-8",
    )
    (tmp_path / "local").mkdir()
    (tmp_path / "local/pyproject.toml").write_text(
        '[project]\nname="local-package"\nversion="1.0"\n', encoding="utf-8"
    )
    (tmp_path / "constraints.txt").write_text("httpx==0.28.1\n", encoding="utf-8")
    reader = DeclarationReader()
    declaration = reader.plugin(tmp_path)
    assert declaration.constraints == ["httpx==0.28.1"]
    assert "httpx[socks]" in declaration.dependencies[0]
    assert 'python_version >= "3.12"' in declaration.dependencies[0]
    assert declaration.dependencies[1] == "local-package @ " + (tmp_path / "local").as_uri()
    assert len(reader.inputs) == 4


@pytest.mark.parametrize(
    "contents",
    [
        "--index-url https://example.org/simple\n",
        "-e ./local\n",
        "package==1 --hash=sha256:abc\n",
        "-r requirements.txt\n",
        "package @ https://user:secret@example.org/file.whl\n",
        "package @ https://example.org/file.whl?token=secret\n",
        "${SECRET}\n",
    ],
)
def test_invalid_or_unsupported_inputs_fail_explicitly(tmp_path, contents):
    (tmp_path / "requirements.txt").write_text(contents, encoding="utf-8")
    with pytest.raises(PublicError) as error:
        DeclarationReader().plugin(tmp_path)
    assert "secret" not in str(error.value)


def test_pyproject_priority_and_tools_only_fallback(tmp_path):
    (tmp_path / "requirements.txt").write_text("old-package\n", encoding="utf-8")
    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\ndependencies=["new-package>=1"]\n', encoding="utf-8")
    assert DeclarationReader().plugin(tmp_path).dependencies == ["new-package>=1"]
    project.write_text("[tool.ruff]\nline-length=100\n", encoding="utf-8")
    assert DeclarationReader().plugin(tmp_path).dependencies == ["old-package"]
    project.write_text('[project]\ndynamic=["dependencies"]\n', encoding="utf-8")
    with pytest.raises(PublicError, match="静态"):
        DeclarationReader().plugin(tmp_path)


def test_named_local_url_keeps_hash_fragment_and_tracks_archive_changes(tmp_path):
    path = tmp_path / "local_package-1.0-py3-none-any.whl"
    path.write_bytes(b"first archive")
    reader = DeclarationReader()
    expected = "local-package @ " + path.as_uri() + "#sha256=abc"
    assert (
        reader.requirement("local-package @ ./" + path.name + "#sha256=abc", tmp_path) == expected
    )
    initial = reader.inputs[str(path)]
    path.write_bytes(b"changed archive")
    assert reader.requirement(expected, tmp_path) == expected
    assert reader.inputs[str(path)] != initial


def test_source_first_discovery_and_secret_redaction(tmp_path):
    core, home = tmp_path / "core", tmp_path / "home"
    for directory in [core / "src/shiritaibot/plugin/example", home / "plugins/example"]:
        directory.mkdir(parents=True)
        (directory / "plugin.toml").write_text("api=1\n", encoding="utf-8")
    assert plugin_paths(core, home)["example"] == core / "src/shiritaibot/plugin/example"
    message = safe_output("failed https://user:secret@example.org/pkg?token=private because foo<2")
    assert "secret" not in message and "private" not in message
    assert "foo<2" in message
