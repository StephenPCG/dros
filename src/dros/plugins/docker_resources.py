from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from dros.config_objects import (
    ConfigMapConfig,
    ConfigObject,
    DockerAppConfig,
    DockerAppFileConfig,
    DockerAppNginxConfFileConfig,
    DockerContainerConfig,
    DockerDNSConfig,
    DockerMountConfig,
    InterfaceConfig,
)
from dros.plugins.base import DrosPlugin, UpdateContext

DOCKER_RESOURCE_KINDS = frozenset({"DockerContainer", "DockerApp"})
DOCKER_KINDS = frozenset({"DockerContainer", "DockerApp", "DockerDNS"})
NGINX_VARIANT_IMAGES = {
    "openresty": ("openresty/openresty", "1.29.2.3-alpine-apk"),
    "nginx": ("nginx", "1.30-alpine"),
}
NGINX_CONF_TARGETS = {
    "openresty": "/usr/local/openresty/nginx/conf/nginx.conf",
    "nginx": "/etc/nginx/nginx.conf",
}
APP_IMAGES = {
    "vlmcsd": ("mikolatero/vlmcsd", "latest"),
    "ddns-go": ("jeessy/ddns-go", "latest"),
    "unifi": ("ghcr.io/goofball222/unifi", "10.3"),
    "certimate": ("certimate/certimate", "latest"),
    "headscale": ("docker.io/headscale/headscale", "v0.28.0-beta.2"),
}
MANAGED_FILES = frozenset(
    {
        "/etc/dnsmasq.d/dros-40-containers.conf",
        "/opt/gateway/containers/*/docker-compose.yml",
        "/opt/gateway/containers/*/generated/*",
    }
)


@dataclass(frozen=True)
class RenderedFile:
    path: Path
    content: str
    mode: int = 0o644


@dataclass(frozen=True)
class ComposeProject:
    obj: ConfigObject
    name: str
    image: str
    network: str = "default"
    restart: str = "unless-stopped"
    environment: dict[str, str] = field(default_factory=dict)
    mounts: list[DockerMountConfig] = field(default_factory=list)
    cap_add: list[str] = field(default_factory=list)
    cap_drop: list[str] = field(default_factory=list)
    devices: list[str] = field(default_factory=list)
    privileged: bool = False
    command: str | list[str] | None = None
    read_only: bool = False
    tmpfs: list[str] = field(default_factory=list)
    healthcheck: dict[str, Any] | None = None
    dns_names: list[str] = field(default_factory=list)
    additional_domains: list[str] = field(default_factory=list)


def create_plugin() -> DrosPlugin:
    return DrosPlugin(
        name="docker.resources",
        depends_on=("docker.core", "network.interfaces"),
        config_kinds=DOCKER_KINDS,
        managed_files=MANAGED_FILES,
        validation_hook=validate,
        update_hook=update,
    )


def validate(context: UpdateContext, objects: list[ConfigObject]) -> list[str]:
    errors: list[str] = []
    for obj in objects:
        if obj.kind == "DockerContainer":
            config = _validate_model(context, obj, DockerContainerConfig, errors)
            if config is not None:
                _validate_container(context, obj, config, errors)
        elif obj.kind == "DockerApp":
            config = _validate_model(context, obj, DockerAppConfig, errors)
            if config is not None:
                _validate_app(context, obj, config, errors)
        elif obj.kind == "DockerDNS":
            config = _validate_model(context, obj, DockerDNSConfig, errors)
            if config is not None:
                _validate_docker_dns(obj, config, errors)
    return errors


def update(context: UpdateContext, objects: list[ConfigObject]) -> None:
    resource_objects = [obj for obj in objects if obj.kind in DOCKER_RESOURCE_KINDS]
    dns_objects = [obj for obj in objects if obj.kind == "DockerDNS"]

    for obj in sorted(resource_objects, key=lambda item: (item.kind, item.name)):
        project = _project_from_object(context, obj)
        if project is None:
            continue
        _apply_project(context, project)

    if dns_objects:
        for obj in sorted(dns_objects, key=lambda item: item.name):
            _apply_docker_dns(context, obj)
    elif resource_objects:
        dns_obj = context.configs.get("DockerDNS", "system")
        if dns_obj is not None:
            _apply_docker_dns(context, dns_obj)


