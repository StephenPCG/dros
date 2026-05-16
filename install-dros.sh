#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="${DROS_SOURCE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
PROFILE="${DROS_PROFILE:-release}"
ETC_DIR="/etc/dros"
SYSTEMD_DIR="/etc/systemd/system"
BIN_DIR="/usr/local/bin"
GATEWAY_DIR="/opt/gateway"
UV_BIN="${DROS_UV_BIN:-}"

usage() {
  cat <<'EOF'
Usage:
  install-dros.sh [--profile release|test]
  install-dros.sh --test

Install DROS from the current source tree. The release profile writes
/etc/dros/settings.yaml. The test profile writes /etc/dros/settings-test.yaml
and installs independent test service names.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --test)
      PROFILE="test"
      shift
      ;;
    --profile)
      PROFILE="$2"
      shift 2
      ;;
    --source-dir)
      SOURCE_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "$PROFILE" in
  release|test) ;;
  *)
    echo "unknown profile: $PROFILE" >&2
    exit 2
    ;;
esac

SETTINGS_PATH="$ETC_DIR/settings.yaml"
DAEMON_SERVICE="dros-daemon.service"
WEB_SERVICE="dros-web.service"
DAEMON_SOCKET="$GATEWAY_DIR/run/drosd.sock"
WEB_PORT="8765"
CONFIGS_DIR="$GATEWAY_DIR/configs"
LOGS_DIR="$GATEWAY_DIR/logs"
RUN_DIR="$GATEWAY_DIR/run"
CONTAINERS_DIR="$GATEWAY_DIR/containers"
SYS_ROOT="/"

if [[ "$PROFILE" == "test" ]]; then
  SETTINGS_PATH="$ETC_DIR/settings-test.yaml"
  DAEMON_SERVICE="dros-daemon-test.service"
  WEB_SERVICE="dros-web-test.service"
  DAEMON_SOCKET="$GATEWAY_DIR/test/run/drosd-test.sock"
  WEB_PORT="8766"
  CONFIGS_DIR="$GATEWAY_DIR/test/configs"
  LOGS_DIR="$GATEWAY_DIR/test/logs"
  RUN_DIR="$GATEWAY_DIR/test/run"
  CONTAINERS_DIR="$GATEWAY_DIR/test/containers"
  SYS_ROOT="$GATEWAY_DIR/test-sysroot"
fi

if [[ "${EUID}" -ne 0 ]]; then
  exec sudo --preserve-env=DROS_SOURCE_DIR,DROS_PROFILE,DROS_UV_BIN \
    DROS_SOURCE_DIR="$SOURCE_DIR" DROS_PROFILE="$PROFILE" DROS_UV_BIN="$UV_BIN" \
    "$0" "$@"
fi

if [[ ! -f "$SOURCE_DIR/pyproject.toml" ]]; then
  echo "DROS source not found at $SOURCE_DIR" >&2
  exit 1
fi

