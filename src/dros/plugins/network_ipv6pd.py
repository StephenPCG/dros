from __future__ import annotations

import ipaddress
import shlex

from pydantic import ValidationError

from dros.config_objects import ConfigObject, IPv6PDConfig, IPv6PDDownstreamConfig
from dros.plugins.base import DrosPlugin, UpdateContext
from dros.plugins.network_firewall import FILTER_TABLE, FIREWALL_PATH, NFTABLES_CONF

IPV6_DIR = "/etc/dros/ipv6"
DHCPC_CONF = f"{IPV6_DIR}/dhcp6c.conf"
DHCPC_SCRIPT = f"{IPV6_DIR}/dhcp6c-script"
DHCPC_DUID = "/var/lib/dhcpv6/dhcp6c_duid"
RADVD_CONF = "/etc/radvd.conf"
IPV6PD_SERVICE = "/etc/systemd/system/dros-ipv6-pd.service"
IFUP_HOOK = "/etc/network/if-up.d/dros-ipv6"
PPP_IP_PRE_UP_HOOK = "/etc/ppp/ip-pre-up.d/dros-ipv6"
PPP_IP_UP_HOOK = "/etc/ppp/ip-up.d/dros-ipv6"
PPP_IPV6_UP_HOOK = "/etc/ppp/ipv6-up.d/dros-ipv6"
NFT_IPV6PD = "/etc/dros/nftables.d/15-ipv6pd.nft"

MANAGED_FILES = frozenset(
    {
        DHCPC_CONF,
        DHCPC_SCRIPT,
        DHCPC_DUID,
        RADVD_CONF,
        IPV6PD_SERVICE,
        IFUP_HOOK,
        PPP_IP_PRE_UP_HOOK,
        PPP_IP_UP_HOOK,
        PPP_IPV6_UP_HOOK,
        NFT_IPV6PD,
    }
)


def create_plugin() -> DrosPlugin:
    return DrosPlugin(
        name="network.ipv6pd",
        depends_on=("network.core", "network.interfaces"),
        config_kinds=frozenset({"IPv6PD"}),
        managed_files=MANAGED_FILES,
        validation_hook=validate,
        update_hook=update,
        event_hooks=frozenset({"ipv6-refresh", "iface-up", "ppp-up"}),
    )


def validate(context: UpdateContext, objects: list[ConfigObject]) -> list[str]:
    errors: list[str] = []
    for obj in objects:
        if obj.kind != "IPv6PD":
            continue
        if obj.name != "system":
            errors.append(f"IPv6PD/{obj.name}: metadata.name must be system")
        config = _validate_model(context, obj, errors)
        if config is None:
            continue
        _validate_config(obj, config, errors)
    return errors


def update(context: UpdateContext, objects: list[ConfigObject]) -> None:
    for obj in sorted(objects, key=lambda item: item.name):
        config = context.configs.resolve_object(obj, IPv6PDConfig)
        _apply_ipv6pd(context, obj, config)


def handle_event(context: UpdateContext, event: str, iface: str | None = None) -> None:
    if event not in {"ipv6-refresh", "iface-up", "ppp-up"}:
        return
    obj = context.configs.get("IPv6PD", "system")
    if obj is None:
        objects = context.configs.by_kind("IPv6PD")
        obj = sorted(objects, key=lambda item: item.name)[-1] if objects else None
    if obj is None:
        return
    config = context.configs.resolve_object(obj, IPv6PDConfig)
    if not config.enabled:
        return
    if event != "ipv6-refresh" and iface and iface not in _hook_interfaces(config):
        return
    _apply_ipv6pd(context, obj, config, force_reload=True)


