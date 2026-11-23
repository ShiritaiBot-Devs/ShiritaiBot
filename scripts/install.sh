#!/usr/bin/env bash
# ShiritaiBot user-local installer. Requires Bash, curl, tar and a SHA-256 utility.
set -Eeuo pipefail

# Messages follow the terminal's language, like the shiritaibot CLI: English when
# LC_ALL / LANG is English, Simplified Chinese (the default) otherwise.
case "${LC_ALL:-${LANG:-}}" in
  en|en[_.@-]*) SHIRITAI_EN=1 ;;
  *) SHIRITAI_EN=0 ;;
esac

# shiritai_say ZH EN [ARG...]: print one line in the terminal's language; %s takes ARGs.
shiritai_say() {
  local text=$1
  [ "$SHIRITAI_EN" -eq 0 ] || text=$2
  shift 2
  printf -- "$text\n" "$@"
}

shiritai_install() {
  local target='' port=8765 no_start=0 platform arch temp_dir='' uv_bin node_dir node_bin npm_bin
  local command_dir command_entry='' pypi_index=''
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --dir) [ "$#" -ge 2 ] || { shiritai_say '--dir 需要目录参数。' '--dir needs a folder.' >&2; return 1; }; target=$2; shift 2 ;;
      --port) [ "$#" -ge 2 ] || { shiritai_say '--port 需要端口参数。' '--port needs a port number.' >&2; return 1; }; port=$2; shift 2 ;;
      --index) [ "$#" -ge 2 ] || { shiritai_say '--index 需要 URL 参数。' '--index needs a URL.' >&2; return 1; }; pypi_index=$2; shift 2 ;;
      --no-start) no_start=1; shift ;;
      --help|-h)
        shiritai_say 'ShiritaiBot 一键部署' 'ShiritaiBot one-click install'
        shiritai_say '用法: bash install.sh [--dir PATH] [--port 8765] [--index URL] [--no-start]' 'Usage: bash install.sh [--dir PATH] [--port 8765] [--index URL] [--no-start]'
        shiritai_say '--index 指定 PyPI 镜像（导出为 UV_DEFAULT_INDEX）。' '--index sets a PyPI mirror (exported as UV_DEFAULT_INDEX).'
        shiritai_say '默认安装后在当前终端启动；Ctrl+C 停止。' 'By default it starts in this terminal after installing; press Ctrl+C to stop.'
        return 0 ;;
      *) shiritai_say '未知参数: %s' 'Unknown option: %s' "$1" >&2; return 1 ;;
    esac
  done
  [[ "$port" =~ ^[0-9]{1,5}$ ]] || { shiritai_say '端口应在 1–65535 之间。' 'The port must be between 1 and 65535.' >&2; return 1; }
  port=$((10#$port))
  [ "$port" -ge 1 ] && [ "$port" -le 65535 ] || { shiritai_say '端口应在 1–65535 之间。' 'The port must be between 1 and 65535.' >&2; return 1; }

  case "$(uname -s)" in
    Darwin) platform=darwin; target=${target:-"$HOME/Library/Application Support/ShiritaiBot"} ;;
    Linux) platform=linux; target=${target:-"${XDG_DATA_HOME:-$HOME/.local/share}/shiritaibot"} ;;
    *) shiritai_say '此脚本用于 Linux 或 macOS；Windows 请使用 install.ps1。' 'This script is for Linux or macOS; on Windows, use install.ps1.' >&2; return 1 ;;
  esac
  case "$(uname -m)" in
    x86_64|amd64) arch=x64 ;;
    aarch64|arm64) arch=arm64 ;;
    *) shiritai_say '当前一键部署支持 x64 和 ARM64。' 'One-click install supports x64 and ARM64.' >&2; return 1 ;;
  esac
  for tool in curl tar awk mktemp; do
    command -v "$tool" >/dev/null 2>&1 || { shiritai_say '请先安装 %s，再运行此命令。' 'Install %s first, then run this command again.' "$tool" >&2; return 1; }
  done
  if [ -e "$target" ] && [ ! -d "$target" ]; then
    shiritai_say '安装路径不是目录: %s' 'The install path is not a folder: %s' "$target" >&2; return 1
  fi
  if [ -d "$target" ] && [ -n "$(ls -A "$target")" ] &&
     ! [ -f "$target/.shiritaibot-installer" ]; then
    shiritai_say '该目录已有其他文件，请用 --dir 选择空目录: %s' 'This folder already has other files; choose an empty one with --dir: %s' "$target" >&2; return 1
  fi
  mkdir -p "$target"
  target=$(cd "$target" && pwd -P)
  printf '%s\n' 'shiritaibot-installer-v1' > "$target/.shiritaibot-installer"
  mkdir -p "$target/runtime/bin" "$target/data"
  chmod 700 "$target/data"
  temp_dir=$(mktemp -d "$target/.install-XXXXXX")
  SHIRITAI_STAGING_DIR=$temp_dir
  # Only remove this invocation's staging files; app/ and data/ survive failures.
  trap 'shiritai_result=$?; [ -z "${SHIRITAI_STAGING_DIR:-}" ] || rm -rf -- "$SHIRITAI_STAGING_DIR"; if [ "$shiritai_result" -ne 0 ]; then shiritai_say "安装未完成，请查看上面的错误；修复后可重新运行同一命令。" "The install did not finish. Check the errors above, fix them, and run the same command again." >&2; fi' EXIT
  export UV_PYTHON_INSTALL_DIR="$target/runtime/python"
  export UV_PYTHON_BIN_DIR="$target/runtime/bin"
  export UV_CACHE_DIR="$target/runtime/cache/uv"
  export npm_config_cache="$target/runtime/cache/npm"
  if [ -n "$pypi_index" ]; then export UV_DEFAULT_INDEX="$pypi_index"; fi
  export PATH="$target/runtime/bin:$target/runtime/node/bin:$PATH"

  shiritai_download() {
    curl --fail --show-error --location --retry 2 --connect-timeout 20 --output "$2" "$1"
  }
  shiritai_node_works() {
    local candidate candidate_dir candidate_npm
    while IFS= read -r candidate; do
      "$candidate" -e 'const [a,b]=process.versions.node.split(".").map(Number);process.exit((a===20&&b>=19)||(a===22&&b>=12)||a>=24?0:1)' >/dev/null 2>&1 || continue
      candidate_dir=$(dirname "$candidate")
      candidate_npm="$candidate_dir/npm"
      if [ ! -x "$candidate_npm" ]; then candidate_npm=$(type -P npm 2>/dev/null) || continue; fi
      PATH="$candidate_dir:$PATH" "$candidate_npm" --version >/dev/null 2>&1 || continue
      node_bin=$candidate
      node_dir=$candidate_dir
      npm_bin=$candidate_npm
      return 0
    done < <(type -aP node 2>/dev/null || true)
    return 1
  }

  if [ -d "$target/app" ]; then
    [ -f "$target/app/src/shiritaibot/cli.py" ] && [ -f "$target/app/uv.lock" ] &&
      [ -f "$target/app/web/package-lock.json" ] || { shiritai_say 'app 目录不是完整的 ShiritaiBot 源码，请先检查该目录。' 'The app folder is not a complete ShiritaiBot source tree; check it first.' >&2; return 1; }
    shiritai_say '使用已有 ShiritaiBot 源码。' 'Using the existing ShiritaiBot source.'
  elif [ -e "$target/app" ]; then
    shiritai_say 'app 路径已被其他文件占用。' 'Something else is already at the app path.' >&2; return 1
  else
    shiritai_say '[1/4] 下载 ShiritaiBot' '[1/4] Downloading ShiritaiBot'
    shiritai_download 'https://github.com/XCM42-Orion/ShiritaiBot/archive/refs/heads/main.tar.gz' "$temp_dir/source.tar.gz"
    mkdir "$temp_dir/app"
    tar -xzf "$temp_dir/source.tar.gz" -C "$temp_dir/app" --strip-components=1
    [ -f "$temp_dir/app/src/shiritaibot/cli.py" ] && [ -f "$temp_dir/app/uv.lock" ] &&
      [ -f "$temp_dir/app/web/package-lock.json" ] || { shiritai_say '下载的源码不完整。' 'The downloaded source is incomplete.' >&2; return 1; }
    mv "$temp_dir/app" "$target/app"
  fi

  shiritai_say '[2/4] 准备 Python 和 Node.js' '[2/4] Preparing Python and Node.js'
  if ! command -v uv >/dev/null 2>&1; then
    shiritai_download 'https://astral.sh/uv/install.sh' "$temp_dir/uv-install.sh"
    UV_UNMANAGED_INSTALL="$target/runtime/bin" sh "$temp_dir/uv-install.sh"
  fi
  uv_bin=$(type -P uv)
  [ -n "$uv_bin" ] && "$uv_bin" --version || { shiritai_say 'uv 无法运行。' "uv doesn't run." >&2; return 1; }

  if ! shiritai_node_works; then
    local archive expected actual
    shiritai_say '准备安装目录内的 Node.js 24。' 'Preparing Node.js 24 inside the install folder.'
    shiritai_download 'https://nodejs.org/download/release/latest-v24.x/SHASUMS256.txt' "$temp_dir/SHASUMS256.txt"
    archive=$(awk -v suffix="-$platform-$arch.tar.gz" '$2 ~ (suffix "$") && $2 ~ /^node-v24\./ { print $2; exit }' "$temp_dir/SHASUMS256.txt")
    [ -n "$archive" ] || { shiritai_say '未找到适合当前系统的 Node.js 24。' 'No Node.js 24 build found for this system.' >&2; return 1; }
    expected=$(awk -v file="$archive" '$2 == file { print $1; exit }' "$temp_dir/SHASUMS256.txt")
    [[ "$expected" =~ ^[[:xdigit:]]{64}$ ]] || { shiritai_say 'Node.js 校验清单无效。' 'The Node.js checksum list is invalid.' >&2; return 1; }
    shiritai_download "https://nodejs.org/download/release/latest-v24.x/$archive" "$temp_dir/node.tar.gz"
    if command -v sha256sum >/dev/null 2>&1; then
      actual=$(sha256sum "$temp_dir/node.tar.gz" | awk '{print $1}')
    elif command -v shasum >/dev/null 2>&1; then
      actual=$(shasum -a 256 "$temp_dir/node.tar.gz" | awk '{print $1}')
    else
      shiritai_say '请安装 sha256sum 或 shasum 后重试。' 'Install sha256sum or shasum, then try again.' >&2; return 1
    fi
    [ "$actual" = "$expected" ] || { shiritai_say 'Node.js 下载校验失败，请重试。' "The Node.js download didn't pass the checksum; try again." >&2; return 1; }
    mkdir "$temp_dir/node"
    tar -xzf "$temp_dir/node.tar.gz" -C "$temp_dir/node" --strip-components=1
    "$temp_dir/node/bin/node" --version
    PATH="$temp_dir/node/bin:$PATH" "$temp_dir/node/bin/npm" --version
    if [ -e "$target/runtime/node" ]; then
      local previous_node
      previous_node=$(mktemp -d "$target/runtime/.node-backup-XXXXXX")
      mv "$target/runtime/node" "$previous_node/node"
    fi
    mv "$temp_dir/node" "$target/runtime/node"
    hash -r
  fi
  shiritai_node_works || { shiritai_say 'Node.js/npm 无法运行，请检查系统兼容性。' "Node.js/npm doesn't run; check that this system is supported." >&2; return 1; }
  export PATH="$node_dir:$PATH"
  shiritai_say '使用 Node.js %s: %s' 'Using Node.js %s: %s' "$("$node_bin" --version)" "$node_bin"

  shiritai_say '[3/4] 安装依赖并构建管理页面' '[3/4] Installing dependencies and building the admin console'
  cd "$target/app"
  "$uv_bin" sync --frozen --no-dev
  "$uv_bin" run --frozen --no-sync python -m shiritaibot.cli sync --home "$target/data"
  "$npm_bin" --prefix web ci --no-audit --no-fund
  "$npm_bin" --prefix web run build

  # Save executable locations without sourcing editable shell configuration.
  {
    printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail'
    printf 'SHIRITAI_UV=%q\nSHIRITAI_NODE_DIR=%q\nSHIRITAI_PORT=%q\n' "$uv_bin" "$node_dir" "$port"
    cat <<'LAUNCHER'
SHIRITAI_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
export PATH="$SHIRITAI_ROOT/runtime/bin:$SHIRITAI_NODE_DIR:$PATH"
export UV_PYTHON_INSTALL_DIR="$SHIRITAI_ROOT/runtime/python"
export UV_PYTHON_BIN_DIR="$SHIRITAI_ROOT/runtime/bin"
export UV_CACHE_DIR="$SHIRITAI_ROOT/runtime/cache/uv"
cd "$SHIRITAI_ROOT/app"
case "${1:-run}" in
  token) shift; exec "$SHIRITAI_UV" run --frozen --no-sync python -m shiritaibot.cli token --home "$SHIRITAI_ROOT/data" --create "$@" ;;
  run) [ "$#" -eq 0 ] || shift; exec "$SHIRITAI_UV" run --frozen --no-sync python -m shiritaibot.cli run --home "$SHIRITAI_ROOT/data" --port "$SHIRITAI_PORT" "$@" ;;
  *) printf '%s\n' '用法 / Usage: start.sh [run|token]'; exit 1 ;;
