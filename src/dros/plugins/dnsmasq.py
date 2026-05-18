from __future__ import annotations

import ipaddress
from pathlib import Path

from pydantic import ValidationError

from dros.config_objects import (
    ConfigObject,
    DnsmasqChinaNamesConfig,
    DnsmasqDHCPConfig,
    DnsmasqDHCPRangeConfig,
    DnsmasqDNSConfig,
)
from dros.dnsmasq_china_names import (
    AVAILABLE_DNSMASQ_CHINA_NAME_FILES,
    CHINA_NAMES_CONF,
    CHINA_NAMES_MANUAL_CONF,
    china_names_cache_dir,
    render_cached_china_names_conf,
    render_manual_names_conf,
)
from dros.plugins.base import DrosPlugin, UpdateContext
from dros.plugins.ip_lists import normalize_schedule

DNSMASQ_KINDS = frozenset({"DnsmasqDNS", "DnsmasqDHCP", "DnsmasqChinaNames"})
DNS_CONF = "/etc/dnsmasq.d/dros-10-dns.conf"
DHCP_CONF = "/etc/dnsmasq.d/dros-20-dhcp.conf"
CHINA_NAMES_CRON = "/etc/cron.d/dros-dnsmasq-china-names"
LOGROTATE_CONF = "/etc/logrotate.d/dros-dnsmasq"


def create_plugin() -> DrosPlugin:
    return DrosPlugin(
        name="network.dnsmasq",
        depends_on=("network.core",),
        config_kinds=DNSMASQ_KINDS,
        managed_files=frozenset(
            {
                DNS_CONF,
                DHCP_CONF,
                CHINA_NAMES_CONF,
                CHINA_NAMES_MANUAL_CONF,
                CHINA_NAMES_CRON,
            }
        ),
        validation_hook=validate,
        update_hook=update,
    )


def validate(context: UpdateContext, objects: list[ConfigObject]) -> list[str]:
    errors: list[str] = []
    for obj in objects:
        if obj.kind == "DnsmasqDNS":
            config = _validate_model(context, obj, DnsmasqDNSConfig, errors)
            if config is not None:
                _validate_dns(obj, config, errors)
        elif obj.kind == "DnsmasqDHCP":
            config = _validate_model(context, obj, DnsmasqDHCPConfig, errors)
            if config is not None:
                _validate_dhcp(obj, config, errors)
        elif obj.kind == "DnsmasqChinaNames":
            config = _validate_model(context, obj, DnsmasqChinaNamesConfig, errors)
            if config is not None:
                _validate_china_names(obj, config, errors)
    return errors


def update(context: UpdateContext, objects: list[ConfigObject]) -> None:
    changed = False
    for obj in sorted(objects, key=lambda item: (item.kind, item.name)):
        if obj.kind == "DnsmasqDNS":
            changed = _update_dns(context, obj) or changed
        elif obj.kind == "DnsmasqDHCP":
            changed = _update_dhcp(context, obj) or changed
        elif obj.kind == "DnsmasqChinaNames":
            changed = _update_china_names(context, obj) or changed
    if changed:
        context.executor.run(["systemctl", "restart", "dnsmasq"], real_only=True)


def _update_dns(context: UpdateContext, obj: ConfigObject) -> bool:
    config = context.configs.resolve_object(obj, DnsmasqDNSConfig)
    changed = context.executor.write_file(DNS_CONF, _render_dns(obj, config))
    if config.enabled and config.log_file:
        log_file = Path(config.log_file)
        context.executor.ensure_dir(str(log_file.parent))
        changed = context.executor.write_file(LOGROTATE_CONF, _render_logrotate(log_file)) or changed
    return changed


def _update_dhcp(context: UpdateContext, obj: ConfigObject) -> bool:
    config = context.configs.resolve_object(obj, DnsmasqDHCPConfig)
    return context.executor.write_file(DHCP_CONF, _render_dhcp(obj, config))


