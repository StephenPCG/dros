# DROS

DROS is a personal Debian Router OS for Stephen's own gateways across Home Lab,
Office, VPC, and VPS environments.

This repository is a fresh rebuild. The first milestone is intentionally small:
establish the project skeleton, dependency state, command entrypoints, and lab
test workflow without carrying over the staged/committed apply model from the
previous rewrite.

## Current Scope

- `gw` is the primary daily CLI entrypoint.
- `drosd` will later handle hook-triggered queued updates and monitoring data.
- `dros-web` will start as a monitoring and tooling surface. Early Web work does
  not include configuration editing.
- ConfigObjects remain the configuration unit, backed by YAML files and overlay
  directories.

## Development

```sh
uv sync
uv run gw help
uv run gw restart daemon
uv run gw restart web
uv run pytest -q
uv run python -m compileall -q src/dros
```

Web skeleton:

```sh
cd web
npm install
npm run build
```

## Lab Testing

The Debian test host is documented in `docs/lab/test-gw.md`.

Bootstrap and plugin design notes live in `docs/bootstrap.md`.

## Install

On a target Debian machine, place the source tree at `/opt/dros` and run:

```sh
/opt/dros/install-dros.sh
```

For the test profile:

```sh
/opt/dros/install-dros.sh --test
```

The installer syncs Python dependencies with `uv`, writes the active settings
file under `/etc/dros`, installs `gw` and service wrappers into
`/usr/local/bin`, and enables systemd services.

Release profile services:

- `dros-daemon.service`
- `dros-web.service`, listening on `0.0.0.0:8765`

Test profile services:

- `dros-daemon-test.service`
- `dros-web-test.service`, listening on `0.0.0.0:8766`
- daemon socket: `/opt/gateway/test/run/drosd-test.sock`

Use `gw restart daemon` and `gw restart web` to restart the release services.
Use `gw-test restart daemon` and `gw-test restart web` to restart the test
services. These commands automatically use `sudo` when the current user is not
root.
