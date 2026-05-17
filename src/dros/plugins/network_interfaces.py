from __future__ import annotations

import ipaddress
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from dros.config_objects import ConfigObject, DevGroupConfig, InterfaceConfig
from dros.ip_lists import IpListStore, load_ip_lists
from dros.plugins.base import BootstrapContext, DrosPlugin, UpdateContext

MANAGED_FILES = frozenset(
    {
        "/etc/network/interfaces.d/dros-*.cfg",
        "/etc/network/interfaces.d/*-dros-*.cfg",
        "/etc/ppp/ip-down.d/dros-hook",
        "/etc/ppp/ip-up.d/dros-hook",
        "/etc/ppp/ipv6-up.d/dros-hook",
        "/etc/ppp/peers/*",
        "/etc/dros/openvpn/*.ovpn",
        "/etc/dros/openvpn/*.up",
        "/etc/dros/nftables.d/30-interface-*.nft",
        "/etc/wireguard/*.conf",
        "/etc/cron.d/dros-wgsd-client-*",
        "/usr/lib/dros/openvpn-iface",
    }
)

IFUPDOWN_DIR = "/etc/network/interfaces.d"
IFUPDOWN_FILE_STEP = 10
FIREWALL_NFT_PATH = "/etc/dros/nftables.d/10-firewall.nft"
NFTABLES_CONF = "/etc/nftables.conf"
NFT_FILTER_TABLE = "dros_filter"
OPENVPN_DIR = "/etc/dros/openvpn"
OPENVPN_HELPER = "/usr/lib/dros/openvpn-iface"
IFUPDOWN_TYPES = frozenset(
    {"eth", "bridge", "vlan", "loopback", "gre", "pppoe", "wireguard", "openvpn"}
)
INTERFACE_TYPE_ORDER = {
    "eth": 0,
    "ethernet": 0,
    "bridge": 1,
    "vlan": 2,
    "loopback": 3,
    "gre": 4,
    "wireguard": 5,
    "openvpn": 6,
    "pppoe": 7,
    "docker": 8,
    "external": 9,
}


@dataclass(frozen=True)
class AuxiliaryChanges:
    changed: bool = False
    wireguard_conf_changed: bool = False


def create_plugin() -> DrosPlugin:
    return DrosPlugin(
        name="network.interfaces",
        depends_on=("network.core",),
        config_kinds=frozenset({"DevGroup", "Interface"}),
        managed_files=MANAGED_FILES,
        bootstrap_hook=bootstrap,
        validation_hook=validate,
        update_hook=update,
        event_hooks=frozenset({"docker-start", "ppp-up", "ppp-down"}),
    )


def bootstrap(context: BootstrapContext) -> None:
    for path, event in (
        ("/etc/ppp/ip-up.d/dros-hook", "ppp-up"),
        ("/etc/ppp/ipv6-up.d/dros-hook", "ppp-up"),
        ("/etc/ppp/ip-down.d/dros-hook", "ppp-down"),
    ):
        context.executor.write_file(path, _ppp_global_hook(event), mode=0o755)
    context.executor.write_file(OPENVPN_HELPER, _render_openvpn_helper(), mode=0o755)


def validate(context: UpdateContext, objects: list[ConfigObject]) -> list[str]:
    errors: list[str] = []
    devgroups = _devgroups(context, errors)
    interface_names = {obj.name for obj in context.configs.by_kind("Interface")}
    xfrm_names = {obj.name for obj in context.configs.by_kind("XfrmTransport")}
    selected_interface_names: set[str] = set()

    for obj in objects:
        if obj.kind == "DevGroup":
            _validate_model(context, obj, DevGroupConfig, errors)
            continue
        if obj.kind != "Interface":
            continue
        selected_interface_names.add(obj.name)
        config = _validate_model(context, obj, InterfaceConfig, errors)
        if config is None:
            continue
        errors.extend(_validate_interface(obj, config, devgroups, interface_names, xfrm_names))
    if selected_interface_names:
        errors.extend(_validate_interface_dependency_graph(context, selected_interface_names))
    return errors


def update(context: UpdateContext, objects: list[ConfigObject]) -> None:
    devgroups = _devgroups(context, [])
    selected_names = {obj.name for obj in objects if obj.kind == "Interface"}
    ordered_interfaces = _interfaces_for_update_order(context, selected_names)
    file_order = _ifupdown_file_order(ordered_interfaces)
    for obj in ordered_interfaces:
        if obj.name not in selected_names:
            continue
        config = context.configs.resolve_object(obj, InterfaceConfig)
        iface_type = _interface_type(config)
        if iface_type == "external":
            apply_runtime_interface_properties(context, obj.name, config, devgroups)
            continue
        if iface_type == "docker":
            update_docker_interface(context, obj.name, config, devgroups)
            continue

        aux_changes = _write_auxiliary_files(context, obj, config, devgroups)
        nft_changed = _write_interface_nft(context, obj.name, config, devgroups)
        path = _interface_file_path(obj.name, file_order[obj.name])
        _migrate_interface_file(context, obj.name, path)
        iface_changed = context.executor.write_file(
            path,
            _render_interface(context, obj.name, config, devgroups),
        )
        if (
            iface_type == "wireguard"
            and aux_changes.wireguard_conf_changed
            and not iface_changed
        ):
            _wireguard_addconf(context, obj.name)
        elif iface_changed or aux_changes.changed:
            _reload_ifupdown_interface(context, obj.name, config)
        if nft_changed and _firewall_has_been_applied(context):
            context.executor.run(["nft", "-f", NFTABLES_CONF], real_only=True)