def _update_china_names(context: UpdateContext, obj: ConfigObject) -> bool:
    config = context.configs.resolve_object(obj, DnsmasqChinaNamesConfig)
    cache_dir = china_names_cache_dir(context.settings)
    changed = context.executor.ensure_dir(cache_dir)
    if config.enabled:
        changed = (
            context.executor.write_file(
                CHINA_NAMES_CONF,
                render_cached_china_names_conf(
                    source=f"{obj.kind}/{obj.name}",
                    cache_dir=context.executor.target_path(cache_dir),
                    servers=config.servers,
                    selected_files=config.files or None,
                ),
                show_diff=False,
            )
            or changed
        )
        manual_content, _manual_results, warnings = render_manual_names_conf(
            source=f"{obj.kind}/{obj.name}",
            servers=config.servers,
            manual_names=config.manual_names,
            manual_name_files=_manual_name_file_paths(obj, config),
        )
        for warning in warnings:
            if context.executor.verbose >= 1:
                context.executor.console.print(f"warning: {warning}", markup=False)
        changed = (
            context.executor.write_file(
                CHINA_NAMES_MANUAL_CONF,
                manual_content,
                show_diff=False,
            )
            or changed
        )
    else:
        changed = (
            context.executor.write_file(
                CHINA_NAMES_CONF,
                _disabled(_header(obj)),
                show_diff=False,
            )
            or changed
        )
        changed = (
            context.executor.write_file(
                CHINA_NAMES_MANUAL_CONF,
                _disabled(_header(obj)),
                show_diff=False,
            )
            or changed
        )
    if config.enabled and config.cron_enabled:
        changed = context.executor.write_file(CHINA_NAMES_CRON, _render_china_names_cron(config)) or changed
    else:
        changed = context.executor.delete_file(CHINA_NAMES_CRON) or changed
    return changed


def _manual_name_file_paths(
    obj: ConfigObject,
    config: DnsmasqChinaNamesConfig,
) -> list[Path]:
    paths: list[Path] = []
    for item in config.manual_name_files:
        path = Path(item).expanduser()
        paths.append(path if path.is_absolute() else obj.source.parent / path)
    return paths


def _validate_model(
    context: UpdateContext,
    obj: ConfigObject,
    model_type: type[DnsmasqDNSConfig] | type[DnsmasqDHCPConfig] | type[DnsmasqChinaNamesConfig],
    errors: list[str],
) -> DnsmasqDNSConfig | DnsmasqDHCPConfig | DnsmasqChinaNamesConfig | None:
    try:
        return context.configs.resolve_object(obj, model_type)
    except ValidationError as exc:
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            errors.append(f"{obj.kind}/{obj.name}: spec.{location}: {error['msg']}")
        return None


def _validate_dns(obj: ConfigObject, config: DnsmasqDNSConfig, errors: list[str]) -> None:
    for field_name in (
        "interfaces",
        "except_interfaces",
        "listen_addresses",
        "conf_files",
        "conf_dirs",
        "addn_hosts",
        "servers",
        "locals",
        "addresses",
        "host_records",
        "srv_hosts",
        "cname_records",
        "raw_options",
        "raw",
    ):
        for index, value in enumerate(getattr(config, field_name)):
            _validate_single_line(obj, f"spec.{field_name}[{index}]", value, errors)
    if config.log_file:
        _validate_single_line(obj, "spec.logFile", config.log_file, errors)
        if not Path(config.log_file).is_absolute():
            errors.append(f"DnsmasqDNS/{obj.name}: spec.logFile must be an absolute path")


def _validate_dhcp(obj: ConfigObject, config: DnsmasqDHCPConfig, errors: list[str]) -> None:
    for field_name in ("domain",):
        value = getattr(config, field_name)
        if value:
            _validate_single_line(obj, f"spec.{field_name}", value, errors)
    for field_name in ("dns_servers", "v6_dns_servers", "options", "hosts", "raw_options", "raw"):
        for index, value in enumerate(getattr(config, field_name)):
            _validate_single_line(obj, f"spec.{field_name}[{index}]", value, errors)
    for index, item in enumerate(config.ranges):
        for field_name in ("tag", "start", "end", "netmask", "broadcast", "router", "mode", "lease"):
            value = getattr(item, field_name)
            if value:
                _validate_single_line(obj, f"spec.ranges[{index}].{field_name}", value, errors)
        if not item.router:
            try:
                _infer_router(item.start)
            except ValueError as exc:
                errors.append(f"DnsmasqDHCP/{obj.name}: spec.ranges[{index}].router: {exc}")


