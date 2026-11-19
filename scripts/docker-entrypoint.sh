#!/bin/sh
set -efu
set -- shiritaibot run --home /data --port "${SHIRITAI_PORT:-8765}"
# One host per whitespace-delimited word. Disable glob expansion for wildcard hosts.
for host in ${SHIRITAI_ALLOWED_HOSTS:-}; do
    set -- "$@" --allowed-host "$host"
done
exec "$@"