def handle_event(context: UpdateContext, event: str, _iface: str | None = None) -> None:
    if event != "docker-start":
        return
    dns_obj = context.configs.get("DockerDNS", "system")
    if dns_obj is not None:
        _apply_docker_dns(context, dns_obj)


def _validate_model(
    context: UpdateContext,
    obj: ConfigObject,
    model_type: type[DockerContainerConfig] | type[DockerAppConfig] | type[DockerDNSConfig],
    errors: list[str],
) -> DockerContainerConfig | DockerAppConfig | DockerDNSConfig | None:
    try:
        return context.configs.resolve_object(obj, model_type)
    except ValidationError as exc:
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            errors.append(f"{obj.kind}/{obj.name}: spec.{location}: {error['msg']}")
        return None


def _validate_container(
    context: UpdateContext,
    obj: ConfigObject,
    config: DockerContainerConfig,
    errors: list[str],
) -> None:
    _validate_resource_name(obj, errors)
    _validate_single_line(obj, "spec.image", config.image, errors)
    _validate_network(context, obj, config.network, errors)
    _validate_common_container_fields(obj, config, errors)
    _validate_mounts(context, obj, config.mounts, errors, path_prefix="spec.mounts")


def _validate_app(
    context: UpdateContext,
    obj: ConfigObject,
    config: DockerAppConfig,
    errors: list[str],
) -> None:
    _validate_resource_name(obj, errors)
    _validate_network(context, obj, config.network, errors)
    for label, value in (
        ("spec.image", config.image),
        ("spec.imageName", config.image_name),
        ("spec.imageTag", config.image_tag),
        ("spec.variant", config.variant),
        ("spec.serverUrl", config.server_url),
        ("spec.listenAddr", config.listen_addr),
        ("spec.metricsListenAddr", config.metrics_listen_addr),
    ):
        if value is not None:
            _validate_single_line(obj, label, value, errors)
    if config.image is not None and config.image_name is not None:
        errors.append(f"{obj.kind}/{obj.name}: spec.image cannot be combined with spec.imageName")
    for key, value in config.environment.items():
        _validate_single_line(obj, f"spec.environment.{key}", value, errors)
    if config.app != "nginx":
        if config.variant is not None:
            errors.append(f"{obj.kind}/{obj.name}: spec.variant is only supported for app nginx")
        if config.nginx_conf_file is not None:
            errors.append(f"{obj.kind}/{obj.name}: spec.nginxConfFile is only supported for app nginx")
        if config.conf_files:
            errors.append(f"{obj.kind}/{obj.name}: spec.confFiles is only supported for app nginx")
    if config.nginx_conf_file is not None:
        _validate_app_file(obj, config.nginx_conf_file, errors, "spec.nginxConfFile")
    _validate_app_files(obj, config.conf_files, errors, path_prefix="spec.confFiles")
    _validate_mounts(context, obj, config.mounts, errors, path_prefix="spec.mounts")
    if config.app != "headscale":
        if config.server_url is not None:
            errors.append(f"{obj.kind}/{obj.name}: spec.serverUrl is only supported for app headscale")
        if _spec_has_any(obj, "listenAddr", "listen_addr"):
            errors.append(f"{obj.kind}/{obj.name}: spec.listenAddr is only supported for app headscale")
        if _spec_has_any(obj, "metricsListenAddr", "metrics_listen_addr"):
            errors.append(
                f"{obj.kind}/{obj.name}: spec.metricsListenAddr is only supported for app headscale"
            )
        if config.raw_config is not None:
            errors.append(f"{obj.kind}/{obj.name}: spec.rawConfig is only supported for app headscale")
    elif config.raw_config is None and config.server_url is None:
        errors.append(f"{obj.kind}/{obj.name}: spec.serverUrl is required for app headscale")


def _spec_has_any(obj: ConfigObject, *keys: str) -> bool:
    return any(key in obj.spec for key in keys)