def _validate_china_names(obj: ConfigObject, config: DnsmasqChinaNamesConfig, errors: list[str]) -> None:
    for field_name in ("servers", "manual_names", "manual_name_files", "files"):
        for index, value in enumerate(getattr(config, field_name)):
            _validate_single_line(obj, f"spec.{field_name}[{index}]", value, errors)
    if config.enabled and not config.servers:
        errors.append(f"DnsmasqChinaNames/{obj.name}: spec.servers must not be empty")
    for file_name in config.files:
        if file_name not in AVAILABLE_DNSMASQ_CHINA_NAME_FILES:
            available = ", ".join(AVAILABLE_DNSMASQ_CHINA_NAME_FILES)
            errors.append(
                f"DnsmasqChinaNames/{obj.name}: unsupported file {file_name!r}; "
                f"available: {available}"
            )
    try:
        normalize_schedule(config.schedule)
    except ValueError as exc:
        errors.append(f"DnsmasqChinaNames/{obj.name}: spec.schedule: {exc}")


def _render_dns(obj: ConfigObject, config: DnsmasqDNSConfig) -> str:
    lines = _header(obj)
    if not config.enabled:
        return _disabled(lines)
    lines.extend(["", "## daemon"])
    for interface in config.interfaces:
        lines.append(f"interface={_normalize_entry(interface)}")
    for interface in config.except_interfaces:
        lines.append(f"except-interface={_normalize_entry(interface)}")
    for address in config.listen_addresses:
        lines.append(f"listen-address={_normalize_entry(address)}")
    _append_bool(lines, config.bind_interfaces, "bind-interfaces")
    _append_bool(lines, config.no_resolv, "no-resolv")
    _append_bool(lines, config.no_negcache, "no-negcache")
    _append_bool(lines, config.no_hosts, "no-hosts")
    _append_bool(lines, config.no_poll, "no-poll")
    _append_bool(lines, config.all_servers, "all-servers")
    _append_bool(lines, config.bogus_priv, "bogus-priv")
    _append_bool(lines, config.domain_needed, "domain-needed")
    _append_bool(lines, config.expand_hosts, "expand-hosts")
    if config.local_ttl is not None:
        lines.append(f"local-ttl={config.local_ttl}")
    if config.cache_size is not None:
        lines.append(f"cache-size={config.cache_size}")
    _append_bool(lines, config.log_queries, "log-queries")
    if config.log_async is not None:
        lines.append(f"log-async={config.log_async}")
    if config.log_file:
        lines.append(f"log-facility={config.log_file}")
    if config.port is not None:
        lines.append(f"port={config.port}")
    for path in config.conf_files:
        lines.append(f"conf-file={path}")
    for path in config.conf_dirs:
        lines.append(f"conf-dir={path}")
    for path in config.addn_hosts:
        lines.append(f"addn-hosts={path}")

    lines.extend(["", "## dns"])
    for server in config.servers:
        lines.append(f"server={_normalize_entry(server)}")
    for suffix in config.locals:
        suffix_value = _normalize_entry(suffix).strip("/")
        lines.append(f"local=/{suffix_value}/")
    for address in config.addresses:
        lines.append(f"address={_normalize_entry(address)}")
    for record in config.host_records:
        lines.append(f"host-record={_normalize_entry(record)}")
    for srv_host in config.srv_hosts:
        lines.append(f"srv-host={_normalize_entry(srv_host)}")
    for record in config.cname_records:
        lines.append(f"cname={_normalize_entry(record)}")
    lines.extend(str(item) for item in config.raw_options)
    lines.extend(str(item) for item in config.raw)
    lines.append("")
    return "\n".join(lines)