def _apply_ipv6pd(
    context: UpdateContext,
    obj: ConfigObject,
    config: IPv6PDConfig,
    *,
    force_reload: bool = False,
) -> None:
    context.executor.ensure_dir(IPV6_DIR)
    context.executor.ensure_dir(context.settings.paths.run / "ipv6")
    context.executor.ensure_dir("/etc/network/if-up.d")
    context.executor.ensure_dir("/etc/ppp/ip-pre-up.d")
    context.executor.ensure_dir("/etc/ppp/ip-up.d")
    context.executor.ensure_dir("/etc/ppp/ipv6-up.d")
    context.executor.ensure_dir("/etc/dros/nftables.d")

    if not config.enabled:
        context.executor.write_file(DHCPC_CONF, _disabled_file(obj))
        context.executor.write_file(RADVD_CONF, _disabled_file(obj))
        context.executor.write_file(NFT_IPV6PD, _disabled_file(obj))
        context.executor.run(
            ["systemctl", "disable", "--now", "dros-ipv6-pd.service"],
            check=False,
            real_only=True,
        )
        context.executor.run(
            ["systemctl", "disable", "--now", "radvd.service"],
            check=False,
            real_only=True,
        )
        return

    dhcpc_changed = context.executor.write_file(
        DHCPC_CONF,
        _render_dhcp6c_conf(config),
        mode=0o600,
    )
    dhcpc_script_changed = context.executor.write_file(
        DHCPC_SCRIPT,
        _render_dhcp6c_script(),
        mode=0o755,
    )
    duid_changed = False
    if config.duid:
        duid_changed = context.executor.write_binary_file(
            DHCPC_DUID,
            _render_dhcp6c_duid(config.duid),
            mode=0o644,
        )
    radvd_changed = context.executor.write_file(RADVD_CONF, _render_radvd_conf(config))
    service_changed = context.executor.write_file(IPV6PD_SERVICE, _render_service(config))
    context.executor.write_file(IFUP_HOOK, _render_ifup_hook(config), mode=0o755)
    context.executor.write_file(
        PPP_IP_PRE_UP_HOOK,
        _render_ppp_ip_pre_up_hook(config),
        mode=0o755,
    )
    context.executor.write_file(PPP_IP_UP_HOOK, _render_ppp_ip_up_hook(config), mode=0o755)
    context.executor.write_file(
        PPP_IPV6_UP_HOOK,
        _render_ppp_ipv6_up_hook(config),
        mode=0o755,
    )
    nft_changed = context.executor.write_file(NFT_IPV6PD, _render_nft_rules(config))

    if service_changed:
        context.executor.run(["systemctl", "daemon-reload"], real_only=True)
    if config.accept_ra is not None:
        context.executor.run(_accept_ra_command(str(config.uplink), config.accept_ra), real_only=True)
    if force_reload or dhcpc_changed or dhcpc_script_changed or duid_changed:
        context.executor.run(["systemctl", "restart", "dros-ipv6-pd.service"], real_only=True)
    else:
        context.executor.run(["systemctl", "enable", "--now", "dros-ipv6-pd.service"], real_only=True)
    if force_reload or radvd_changed:
        context.executor.run(["systemctl", "restart", "radvd.service"], real_only=True)
    else:
        context.executor.run(["systemctl", "enable", "--now", "radvd.service"], real_only=True)
    if force_reload or nft_changed:
        context.executor.run(
            [
                "sh",
                "-c",
                'if [ -f "$1" ] && [ -f "$2" ]; then nft -f "$1"; fi',
                "sh",
                NFTABLES_CONF,
                FIREWALL_PATH,
            ],
            real_only=True,
        )
    _warn_missing_ula_addresses(context, config)


def _validate_model(
    context: UpdateContext,
    obj: ConfigObject,
    errors: list[str],
) -> IPv6PDConfig | None:
    try:
        return context.configs.resolve_object(obj, IPv6PDConfig)
    except ValidationError as exc:
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            errors.append(f"IPv6PD/{obj.name}: spec.{location}: {error['msg']}")
        return None


def _validate_config(obj: ConfigObject, config: IPv6PDConfig, errors: list[str]) -> None:
    if config.iaid < 0:
        errors.append(f"IPv6PD/{obj.name}: spec.iaid must be non-negative")
    _validate_prefix_length(obj, "prefixLengthHint", config.prefix_length_hint, errors)
    _validate_prefix_length(obj, "delegatedPrefixLength", config.delegated_prefix_length, errors)
    delegated_length = _delegated_prefix_length(config)
    if config.accept_ra not in {None, 0, 1, 2}:
        errors.append(f"IPv6PD/{obj.name}: spec.acceptRA must be 0, 1, 2, or null")
    if config.duid:
        try:
            duid = _parse_duid_bytes(config.duid)
            if not 2 <= len(duid) <= 130:
                raise ValueError
        except ValueError:
            errors.append(f"IPv6PD/{obj.name}: spec.duid must be 2 to 130 hex bytes")
    if config.enabled:
        if not config.uplink:
            errors.append(f"IPv6PD/{obj.name}: spec.uplink is required when enabled")
        if not config.downstream:
            errors.append(f"IPv6PD/{obj.name}: spec.downstream must contain at least one item")
    _validate_ipv6_addresses(obj, "dnsServers", config.dns_servers, errors)
    for index, item in enumerate(config.downstream):
        _validate_downstream(obj, index, item, delegated_length, errors)