def _validate_common_container_fields(
    obj: ConfigObject,
    config: DockerContainerConfig,
    errors: list[str],
) -> None:
    for field_name, values in (
        ("capAdd", config.cap_add),
        ("capDrop", config.cap_drop),
        ("devices", config.devices),
        ("dnsNames", config.dns_names),
        ("additionalDomains", config.additional_domains),
    ):
        for index, value in enumerate(values):
            _validate_single_line(obj, f"spec.{field_name}[{index}]", value, errors)
    for key, value in config.environment.items():
        _validate_single_line(obj, f"spec.environment.{key}", value, errors)
    if isinstance(config.command, str):
        _validate_single_line(obj, "spec.command", config.command, errors)
    elif isinstance(config.command, list):
        for index, value in enumerate(config.command):
            _validate_single_line(obj, f"spec.command[{index}]", value, errors)


def _validate_docker_dns(
    obj: ConfigObject,
    config: DockerDNSConfig,
    errors: list[str],
) -> None:
    if obj.name != "system":
        errors.append(f"{obj.kind}/{obj.name}: metadata.name must be system")
    for label, value in (
        ("spec.suffix", config.suffix),
        ("spec.file", config.file),
        ("spec.hostNetworkAddress", config.host_network_address),
    ):
        if value is not None:
            _validate_single_line(obj, label, value, errors)
    if not Path(config.file).is_absolute():
        errors.append(f"{obj.kind}/{obj.name}: spec.file must be an absolute path")
    for key, value in config.host_network_addresses.items():
        _validate_single_line(obj, f"spec.hostNetworkAddresses.{key}", value, errors)


def _validate_resource_name(obj: ConfigObject, errors: list[str]) -> None:
    if re.fullmatch(r"[A-Za-z0-9_.-]+", obj.name) is None:
        errors.append(f"{obj.kind}/{obj.name}: metadata.name may only contain letters, numbers, dot, underscore, and dash")


def _validate_network(
    context: UpdateContext,
    obj: ConfigObject,
    network: str,
    errors: list[str],
) -> None:
    _validate_single_line(obj, "spec.network", network, errors)
    if network in {"default", "host"}:
        return
    iface = context.configs.get("Interface", network)
    if iface is None:
        errors.append(f"{obj.kind}/{obj.name}: spec.network references undefined Interface/{network}")
        return
    try:
        config = context.configs.resolve_object(iface, InterfaceConfig)
    except ValidationError as exc:
        errors.append(f"{obj.kind}/{obj.name}: Interface/{network} is invalid: {exc}")
        return
    if config.type != "docker":
        errors.append(f"{obj.kind}/{obj.name}: spec.network Interface/{network} must be type docker")


def _validate_mounts(
    context: UpdateContext,
    obj: ConfigObject,
    mounts: list[DockerMountConfig],
    errors: list[str],
    *,
    path_prefix: str,
) -> None:
    for index, mount in enumerate(mounts):
        _validate_mount(context, obj, mount, errors, f"{path_prefix}[{index}]")


def _validate_mount(
    context: UpdateContext,
    obj: ConfigObject,
    mount: DockerMountConfig,
    errors: list[str],
    path_prefix: str,
) -> None:
    if not mount.target.startswith("/"):
        errors.append(f"{obj.kind}/{obj.name}: {path_prefix}.target must be an absolute path")
    _validate_single_line(obj, f"{path_prefix}.target", mount.target, errors)
    if mount.name is not None:
        _validate_single_line(obj, f"{path_prefix}.name", mount.name, errors)
    if mount.source is not None and mount.source_type != "inline":
        _validate_single_line(obj, f"{path_prefix}.source", mount.source, errors)
    if mount.key is not None:
        _validate_single_line(obj, f"{path_prefix}.key", mount.key, errors)
    if mount.source_type in {"inline", "file", "dir", "configMap"} and mount.source is None:
        errors.append(f"{obj.kind}/{obj.name}: {path_prefix}.source is required for {mount.source_type}")
    if mount.source_type == "configMap" and not mount.key:
        errors.append(f"{obj.kind}/{obj.name}: {path_prefix}.key is required for configMap")
    if mount.source_type == "configMap" and mount.source and mount.key:
        config_map = context.configs.get("ConfigMap", mount.source)
        if config_map is None:
            errors.append(f"{obj.kind}/{obj.name}: {path_prefix}.source references undefined ConfigMap/{mount.source}")
        else:
            try:
                config = context.configs.resolve_object(config_map, ConfigMapConfig)
            except ValidationError as exc:
                errors.append(f"{obj.kind}/{obj.name}: ConfigMap/{mount.source} is invalid: {exc}")
            else:
                if mount.key not in config.files:
                    errors.append(
                        f"{obj.kind}/{obj.name}: {path_prefix}.key references missing "
                        f"ConfigMap/{mount.source} file {mount.key!r}"
                    )