def _render_dhcp(obj: ConfigObject, config: DnsmasqDHCPConfig) -> str:
    lines = _header(obj)
    if not config.enabled:
        return _disabled(lines)
    lines.extend(["", "## dhcp"])
    _append_bool(lines, config.authoritative, "dhcp-authoritative")
    if config.domain:
        lines.append(f"domain={config.domain}")
    if config.dns_servers:
        lines.append("dhcp-option=option:dns-server," + ",".join(config.dns_servers))
    if config.v6_dns_servers:
        lines.append("dhcp-option=option6:dns-server," + ",".join(_bracket_ipv6(item) for item in config.v6_dns_servers))
    for item in config.options:
        lines.append(f"dhcp-option={_normalize_entry(item)}")
    for range_item in config.ranges:
        lines.append(f"dhcp-range={_render_dhcp_range(range_item)}")
        router = range_item.router or _infer_router(range_item.start)
        lines.append(f"dhcp-option={range_item.tag},option:router,{router}")
    for host in config.hosts:
        lines.append(f"dhcp-host={_normalize_entry(host)}")
    lines.extend(str(item) for item in config.raw_options)
    lines.extend(str(item) for item in config.raw)
    lines.append("")
    return "\n".join(lines)


def _render_china_names_cron(config: DnsmasqChinaNamesConfig) -> str:
    command = config.command or "/usr/local/bin/gw dnsmasq china-names update --verbose 1"
    return "\n".join(
        [
            "# Generated by DROS. Manual changes will be overwritten.",
            "SHELL=/bin/sh",
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            f"{normalize_schedule(config.schedule)} root {command}",
            "",
        ]
    )


def _render_logrotate(log_file: Path) -> str:
    return "\n".join(
        [
            "# Generated by DROS. Manual changes will be overwritten.",
            f"{log_file} {{",
            "    daily",
            "    missingok",
            "    rotate 90",
            "    compress",
            "    delaycompress",
            "    notifempty",
            "    create 0640 dnsmasq adm",
            "    sharedscripts",
            "    postrotate",
            "        if [ -f /run/dnsmasq/dnsmasq.pid ]; then",
            "            kill -USR2 \"$(cat /run/dnsmasq/dnsmasq.pid)\" 2>/dev/null || true",
            "        fi",
            "    endscript",
            "}",
            "",
        ]
    )


def _render_dhcp_range(item: DnsmasqDHCPRangeConfig) -> str:
    parts = [item.tag, item.start]
    if item.end:
        parts.append(item.end)
    if item.netmask:
        parts.append(item.netmask)
        if item.broadcast:
            parts.append(item.broadcast)
    elif item.mode:
        parts.append(item.mode)
    parts.append(item.lease)
    return ",".join(parts)


def _infer_router(start: str) -> str:
    address = ipaddress.ip_address(start)
    if address.version != 4:
        raise ValueError(f"router must be specified explicitly for non-IPv4 range {start}")
    network = ipaddress.ip_network(f"{address}/24", strict=False)
    return str(network.network_address + 1)


def _bracket_ipv6(value: str) -> str:
    return f"[{value}]" if ":" in value and not value.startswith("[") else value


def _normalize_entry(value: object) -> str:
    return str(value).strip()


def _append_bool(lines: list[str], value: bool, option: str) -> None:
    if value:
        lines.append(option)


def _header(obj: ConfigObject) -> list[str]:
    return [
        "# Generated by DROS. Manual changes will be overwritten.",
        f"# Source: {obj.kind}/{obj.name}",
    ]


def _disabled(lines: list[str]) -> str:
    lines.extend(["", "# disabled", ""])
    return "\n".join(lines)


def _validate_single_line(
    obj: ConfigObject,
    label: str,
    value: object,
    errors: list[str],
) -> None:
    if any(char in str(value) for char in "\r\n"):
        errors.append(f"{obj.kind}/{obj.name}: {label} must be a single-line value")
