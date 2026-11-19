#!/usr/bin/env bash
set -euo pipefail

if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    cat <<'HELP'
Usage: bash scripts/deploy-docker.sh
Build and start ShiritaiBot with Docker Compose on Linux.
Optional environment variables:
  SHIRITAI_DATA_DIR        Persistent data directory (default: ./.local/docker-data)
  SHIRITAI_PORT            Local WebUI port (default: 8765)
  SHIRITAI_ALLOWED_HOSTS   Space-separated reverse-proxy hosts
HELP
    exit 0
fi
if (( $# )); then
    printf 'Unknown argument: %s\n' "$1" >&2
    exit 2
fi

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
if ! docker compose version >/dev/null 2>&1; then
    printf 'Docker and the Docker Compose plugin are required.\n' >&2
    exit 1
fi

export SHIRITAI_UID="$(id -u)" SHIRITAI_GID="$(id -g)"
export SHIRITAI_DATA_DIR="${SHIRITAI_DATA_DIR:-./.local/docker-data}"
export SHIRITAI_PORT="${SHIRITAI_PORT:-8765}"
if [[ ! "$SHIRITAI_PORT" =~ ^[1-9][0-9]{0,4}$ ]] || (( SHIRITAI_PORT > 65535 )); then
    printf 'SHIRITAI_PORT must be between 1 and 65535.\n' >&2
    exit 2
fi
umask 077
mkdir -p -- "$SHIRITAI_DATA_DIR"
if [[ ! -w "$SHIRITAI_DATA_DIR" ]]; then
    printf 'The data directory must be writable: %s\n' "$SHIRITAI_DATA_DIR" >&2
    exit 1
fi
if ! docker compose up --detach --build --wait --wait-timeout 300; then
    printf 'Deployment did not become healthy. Inspect it with docker compose logs bot.\n' >&2
    exit 1
fi
printf 'WebUI: http://127.0.0.1:%s\n' "$SHIRITAI_PORT"
printf 'Admin token: docker compose exec bot shiritaibot token --home /data\n'