def _validate_app_files(
    obj: ConfigObject,
    files: list[DockerAppFileConfig],
    errors: list[str],
    *,
    path_prefix: str,
) -> None:
    for index, item in enumerate(files):
        _validate_app_file(obj, item, errors, f"{path_prefix}[{index}]")


def _validate_app_file(
    obj: ConfigObject,
    item: DockerAppFileConfig | DockerAppNginxConfFileConfig,
    errors: list[str],
    path_prefix: str,
) -> None:
    if item.source_type != "inline":
        _validate_single_line(obj, f"{path_prefix}.source", item.source, errors)
    if item.key is not None:
        _validate_single_line(obj, f"{path_prefix}.key", item.key, errors)
    if item.source_type == "configMap" and not item.key:
        errors.append(f"{obj.kind}/{obj.name}: {path_prefix}.key is required for configMap")
    if item.name is not None:
        _validate_single_line(obj, f"{path_prefix}.name", item.name, errors)
    target = getattr(item, "target", None)
    if target is not None:
        if not target.startswith("/"):
            errors.append(f"{obj.kind}/{obj.name}: {path_prefix}.target must be an absolute path")
        _validate_single_line(obj, f"{path_prefix}.target", target, errors)


def _validate_single_line(
    obj: ConfigObject,
    label: str,
    value: object,
    errors: list[str],
) -> None:
    if any(char in str(value) for char in "\r\n"):
        errors.append(f"{obj.kind}/{obj.name}: {label} must be a single-line value")


def _project_from_object(context: UpdateContext, obj: ConfigObject) -> ComposeProject | None:
    if obj.kind == "DockerContainer":
        config = context.configs.resolve_object(obj, DockerContainerConfig)
        if not config.enabled:
            return None
        return ComposeProject(
            obj=obj,
            name=obj.name,
            image=config.image,
            network=config.network,
            restart=config.restart,
            environment=dict(config.environment),
            mounts=list(config.mounts),
            cap_add=list(config.cap_add),
            cap_drop=list(config.cap_drop),
            devices=list(config.devices),
            privileged=config.privileged,
            command=config.command,
            dns_names=list(config.dns_names),
            additional_domains=list(config.additional_domains),
        )
    if obj.kind == "DockerApp":
        config = context.configs.resolve_object(obj, DockerAppConfig)
        if not config.enabled:
            return None
        return _app_project(obj, config)
    return None


