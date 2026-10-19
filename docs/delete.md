# Uninstalling a resource

Removing or disabling source YAML does not uninstall the running resource.
`gw update` visits objects that are still present; it does not garbage-collect
interfaces or files for objects that disappeared. Keep the object available
until its uninstall finishes:

```sh
gw delete iface/gre-old
gw delete iface/gre-old --apply
# Then remove the Interface/gre-old YAML document manually.
```

`gw remove` is an alias for `gw delete`. A kind without a name selects all objects
of that kind, for example `gw delete cron`; the preview lists each target.
An empty target, broad kind groups such as `route`, an unknown resource, and an
unsupported resource type fail explicitly. Prefer `kind/name` for network work.

The default is a read-only plan: it lists files, commands, source documents and
conditional actions. It does not acquire a lock, elevate privileges, create
temporary directories, or write invocation logs. On a live system it reads
kernel interface details with `ip -json -details link show`. `--apply` obtains
the usual gateway apply lock, builds a fresh plan, validates the entire batch,
and then executes it. With a non-root `sysRoot`, only files under that root are
removed; kernel commands are skipped and this limitation is printed.

## Supported lifecycles

| Resource | Uninstall behavior |
| --- | --- |
| `Interface` of type `gre`, `wireguard`, `vlan`, `bridge` | Bring down the selected interface, delete it if still present, verify absence, then remove its DROS-generated ifupdown and auxiliary files. |
| `CronJob` | Remove its individual generated cron file; already running jobs continue. |
| `Gateway` | Check references and report a logical-only uninstall; the declaration has no independently owned system file or kernel object. Apply changes to the referring route tables first. |

For interfaces, files include both legacy `dros-<name>.cfg` and numbered
`NNN-dros-<name>.cfg` layouts. WireGuard's generated config and wgsd-client cron
are included. A `privateKeyFile`, shared services/hooks, packages and persistent
data are retained.

Physical, loopback, external, Docker, OpenVPN, PPPoE and Tailscale interfaces are
not supported yet. They involve externally owned devices, process management,
authentication state or shared configuration that requires a separate lifecycle.
Other kinds, including routing tables, firewall, DNS, device groups and XFRM,
also fail explicitly rather than deleting shared files. IP lists are inputs,
not individually installed ConfigObjects. Edit the relevant shared source and
use the corresponding `gw update` command.

A GRE interface with an exclusive, manual `XfrmTransport` can be uninstalled
together with its transport; see below. Other interface/XFRM combinations remain
unsupported. An interface with an active nftables listen-rule fragment is rejected:
remove `listenPort`/`listen` from source and update the shared firewall through
the normal interface update first. The uninstall command does not flush a
shared firewall to remove one interface's rules.

## Retiring a GRE interface with XFRM

Keep both original YAML documents in place and select the interface:

```sh
gw delete iface/gre-old
gw delete iface/gre-old --apply
# After success, manually remove the Interface and its exclusive XfrmTransport.
```

The preview checks exclusivity and installed ownership before listing the
interface, transport, generated files and exact scoped removal commands. Live
XFRM inspection needs sufficient kernel privileges; if a non-root preview is
denied, run `sudo gw delete iface/gre-old` without `--apply` to preview as root.
No state changes occur during preview. An offline `sysRoot` only validates source
and files; it cannot establish runtime exclusivity and skips kernel/service work.

Another object referencing the transport blocks deletion, including disabled
source objects and other interfaces selected in the same batch. Different
transport names also block deletion when their policy selectors or ESP state
identities overlap. Other GRE interfaces using the same outer endpoints share
the selector even without an explicit `xfrmTransport` field; source and live
interfaces are checked. Errors identify the conflicting resource. Resolve that
sharing deliberately before retrying; there is no force option.

Apply rechecks ownership, brings down the GRE using its original installed
configuration, verifies the interface has disappeared, and deletes only the
transport's two policies and two ESP states. It verifies their absence before
removing generated files. A matching, inactive historical DROS systemd unit is
disabled and removed if present. Stop any active historical unit explicitly
before retrying: its `gw stop` action needs the same lock as delete. Unexpected
units, drop-ins or kernel identities block deletion. No global XFRM flush is used,
and inventory suppresses key material.
Keep the XFRM association attached throughout this process; an intermediate
update without it can bring the GRE back without its encryption.

