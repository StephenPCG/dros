# Bootstrap and Plugin Notes

DROS keeps `gw bootstrap` as a hook-driven command. Bootstrap reads
ConfigObjects, but ConfigObjects do not decide whether bootstrap runs them.
Each plugin registers the bootstrap work it owns.

The CLI loads `/etc/dros/settings.yaml` by default. On development hosts, pass
an explicit `--settings` file that points `sysRoot` at a test directory.

`gw update` will be object-driven later: each ConfigObject is routed to the
plugin that owns its kind, even if the object represents something that also
matters during bootstrap.

## Initial Plugins

- `system.mirror` owns Debian apt sources and the Docker CE apt source.
- `network.core` owns hostname, hosts, sysctl, nftables entrypoint, dnsmasq,
  avahi, and core network packages.
- `system.utilities` owns general troubleshooting and admin packages.
- `docker.core` owns Docker packages and `/etc/docker/daemon.json`.

Plugins declare their owned system packages and managed files. The registry
rejects duplicate ownership so later work cannot accidentally make two plugins
fight over the same package or file.

The registry also keeps a placeholder for event hooks. Later, commands like
`gw hook iface-up ...` can be dispatched through the same plugin boundary.

## ConfigObjects

ConfigObjects are YAML documents under configured directories. Later
directories override earlier directories for the same `kind/name`.

The bootstrap singleton objects currently used are:

- `SystemNetworkConfig`, recommended name `system`
  - `hostname`, default `gateway`
  - `domain`, default `lan`
  - `nfConntrackMax`, default `524288`
- `SystemMirrorConfig`, recommended name `system`
  - `aptMirror`, default `https://mirrors.ustc.edu.cn/debian`
  - `dockerAptMirror`, default `https://mirrors.ustc.edu.cn/docker-ce`
  - `dockerRegistryMirror`, default empty

Detailed ConfigObject references:

- `docs/config-objects/SystemNetworkConfig.md`
- `docs/config-objects/SystemMirrorConfig.md`

Defaults live in code, not in built-in ConfigObjects. A user object can provide
only the fields it wants to override.

For singleton kinds, if no `metadata.name: default` object exists but there is
exactly one object of that kind, DROS uses that object. This keeps names like
`system` valid while still detecting ambiguous duplicate singleton objects.

Objects with `metadata.disabled: true` are ignored. If the system side also
needs cleanup, run the future `gw remove kind/name` command.

## Output And Idempotence

Bootstrap uses the same execution rules future update code should follow:

- `--verbose 0`: only errors.
- `--verbose 1`: default. Show actual changes, file diffs, commands that are
  run, and command success.
- `--verbose 2`: include full command output.

Managed files are diffed before writing. Existing matching files are left
untouched.

Packages are checked with `dpkg-query` before `apt-get install`. Only missing
packages are installed.

When `sysRoot` is not `/`, bootstrap writes files under that root but skips
real host commands such as `hostname`, `sysctl`, `apt-get`, and `systemctl`.
The skipped command is still recorded so test output shows what would have
happened on a real Debian host.

## Current Bootstrap Effects

Bootstrap currently manages:

- `/etc/hostname`
- `/etc/hosts`
- `/etc/cloud/cloud.cfg.d/99-dros-hostname.cfg` when cloud-init is detected
- `/etc/sysctl.d/99-dros.conf`
- `/etc/apt/sources.list`
- `/etc/apt/sources.list.d/docker-ce.list`
- `/etc/docker/daemon.json`
- `/etc/dnsmasq.conf`
- `/etc/avahi/avahi-daemon.conf`
- `/etc/nftables.conf`
- `/etc/dros/nftables.d`

The nftables entrypoint only includes `/etc/dros/nftables.d/*.nft`; generated
rules will live there in later phases.
