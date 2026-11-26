# ShiritaiBot Desktop

源码部署使用仓库根目录的 WebUI 部署流程。Windows 和 macOS 桌面版使用同一个 WebUI，
由 Electron 启动本地 Python 服务。安装包自带 Python 和 uv，首次启动需要联网安装依赖。
插件仍可通过 ZIP 或公开 GitHub 仓库安装。数据、配置和插件保存在桌面应用的用户数据目录下的
`data/`。Windows 安装向导可分别选择程序目录和数据目录。桌面版也可在通用设置中
选择数据目录，重启后生效，原目录里的数据会保留。首次启动时重新准备 Python 运行环境。

桌面版启动时进入对话模式。通用设置可启用会话任务结束通知，并设置关闭窗口时询问、
退出或最小化到托盘。Windows 用户可从同一页面打开系统通知设置。

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

在 GitHub Actions 中对待发布的 commit 或 tag 手动运行 **Desktop packages**，下载对应平台的
artifact。验收后，将安装包作为附件上传至同一版本的 GitHub Release。桌面应用的「检查更新」会
打开 GitHub Releases，用户可以手动下载安装。面向公众发布前，给 Windows 程序和安装包装上可信
签名，并完成 macOS Developer ID 签名和公证。仓库中的构建配置用于本地及 CI 试包。