def handle_event(context: UpdateContext, event: str, iface: str | None = None) -> None:
    devgroups = _devgroups(context, [])
    for obj in sorted(context.configs.by_kind("Interface"), key=_interface_sort_key):
        config = context.configs.resolve_object(obj, InterfaceConfig)
        iface_type = _interface_type(config)
        if event == "docker-start" and iface_type == "docker":
            update_docker_interface(context, obj.name, config, devgroups)
        elif event == "ppp-up" and iface_type == "pppoe" and iface == obj.name:
            apply_runtime_interface_properties(context, obj.name, config, devgroups)


def update_docker_interface(
    context: UpdateContext,
    name: str,
    config: InterfaceConfig,
    devgroups: dict[str, int],
) -> None:
    if name != "docker0":
        _ensure_docker_network(context, name, config)
    apply_runtime_interface_properties(context, name, config, devgroups)


def apply_runtime_interface_properties(
    context: UpdateContext,
    name: str,
    config: InterfaceConfig,
    devgroups: dict[str, int],
) -> None:
    if config.devgroup:
        context.executor.run(
            ["ip", "link", "set", "dev", name, "group", str(devgroups[config.devgroup])],
            check=False,
            real_only=True,
        )
    for address in config.extra_addresses:
        context.executor.run(
            ["ip", "addr", "replace", address, "dev", name],
            check=False,
            real_only=True,
        )


def _devgroups(context: UpdateContext | BootstrapContext, errors: list[str]) -> dict[str, int]:
    groups: dict[str, int] = {}
    for obj in context.configs.by_kind("DevGroup"):
        config = _validate_model(context, obj, DevGroupConfig, errors)
        if config is None:
            continue
        groups[obj.name] = config.id
    return groups


def _validate_interface(
    obj: ConfigObject,
    config: InterfaceConfig,
    devgroups: dict[str, int],
    interface_names: set[str],
    xfrm_names: set[str],
) -> list[str]:
    errors: list[str] = []
    if config.devgroup and config.devgroup not in devgroups:
        errors.append(f"Interface/{obj.name}: references undefined DevGroup/{config.devgroup}")
    iface_type = _interface_type(config)
    if iface_type == "vlan":
        _validate_vlan(obj, config, interface_names, errors)
    elif iface_type == "docker":
        _validate_docker(obj, config, errors)
    elif iface_type == "gre":
        _validate_gre(obj, config, xfrm_names, errors)
    elif iface_type == "pppoe":
        _validate_pppoe(obj, config, errors)
    elif iface_type == "wireguard":
        _validate_wireguard(obj, config, errors)
    elif iface_type == "openvpn":
        _validate_openvpn(obj, config, errors)
        _validate_openvpn_listen(obj, config, devgroups, errors)
    return errors


def _validate_vlan(
    obj: ConfigObject,
    config: InterfaceConfig,
    interface_names: set[str],
    errors: list[str],
) -> None:
    if not config.parent:
        errors.append(f"Interface/{obj.name}: type vlan requires spec.parent")
    elif config.parent not in interface_names:
        errors.append(f"Interface/{obj.name}: parent Interface/{config.parent} is not defined")
    if config.id is None:
        errors.append(f"Interface/{obj.name}: type vlan requires spec.id")
    elif not 1 <= config.id <= 4094:
        errors.append(f"Interface/{obj.name}: vlan id must be between 1 and 4094, got {config.id}")


def _validate_docker(obj: ConfigObject, config: InterfaceConfig, errors: list[str]) -> None:
    if config.subnet:
        try:
            ipaddress.ip_network(config.subnet, strict=False)
        except ValueError as exc:
            errors.append(f"Interface/{obj.name}: spec.subnet is not a valid CIDR: {exc}")


def _validate_gre(
    obj: ConfigObject,
    config: InterfaceConfig,
    xfrm_names: set[str],
    errors: list[str],
) -> None:
    if not (config.address or config.local_vip):
        errors.append(f"Interface/{obj.name}: type gre requires spec.address or spec.localVip")
    if not config.local_public_ip:
        errors.append(f"Interface/{obj.name}: type gre requires spec.localPublicIp")
    if not config.remote_public_ip:
        errors.append(f"Interface/{obj.name}: type gre requires spec.remotePublicIp")
    if config.xfrm_transport and config.xfrm_transport not in xfrm_names:
        errors.append(
            f"Interface/{obj.name}: references undefined XfrmTransport/{config.xfrm_transport}"
        )
    if not 1 <= config.ttl <= 255:
        errors.append(f"Interface/{obj.name}: spec.ttl must be between 1 and 255")


def _validate_pppoe(obj: ConfigObject, config: InterfaceConfig, errors: list[str]) -> None:
    if not config.device:
        errors.append(f"Interface/{obj.name}: type pppoe requires spec.device")
    if not config.user:
        errors.append(f"Interface/{obj.name}: type pppoe requires spec.user")
    if not config.password:
        errors.append(f"Interface/{obj.name}: type pppoe requires spec.password")


def _validate_wireguard(obj: ConfigObject, config: InterfaceConfig, errors: list[str]) -> None:
    if not (config.private_key or config.private_key_file):
        errors.append(
            f"Interface/{obj.name}: type wireguard requires spec.privateKey or spec.privateKeyFile"
        )
    if config.listen_port is not None and not 1 <= config.listen_port <= 65535:
        errors.append(f"Interface/{obj.name}: spec.listenPort must be between 1 and 65535")
    for index, peer in enumerate(config.peers):
        if not _peer_value(peer, "publicKey", "public_key"):
            errors.append(f"Interface/{obj.name}: spec.peers[{index}].publicKey is required")
        allowed_ips = _peer_value(peer, "allowedIPs", "allowed_ips")
        if allowed_ips is not None and not isinstance(allowed_ips, list):
            errors.append(f"Interface/{obj.name}: spec.peers[{index}].allowedIPs must be a list")
    if config.wgsd_client is not None:
        if len(config.wgsd_client.schedule.split()) != 5:
            errors.append(
                f"Interface/{obj.name}: spec.wgsdClient.schedule must be a 5-field cron schedule"
            )
        for field_name, value in (
            ("dns", config.wgsd_client.dns),
            ("zone", config.wgsd_client.zone),
            ("schedule", config.wgsd_client.schedule),
        ):
            if any(char in str(value) for char in "\r\n"):
                errors.append(
                    f"Interface/{obj.name}: spec.wgsdClient.{field_name} must be single-line"
                )