esac
LAUNCHER
  } > "$temp_dir/start.sh"
  chmod 700 "$temp_dir/start.sh"
  mv "$temp_dir/start.sh" "$target/start.sh"

  command_dir=${SHIRITAI_COMMAND_DIR:-"$HOME/.local/bin"}
  if { [ ! -e "$command_dir/shiritaibot" ] && [ ! -L "$command_dir/shiritaibot" ]; } ||
     { [ -f "$command_dir/shiritaibot" ] && [ ! -L "$command_dir/shiritaibot" ] &&
       [ "$(sed -n '2p' "$command_dir/shiritaibot")" = '# Managed by ShiritaiBot installer' ]; }; then
    mkdir -p "$command_dir"
    {
      printf '%s\n' '#!/usr/bin/env bash' '# Managed by ShiritaiBot installer'
      printf 'exec %q "$@"\n' "$target/start.sh"
    } > "$temp_dir/shiritaibot"
    chmod 700 "$temp_dir/shiritaibot"
    mv "$temp_dir/shiritaibot" "$command_dir/shiritaibot"
    case ":$PATH:" in
      *":$command_dir:"*) command_entry="$command_dir/shiritaibot" ;;
    esac
  fi
  rm -rf -- "$temp_dir"
  temp_dir=''
  SHIRITAI_STAGING_DIR=''
  printf '\n'
  shiritai_say '[4/4] 部署完成' '[4/4] Install complete'
  shiritai_say '管理页面: http://127.0.0.1:%s' 'Admin console: http://127.0.0.1:%s' "$port"
  shiritai_say '重新启动: %s' 'Restart: %s' "$(printf '%q' "$target/start.sh")"
  shiritai_say '获取管理令牌: %s token' 'Get the admin token: %s token' "$(printf '%q' "$target/start.sh")"
  if [ -n "$command_entry" ]; then
    shiritai_say '快捷启动: shiritaibot' 'Shortcut to start: shiritaibot'
    shiritai_say '快捷获取令牌: shiritaibot token' 'Shortcut to get the token: shiritaibot token'
  fi
  shiritai_say '数据目录: %s' 'Data folder: %s' "$target/data"
  if [ "$no_start" -eq 0 ]; then
    shiritai_say '正在启动。保持此终端打开；按 Ctrl+C 停止。' 'Starting. Keep this terminal open; press Ctrl+C to stop.'
    exec "$target/start.sh"
  fi
}

shiritai_install "$@"
