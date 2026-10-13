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

An interface still attached to `XfrmTransport` is rejected. Detach its XFRM
association deliberately, apply it, and stop the transport using its existing
lifecycle before uninstalling the interface. Review other users of the transport.
An interface with an active nftables listen-rule fragment is also rejected:
remove `listenPort`/`listen` from source and update the shared firewall through
the normal interface update first. The uninstall command does not flush a
shared firewall to remove one interface's rules.

## Dependencies, ownership and failure

Remove or migrate references before deleting an object. DROS checks remaining
loaded configuration, including Gateway/RouteTable, interface parents/ports,
DNS listeners, discovery, raw rules and scripts. The conservative identifier
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