find_uv() {
  if [[ -n "$UV_BIN" && -x "$UV_BIN" ]]; then
    printf '%s\n' "$UV_BIN"
    return 0
  fi
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return 0
  fi

  local user_home=""
  if [[ -n "${SUDO_USER:-}" && "${SUDO_USER:-}" != "root" ]]; then
    user_home="$(getent passwd "$SUDO_USER" | cut -d: -f6 || true)"
  fi

  local candidates=(
    /usr/local/bin/uv
    /usr/bin/uv
    /home/stephen/.local/bin/uv
  )
  if [[ -n "$user_home" ]]; then
    candidates+=("$user_home/.local/bin/uv")
  fi

  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

UV_BIN="$(find_uv)" || {
  echo "uv was not found. Install uv before running this script." >&2
  exit 1
}

install -d -m 0755 "$ETC_DIR" "$ETC_DIR/nftables.d" "$SYSTEMD_DIR" "$BIN_DIR"
install -d -m 0755 \
  "$GATEWAY_DIR/configs" \
  "$GATEWAY_DIR/logs" \
  "$GATEWAY_DIR/run" \
  "$GATEWAY_DIR/containers"

if [[ "$PROFILE" == "test" ]]; then
  install -d -m 0755 \
    "$GATEWAY_DIR/test/configs" \
    "$GATEWAY_DIR/test/logs" \
    "$GATEWAY_DIR/test/run" \
    "$GATEWAY_DIR/test/containers" \
    "$GATEWAY_DIR/test-sysroot"
fi

cd "$SOURCE_DIR"
"$UV_BIN" sync
VENV_BIN="$SOURCE_DIR/.venv/bin"

if [[ -f "$SOURCE_DIR/web/package.json" ]] && command -v npm >/dev/null 2>&1; then
  if (cd "$SOURCE_DIR/web" && npm install && npm run build); then
    echo "built web assets"
  else
    echo "warning: web asset build failed; FastAPI fallback page will still be served" >&2
  fi
else
  echo "npm not found or web/package.json missing; FastAPI fallback page will be served"
fi

write_file() {
  local path="$1"
  local mode="$2"
  local tmp
  tmp="$(mktemp)"
  cat >"$tmp"
  install -m "$mode" "$tmp" "$path"
  rm -f "$tmp"
  echo "installed $path"
}

write_if_missing() {
  local path="$1"
  local mode="$2"
  if [[ -e "$path" ]]; then
    echo "kept existing $path"
    cat >/dev/null
    return 0
  fi
  write_file "$path" "$mode"
}

cleanup_legacy_test_profile_unit() {
  local service_name="$1"
  local unit_path="$SYSTEMD_DIR/$service_name"
  if [[ ! -f "$unit_path" ]]; then
    systemctl reset-failed "$service_name" >/dev/null 2>&1 || true
    return 0
  fi
  if ! grep -q -- "settings-test.yaml" "$unit_path"; then
    return 0
  fi

  systemctl disable --now "$service_name" >/dev/null 2>&1 || true
  systemctl reset-failed "$service_name" >/dev/null 2>&1 || true
  rm -f "$unit_path"
  echo "removed legacy test-profile unit $service_name"
}

wait_for_web_health() {
  if ! command -v curl >/dev/null 2>&1; then
    echo "curl not found; skipped Web readiness check"
    return 0
  fi

  local url="http://127.0.0.1:${WEB_PORT}/api/health"
  local attempt
  for attempt in $(seq 1 20); do
    if curl -fsS --max-time 2 "$url" >/dev/null; then
      echo "web health ready at $url"
      return 0
    fi
    sleep 1
  done

  echo "web health check failed at $url" >&2
  systemctl --no-pager --lines=30 status "$WEB_SERVICE" >&2 || true
  return 1
}

if [[ "$PROFILE" == "test" ]]; then
  cleanup_legacy_test_profile_unit "dros-daemon.service"
  cleanup_legacy_test_profile_unit "dros-web.service"
fi

write_file "$BIN_DIR/dros" 0755 <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "$SOURCE_DIR"
exec "$UV_BIN" run dros "\$@"
EOF

write_file "$BIN_DIR/gw" 0755 <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec "$BIN_DIR/dros" "\$@"
EOF

write_file "$BIN_DIR/drosd" 0755 <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "$SOURCE_DIR"
exec "$UV_BIN" run drosd "\$@"
EOF

write_file "$BIN_DIR/dros-web" 0755 <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "$SOURCE_DIR"
exec "$UV_BIN" run dros-web "\$@"
EOF

if [[ "$PROFILE" == "test" ]]; then
  write_file "$BIN_DIR/gw-test" 0755 <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec "$BIN_DIR/gw" --settings "$SETTINGS_PATH" "\$@"
EOF

  write_file "$SETTINGS_PATH" 0644 <<EOF
sysRoot: $SYS_ROOT
paths:
  configs: $CONFIGS_DIR
  logs: $LOGS_DIR
  run: $RUN_DIR
  containers: $CONTAINERS_DIR
daemon:
  socketPath: $GATEWAY_DIR/test/run/drosd-test.sock
web:
  host: 0.0.0.0
  port: 8766
  staticDir: $SOURCE_DIR/web/dist
EOF
else
  write_if_missing "$SETTINGS_PATH" 0644 <<EOF
sysRoot: $SYS_ROOT
paths:
  configs: $CONFIGS_DIR
  logs: $LOGS_DIR
  run: $RUN_DIR
  containers: $CONTAINERS_DIR
daemon:
  socketPath: $DAEMON_SOCKET
web:
  host: 0.0.0.0
  port: $WEB_PORT
  staticDir: $SOURCE_DIR/web/dist
EOF
fi

write_file "$SYSTEMD_DIR/$DAEMON_SERVICE" 0644 <<EOF
[Unit]
Description=DROS daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$SOURCE_DIR
ExecStart=$VENV_BIN/drosd --settings $SETTINGS_PATH
Restart=on-failure
RestartSec=2s

[Install]
WantedBy=multi-user.target
EOF

write_file "$SYSTEMD_DIR/$WEB_SERVICE" 0644 <<EOF
[Unit]
Description=DROS web console
After=network-online.target $DAEMON_SERVICE
Wants=$DAEMON_SERVICE

[Service]
Type=simple
WorkingDirectory=$SOURCE_DIR
ExecStart=$VENV_BIN/dros-web --settings $SETTINGS_PATH
Restart=on-failure
RestartSec=2s

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$DAEMON_SERVICE" "$WEB_SERVICE" >/dev/null
systemctl kill --kill-who=all "$DAEMON_SERVICE" "$WEB_SERVICE" >/dev/null 2>&1 || true
systemctl reset-failed "$DAEMON_SERVICE" "$WEB_SERVICE" >/dev/null 2>&1 || true
systemctl restart "$DAEMON_SERVICE" "$WEB_SERVICE"
wait_for_web_health

echo "DROS $PROFILE profile installed from $SOURCE_DIR"
echo "settings: $SETTINGS_PATH"
echo "services: $DAEMON_SERVICE, $WEB_SERVICE"
echo "commands: gw, dros, drosd, dros-web"
if [[ "$PROFILE" == "test" ]]; then
  echo "test command: gw-test"
fi