def _validate_openvpn(obj: ConfigObject, config: InterfaceConfig, errors: list[str]) -> None:
    has_inline = config.config is not None
    has_file = bool(config.config_file)
    if has_inline == has_file:
        errors.append(
            f"Interface/{obj.name}: type openvpn requires exactly one of "
            "spec.config or spec.configFile"
        )


def _validate_openvpn_listen(
    obj: ConfigObject,
    config: InterfaceConfig,
    devgroups: dict[str, int],
    errors: list[str],
) -> None:
    for index, item in enumerate(_openvpn_listen_items(config.listen)):
        if item.get("proto") not in {"tcp", "udp"}:
            errors.append(f"Interface/{obj.name}: spec.listen[{index}].proto must be tcp or udp")
        port = item.get("port")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            errors.append(
                f"Interface/{obj.name}: spec.listen[{index}].port must be between 1 and 65535"
            )
        from_spec = item.get("from") or {}
        if not isinstance(from_spec, dict):
            errors.append(f"Interface/{obj.name}: spec.listen[{index}].from must be a mapping")
            continue
        for value in _list_values(from_spec.get("devGroups")):
            name = value.removeprefix("devgroup/")
            if name not in devgroups:
                errors.append(
                    f"Interface/{obj.name}: spec.listen[{index}].from.devGroups "
                    f"references undefined DevGroup/{name}"
                )


def _validate_model(
    context: UpdateContext | BootstrapContext,
    obj: ConfigObject,
    model_type: type[DevGroupConfig] | type[InterfaceConfig],
    errors: list[str],
) -> DevGroupConfig | InterfaceConfig | None:
    try:
        return context.configs.resolve_object(obj, model_type)
    except ValidationError as exc:
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            errors.append(f"{obj.kind}/{obj.name}: spec.{location}: {error['msg']}")
        return None


def _validate_interface_dependency_graph(
    context: UpdateContext,
    selected_names: set[str],
) -> list[str]:
    _ordered, errors = _sort_interface_objects(
        context.configs.by_kind("Interface"),
        roots=selected_names,
    )
    return errors


def _interfaces_for_update_order(
    context: UpdateContext,
    selected_names: set[str],
) -> list[ConfigObject]:
    ordered, errors = _sort_interface_objects(context.configs.by_kind("Interface"))
    if not errors:
        return ordered

    selected_ordered, selected_errors = _sort_interface_objects(
        context.configs.by_kind("Interface"),
        roots=selected_names,
    )
    if selected_errors:
        raise ValueError("\n".join(selected_errors))
    return selected_ordered


def _ifupdown_file_order(ordered_interfaces: list[ConfigObject]) -> dict[str, int]:
    ifupdown_interfaces = [
        obj for obj in ordered_interfaces if _normalized_interface_type(_raw_interface_type(obj)) in IFUPDOWN_TYPES
    ]
    return {obj.name: index for index, obj in enumerate(ifupdown_interfaces)}


def _interface_file_path(name: str, order: int) -> str:
    prefix = (order + 1) * IFUPDOWN_FILE_STEP
    return f"{IFUPDOWN_DIR}/{prefix:03d}-dros-{_safe_name(name)}.cfg"


def _migrate_interface_file(context: UpdateContext, name: str, desired_path: str) -> bool:
    legacy_paths = _legacy_interface_file_paths(context, name, desired_path)
    changed = False

    if not context.executor.exists(desired_path):
        for legacy_path in legacy_paths:
            if context.executor.exists(legacy_path):
                changed = context.executor.rename_file(legacy_path, desired_path) or changed
                break

    for legacy_path in legacy_paths:
        changed = context.executor.delete_file(legacy_path) or changed
    return changed


def _legacy_interface_file_paths(
    context: UpdateContext,
    name: str,
    desired_path: str,
) -> list[str]:
    safe_name = _safe_name(name)
    paths = [f"{IFUPDOWN_DIR}/dros-{safe_name}.cfg"]
    target_dir = context.executor.target_path(IFUPDOWN_DIR)
    if target_dir.exists():
        for match in sorted(target_dir.glob(f"*-dros-{safe_name}.cfg")):
            logical = _target_to_logical_path(context, match)
            if logical != desired_path:
                paths.append(logical)

    deduped: list[str] = []
    for path in paths:
        if path != desired_path and path not in deduped:
            deduped.append(path)
    return deduped


def _target_to_logical_path(context: UpdateContext, target: Path) -> str:
    if context.executor.is_real_root:
        return str(target)
    return "/" + str(target.relative_to(context.settings.sys_root))


def _write_auxiliary_files(
    context: UpdateContext,
    obj: ConfigObject,
    config: InterfaceConfig,
    devgroups: dict[str, int],
) -> AuxiliaryChanges:
    changed = False
    wireguard_conf_changed = False
    name = obj.name
    iface_type = _interface_type(config)
    if iface_type == "pppoe":
        changed = context.executor.write_file(
            f"/etc/ppp/peers/{_safe_name(name)}",
            _render_pppoe_peer(name, config),
            mode=0o600,
        )
    elif iface_type == "wireguard":
        wireguard_conf_changed = context.executor.write_file(
            f"/etc/wireguard/{_safe_name(name)}.conf",
            _render_wireguard_conf(context, obj, config),
            mode=0o600,
        )
        changed = wireguard_conf_changed
        changed = _write_wgsd_cron(context, name, config) or changed
    elif iface_type == "openvpn":
        changed = context.executor.write_file(
            _openvpn_config_path(name),
            _render_openvpn_config(context, obj, config),
            mode=0o600,
        )
        if _openvpn_needs_up_script(config):
            changed = (
                context.executor.write_file(
                    _openvpn_up_path(name),
                    _render_openvpn_up_script(name, config, devgroups),
                    mode=0o755,
                )
                or changed
            )
        else:
            changed = context.executor.delete_file(_openvpn_up_path(name)) or changed
    return AuxiliaryChanges(changed=changed, wireguard_conf_changed=wireguard_conf_changed)