def _validate_prefix_length(
    obj: ConfigObject,
    field_name: str,
    value: int | None,
    errors: list[str],
) -> None:
    if value is not None and not 1 <= value <= 64:
        errors.append(f"IPv6PD/{obj.name}: spec.{field_name} must be between 1 and 64")


def _validate_downstream(
    obj: ConfigObject,
    index: int,
    item: IPv6PDDownstreamConfig,
    delegated_length: int,
    errors: list[str],
) -> None:
    if item.prefix_length != 64:
        errors.append(f"IPv6PD/{obj.name}: spec.downstream[{index}].prefixLength must be 64")
    if item.subnet_id < 0:
        errors.append(f"IPv6PD/{obj.name}: spec.downstream[{index}].subnetId must be non-negative")
    elif item.delegated and delegated_length <= item.prefix_length:
        max_subnet = (1 << (item.prefix_length - delegated_length)) - 1
        if item.subnet_id > max_subnet:
            errors.append(
                f"IPv6PD/{obj.name}: spec.downstream[{index}].subnetId must be between "
                f"0 and {max_subnet} for delegated prefix length {delegated_length}"
            )
    _validate_ipv6_addresses(obj, f"downstream[{index}].rdnss", item.rdnss, errors)
    try:
        ipaddress.IPv6Address(item.address)
    except ValueError:
        errors.append(f"IPv6PD/{obj.name}: spec.downstream[{index}].address must be an IPv6 address")
    if item.ula_prefix:
        try:
            network = ipaddress.IPv6Network(item.ula_prefix, strict=False)
            if network.prefixlen != 64:
                raise ValueError
        except ValueError:
            errors.append(f"IPv6PD/{obj.name}: spec.downstream[{index}].ulaPrefix must be an IPv6 /64 prefix")


def _validate_ipv6_addresses(
    obj: ConfigObject,
    field_name: str,
    values: list[str],
    errors: list[str],
) -> None:
    for index, value in enumerate(values):
        try:
            ipaddress.IPv6Address(value)
        except ValueError:
            errors.append(f"IPv6PD/{obj.name}: spec.{field_name}[{index}] must be an IPv6 address")


def _render_dhcp6c_conf(config: IPv6PDConfig) -> str:
    uplink = str(config.uplink)
    iaid = int(config.iaid)
    lines = [
        "# Generated by DROS. Manual changes will be overwritten.",
        "# Resource: IPv6PD/system",
        "",
        f"interface {uplink} {{",
    ]
    if config.request_address:
        lines.append(f"  send ia-na {iaid};")
    lines.extend(
        [
            f"  send ia-pd {iaid};",
            "  request domain-name-servers;",
            "  request domain-name;",
            f'  script "{DHCPC_SCRIPT}";',
            "};",
        ]
    )
    if config.request_address:
        lines.extend(["", f"id-assoc na {iaid} {{", "};"])

    lines.extend(["", f"id-assoc pd {iaid} {{"])
    if config.prefix_length_hint is not None:
        lines.append(f"  prefix ::/{int(config.prefix_length_hint)} infinity;")
    delegated_length = _delegated_prefix_length(config)
    for item in config.downstream:
        if not item.delegated:
            continue
        sla_len = max(0, item.prefix_length - delegated_length)
        lines.extend(
            [
                f"  prefix-interface {item.iface} {{",
                f"    sla-id {int(item.subnet_id)};",
                f"    sla-len {sla_len};",
                f"    ifid {_wide_ifid(item.address)};",
                "  };",
            ]
        )
    lines.append("};")
    lines.append("")
    return "\n".join(lines)


def _render_dhcp6c_script() -> str:
    return """#!/bin/sh
# Generated by DROS. Manual changes will be overwritten.
set -eu

command -v systemctl >/dev/null 2>&1 || exit 0
systemctl try-restart radvd.service >/dev/null 2>&1 || true
"""