Queued interface hooks check the live associated interfaces: an old start event
cannot recreate XFRM after the GRE is gone, and an old stop event cannot tear it
down after the GRE has been recreated. Source YAML is still retained. An explicit
later `gw update iface/gre-old` or `gw start xfrm/<name>` can reinstall state, so
remove the two source documents yourself after successful retirement.

## Dependencies, ownership and failure

Remove or migrate references before deleting an object. DROS checks remaining
loaded configuration, including disabled objects: Gateway/RouteTable references,
interface parents/ports, DNS listeners, discovery, raw rules and scripts. The conservative identifier
check reports the referring object, source file and field path. A reference in
a comment string may also need review; arbitrary external scripts and clients
cannot be inferred. For a kind-wide deletion, dependents are removed before
their parents. Runtime interfaces attached to the target but outside the
selection block deletion.

Generated files must have the DROS marker; ifupdown and WireGuard files must
match the current source. Uninstall refuses mismatched runtime interface types,
unowned live interfaces, symbolic links, hard-linked files, unsafe names and
path traversal. File hashes are rechecked before mutation. Reconcile source
and installed state if these checks fail; do not remove source YAML to bypass
them. A disabled or already removed object cannot be selected: restore its
original source definition to uninstall it.

`ifdown` executes existing system down hooks, which can enqueue route refreshes.
Keeping source YAML allows later updates, bootstrap operations or hooks to
reinstall state. After success, remove the selected YAML **document**; a file
may contain several resources that must be preserved.

Missing files and interfaces are treated as already absent. A command error
stops the remaining work and preserves that resource's configuration. Earlier
resources may already be uninstalled; the command reports partial application
and never reinstalls networking automatically as a rollback. Inspect the state,
fix the cause, and preview again before retrying. Uninstalling the interface
carrying your management connection can disconnect you; use an independent
management path when retiring it.

## Removing a peer from a shared WireGuard interface

Keep the shared interface. Remove only the peer from its complete YAML `peers`
list, then run `gw update iface/<name>`. Each selected existing interface applies
`wg addconf`, reads the actual peer list, explicitly removes only keys absent
from source, and verifies the final peer set. It reconciles even when the
rendered file has not changed, so historical leftover peers and failed previous
applications can be corrected without bouncing the interface.

The source peer list is authoritative. Peers installed manually outside it are
removed; all peers to retain must be in YAML. A shared full-mesh list may include
the interface's own public key: WireGuard ignores that entry, and DROS excludes
it from the required runtime peers. The comparison uses the applied interface
public key, so it also handles private-key rotation. `addconf` preserves unspecified
interface fields, including a key previously loaded through `privateKeyFile`,
a runtime listen port and fwmark. It also preserves omitted endpoint, preshared
key and keepalive fields of retained peers. An explicit endpoint is reapplied
from source. Using `syncconf` here would reset some omitted interface fields,
so deletion of surplus peers is explicit instead.

The supported wgsd-client updates endpoints of existing peers with `UpdateOnly`
and does not recreate deleted peers. Empty `peers` removes all peers. A failed
add/read/remove/verification stops the update with an error; another update
retries the reconciliation even if the file was already written. Failures report
the interface and failed stage; raw WireGuard output is suppressed even in
verbose mode because malformed configuration errors can contain private keys.

Upstream contracts: [WireGuard configuration defaults](https://git.zx2c4.com/wireguard-tools/tree/src/config.c),
[WireGuard peer synchronization](https://git.zx2c4.com/wireguard-tools/tree/src/setconf.c),
[WireGuard kernel endpoint update](https://git.zx2c4.com/wireguard-linux/tree/drivers/net/wireguard/netlink.c),
[wgsd-client endpoint discovery](https://github.com/jwhited/wgsd/blob/master/cmd/wgsd-client/main.go).
