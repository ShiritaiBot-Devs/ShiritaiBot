# One-click install

**English** · [简体中文](README.md)

The website's home page and Quick Start guide give commands to copy for each system. The script prepares the runtime, gets the source, installs dependencies, builds the admin console, and then starts the service in the current terminal.

If you already have the source, run:

```sh
bash scripts/install.sh
```

Windows PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install.ps1
```

## Options

| Purpose | macOS / Linux | Windows |
|---|---|---|
| Install folder | `--dir PATH` | `-InstallDir PATH` |
| Port (default 8765) | `--port 8765` | `-Port 8765` |
| Python package mirror | `--index URL` | `-PypiIndex URL` |
| Don't start after installing | `--no-start` | `-NoStart` |

`--index` / `-PypiIndex` only affects this install and is exported as `uv`'s `UV_DEFAULT_INDEX`. Without it, any value already in the environment is used. If PyPI is slow from your network, use it to pick a mirror, for example `--index https://pypi.tuna.tsinghua.edu.cn/simple`.

The default folder is `~/Library/Application Support/ShiritaiBot` on macOS, `${XDG_DATA_HOME:-~/.local/share}/shiritaibot` on Linux, and `%LOCALAPPDATA%\ShiritaiBot` on Windows. Inside it, `app/` holds the source, `data/` holds chats and configuration, and `runtime/` holds the runtime environment and caches. Running the script again reuses the existing source and data.

`app/.venv/` is for starting and development. The combined lock file and Python environment the bot and plugins actually use are in `data/runtime/projects/<generation>/`, chosen by `data/runtime/current.json`. The install script prepares this environment ahead of time, even with "don't start". You can also sync it on its own later by running `uv run shiritaibot sync --home ../data` in `app/`. See [Plugin dependency management](../docs/PLUGINS.en.md#managing-plugin-dependencies).

The install output shows the admin page address, the restart command, and the command to get the token. On Bash, start with `start.sh` in the install folder and get the token with `start.sh token`. On Windows, start with the PowerShell command shown in the output, and add `-Token` to get the token. The token command works even before the first start: it generates and saves a token on the spot. Add models and connect QQ on the admin page; press Ctrl+C to stop the foreground service.

On macOS / Linux, a `shiritaibot` shortcut is also added to `~/.local/bin`. If that folder is on your PATH, `shiritaibot token` from any folder always gets the installed instance's token, and `shiritaibot` starts the same instance again. An existing command with the same name is never overwritten. Use `SHIRITAI_COMMAND_DIR` to choose the shortcut folder. On Windows, keep using `start.ps1 -Token`.

The script downloads the source from this repository's `main`, prepares Python through uv following the project's `.python-version`, and reuses a compatible Node.js or downloads the official Node.js 24 with its SHA256 checked. New environments go in the install folder; system packages, shell configuration, and the global PowerShell execution policy are left alone. Detection actually runs Node.js and its npm, trying each candidate on PATH in turn. On Windows, the native `node.exe` runs `npm-cli.js` directly, which works with the built-in PowerShell 5.1. A working existing environment is reused; a broken installer-private Node is kept in `runtime/.node-backup-*` before being replaced.

The implementation follows the [uv installation docs](https://docs.astral.sh/uv/getting-started/installation/), [uv Python management](https://docs.astral.sh/uv/concepts/python-versions/), and [official Node.js releases](https://nodejs.org/download/release/latest-v24.x/). No third-party installer source was copied.

## Check

```sh
bash -n scripts/install.sh
```

## Docker deployment

On a Linux host with Docker Engine and the Compose plugin, run this from the source folder:

```bash
bash scripts/deploy-docker.sh
```

The script builds the WebUI and Python dependencies from the lock files and waits for the health check to pass. The WebUI listens on the host's `127.0.0.1:8765` by default, so NapCat on the same host can keep using the loopback address. Data is stored in `./.local/docker-data` by default.

```bash
SHIRITAI_PORT=8766 SHIRITAI_DATA_DIR=/srv/shiritaibot \
  SHIRITAI_ALLOWED_HOSTS="bot.example.com" bash scripts/deploy-docker.sh
```

Read the admin token with `docker compose exec bot shiritaibot token --home /data`, and view logs with `docker compose logs -f bot`. After updating the source, rerun the script with the same environment variables.

To migrate an existing install, first stop any process using that data folder, then point `SHIRITAI_DATA_DIR` at it. The image includes the repository's built-in plugins; standalone plugins load from `plugins/` in the mounted data folder.