def _render_radvd_conf(config: IPv6PDConfig) -> str:
    lines = [
        "# Generated by DROS. Manual changes will be overwritten.",
        "# Resource: IPv6PD/system",
    ]
    for item in config.downstream:
        if not item.advertise:
            continue
        rdnss = item.rdnss or config.dns_servers
        dnssl = item.dnssl or config.search_domains
        lines.extend(
            [
                "",
                f"interface {item.iface}",
                "{",
                "  IgnoreIfMissing on;",
                "  AdvSendAdvert on;",
                "  AdvManagedFlag off;",
                f"  AdvOtherConfigFlag {_on_off(bool(rdnss or dnssl))};",
                "  MaxRtrAdvInterval 600;",
            ]
        )
        if item.ula_prefix:
            lines.extend(["", "  autoignoreprefixes", "  {", f"    {item.ula_prefix};", "  };"])
        if item.delegated:
            lines.extend(
                [
                    "",
                    "  prefix ::/64",
                    "  {",
                    "    AdvOnLink on;",
                    "    AdvAutonomous on;",
                    "  };",
                ]
            )
        if item.ula_prefix:
            lines.extend(_render_radvd_prefix(item.ula_prefix))
        if rdnss:
            lines.extend(["", f"  RDNSS {' '.join(rdnss)}", "  {", "    AdvRDNSSLifetime 1200;", "  };"])
        if dnssl:
            lines.extend(["", f"  DNSSL {' '.join(dnssl)}", "  {", "    AdvDNSSLLifetime 1200;", "  };"])
        lines.append("};")
    lines.append("")
    return "\n".join(lines)


def _render_radvd_prefix(prefix: str) -> list[str]:
    return [
        "",
        f"  prefix {prefix}",
        "  {",
        "    AdvOnLink on;",
        "    AdvAutonomous on;",
        "  };",
    ]


def _render_service(config: IPv6PDConfig) -> str:
    uplink = str(config.uplink)
    interfaces = _hook_interfaces(config)
    interface_args = " ".join(shlex.quote(item) for item in interfaces)
    exec_condition = (
        "ExecCondition=/bin/sh -c 'for iface do ip link show dev \"$iface\" >/dev/null 2>&1 "
        "|| { echo \"dros: IPv6PD interface $iface is missing; skip service start\" >&2; exit 1; }; done' "
        f"sh {interface_args}\n"
    )
    accept_ra = ""
    if config.accept_ra is not None:
        accept_ra = (
            f"ExecStartPre=/bin/sh -c 'test ! -e /proc/sys/net/ipv6/conf/{shlex.quote(uplink)}/accept_ra "
            f"|| echo {int(config.accept_ra)} > /proc/sys/net/ipv6/conf/{shlex.quote(uplink)}/accept_ra'\n"
        )
    return f"""# Generated by DROS. Manual changes will be overwritten.
[Unit]
Description=DROS DHCPv6 Prefix Delegation client

[Service]
Type=simple
{exec_condition}{accept_ra}ExecStart=/usr/sbin/dhcp6c -f -c {DHCPC_CONF} {shlex.quote(uplink)}
Restart=always
RestartSec=30s

[Install]
WantedBy=multi-user.target
"""


def _render_ifup_hook(config: IPv6PDConfig) -> str:
    interfaces = " ".join(shlex.quote(item) for item in _hook_interfaces(config))
    return f"""#!/bin/sh
# Generated by DROS. Manual changes will be overwritten.
set -eu
IFACE="${{IFACE:-${{1:-}}}}"

case " {interfaces} " in
  *" $IFACE "*)
    {_hook_command()}
    ;;
esac
"""


def _render_ppp_ip_pre_up_hook(config: IPv6PDConfig) -> str:
    uplink = shlex.quote(str(config.uplink))
    accept_ra_line = ""
    if config.accept_ra is not None:
        accept_ra_line = (
            f'test ! -e "/proc/sys/net/ipv6/conf/$IFACE/accept_ra" '
            f'|| echo {int(config.accept_ra)} > "/proc/sys/net/ipv6/conf/$IFACE/accept_ra"'
        )
    return f"""#!/bin/sh
# Generated by DROS. Manual changes will be overwritten.
set -eu
IFACE="${{1:-${{PPP_IFACE:-}}}}"
[ "$IFACE" = {uplink} ] || exit 0
{accept_ra_line}
"""


def _render_ppp_ip_up_hook(config: IPv6PDConfig) -> str:
    return _render_ppp_up_hook(config)


def _render_ppp_ipv6_up_hook(config: IPv6PDConfig) -> str:
    return _render_ppp_up_hook(config)


def _render_ppp_up_hook(config: IPv6PDConfig) -> str:
    uplink = shlex.quote(str(config.uplink))
    accept_ra_line = ""
    if config.accept_ra is not None:
        accept_ra_line = (
            f'test ! -e "/proc/sys/net/ipv6/conf/$IFACE/accept_ra" '
            f'|| echo {int(config.accept_ra)} > "/proc/sys/net/ipv6/conf/$IFACE/accept_ra"'
        )
    return f"""#!/bin/sh
# Generated by DROS. Manual changes will be overwritten.
set -eu
IFACE="${{1:-${{PPP_IFACE:-}}}}"
[ "$IFACE" = {uplink} ] || exit 0
{accept_ra_line}
{_hook_command()}
"""


