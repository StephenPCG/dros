from __future__ import annotations

import ipaddress
import shlex
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from dros.config_objects import ConfigObject, DevGroupConfig, InterfaceConfig
from dros.plugins.base import BootstrapContext, DrosPlugin, UpdateContext

MANAGED_FILES = frozenset(
    {
        "/etc/network/interfaces.d/dros-*.cfg",
        "/etc/ppp/ip-down.d/dros-hook",
        "/etc/ppp/ip-up.d/dros-hook",
        "/etc/ppp/ipv6-up.d/dros-hook",
        "/etc/ppp/peers/*",
        "/etc/dros/openvpn/*.ovpn",
        "/etc/dros/openvpn/*.up",
        "/etc/wireguard/*.conf",
        "/usr/lib/dros/openvpn-iface",
    }
)

OPENVPN_DIR = "/etc/dros/openvpn"
OPENVPN_HELPER = "/usr/lib/dros/openvpn-iface"


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

    for obj in objects:
        if obj.kind == "DevGroup":
            _validate_model(context, obj, DevGroupConfig, errors)
            continue
        if obj.kind != "Interface":
            continue
        config = _validate_model(context, obj, InterfaceConfig, errors)
        if config is None:
            continue
        errors.extend(_validate_interface(obj, config, devgroups, interface_names))
    return errors


def update(context: UpdateContext, objects: list[ConfigObject]) -> None:
    devgroups = _devgroups(context, [])
    interfaces = [obj for obj in objects if obj.kind == "Interface"]
    for obj in sorted(interfaces, key=_interface_sort_key):
        config = context.configs.resolve_object(obj, InterfaceConfig)
        if config.type == "docker":
            update_docker_interface(context, obj.name, config, devgroups)
            continue

        changed = _write_auxiliary_files(context, obj, config, devgroups)
        path = f"/etc/network/interfaces.d/dros-{_safe_name(obj.name)}.cfg"
        changed = (
            context.executor.write_file(path, _render_interface(context, obj.name, config, devgroups))
            or changed
        )
        if changed:
            _reload_ifupdown_interface(context, obj.name, config)


def handle_event(context: UpdateContext, event: str, iface: str | None = None) -> None:
    devgroups = _devgroups(context, [])
    for obj in sorted(context.configs.by_kind("Interface"), key=_interface_sort_key):
        config = context.configs.resolve_object(obj, InterfaceConfig)
        if event == "docker-start" and config.type == "docker":
            update_docker_interface(context, obj.name, config, devgroups)
        elif event == "ppp-up" and config.type == "pppoe" and iface == obj.name:
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
) -> list[str]:
    errors: list[str] = []
    if config.devgroup and config.devgroup not in devgroups:
        errors.append(f"Interface/{obj.name}: references undefined DevGroup/{config.devgroup}")
    if config.type == "vlan":
        _validate_vlan(obj, config, interface_names, errors)
    elif config.type == "docker":
        _validate_docker(obj, config, errors)
    elif config.type == "gre":
        _validate_gre(obj, config, errors)
    elif config.type == "pppoe":
        _validate_pppoe(obj, config, errors)
    elif config.type == "wireguard":
        _validate_wireguard(obj, config, errors)
    elif config.type == "openvpn":
        _validate_openvpn(obj, config, errors)
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


def _validate_gre(obj: ConfigObject, config: InterfaceConfig, errors: list[str]) -> None:
    if not (config.address or config.local_vip):
        errors.append(f"Interface/{obj.name}: type gre requires spec.address or spec.localVip")
    if not config.local_public_ip:
        errors.append(f"Interface/{obj.name}: type gre requires spec.localPublicIp")
    if not config.remote_public_ip:
        errors.append(f"Interface/{obj.name}: type gre requires spec.remotePublicIp")
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


