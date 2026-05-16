#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HOST="${DROS_TEST_GW_HOST:-test-gw}"
TARGET="${DROS_TEST_GW_DIR:-/opt/dros}"

ssh "$HOST" "mkdir -p '$TARGET'"
rsync -az --delete \
  --exclude-from "$ROOT/.rsync-test-gw-exclude" \
  "$ROOT/" \
  "$HOST:$TARGET/"

echo "synced $ROOT -> $HOST:$TARGET"