def _write_interface_nft(
    context: UpdateContext,
    name: str,
    config: InterfaceConfig,
    devgroups: dict[str, int],
) -> bool:
    path = _interface_nft_path(name)
    content = _render_interface_nft(name, config, devgroups)
    if content is None:
        if context.executor.exists(path):
            return context.executor.write_file(path, _disabled_interface_nft(name))
        return False
    return context.executor.write_file(path, content)


def _render_interface_nft(
    name: str,
    config: InterfaceConfig,
    devgroups: dict[str, int],
) -> str | None:
    iface_type = _interface_type(config)
    if iface_type == "wireguard" and config.listen_port:
        return "\n".join(
            [
                "# Generated by DROS. Manual changes will be overwritten.",
                f"# WireGuard listen port for Interface/{name}.",
                (
                    f"add rule inet {NFT_FILTER_TABLE} input_pre "
                    f"udp dport {int(config.listen_port)} accept"
                ),
                "",
            ]
        )

    if iface_type == "openvpn":
        listen_items = _openvpn_listen_items(config.listen)
        if not listen_items:
            return None
        lines = [
            "# Generated by DROS. Manual changes will be overwritten.",
            f"# OpenVPN listen port rules for Interface/{name}.",
        ]
        for item in listen_items:
            proto = str(item["proto"])
            port = int(item["port"])
            selectors = _openvpn_listen_selectors(item.get("from") or {}, devgroups)
            if not selectors:
                selectors = [""]
            for selector in selectors:
                match = f"{selector} {proto} dport {port}".strip()
                lines.append(f"add rule inet {NFT_FILTER_TABLE} input_pre {match} accept")
        lines.append("")
        return "\n".join(lines)

    return None


def _disabled_interface_nft(name: str) -> str:
    return "\n".join(
        [
            "# Generated by DROS. Manual changes will be overwritten.",
            f"# Interface/{name} has no nftables listen rules.",
            "",
        ]
    )


def _interface_nft_path(name: str) -> str:
    return f"/etc/dros/nftables.d/30-interface-{_safe_name(name)}.nft"


def _render_interface(
    context: UpdateContext,
    name: str,
    config: InterfaceConfig,
    devgroups: dict[str, int],
) -> str:
    lines = [
        "# Generated by DROS. Manual changes will be overwritten.",
    ]
    if config.auto:
        lines.append(f"auto {name}")
    elif config.allow_hotplug:
        lines.append(f"allow-hotplug {name}")
    lines.extend(_interface_body(context, name, config))
    if config.devgroup and _interface_type(config) not in {"pppoe", "openvpn"}:
        lines.append(f"  post-up ip link set dev {name} group {devgroups[config.devgroup]}")
    lines.append("")
    return "\n".join(lines)


def _interface_body(context: UpdateContext, name: str, config: InterfaceConfig) -> list[str]:
    iface_type = _interface_type(config)
    if iface_type == "loopback":
        return _render_loopback(name, config)
    if iface_type == "gre":
        return _render_gre(name, config)
    if iface_type == "pppoe":
        return _render_pppoe_iface(context, name, config)
    if iface_type == "wireguard":
        return _render_wireguard_iface(name, config)
    if iface_type == "openvpn":
        return _render_openvpn_iface(context, name, config)

    lines = _address_family(name, config)
    if iface_type == "bridge":
        _append_bridge(lines, config)
    elif iface_type == "vlan":
        _append_vlan(context, lines, config)
    return lines


def _address_family(name: str, config: InterfaceConfig) -> list[str]:
    address, extra_addresses = _address_fields(config)
    if config.dhcp:
        lines = [f"iface {name} inet dhcp"]
        _append_gateway_route(lines, name, config)
        _append_extra_addresses(lines, extra_addresses)
        return lines
    elif address:
        lines = [f"iface {name} inet static", f"  address {address}"]
    else:
        lines = [f"iface {name} inet manual"]
        _append_gateway_route(lines, name, config)
        _append_extra_addresses(lines, extra_addresses)
        return lines

    if config.gateway:
        lines.append(f"  gateway {config.gateway}")
    _append_extra_addresses(lines, extra_addresses)
    return lines


def _address_fields(config: InterfaceConfig) -> tuple[str | None, list[str]]:
    if config.addresses:
        return config.addresses[0], [*config.addresses[1:], *config.extra_addresses]
    return config.address, list(config.extra_addresses)


def _append_extra_addresses(lines: list[str], addresses: list[str]) -> None:
    for address in addresses:
        lines.append(f"  up ip addr add {address} dev $IFACE")
        lines.append(f"  down ip addr del {address} dev $IFACE || true")


def _append_gateway_route(lines: list[str], name: str, config: InterfaceConfig) -> None:
    if not config.gateway:
        return
    lines.append(f"  post-up ip route replace default via {config.gateway} dev {name}")
    lines.append(f"  pre-down ip route del default via {config.gateway} dev {name} || true")


def _render_loopback(name: str, config: InterfaceConfig) -> list[str]:
    lines = [f"iface {name} inet loopback"]
    _append_extra_addresses(lines, config.extra_addresses)
    return lines


def _append_bridge(lines: list[str], config: InterfaceConfig) -> None:
    ports = " ".join(config.ports) if config.ports else "none"
    lines.append(f"  bridge_ports {ports}")
    lines.append(f"  bridge_stp {_on_off(config.stp)}")
    lines.append(f"  bridge_fd {config.forward_delay}")
    if config.vlan_aware:
        lines.append("  bridge_vlan_aware yes")