def _validate_openvpn(obj: ConfigObject, config: InterfaceConfig, errors: list[str]) -> None:
    has_inline = config.config is not None
    has_file = bool(config.config_file)
    if has_inline == has_file:
        errors.append(
            f"Interface/{obj.name}: type openvpn requires exactly one of "
            "spec.config or spec.configFile"
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


def _write_auxiliary_files(
    context: UpdateContext,
    obj: ConfigObject,
    config: InterfaceConfig,
    devgroups: dict[str, int],
) -> bool:
    changed = False
    name = obj.name
    if config.type == "pppoe":
        changed = context.executor.write_file(
            f"/etc/ppp/peers/{_safe_name(name)}",
            _render_pppoe_peer(name, config),
            mode=0o600,
        )
    elif config.type == "wireguard":
        changed = context.executor.write_file(
            f"/etc/wireguard/{_safe_name(name)}.conf",
            _render_wireguard_conf(config),
            mode=0o600,
        )
    elif config.type == "openvpn":
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
    return changed


def _render_interface(
    context: UpdateContext,
    name: str,
    config: InterfaceConfig,
    devgroups: dict[str, int],
) -> str:
    lines = [
        "# Generated by DROS. Manual changes will be overwritten.",
        f"auto {name}",
        *_interface_body(context, name, config),
    ]
    if config.devgroup and config.type not in {"pppoe", "openvpn"}:
        lines.append(f"  post-up ip link set dev {name} group {devgroups[config.devgroup]}")
    lines.append("")
    return "\n".join(lines)


def _interface_body(context: UpdateContext, name: str, config: InterfaceConfig) -> list[str]:
    if config.type == "loopback":
        return _render_loopback(name, config)
    if config.type == "gre":
        return _render_gre(name, config)
    if config.type == "pppoe":
        return _render_pppoe_iface(name, config)
    if config.type == "wireguard":
        return _render_wireguard_iface(name, config)
    if config.type == "openvpn":
        return _render_openvpn_iface(context, name, config)

    lines = _address_family(name, config)
    if config.type == "bridge":
        _append_bridge(lines, config)
    elif config.type == "vlan":
        _append_vlan(lines, config)
    return lines


def _address_family(name: str, config: InterfaceConfig) -> list[str]:
    if config.dhcp:
        lines = [f"iface {name} inet dhcp"]
    elif config.address:
        lines = [f"iface {name} inet static", f"  address {config.address}"]
    else:
        lines = [f"iface {name} inet manual"]

    if config.gateway:
        lines.append(f"  gateway {config.gateway}")
    _append_extra_addresses(lines, config.extra_addresses)
    return lines


def _append_extra_addresses(lines: list[str], addresses: list[str]) -> None:
    for address in addresses:
        lines.append(f"  up ip addr add {address} dev $IFACE")
        lines.append(f"  down ip addr del {address} dev $IFACE || true")


def _render_loopback(name: str, config: InterfaceConfig) -> list[str]:
    lines = [f"iface {name} inet loopback"]
    _append_extra_addresses(lines, config.extra_addresses)
    return lines


def _append_bridge(lines: list[str], config: InterfaceConfig) -> None:
    ports = " ".join(config.ports) if config.ports else "none"
    lines.append(f"  bridge_ports {ports}")
    lines.append("  bridge_fd 0")
    if config.vlan_aware:
        lines.append("  bridge_vlan_aware yes")


def _append_vlan(lines: list[str], config: InterfaceConfig) -> None:
    parent = shlex.quote(str(config.parent))
    vlan_id = int(config.id or 0)
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


def _render_pppoe_iface(name: str, config: InterfaceConfig) -> list[str]:
    device = shlex.quote(str(config.device))
    lines = [f"iface {name} inet ppp"]
    if config.manage_device:
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
    _append_ppp_bool(lines, config.defaultroute, "defaultroute")
    _append_ppp_bool(lines, config.replacedefaultroute, "replacedefaultroute")
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


def _render_wireguard_conf(config: InterfaceConfig) -> str:
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
            lines.append(f"AllowedIPs = {', '.join(str(item) for item in allowed_ips)}")
        endpoint = _peer_value(peer, "endpoint")
        if endpoint:
            lines.append(f"Endpoint = {endpoint}")
        keepalive = _peer_value(peer, "persistentKeepalive", "persistent_keepalive")
        if keepalive:
            lines.append(f"PersistentKeepalive = {keepalive}")
    lines.append("")
    return "\n".join(lines)


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
    if config.type == "loopback":
        context.executor.run(["ifup", name], check=False, real_only=True)
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


def _interface_sort_key(obj: ConfigObject) -> tuple[int, str]:
    type_order = {
        "eth": 0,
        "bridge": 1,
        "vlan": 2,
        "loopback": 3,
        "gre": 4,
        "wireguard": 5,
        "openvpn": 6,
        "pppoe": 7,
        "docker": 8,
    }
    iface_type = str(obj.spec.get("type", ""))
    return (type_order.get(iface_type, 50), obj.name)


def _safe_name(name: str) -> str:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-")
    return "".join(char if char in allowed else "_" for char in name)