def _hook_command() -> str:
    return 'gw hook ipv6-refresh "$IFACE" --verbose 0'


def _render_nft_rules(config: IPv6PDConfig) -> str:
    uplink = _nft_string(str(config.uplink))
    downstream = [_nft_string(item.iface) for item in config.downstream if item.advertise]
    lines = [
        "# Generated by DROS. Manual changes will be overwritten.",
        "# IPv6 protocol traffic required by IPv6PD, DHCPv6, SLAAC, and RA.",
        (
            f'add rule inet {FILTER_TABLE} input_pre iifname "{uplink}" meta nfproto ipv6 '
            "icmpv6 type { destination-unreachable, packet-too-big, time-exceeded, "
            "parameter-problem, nd-router-advert, nd-neighbor-solicit, nd-neighbor-advert } accept"
        ),
        (
            f'add rule inet {FILTER_TABLE} input_pre iifname "{uplink}" '
            "udp sport 547 udp dport 546 accept"
        ),
        (
            f'add rule inet {FILTER_TABLE} output_user oifname "{uplink}" meta nfproto ipv6 '
            "icmpv6 type { nd-router-solicit, nd-neighbor-solicit, nd-neighbor-advert } accept"
        ),
        (
            f'add rule inet {FILTER_TABLE} output_user oifname "{uplink}" '
            "udp sport 546 udp dport 547 accept"
        ),
    ]
    for iface in downstream:
        lines.append(
            f'add rule inet {FILTER_TABLE} output_user oifname "{iface}" meta nfproto ipv6 '
            "icmpv6 type { nd-router-advert, nd-neighbor-solicit, nd-neighbor-advert } accept"
        )
    lines.append("")
    return "\n".join(lines)


def _accept_ra_command(uplink: str, value: int) -> list[str]:
    return [
        "sh",
        "-c",
        'test ! -e "/proc/sys/net/ipv6/conf/$1/accept_ra" || '
        'echo "$2" > "/proc/sys/net/ipv6/conf/$1/accept_ra"',
        "sh",
        uplink,
        str(value),
    ]


def _hook_interfaces(config: IPv6PDConfig) -> list[str]:
    result = [str(config.uplink)]
    for item in config.downstream:
        if item.iface not in result:
            result.append(item.iface)
    return result


def _delegated_prefix_length(config: IPv6PDConfig) -> int:
    value = config.delegated_prefix_length
    if value is None:
        value = config.prefix_length_hint
    if value is None:
        value = 60
    return int(value)


def _wide_ifid(address: str) -> int:
    try:
        return int(ipaddress.IPv6Address(address))
    except ValueError:
        return 1


def _render_dhcp6c_duid(value: str) -> bytes:
    duid = _parse_duid_bytes(value)
    return len(duid).to_bytes(2, "big") + duid


def _parse_duid_bytes(value: str) -> bytes:
    return bytes.fromhex(value.replace(":", "").replace("-", "").replace(" ", ""))


def _disabled_file(obj: ConfigObject) -> str:
    return (
        "# Generated by DROS. Manual changes will be overwritten.\n"
        f"# IPv6PD/{obj.name} is disabled.\n"
    )


def _warn_missing_ula_addresses(context: UpdateContext, config: IPv6PDConfig) -> None:
    for item in config.downstream:
        if not item.ula_prefix:
            continue
        iface = context.configs.get("Interface", item.iface)
        if iface is None:
            continue
        addresses = []
        address = iface.spec.get("address")
        if isinstance(address, str):
            addresses.append(address)
        for key in ("addresses", "extraAddresses", "extra_addresses"):
            values = iface.spec.get(key)
            if isinstance(values, list):
                addresses.extend(str(value) for value in values)
        if not any(_address_in_prefix(address, item.ula_prefix) for address in addresses):
            _warn(
                context,
                f"IPv6PD/system: downstream {item.iface} advertises {item.ula_prefix}, "
                "but Interface has no address inside that prefix",
            )


def _address_in_prefix(address: str, prefix: str) -> bool:
    try:
        interface = ipaddress.ip_interface(address)
        network = ipaddress.ip_network(prefix, strict=False)
    except ValueError:
        return False
    return interface.ip in network


def _on_off(value: bool) -> str:
    return "on" if value else "off"


def _nft_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _warn(context: UpdateContext, message: str) -> None:
    if context.executor.verbose >= 1:
        context.executor.console.print(f"warning: {message}", markup=False)
