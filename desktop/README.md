# ShiritaiBot Desktop

源码部署使用仓库根目录的 WebUI 部署流程。Windows 和 macOS 桌面版使用同一个 WebUI，
由 Electron 启动本地 Python 服务。安装包自带 Python 和 uv，首次启动需要联网安装依赖。
插件仍可通过 ZIP 或公开 GitHub 仓库安装。数据、配置和插件保存在桌面应用的用户数据目录下的
`data/`。Windows 安装向导可分别选择程序目录和数据目录。桌面版也可在通用设置中
选择数据目录，重启后生效，原目录里的数据会保留。首次启动时重新准备 Python 运行环境。

桌面版启动时进入对话模式。通用设置可启用会话任务结束通知，并设置关闭窗口时询问、
退出或最小化到托盘。Windows 用户可从同一页面打开系统通知设置。

准备 Python 依赖时会探测官方 PyPI、清华、中科大和阿里云镜像。首次启动优先尝试
响应最快的源，后续会优先复用已准备好的环境。遇到解析或同步失败会切换到下一个源。
设置了 `UV_DEFAULT_INDEX` 等 uv 源配置时使用指定的源。

## 本地开发

安装 Node.js 24 和 uv 后，在仓库根目录运行：

```sh
npm --prefix web ci
npm --prefix desktop ci
npm run dev
```

命令会打开 Electron 窗口，终端会打印 Vite 的本地地址和后端日志。React 和 CSS
保存后热更新，Python 源码变更后自动重启本地服务。修改 Electron 主进程或
preload 脚本后，重新运行命令。默认开发数据和桌面偏好保存在 `.local/desktop-dev/`。
按 Ctrl+C 结束开发会话。

## 构建试包

在 Windows 或 macOS 上安装 Git、Node.js 24 和 uv，然后在仓库根目录运行：

```sh
npm --prefix web ci
npm --prefix desktop ci
npm --prefix desktop run dist
```

构建产物在 `desktop/dist/`。Windows 生成 NSIS `.exe`，macOS 生成 `.dmg`。
每个平台用目标架构的机器构建，因为安装包中的 Python 和 uv 与系统架构绑定。
`npm --prefix desktop run pack` 可生成解包目录，用于本地调试。
更新版本时，保持 Core 版本与 `desktop/package.json`、`desktop/package-lock.json` 一致。

## 发布

发布 GitHub Release 时，**Desktop packages** 会从该 Release 的 tag 构建 Windows x64 的 NSIS
安装包，以及 macOS Apple Silicon 和 Intel 的 DMG。三种安装包全部构建成功后会自动上传到同一个
Release。tag 须与 Core 和桌面包版本一致，可带 `v` 前缀，例如 `0.3.2` 或 `v0.3.2`。

也可以在 GitHub Actions 中手动运行 **Desktop packages**，下载构建产物进行验收。
桌面应用的「检查更新」会打开 GitHub Releases，用户可以手动下载安装。
自动构建沿用现有签名配置。Windows 安装包未做可信签名，macOS 使用临时签名且未公证。
