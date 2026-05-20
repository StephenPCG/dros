from __future__ import annotations

from pydantic import ValidationError

from dros.config_objects import ConfigObject, ConfigStore, WgsdCoreDNSConfig
from dros.plugins.base import BootstrapContext, DrosPlugin, UpdateContext

KIND = "WgsdCoreDNS"
NAME = "system"
SERVICE = "dros-wgsd-coredns.service"
COREDNS_BINARY = "/usr/local/bin/coredns"
CONFIG_DIR = "/etc/dros/wgsd"
COREFILE = f"{CONFIG_DIR}/Corefile"
SERVICE_FILE = f"/etc/systemd/system/{SERVICE}"
MANAGED_FILES = frozenset({COREFILE, SERVICE_FILE})


def create_plugin() -> DrosPlugin:
    return DrosPlugin(
        name="network.wgsd-coredns",
        config_kinds=frozenset({KIND}),
        managed_files=MANAGED_FILES,
        bootstrap_hook=bootstrap,
        validation_hook=validate,
        update_hook=update,
    )


def bootstrap(context: BootstrapContext) -> None:
    obj = _select_config(context.configs)
    if obj is None:
        return
    config = context.configs.resolve_object(obj, WgsdCoreDNSConfig)
    _apply_wgsd_coredns(context, obj, config)


def validate(context: UpdateContext, objects: list[ConfigObject]) -> list[str]:
    errors: list[str] = []
    for obj in objects:
        if obj.kind != KIND:
            continue
        if obj.name != NAME:
            errors.append(f"{KIND}/{obj.name}: metadata.name must be {NAME}")
            continue
        try:
            config = context.configs.resolve_object(obj, WgsdCoreDNSConfig)
        except ValidationError as exc:
            for error in exc.errors():
                location = ".".join(str(part) for part in error["loc"])
                errors.append(f"{KIND}/{obj.name}: spec.{location}: {error['msg']}")
            continue
        _validate_config(obj, config, errors)
    return errors


def update(context: UpdateContext, objects: list[ConfigObject]) -> None:
    for obj in sorted(objects, key=lambda item: item.name):
        config = context.configs.resolve_object(obj, WgsdCoreDNSConfig)
        _apply_wgsd_coredns(context, obj, config)


def _select_config(configs: ConfigStore) -> ConfigObject | None:
    objects = configs.by_kind(KIND)
    if not objects:
        return None
    system = [obj for obj in objects if obj.name == NAME]
    if system:
        return system[-1]
    if len(objects) == 1:
        obj = objects[0]
        raise ValueError(f"{KIND}/{obj.name}: metadata.name must be {NAME}")
    names = ", ".join(sorted(obj.name for obj in objects))
    raise ValueError(f"multiple {KIND} configs found; expected only {KIND}/{NAME}: {names}")


def _apply_wgsd_coredns(
    context: BootstrapContext | UpdateContext,
    obj: ConfigObject,
    config: WgsdCoreDNSConfig,
) -> None:
    context.executor.ensure_dir(CONFIG_DIR)
    corefile_changed = context.executor.write_file(COREFILE, _render_corefile(obj, config))

    if not context.executor.exists(COREDNS_BINARY):
        _warn(
            context,
            f"{KIND}/{obj.name}: {COREDNS_BINARY} is not installed; "
            "run gw install wgsd-coredns",
        )
        service_deleted = context.executor.delete_file(SERVICE_FILE)
        if service_deleted:
            context.executor.run(["systemctl", "daemon-reload"], real_only=True)
        return

    service_changed = context.executor.write_file(SERVICE_FILE, _render_service())

    if service_changed:
        context.executor.run(["systemctl", "daemon-reload"], real_only=True)
    if corefile_changed or service_changed:
        context.executor.run(["systemctl", "restart", SERVICE], real_only=True)
        context.executor.run(["systemctl", "enable", SERVICE], real_only=True)


def _warn(context: BootstrapContext | UpdateContext, message: str) -> None:
    if context.executor.verbose > 0:
        context.executor.console.print(f"warning: {message}", markup=False)


def _validate_config(
    obj: ConfigObject,
    config: WgsdCoreDNSConfig,
    errors: list[str],
) -> None:
    _validate_single_line(obj, "spec.bind", config.bind, errors)
    if not config.interfaces:
        errors.append(f"{KIND}/{obj.name}: spec.interfaces must contain at least one item")

    names: set[str] = set()
    domains: set[str] = set()
    for index, interface in enumerate(config.interfaces):
        _validate_single_line(obj, f"spec.interfaces[{index}].name", interface.name, errors)
        _validate_single_line(obj, f"spec.interfaces[{index}].domain", interface.domain, errors)
        if any(char.isspace() for char in interface.name):
            errors.append(f"{KIND}/{obj.name}: spec.interfaces[{index}].name must not contain whitespace")
        domain = _normalize_domain(interface.domain)
        if interface.name in names:
            errors.append(f"{KIND}/{obj.name}: spec.interfaces[{index}].name is duplicated")
        if domain in domains:
            errors.append(f"{KIND}/{obj.name}: spec.interfaces[{index}].domain is duplicated")
        names.add(interface.name)
        domains.add(domain)


def _validate_single_line(
    obj: ConfigObject,
    label: str,
    value: object,
    errors: list[str],
) -> None:
    if any(char in str(value) for char in "\r\n"):
        errors.append(f"{KIND}/{obj.name}: {label} must be a single-line value")


def _render_corefile(obj: ConfigObject, config: WgsdCoreDNSConfig) -> str:
    lines = [
        "# Generated by DROS. Manual changes will be overwritten.",
        f"# Resource: {obj.kind}/{obj.name}",
        "",
        "(common) {",
        f"    bind {config.bind}",
        "    cache",
        "}",
        "",
        f".:{config.listen} {{",
        "    import common",
    ]
    for interface in config.interfaces:
        lines.append(f"    wgsd {_normalize_domain(interface.domain)} {interface.name}")
    lines.extend(["}", ""])
    return "\n".join(lines)


def _render_service() -> str:
    return f"""# Generated by DROS. Manual changes will be overwritten.
[Unit]
Description=DROS WGSD CoreDNS
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={COREDNS_BINARY} -conf {COREFILE}
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
"""


def _normalize_domain(domain: str) -> str:
    return f"{domain.rstrip('.')}."