def _app_project(obj: ConfigObject, config: DockerAppConfig) -> ComposeProject:
    mounts: list[DockerMountConfig] = []
    if config.app == "nginx":
        variant = config.variant or "openresty"
        if config.nginx_conf_file is not None:
            mounts.append(_nginx_conf_mount(config.nginx_conf_file, variant))
        mounts.extend(_nginx_conf_file_mounts(config.conf_files))
    elif config.app == "ddns-go":
        mounts.append(
            DockerMountConfig.model_validate(
                {"sourceType": "data-dir", "source": "data", "target": "/root", "mode": "rw"}
            )
        )
    elif config.app == "unifi":
        mounts.extend(
            _mounts_from_dicts(
                [
                    {"sourceType": "file", "source": "/etc/localtime", "target": "/etc/localtime", "mode": "ro"},
                    {"sourceType": "dir", "source": "cert", "target": "/usr/lib/unifi/cert", "mode": "rw"},
                    {"sourceType": "dir", "source": "data", "target": "/usr/lib/unifi/data", "mode": "rw"},
                    {"sourceType": "dir", "source": "logs", "target": "/usr/lib/unifi/logs", "mode": "rw"},
                ]
            )
        )
    elif config.app == "certimate":
        mounts.extend(
            _mounts_from_dicts(
                [
                    {"sourceType": "file", "source": "/etc/localtime", "target": "/etc/localtime", "mode": "ro"},
                    {"sourceType": "file", "source": "/etc/timezone", "target": "/etc/timezone", "mode": "ro"},
                    {"sourceType": "dir", "source": "data", "target": "/app/pb_data", "mode": "rw"},
                ]
            )
        )
    elif config.app == "headscale":
        mounts.extend(
            _mounts_from_dicts(
                [
                    {
                        "sourceType": "inline",
                        "source": _headscale_config(config),
                        "name": "config.yaml",
                        "target": "/etc/headscale/config.yaml",
                        "mode": "ro",
                    },
                    {
                        "sourceType": "data-dir",
                        "source": "data",
                        "target": "/var/lib/headscale",
                        "mode": "rw",
                    },
                ]
            )
        )
    mounts.extend(config.mounts)
    return ComposeProject(
        obj=obj,
        name=obj.name,
        image=_app_image(config),
        network=config.network,
        environment={"TZ": "Asia/Shanghai", **dict(config.environment)},
        mounts=mounts,
        command="serve" if config.app == "headscale" else None,
        read_only=config.app == "headscale",
        tmpfs=["/var/run/headscale"] if config.app == "headscale" else [],
        healthcheck=(
            {"test": ["CMD", "headscale", "health"]} if config.app == "headscale" else None
        ),
        dns_names=list(config.dns_names),
        additional_domains=list(config.additional_domains),
    )


def _headscale_config(config: DockerAppConfig) -> str:
    if config.raw_config is not None:
        return _with_trailing_newline(config.raw_config)
    payload = {
        "server_url": config.server_url,
        "listen_addr": config.listen_addr,
        "metrics_listen_addr": config.metrics_listen_addr,
        "database": {
            "type": "sqlite",
            "sqlite": {"path": "/var/lib/headscale/db.sqlite"},
        },
        "noise": {"private_key_path": "/var/lib/headscale/noise_private.key"},
        "prefixes": {
            "v4": "100.64.0.0/10",
            "v6": "fd7a:115c:a1e0::/48",
        },
        "derp": {
            "server": {"enabled": False},
            "urls": ["https://controlplane.tailscale.com/derpmap/default"],
            "paths": [],
        },
        "log": {"level": "info"},
    }
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)


def _with_trailing_newline(value: str) -> str:
    return value if value.endswith("\n") else f"{value}\n"


def _nginx_conf_mount(
    config: DockerAppNginxConfFileConfig,
    variant: str,
) -> DockerMountConfig:
    return DockerMountConfig.model_validate(
        {
            "sourceType": config.source_type,
            "source": config.source,
            "key": config.key,
            "target": NGINX_CONF_TARGETS[variant],
            "mode": config.mode,
            "name": config.name,
        }
    )


def _nginx_conf_file_mounts(configs: list[DockerAppFileConfig]) -> list[DockerMountConfig]:
    mounts: list[DockerMountConfig] = []
    for index, config in enumerate(configs):
        target = config.target
        if target is None:
            filename = config.name or f"dros-{index}.conf"
            target = f"/etc/nginx/conf.d/{Path(filename).name}"
        mounts.append(
            DockerMountConfig.model_validate(
                {
                    "sourceType": config.source_type,
                    "source": config.source,
                    "key": config.key,
                    "target": target,
                    "mode": config.mode,
                    "name": config.name,
                }
            )
        )
    return mounts


def _mounts_from_dicts(items: list[dict[str, object]]) -> list[DockerMountConfig]:
    return [DockerMountConfig.model_validate(item) for item in items]


def _app_image(config: DockerAppConfig) -> str:
    if config.image:
        if config.image_tag:
            return _replace_or_append_image_tag(config.image, config.image_tag)
        return config.image
    if config.app == "nginx":
        name, tag = NGINX_VARIANT_IMAGES[config.variant or "openresty"]
    else:
        name, tag = APP_IMAGES[config.app]
    return _replace_or_append_image_tag(config.image_name or name, config.image_tag or tag)


def _replace_or_append_image_tag(image_name: str, tag: str) -> str:
    repository, sep, current_tag = image_name.rpartition(":")
    if sep and "/" not in current_tag:
        return f"{repository}:{tag}"
    return f"{image_name}:{tag}"