def _append_vlan(context: UpdateContext, lines: list[str], config: InterfaceConfig) -> None:
    parent = shlex.quote(str(config.parent))
    vlan_id = int(config.id or 0)
    if config.parent and _should_ifup_dependency(context, config.parent):
        lines.append(f"  pre-up ifup {parent} 2>/dev/null || true")
    lines.append(
        f"  pre-up ip link show dev {parent} >/dev/null 2>&1 || "
        f"{{ echo 'dros: vlan parent {parent} for $IFACE is missing' >&2; exit 1; }}"
    )
    lines.append(
        "  pre-up ip link show dev $IFACE >/dev/null 2>&1 || "
        f"ip link add link {parent} name $IFACE type vlan id {vlan_id}"
    )
    lines.append("  post-down ip link del dev $IFACE || true")


def _render_gre(name: str, config: InterfaceConfig) -> list[str]:
    local_vip = config.local_vip or config.address or ""
    lines = [
        f"iface {name} inet static",
        f"  address {_host_cidr(local_vip, default_prefix=32)}",
    ]
    if config.remote_vip:
        lines.append(f"  pointopoint {config.remote_vip}")
    if config.xfrm_transport:
        selector = shlex.quote(f"xfrm/{config.xfrm_transport}")
        lines.append(f"  pre-up gw start {selector} --verbose 0")
    lines.extend(
        [
            "  pre-up ip tunnel add $IFACE mode gre "
            f"local {config.local_public_ip} remote {config.remote_public_ip} ttl {config.ttl}",
            "  pre-up ip link set $IFACE up",
            "  post-down ip tunnel del $IFACE || true",
        ]
    )
    if config.xfrm_transport:
        selector = shlex.quote(f"xfrm/{config.xfrm_transport}")
        lines.append(f"  post-down gw stop {selector} --verbose 0 || true")
    return lines


def _render_pppoe_iface(context: UpdateContext, name: str, config: InterfaceConfig) -> list[str]:
    device = shlex.quote(str(config.device))
    lines = [f"iface {name} inet ppp"]
    if config.manage_device:
        lines.append(f"  pre-up ifup {device} 2>/dev/null || true")
        lines.extend(
            [
                f"  pre-up ip link show dev {device} >/dev/null 2>&1 || "
                f"{{ echo 'dros: pppoe device {device} for $IFACE is missing' >&2; exit 1; }}",
                f"  pre-up ip link set dev {device} up",
            ]
        )
    lines.append(f"  provider {name}")
    return lines


def _render_pppoe_peer(name: str, config: InterfaceConfig) -> str:
    lines = [
        "# Generated by DROS. Manual changes will be overwritten.",
        f"plugin rp-pppoe.so {config.device}",
        f"ifname {name}",
        f"user {_ppp_option_value(config.user)}",
        f"password {_ppp_option_value(config.password)}",
    ]
    _append_ppp_bool(lines, config.hide_password, "hide-password")
    _append_ppp_bool(lines, config.noauth, "noauth")
    lines.append(f"maxfail {config.maxfail}")
    _append_ppp_bool(lines, config.persist, "persist")
    _append_ppp_bool(lines, config.debug, "debug")
    lines.append(f"holdoff {config.holdoff}")
    _append_ppp_bool(lines, config.noipdefault, "noipdefault")
    if config.nodefaultroute:
        lines.append("nodefaultroute")
    elif config.defaultroute:
        lines.append("defaultroute")
        if config.replacedefaultroute and not config.noreplacedefaultroute:
            lines.append("replacedefaultroute")
    elif config.noreplacedefaultroute:
        lines.append("noreplacedefaultroute")
    if config.nodefaultroute6:
        lines.append("nodefaultroute6")
    elif config.defaultroute6:
        lines.append("defaultroute6")
    _append_ppp_bool(lines, config.noproxyarp, "noproxyarp")
    ipv6_options: list[str] = []
    if config.ipv6:
        ipv6_options.append("+ipv6")
    if config.ipv6cp_use_ipaddr:
        ipv6_options.append("ipv6cp-use-ipaddr")
    if ipv6_options:
        lines.append(" ".join(ipv6_options))
    _append_ppp_bool(lines, config.use_peer_dns, "usepeerdns")
    lines.append("")
    return "\n".join(lines)


def _append_ppp_bool(lines: list[str], enabled: bool, option: str) -> None:
    if enabled:
        lines.append(option)


def _ppp_option_value(value: object) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _render_wireguard_iface(name: str, config: InterfaceConfig) -> list[str]:
    lines = _address_family(name, config)
    lines.append("  pre-up ip link add dev $IFACE type wireguard")
    if config.private_key_file and not config.private_key:
        lines.append(
            f"  pre-up wg set $IFACE private-key {shlex.quote(config.private_key_file)}"
        )
    lines.extend(
        [
            f"  pre-up wg setconf $IFACE /etc/wireguard/{_safe_name(name)}.conf",
            "  post-down ip link del dev $IFACE || true",
        ]
    )
    return lines


def _wireguard_addconf(context: UpdateContext, name: str) -> None:
    safe_name = _safe_name(name)
    quoted_name = shlex.quote(name)
    quoted_config = shlex.quote(f"/etc/wireguard/{safe_name}.conf")
    context.executor.run(
        [
            "sh",
            "-c",
            (
                f"if ip link show dev {quoted_name} >/dev/null 2>&1; then "
                f"wg addconf {quoted_name} {quoted_config}; "
                f"else ifup {quoted_name}; fi"
            ),
        ],
        real_only=True,
    )


