# Bootstrap and Plugin Notes

DROS keeps `gw bootstrap` as a hook-driven command. Bootstrap reads
ConfigObjects, but ConfigObjects do not decide whether bootstrap runs them.
Each plugin registers the bootstrap work it owns.

The CLI loads `/etc/dros/settings.yaml` by default. On development hosts, pass
an explicit `--settings` file that points `sysRoot` at a test directory.

`gw update` is object-driven: each ConfigObject is routed to the plugin that
owns its kind, even if the object represents something that also matters during
bootstrap.

Before `gw update` writes files or runs commands, it validates every selected
ConfigObject and prints all collected errors. No selected object is applied if
any selected object is invalid.

## Initial Plugins

- `system.mirror` owns Debian apt sources, the Docker CE apt source, and the
  Tailscale apt source.
- `network.core` owns hostname, hosts, sysctl, the DROS nftables snippet
  directory, dnsmasq, avahi, and core network packages.
- `network.interfaces` owns `DevGroup` and `Interface` objects, ifupdown
  fragments, PPP hook dispatch, Tailscale service defaults, and runtime
  interface properties.
- `network.ipv6pd` owns `IPv6PD` objects, wide-dhcpv6 configuration, radvd
  configuration, IPv6 refresh hooks, and the IPv6PD nftables snippet.
- `network.routing` owns `FwMark`, `Gateway`, `RouteTable`, and
  `RouteRuleSet` objects, route table names, policy route rules, and route
  refresh hooks.
- `network.firewall` owns `Firewall` objects, `/etc/nftables.conf`, and the
  generated base nftables ruleset under `/etc/dros/nftables.d`.
- `network.resolvconf` owns `ResolvConf` objects and `/etc/resolv.conf`.
- `network.dnsmasq` owns `DnsmasqDNS`, `DnsmasqDHCP`, and
  `DnsmasqChinaNames` objects, dnsmasq include fragments, and the China Names
  refresh cron job.
- `ip_lists` owns `IpListUpdater` objects and the cron job that refreshes
  runtime IP list files.
- `system.utilities` owns general troubleshooting and admin packages.
- `monitoring.collectd` owns `Collectd` objects, the local collectd package
  set, `/etc/collectd/collectd.conf`, and `collectd.service` restart/enable
  actions when the rendered config changes.
- `docker.core` owns Docker packages, `/etc/docker/daemon.json`, and the Docker
  service post-start hook.
- `docker.resources` owns `DockerContainer`, `DockerApp`, and `DockerDNS`
  objects, compose project files under `/opt/gateway/containers`, and optional
  dnsmasq records for Docker containers.
- `ovpn` is intentionally not a ConfigObject plugin. It owns OpenVPN instances,
  profile files, CA material, CRLs, and generated `.ovpn` files under
  `/opt/gateway/ovpn`; see `docs/openvpn.md`.

Plugins declare their owned system packages and managed files. The registry
rejects duplicate ownership so later work cannot accidentally make two plugins
fight over the same package or file.

The registry also keeps event hook metadata. System hooks currently enqueue
events through `gw hook ...`; `drosd` polls the run directory queue and handles
the event against the effective ConfigObjects loaded from `paths.configs`.
Generated hooks do not run `gw update` inline. They append events to
`{paths.run}/events.jsonl`, and the daemon processes the queue under a single
process lock. Duplicate events in the same daemon polling batch are coalesced by
`event + iface`.

Every `gw` CLI invocation that can load settings writes a JSONL trace to
`{paths.logs}/gw-invocations.log`. Event enqueue and daemon-side event
processing are logged there too. Config-apply paths such as `gw update`,
`gw bootstrap`, and daemon event processing use `{paths.run}/locks/gw-apply.lock`
so only one system apply path runs at a time.

## ConfigObjects

ConfigObjects are YAML documents under configured directories. Later
directories override earlier directories for the same `kind/name`.

A single YAML file may contain multiple ConfigObject documents separated by
`---`.

The bootstrap singleton objects currently used are:

- `SystemNetworkConfig`, recommended name `system`
  - `hostname`, default `gateway`
  - `domain`, default `lan`
  - `nfConntrackMax`, default `524288`
- `SystemMirrorConfig`, recommended name `system`
  - `aptMirror`, default `https://mirrors.ustc.edu.cn/debian`
  - `dockerAptMirror`, default `https://mirrors.ustc.edu.cn/docker-ce`
  - `tailscaleAptMirror`, default `https://mirrors.ustc.edu.cn/tailscale`
  - `dockerRegistryMirror`, default empty
- `Collectd`, fixed name `system`
  - `interval`, default `10`
  - `rrdDir`, default `/var/lib/collectd/rrd`
  - `plugins.ping.hosts`, default empty

Detailed ConfigObject references:

- `docs/config-objects/Collectd.md`
- `docs/config-objects/DevGroup.md`
- `docs/config-objects/DockerApp.md`
- `docs/config-objects/DockerContainer.md`
- `docs/config-objects/DockerDNS.md`
- `docs/config-objects/DnsmasqChinaNames.md`
- `docs/config-objects/DnsmasqDHCP.md`
- `docs/config-objects/DnsmasqDNS.md`
- `docs/config-objects/Firewall.md`
- `docs/config-objects/FwMark.md`
- `docs/config-objects/Gateway.md`
- `docs/config-objects/Interface.md`
- `docs/config-objects/IpListUpdater.md`
- `docs/config-objects/IPv6PD.md`
- `docs/config-objects/ResolvConf.md`
- `docs/config-objects/RouteRuleSet.md`
- `docs/config-objects/RouteTable.md`
- `docs/config-objects/SystemNetworkConfig.md`
- `docs/config-objects/SystemMirrorConfig.md`

Defaults live in code, not in built-in ConfigObjects. A user object can provide
only the fields it wants to override.

For singleton kinds, if no `metadata.name: default` object exists but there is
exactly one object of that kind, DROS uses that object. This keeps names like
`system` valid while still detecting ambiguous duplicate singleton objects.

Objects with `metadata.disabled: true` are ignored. If the system side also
needs cleanup, run the future `gw remove kind/name` command.

## Config Directory Semantics

Configured `paths.configs` is treated as the desired current state.

There is no separate committed ConfigObject tree. Daily usage assumes config
files are edited deliberately and usually followed immediately by `gw update`.
This keeps CLI output and system changes easy to reason about: the same
effective config loader is used by manual CLI updates, daemon-triggered hook
work, bootstrap hooks, and Web status helpers.

`gw update` still supports a target such as `gw update iface/br0`; in that case
only the selected objects are validated and applied, while dependency checks may
still inspect other loaded ConfigObjects where needed.

Runtime queues, logs, Web auth data, and similar state may still live under
`paths.run`; DROS does not mirror ConfigObjects there.

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
- `/etc/apt/sources.list.d/debian.sources` is removed
- `/etc/apt/sources.list.d/docker-ce.list`
- `/etc/apt/sources.list.d/tailscale.list`
- `/etc/docker/daemon.json`
- `/etc/systemd/system/docker.service.d/40-dros-hook.conf`
- `/etc/dnsmasq.conf`
- `/etc/avahi/avahi-daemon.conf`
- `/etc/dros/nftables.d`
- `/etc/network/if-up.d/dros-route`
- `/etc/network/if-down.d/dros-route`
- `/etc/ppp/ip-up.d/dros-route`
- `/etc/ppp/ip-down.d/dros-route`
- `/etc/ppp/ip-up.d/dros-hook`
- `/etc/ppp/ipv6-up.d/dros-hook`
- `/etc/ppp/ip-down.d/dros-hook`
- `/usr/lib/dros/openvpn-iface`
- `/usr/share/keyrings/tailscale-archive-keyring.gpg`

DROS intentionally standardizes Debian base repositories on the one-line
`/etc/apt/sources.list` file. Bootstrap removes
`/etc/apt/sources.list.d/debian.sources` before running apt package installs, so
the Debian base repositories are not declared twice.

Bootstrap also disables the package-provided `tailscaled.service`. DROS-managed
Tailscale interfaces use per-interface units named `dros-tailscaled-<name>.service`
so one gateway can join multiple tailnets at the same time.

Bootstrap intentionally does not manage `/etc/nftables.conf`. The operating
system default stays in place until a `Firewall` object is applied.

`gw update` additionally manages files such as
`/etc/nftables.conf`, `/etc/dros/nftables.d/10-firewall.nft`, interface-owned
listen snippets under `/etc/dros/nftables.d/30-interface-*.nft`, and
`/etc/iproute2/rt_tables.d/dros.conf`, per-interface Tailscale systemd units
under `/etc/systemd/system/dros-tailscaled-*.service`, plus dnsmasq include
files under `/etc/dnsmasq.d/dros-*.conf`. Route updates are rendered to
`{paths.run}/tmp/update-route.sh` first, then applied from that script; each
selected route table is written through a separate `ip -batch` block.

IP list files are not ConfigObjects. They are loaded from
`{paths.configs}/ip-lists`, `{paths.run}/ip-lists`, and
`{paths.source}/ip-lists`, in that priority order. `gw ip-list update` writes
downloaded files to `{paths.run}/ip-lists`; DROS also ships a source-tree copy
under `{paths.source}/ip-lists` so route rules have a useful baseline before
the first download. If a referenced list is not available, that route is
skipped; normal CLI updates warn, while hook-triggered silent updates do not.
