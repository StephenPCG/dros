# test-gw Lab

`test-gw` is the Debian VM used for DROS integration testing.

## Access

```sh
ssh test-gw
```

The VM has passwordless sudo and `uv` installed. Source code is deployed to
`/opt/dros`:

```sh
scripts/dev/sync-test-gw.sh
ssh test-gw 'cd /opt/dros && bash -lc "uv run pytest -q"'
ssh test-gw 'cd /opt/dros && ./install-dros.sh --test'
```

The sync script uses `rsync --delete` and excludes local virtualenvs, Python
caches, sockets, and runtime state. It deliberately syncs `.git/` and
`web/dist/`: gateway hosts should consume built Web assets from the source tree,
not build the frontend locally. Run `npm run build` under `web/` before syncing
after Web source changes.

`uv` is installed at `/home/stephen/.local/bin/uv` and appears in the login
shell `PATH`.

## Network Safety

`ens18` is the management and test parent NIC. Keep its default VLAN and IP
configuration unchanged. DROS should not modify `ens18` itself.

VLAN test interfaces may be created on `ens18` with VLAN IDs `1000` through
`1255`. These map to isolated test networks `172.16.0.0/24` through
`172.16.255.0/24`, where VLAN `1000 + n` uses `172.16.n.0/24`.

VLAN `4001` is reserved for PPPoE testing through the optical modem. Use it
only when testing PPPoE behavior.

## Manual System Changes

Record manual system changes made during testing here. For each change, decide
whether it belongs in DROS bootstrap, a plugin, or a one-off repair after a
broken test.

Current log:

- 2026-05-16: Created the fresh repository skeleton and copied the lab access
  and network safety rules into this repo. No commands have been run on
  `test-gw` from this new skeleton yet.
- 2026-05-16: Added the new `install-dros.sh` workflow. The test profile is
  installed with `/opt/dros/install-dros.sh --test`, which writes
  `/etc/dros/settings-test.yaml` and starts `dros-daemon-test.service` plus
  `dros-web-test.service` against the test settings. The test Web service uses
  port `8766`, so it can run alongside the release Web service on `8765`.