def _write_wgsd_cron(context: UpdateContext, name: str, config: InterfaceConfig) -> bool:
    path = f"/etc/cron.d/dros-wgsd-client-{_safe_name(name)}"
    if config.wgsd_client is None:
        return context.executor.delete_file(path)
    lines = [
        "# Generated by DROS. Manual changes will be overwritten.",
        f"# Resource: Interface/{name} wgsdClient",
    ]
    command = " ".join(
        [
            "/usr/local/bin/wgsd-client",
            "-device",
            shlex.quote(name),
            "-dns",
            shlex.quote(config.wgsd_client.dns),
            "-zone",
            shlex.quote(config.wgsd_client.zone),
        ]
    )
    entry = f"{config.wgsd_client.schedule} root {command}"
    if config.wgsd_client.enabled:
        lines.append(entry)
    else:
        lines.append("# disabled")
        lines.append(f"# {entry}")
    lines.append("")
    return context.executor.write_file(path, "\n".join(lines))


def _render_wireguard_conf(
    context: UpdateContext,
    obj: ConfigObject,
    config: InterfaceConfig,
) -> str:
    ip_lists = load_ip_lists(context.settings)
    lines = ["# Generated by DROS. Manual changes will be overwritten.", "[Interface]"]
    if config.private_key:
        lines.append(f"PrivateKey = {config.private_key}")
    if config.listen_port:
        lines.append(f"ListenPort = {config.listen_port}")
    for peer in config.peers:
        lines.extend(["", "[Peer]"])
        lines.append(f"PublicKey = {_peer_value(peer, 'publicKey', 'public_key')}")
        allowed_ips = _peer_value(peer, "allowedIPs", "allowed_ips")
        if allowed_ips:
            expanded, warnings = _expand_wireguard_allowed_ips(allowed_ips, ip_lists)
            for warning in warnings:
                _warn(context, f"Interface/{obj.name}: {warning}")
            if expanded:
                lines.append(f"AllowedIPs = {', '.join(expanded)}")
        endpoint = _peer_value(peer, "endpoint")
        if endpoint:
            lines.append(f"Endpoint = {endpoint}")
        keepalive = _peer_value(peer, "persistentKeepalive", "persistent_keepalive")
        if keepalive:
            lines.append(f"PersistentKeepalive = {keepalive}")
    lines.append("")
    return "\n".join(lines)


def _expand_wireguard_allowed_ips(
    values: list[object],
    ip_lists: IpListStore,
) -> tuple[list[str], list[str]]:
    allowed_ips: list[str] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        value = str(raw_value)
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError:
            networks, list_warnings = _wireguard_ip_list_networks(value, ip_lists)
            warnings.extend(list_warnings)
        else:
            networks = [str(network)]
        for network in networks:
            if network in seen:
                continue
            seen.add(network)
            allowed_ips.append(network)
    return allowed_ips, warnings


def _wireguard_ip_list_networks(
    reference: str,
    ip_lists: IpListStore,
) -> tuple[list[str], list[str]]:
    if "@" in reference:
        name, suffix = reference.rsplit("@", 1)
        if suffix == "v4":
            return ip_lists.resolve(name, "ipv4")
        if suffix == "v6":
            return ip_lists.resolve(name, "ipv6")
        if suffix == "all":
            return _resolve_wireguard_all_ip_list(name, ip_lists)
        return [], [f"allowedIPs ip list reference {reference!r} has unsupported suffix @{suffix}"]
    return _resolve_wireguard_all_ip_list(reference, ip_lists)


def _resolve_wireguard_all_ip_list(
    name: str,
    ip_lists: IpListStore,
) -> tuple[list[str], list[str]]:
    v4, v4_warnings = ip_lists.resolve(name, "ipv4")
    v6, v6_warnings = ip_lists.resolve(name, "ipv6")
    if v4_warnings and v6_warnings:
        return [], [f"ip list {name!r} not found"]
    warnings = [warning for warning in [*v4_warnings, *v6_warnings] if "not found" not in warning]
    return [*v4, *v6], warnings


def _render_openvpn_iface(
    context: UpdateContext,
    name: str,
    config: InterfaceConfig,
) -> list[str]:
    pid_path = str(context.settings.paths.run / f"openvpn.{_safe_name(name)}.pid")
    log_path = str(context.settings.paths.logs / f"openvpn-{_safe_name(name)}.log")
    crl_path = config.crl_file or "-"
    start_command = [
        OPENVPN_HELPER,
        "start",
        name,
        _openvpn_config_path(name),
        pid_path,
        _openvpn_up_path(name) if _openvpn_needs_up_script(config) else "-",
        crl_path,
        log_path,
    ]
    stop_command = [OPENVPN_HELPER, "stop", name, pid_path]
    return [
        f"iface {name} inet manual",
        "  pre-up " + _shell_command(start_command),
        "  post-down " + _shell_command(stop_command),
    ]


def _render_openvpn_config(
    context: UpdateContext,
    obj: ConfigObject,
    config: InterfaceConfig,
) -> str:
    if config.config is not None:
        return _with_trailing_newline(config.config)
    source = _openvpn_config_file_path(context, obj, str(config.config_file))
    try:
        content = source.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ValueError(f"Interface/{obj.name}: openvpn configFile not found: {source}") from exc
    return _with_trailing_newline(content)


def _openvpn_config_file_path(
    context: UpdateContext,
    obj: ConfigObject,
    value: str,
) -> Path:
    raw = Path(value).expanduser()
    if raw.is_absolute():
        return context.executor.target_path(raw)
    return obj.source.parent / raw


def _render_openvpn_up_script(
    name: str,
    config: InterfaceConfig,
    devgroups: dict[str, int],
) -> str:
    lines = [
        "#!/bin/sh",
        "# Generated by DROS. Manual changes will be overwritten.",
        "set -eu",
        f"IFACE={_shell_double_quote(name)}",
        'if ! ip link show dev "$IFACE" >/dev/null 2>&1; then',
        '  echo "dros: openvpn interface $IFACE is missing in up hook" >&2',
        "  exit 1",
        "fi",
    ]
    if config.devgroup:
        lines.append(f'ip link set dev "$IFACE" group {devgroups[config.devgroup]}')
    if config.up:
        lines.append(f"sh -c {shlex.quote(config.up)}")
    lines.append("")
    return "\n".join(lines)