def _apply_project(context: UpdateContext, project: ComposeProject) -> None:
    rendered = _render_project(context, project)
    for directory in rendered["dirs"]:
        context.executor.ensure_dir(directory)

    non_compose_changed = False
    for item in rendered["files"]:
        changed = context.executor.write_file(item.path, item.content, mode=item.mode)
        if item.path != _project_root(context, project) / "docker-compose.yml":
            non_compose_changed = changed or non_compose_changed

    compose_file = _project_root(context, project) / "docker-compose.yml"
    command = [
        "docker",
        "compose",
        "--project-name",
        _compose_project_name(project.name),
        "-f",
        str(compose_file),
        "up",
        "-d",
    ]
    context.executor.run(command, real_only=True)
    if non_compose_changed:
        context.executor.run(
            [
                "docker",
                "compose",
                "--project-name",
                _compose_project_name(project.name),
                "-f",
                str(compose_file),
                "restart",
            ],
            real_only=True,
        )


def _apply_docker_dns(context: UpdateContext, obj: ConfigObject) -> None:
    config = context.configs.resolve_object(obj, DockerDNSConfig)
    if not config.enabled:
        if context.executor.delete_file(config.file):
            context.executor.run(["systemctl", "restart", "dnsmasq"], real_only=True)
        return

    records = _docker_dns_records(context, config)
    changed = context.executor.write_file(config.file, _render_dnsmasq_records(records))
    if changed:
        context.executor.run(["systemctl", "restart", "dnsmasq"], real_only=True)


def _docker_dns_records(
    context: UpdateContext,
    config: DockerDNSConfig,
) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    for project in _all_projects(context):
        if project.network == "host":
            address = config.host_network_addresses.get(project.name) or config.host_network_address
            if not address:
                _warn(context, f"DockerDNS: skip host network container {project.name}: no host network address")
                continue
        else:
            address = _inspect_container_address(context, project)
            if not address:
                _warn(context, f"DockerDNS: skip container {project.name}: no Docker IP address")
                continue
        for name in _docker_dns_names(project, config.suffix):
            records.append((name, address))
    return sorted(set(records))


def _all_projects(context: UpdateContext) -> list[ComposeProject]:
    projects: list[ComposeProject] = []
    for obj in sorted(context.configs.objects(), key=lambda item: (item.kind, item.name)):
        if obj.kind not in DOCKER_RESOURCE_KINDS:
            continue
        try:
            project = _project_from_object(context, obj)
        except Exception as exc:
            _warn(context, f"DockerDNS: skip {obj.kind}/{obj.name}: {exc}")
            continue
        if project is not None:
            projects.append(project)
    return projects


def _inspect_container_address(context: UpdateContext, project: ComposeProject) -> str | None:
    output = context.executor.output(["docker", "inspect", project.name], default="", real_only=True)
    if not output:
        return None
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        _warn(context, f"DockerDNS: docker inspect {project.name} returned invalid JSON: {exc}")
        return None
    if not payload:
        return None
    item = payload[0] if isinstance(payload, list) else payload
    if not isinstance(item, dict):
        return None
    return _inspect_ip_address(item)


def _inspect_ip_address(item: dict[str, Any]) -> str | None:
    settings = item.get("NetworkSettings") or {}
    if isinstance(settings, dict):
        if settings.get("IPAddress"):
            return str(settings["IPAddress"])
        networks = settings.get("Networks") or {}
        if isinstance(networks, dict):
            bridge = networks.get("bridge") or {}
            if isinstance(bridge, dict) and bridge.get("IPAddress"):
                return str(bridge["IPAddress"])
            for network in networks.values():
                if isinstance(network, dict) and network.get("IPAddress"):
                    return str(network["IPAddress"])
    return None


def _docker_dns_names(project: ComposeProject, suffix: str) -> list[str]:
    return [*(project.dns_names or [f"{project.name}.{suffix}"]), *project.additional_domains]


def _render_dnsmasq_records(records: list[tuple[str, str]]) -> str:
    lines = [
        "# Generated by DROS. Manual changes will be overwritten.",
        "# Docker container DNS records",
    ]
    for name, address in records:
        lines.append(f"host-record={name},{address}")
    lines.append("")
    return "\n".join(lines)


