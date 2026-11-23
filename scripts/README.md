# 一键部署

[English](README.en.md) · **简体中文**

官网首页与快速开始文档提供可复制的系统命令。脚本准备运行环境、获取源码、
安装依赖并构建管理后台，完成后在当前终端启动服务。

已有源码时可以直接执行：

```sh
bash scripts/install.sh
```

Windows PowerShell：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install.ps1
```

`install.sh` 的提示语跟随终端语言：`LC_ALL` / `LANG` 为英文时显示英文，其余情况显示中文。`install.ps1` 的提示语为英文。

## 选项

| 用途 | macOS / Linux | Windows |
|---|---|---|
| 安装目录 | `--dir PATH` | `-InstallDir PATH` |
| 端口（默认 8765） | `--port 8765` | `-Port 8765` |
| Python 包镜像 | `--index URL` | `-PypiIndex URL` |
| 安装完成后暂不启动 | `--no-start` | `-NoStart` |

`--index` / `-PypiIndex` 只影响本次安装，导出为 `uv` 的 `UV_DEFAULT_INDEX`。不传时
沿用环境里已有的值；网络访问 PyPI 慢时可以用它指定镜像，例如
`--index https://pypi.tuna.tsinghua.edu.cn/simple`。

默认目录是 macOS 的 `~/Library/Application Support/ShiritaiBot`、Linux 的
`${XDG_DATA_HOME:-~/.local/share}/shiritaibot`、Windows 的
`%LOCALAPPDATA%\ShiritaiBot`。目录中 `app/` 保存源码、`data/` 保存聊天和配置、
`runtime/` 保存运行环境与缓存。再次执行会复用已有源码和数据。

其中 `app/.venv/` 用于启动和开发，Bot 与插件实际使用的总锁文件和 Python 环境位于 `data/runtime/projects/<generation>/`，由 `data/runtime/current.json` 选择。安装脚本会提前准备该环境，包括选择暂不启动时；以后也可在 `app/` 中执行 `uv run shiritaibot sync --home ../data` 单独同步。详见 [插件依赖管理](../docs/PLUGINS.md#插件依赖管理)。

安装输出会给出管理页面、重启命令和获取令牌的命令。Bash 使用安装目录里的
`start.sh` 启动、`start.sh token` 获取令牌；Windows 使用输出的 PowerShell
命令启动，附加 `-Token` 获取令牌。尚未启动过时也能直接运行令牌命令，它会当场
生成并保存令牌。在管理页面添加模型并连接 QQ；按 Ctrl+C 停止前台服务。

macOS / Linux 还会在 `~/.local/bin` 添加 `shiritaibot` 快捷入口。
该目录已在 PATH 中时，可从任意目录运行 `shiritaibot token`，获取的始终是
安装实例的令牌；`shiritaibot` 可重新启动同一实例。已有的其他同名命令不会被覆盖。
可用 `SHIRITAI_COMMAND_DIR` 指定快捷入口目录。Windows 继续使用 `start.ps1 -Token`。

脚本从当前仓库 `main` 下载源码，通过 uv 遵循项目 `.python-version` 准备 Python，
复用兼容的 Node.js 或下载校验过 SHA256 的官方 Node.js 24。新增环境放在安装目录，
无须修改系统包、Shell 配置或全局 PowerShell 执行策略。
检测会实际运行 Node.js 与配套 npm，并逐个尝试 PATH 中的候选项。Windows 使用
原生 `node.exe` 直接运行 `npm-cli.js`，兼容系统自带的 PowerShell 5.1。
已有可用环境会直接复用；损坏的安装器私有 Node 会保留到 `runtime/.node-backup-*`
后再替换。

实现参考了 [uv 安装文档](https://docs.astral.sh/uv/getting-started/installation/)、
[uv Python 管理](https://docs.astral.sh/uv/concepts/python-versions/) 和
[Node.js 官方分发](https://nodejs.org/download/release/latest-v24.x/)。没有复制第三方安装器源码。

## 验证

```sh
bash -n scripts/install.sh
```

## Docker 部署

在装有 Docker Engine 和 Compose 插件的 Linux 主机上，从源码目录运行：

```bash
bash scripts/deploy-docker.sh
```

脚本会按锁文件构建 WebUI 和 Python 依赖，并等待健康检查通过。WebUI 默认监听宿主机的 `127.0.0.1:8765`，同一主机上的 NapCat 可以继续使用回环地址。数据默认保存在 `./.local/docker-data`。

```bash
SHIRITAI_PORT=8766 SHIRITAI_DATA_DIR=/srv/shiritaibot \
  SHIRITAI_ALLOWED_HOSTS="bot.example.com" bash scripts/deploy-docker.sh
```

使用 `docker compose exec bot shiritaibot token --home /data` 读取管理令牌，使用 `docker compose logs -f bot` 查看日志。更新源码后用同一组环境变量重跑脚本。

迁移已有安装时，先停止使用该数据目录的进程，再令 `SHIRITAI_DATA_DIR` 指向该目录。镜像打包仓库内置插件，独立插件从挂载数据目录的 `plugins/` 加载。