def _openvpn_needs_up_script(config: InterfaceConfig) -> bool:
    return bool(config.devgroup or config.up)


def _openvpn_config_path(name: str) -> str:
    return f"{OPENVPN_DIR}/{_safe_name(name)}.ovpn"


def _openvpn_up_path(name: str) -> str:
    return f"{OPENVPN_DIR}/{_safe_name(name)}.up"


def _ensure_docker_network(context: UpdateContext, name: str, config: InterfaceConfig) -> None:
    if not _docker_network_exists(context, name):
        _create_docker_network(context, name, config)
        return

    current_subnet = context.executor.output(
        ["docker", "network", "inspect", "-f", "{{range .IPAM.Config}}{{.Subnet}}{{end}}", name]
    )
    current_bridge = context.executor.output(
        [
            "docker",
            "network",
            "inspect",
            "-f",
            '{{index .Options "com.docker.network.bridge.name"}}',
            name,
        ]
    )
    expected_subnet = config.subnet or ""
    if current_subnet != expected_subnet or current_bridge != name:
        context.executor.run(["docker", "network", "rm", name], real_only=True)
        _create_docker_network(context, name, config)


def _docker_network_exists(context: UpdateContext, name: str) -> bool:
    result = context.executor.run(
        ["docker", "network", "inspect", name],
        check=False,
        quiet=True,
        real_only=True,
    )
    return result is not None and result.returncode == 0


def _create_docker_network(context: UpdateContext, name: str, config: InterfaceConfig) -> None:
    command = [
        "docker",
        "network",
        "create",
        "--driver",
        "bridge",
        "--opt",
        f"com.docker.network.bridge.name={name}",
    ]
    if config.subnet:
        command.extend(["--subnet", config.subnet])
    command.append(name)
    context.executor.run(command, real_only=True)


def _reload_ifupdown_interface(
    context: UpdateContext,
    name: str,
    config: InterfaceConfig,
) -> None:
    iface_type = _interface_type(config)
    if iface_type == "loopback":
        context.executor.run(["ifup", name], check=False, real_only=True)
        return
    if iface_type == "pppoe":
        context.executor.run(["ifdown", "--force", name], check=False, real_only=True, timeout=60)
        context.executor.run(["ifup", name], real_only=True, timeout=120)
        return
    context.executor.run(["ifdown", "--force", name], check=False, real_only=True)
    context.executor.run(["ifup", name], real_only=True)


def _ppp_global_hook(event: str) -> str:
    return "\n".join(
        [
            "#!/bin/sh",
            "# Generated by DROS. Manual changes will be overwritten.",
            "set -eu",
            'IFACE="${1:-}"',
            '[ -n "$IFACE" ] || exit 0',
            'case "$IFACE" in -*) exit 0 ;; esac',
            f'exec /usr/local/bin/gw hook {event} "$IFACE" --verbose 0',
            "",
        ]
    )


def _render_openvpn_helper() -> str:
    return r'''#!/bin/sh
# Generated by DROS. Manual changes will be overwritten.
set -eu

usage() {
  echo "usage: openvpn-iface start IFACE CONFIG PID UP_SCRIPT CRL_FILE LOG_FILE | openvpn-iface stop IFACE PID" >&2
  exit 2
}

action="${1:-}"
[ -n "$action" ] || usage
shift

find_openvpn() {
  if [ -n "${OPENVPN:-}" ]; then
    echo "$OPENVPN"
    return
  fi
  if [ -x /usr/sbin/openvpn ]; then
    echo /usr/sbin/openvpn
    return
  fi
  command -v openvpn 2>/dev/null || {
    echo "dros: openvpn executable not found" >&2
    exit 1
  }
}

pid_alive() {
  [ -n "${1:-}" ] && kill -0 "$1" >/dev/null 2>&1
}

report_openvpn_failure() {
  status="$1"
  log_file="$2"
  echo "dros: openvpn failed for $iface with exit status $status" >&2
  if [ -s "$log_file" ]; then
    echo "dros: last openvpn log lines from $log_file:" >&2
    tail -n 40 "$log_file" >&2 || true
  fi
}

case "$action" in
  start)
    [ "$#" -eq 6 ] || usage
    iface="$1"
    config="$2"
    pid_file="$3"
    up_script="$4"
    crl_file="$5"
    log_file="$6"
    [ -f "$config" ] || {
      echo "dros: openvpn config not found: $config" >&2
      exit 1
    }
    if [ "$up_script" != "-" ] && [ ! -x "$up_script" ]; then
      echo "dros: openvpn up script is not executable: $up_script" >&2
      exit 1
    fi
    if [ "$crl_file" != "-" ] && [ ! -r "$crl_file" ]; then
      echo "dros: openvpn crl file is not readable: $crl_file" >&2
      exit 1
    fi
    if [ -s "$pid_file" ]; then
      old_pid="$(cat "$pid_file" 2>/dev/null || true)"
      if pid_alive "$old_pid"; then
        exit 0
      fi
      rm -f "$pid_file"
    fi
    mkdir -p "$(dirname "$pid_file")" "$(dirname "$log_file")"
    openvpn_bin="$(find_openvpn)"
    set -- "$openvpn_bin" --daemon "$iface" --config "$config" --writepid "$pid_file" --dev "$iface" --log-append "$log_file"
    if [ "$crl_file" != "-" ]; then
      set -- "$@" --crl-verify "$crl_file"
    fi
    if [ "$up_script" != "-" ]; then
      set -- "$@" --script-security 2 --up "$up_script"
    fi
    "$@" || {
      status="$?"
      report_openvpn_failure "$status" "$log_file"
      exit "$status"
    }
    ;;
  stop)
    [ "$#" -eq 2 ] || usage
    iface="$1"
    pid_file="$2"
    if [ ! -s "$pid_file" ]; then
      exit 0
    fi
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if ! pid_alive "$pid"; then
      rm -f "$pid_file"
      exit 0
    fi
    kill "$pid" 2>/dev/null || true
    i=0
    while pid_alive "$pid" && [ "$i" -lt 50 ]; do
      sleep 0.1
      i=$((i + 1))
    done
    if pid_alive "$pid"; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
    ;;
  *)
    usage
    ;;
esac
'''