def _warn(context: UpdateContext, message: str) -> None:
    if context.executor.verbose >= 1:
        context.executor.console.print(f"warning: {message}", markup=False)


def _render_project(context: UpdateContext, project: ComposeProject) -> dict[str, Any]:
    root = _project_root(context, project)
    dirs: list[Path] = [root, root / "data", root / "config", root / "generated"]
    files: list[RenderedFile] = []
    volumes: list[str] = []

    for index, mount in enumerate(project.mounts):
        source_path, generated_file, generated_dirs = _mount_source(context, project, mount, index)
        dirs.extend(generated_dirs)
        if generated_file is not None:
            files.append(generated_file)
        volumes.append(f"{source_path}:{mount.target}:{mount.mode}")

    compose = {"services": {"app": _service(project, volumes)}}
    if project.network not in {"default", "host"}:
        compose["networks"] = {project.network: {"external": True}}
    files.append(
        RenderedFile(
            path=root / "docker-compose.yml",
            content=yaml.safe_dump(compose, sort_keys=False, allow_unicode=False),
        )
    )
    return {"dirs": dirs, "files": files}


def _service(project: ComposeProject, volumes: list[str]) -> dict[str, Any]:
    service: dict[str, Any] = {
        "image": project.image,
        "container_name": project.name,
        "restart": project.restart,
    }
    if project.network == "host":
        service["network_mode"] = "host"
    elif project.network == "default":
        service["network_mode"] = "bridge"
    else:
        service["networks"] = [project.network]
    if project.environment:
        service["environment"] = project.environment
    if volumes:
        service["volumes"] = volumes
    if project.cap_add:
        service["cap_add"] = project.cap_add
    if project.cap_drop:
        service["cap_drop"] = project.cap_drop
    if project.devices:
        service["devices"] = project.devices
    if project.privileged:
        service["privileged"] = True
    if project.command:
        service["command"] = project.command
    if project.read_only:
        service["read_only"] = True
    if project.tmpfs:
        service["tmpfs"] = project.tmpfs
    if project.healthcheck:
        service["healthcheck"] = project.healthcheck
    return service


def _mount_source(
    context: UpdateContext,
    project: ComposeProject,
    mount: DockerMountConfig,
    index: int,
) -> tuple[Path, RenderedFile | None, list[Path]]:
    root = _project_root(context, project)
    if mount.source_type == "data-dir":
        source = mount.source or "data"
        path = _container_relative(root, source)
        return path, None, [path]
    if mount.source_type == "dir":
        path = _container_relative(root, str(mount.source))
        return path, None, [path]
    if mount.source_type == "file":
        return _container_relative(root, str(mount.source)), None, []
    if mount.source_type == "inline":
        name = mount.name or Path(mount.target).name or f"inline-{index}"
        path = root / "generated" / "inline" / name
        return path, RenderedFile(path=path, content=str(mount.source)), [path.parent]
    if mount.source_type == "configMap":
        config_map_name = str(mount.source)
        key = str(mount.key or "")
        content = _configmap_file_content(context, config_map_name, key)
        path = root / "generated" / "configmap" / _safe_path_segment(config_map_name) / key
        return path, RenderedFile(path=path, content=content), [path.parent]
    raise ValueError(f"unsupported mount sourceType {mount.source_type!r}")


def _configmap_file_content(context: UpdateContext, name: str, key: str) -> str:
    obj = context.configs.get("ConfigMap", name)
    if obj is None:
        raise ValueError(f"ConfigMap/{name} is not defined")
    config = context.configs.resolve_object(obj, ConfigMapConfig)
    if key not in config.files:
        raise ValueError(f"ConfigMap/{name} does not contain file {key!r}")
    return config.files[key]


def _safe_path_segment(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value)


def _container_relative(root: Path, source: str) -> Path:
    path = Path(source)
    if path.is_absolute():
        return path
    return root / path


def _project_root(context: UpdateContext, project: ComposeProject) -> Path:
    return context.settings.paths.containers / project.name


def _compose_project_name(name: str) -> str:
    safe = "".join(char if char.isalnum() or char in "-_" else "_" for char in name)
    return f"dros-{safe}"