def _firewall_has_been_applied(context: UpdateContext) -> bool:
    return context.executor.exists(FIREWALL_NFT_PATH) and context.executor.exists(NFTABLES_CONF)


def _openvpn_listen_items(value: object) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _openvpn_listen_selectors(from_spec: dict[str, Any], devgroups: dict[str, int]) -> list[str]:
    selectors: list[str] = []
    ifaces = _list_values(from_spec.get("ifaces"))
    if ifaces:
        selectors.append(_nft_interface_set("iifname", ifaces))

    group_values = []
    for value in _list_values(from_spec.get("devGroups")):
        name = value.removeprefix("devgroup/")
        group_values.append(str(devgroups[name]))
    if group_values:
        selectors.append(_nft_set("iifgroup", group_values, quote=False))
    return selectors


def _list_values(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _nft_interface_set(field: str, values: list[str]) -> str:
    return _nft_set(field, values, quote=True)


def _nft_set(field: str, values: list[str], *, quote: bool) -> str:
    if len(values) == 1:
        value = f'"{_nft_string(values[0])}"' if quote else values[0]
        return f"{field} {value}"
    rendered = [f'"{_nft_string(value)}"' if quote else value for value in values]
    return f"{field} {{ {', '.join(rendered)} }}"


def _nft_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _peer_value(peer: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in peer:
            return peer[name]
    return None


def _host_cidr(value: str, *, default_prefix: int) -> str:
    return value if "/" in value else f"{value}/{default_prefix}"


def _with_trailing_newline(value: str) -> str:
    return value if value.endswith("\n") else f"{value}\n"


def _shell_command(parts: list[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def _shell_double_quote(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )
    return f'"{escaped}"'


def _sort_interface_objects(
    interfaces: list[ConfigObject],
    *,
    roots: set[str] | None = None,
) -> tuple[list[ConfigObject], list[str]]:
    by_name = {obj.name: obj for obj in interfaces}
    interface_names = set(by_name)
    ordered: list[ConfigObject] = []
    errors: list[str] = []
    states: dict[str, str] = {}
    stack: list[str] = []
    reported_cycles: set[tuple[str, ...]] = set()

    def visit(name: str) -> None:
        state = states.get(name)
        if state == "done":
            return
        if state == "visiting":
            cycle = stack[stack.index(name) :] + [name]
            marker = tuple(cycle)
            if marker not in reported_cycles:
                reported_cycles.add(marker)
                errors.append(
                    f"Interface/{cycle[0]}: dependency cycle detected: {' -> '.join(cycle)}"
                )
            return

        obj = by_name.get(name)
        if obj is None:
            return

        states[name] = "visiting"
        stack.append(name)
        for dependency in sorted(
            _interface_dependencies(obj, interface_names),
            key=lambda item: _interface_sort_key(by_name[item]),
        ):
            visit(dependency)
        stack.pop()
        states[name] = "done"
        ordered.append(obj)

    root_names = interface_names if roots is None else {name for name in roots if name in by_name}
    for name in sorted(root_names, key=lambda item: _interface_sort_key(by_name[item])):
        visit(name)

    if errors:
        return [], errors
    return ordered, []


def _interface_dependencies(obj: ConfigObject, interface_names: set[str]) -> list[str]:
    dependencies: list[str] = []
    iface_type = _normalized_interface_type(_raw_interface_type(obj))
    if iface_type == "vlan":
        _append_dependency(dependencies, obj.spec.get("parent"), interface_names)
    elif iface_type == "bridge":
        ports = obj.spec.get("ports")
        if isinstance(ports, list):
            for port in ports:
                _append_dependency(dependencies, port, interface_names)
    elif iface_type == "pppoe":
        _append_dependency(dependencies, obj.spec.get("device"), interface_names)
    return dependencies


def _append_dependency(
    dependencies: list[str],
    value: object,
    interface_names: set[str],
) -> None:
    if isinstance(value, str) and value in interface_names and value not in dependencies:
        dependencies.append(value)


def _interface_sort_key(obj: ConfigObject) -> tuple[int, str]:
    return (INTERFACE_TYPE_ORDER.get(_normalized_interface_type(_raw_interface_type(obj)), 50), obj.name)


def _raw_interface_type(obj: ConfigObject) -> str:
    value = obj.spec.get("type", "")
    return value if isinstance(value, str) else ""


def _interface_type(config: InterfaceConfig) -> str:
    return _normalized_interface_type(config.type)


def _normalized_interface_type(value: str) -> str:
    return "eth" if value == "ethernet" else value


def _should_ifup_dependency(context: UpdateContext, name: str) -> bool:
    obj = context.configs.get("Interface", name)
    if obj is None:
        return False
    iface_type = _normalized_interface_type(_raw_interface_type(obj))
    return iface_type not in {"external", "docker"}


def _on_off(value: bool) -> str:
    return "on" if value else "off"


def _warn(context: UpdateContext, message: str) -> None:
    if context.executor.verbose >= 1:
        context.executor.console.print(f"warning: {message}", markup=False)


def _safe_name(name: str) -> str:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")
    return "".join(char if char in allowed else "_" for char in name)
