from __future__ import annotations

import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from dros.settings import DrosSettings


@dataclass(frozen=True)
class OvpnInstance:
    name: str
    logical_root: Path
    root: Path
    config_path: Path


@dataclass(frozen=True)
class OvpnProfile:
    kind: str
    name: str
    logical_root: Path
    root: Path
    path: Path
    data: dict[str, Any]


@dataclass(frozen=True)
class OvpnInstanceSummary:
    name: str
    root: str
    server_profiles: int
    client_profiles: int
    server_certs: int
    client_certs: int
    crl_exists: bool


@dataclass(frozen=True)
class OvpnProfileSummary:
    kind: str
    name: str
    path: str
    latest_cert: str | None
    output_files: tuple[str, ...]


@dataclass(frozen=True)
class OvpnCertSummary:
    kind: str
    name: str
    cert_id: str
    path: str
    latest: bool
    revoked: bool


def init_instance(
    settings: DrosSettings,
    name: str,
    *,
    ca_cn: str | None = None,
    force: bool = False,
) -> OvpnInstance:
    _validate_safe_name("instance", name)
    instance = _instance_for_name(settings, name)
    if instance.config_path.exists() and not force:
        raise ValueError(f"ovpn instance already exists: {name}; use --force to overwrite config")
    _ensure_instance_dirs(instance)
    config = _default_config(name=name, ca_cn=ca_cn)
    _write_yaml(instance.config_path, config, mode=0o600)
    _ensure_ca(instance, config, force=False)
    return instance


def bootstrap_ca(
    settings: DrosSettings,
    *,
    instance: str | None = None,
    force: bool = False,
) -> OvpnInstance:
    selected = resolve_instance(settings, instance)
    config = load_instance_config(selected)
    _ensure_instance_dirs(selected)
    _ensure_ca(selected, config, force=force)
    return selected


def resolve_instance(settings: DrosSettings, instance: str | None) -> OvpnInstance:
    if instance:
        selected = _instance_for_name(settings, instance)
        if not selected.config_path.exists():
            raise ValueError(f"ovpn instance not found: {instance}; run `gw ovpn init {instance}`")
        return selected

    instances = list_instances(settings)
    if not instances:
        raise ValueError("no ovpn instance found; run `gw ovpn init <name>`")
    if len(instances) > 1:
        names = ", ".join(item.name for item in instances)
        raise ValueError(f"multiple ovpn instances found ({names}); pass --instance")
    return instances[0]


def list_instances(settings: DrosSettings) -> list[OvpnInstance]:
    root = _system_path(settings, _ovpn_root(settings))
    if not root.exists():
        return []
    result: list[OvpnInstance] = []
    for item in sorted(root.iterdir(), key=lambda path: path.name):
        if item.is_dir() and (item / "config.yaml").exists():
            result.append(
                OvpnInstance(
                    name=item.name,
                    logical_root=_ovpn_root(settings) / item.name,
                    root=item,
                    config_path=item / "config.yaml",
                )
            )
    return result


def list_instances_summary(settings: DrosSettings) -> list[OvpnInstanceSummary]:
    result: list[OvpnInstanceSummary] = []
    for instance in list_instances(settings):
        server_profiles = list_profiles(instance, "server")
        client_profiles = list_profiles(instance, "client")
        result.append(
            OvpnInstanceSummary(
                name=instance.name,
                root=str(instance.logical_root),
                server_profiles=len(server_profiles),
                client_profiles=len(client_profiles),
                server_certs=sum(len(_profile_cert_summaries(profile)) for profile in server_profiles),
                client_certs=sum(len(_profile_cert_summaries(profile)) for profile in client_profiles),
                crl_exists=_crl_path(instance).exists(),
            )
        )
    return result


def create_server_profile(
    settings: DrosSettings,
    name: str,
    *,
    instance: str | None = None,
    endpoint: str | None = None,
    cn: str | None = None,
    network: str | None = None,
    netmask: str | None = None,
    force: bool = False,
) -> OvpnProfile:
    selected = resolve_instance(settings, instance)
    _validate_safe_name("server name", name)
    path = _profile_path(selected, "server", name)
    if path.exists() and not force:
        raise ValueError(f"server profile already exists: {name}; use --force to overwrite")
    endpoint = _value_or_prompt("endpoint", endpoint, default=f"{name}.vpn.example.net")
    data = {
        "name": name,
        "endpoint": endpoint,
        "cn": cn or endpoint,
        "network": network or "10.8.0.0",
        "netmask": netmask or "255.255.255.0",
        "options": {},
    }
    _write_yaml(path, data, mode=0o600)
    return load_profile(selected, "server", name)


def create_client_profile(
    settings: DrosSettings,
    name: str,
    *,
    instance: str | None = None,
    cn: str | None = None,
    force: bool = False,
) -> OvpnProfile:
    selected = resolve_instance(settings, instance)
    _validate_safe_name("client name", name)
    path = _profile_path(selected, "client", name)
    if path.exists() and not force:
        raise ValueError(f"client profile already exists: {name}; use --force to overwrite")
    data = {
        "name": name,
        "cn": cn or name,
        "options": {},
    }
    _write_yaml(path, data, mode=0o600)
    return load_profile(selected, "client", name)


def update_server(settings: DrosSettings, name: str, *, instance: str | None = None) -> list[Path]:
    selected = resolve_instance(settings, instance)
    config = load_instance_config(selected)
    profile = load_profile(selected, "server", name)
    _server_options(config, profile)
    if not _latest_cert_link(profile).exists():
        _issue_cert(selected, config, profile, cn=_profile_cn(profile), subject=None, days=None)
    target = profile.root / "server.conf"
    target.write_text(render_server_config(selected, config, profile), encoding="utf-8")
    target.chmod(0o600)
    return [target]


def renew_server(settings: DrosSettings, name: str, *, instance: str | None = None) -> list[Path]:
    selected = resolve_instance(settings, instance)
    config = load_instance_config(selected)
    profile = load_profile(selected, "server", name)
    _issue_cert(selected, config, profile, cn=_profile_cn(profile), subject=None, days=None)
    return update_server(settings, name, instance=selected.name)


def update_client(settings: DrosSettings, name: str, *, instance: str | None = None) -> list[Path]:
    selected = resolve_instance(settings, instance)
    config = load_instance_config(selected)
    profile = load_profile(selected, "client", name)
    _client_options(config, profile)
    client_cert = _auth_client_cert(config)
    if client_cert != "none" and not _latest_cert_link(profile).exists():
        _issue_cert(selected, config, profile, cn=_profile_cn(profile), subject=None, days=None)
    servers = list_profiles(selected, "server")
    if not servers:
        raise ValueError(f"{selected.name}: no server profiles found; run `gw ovpn server create <name>`")
    rendered_outputs: list[tuple[Path, str]] = []
    for server in servers:
        target = profile.root / f"client-{server.name}.ovpn"
        rendered_outputs.append(
            (target, render_client_config(selected, config, profile, [server], auto=False))
        )
    auto_target = profile.root / "client-auto.ovpn"
    rendered_outputs.append((auto_target, render_client_config(selected, config, profile, servers, auto=True)))

    outputs: list[Path] = []
    for target, content in rendered_outputs:
        target.write_text(content, encoding="utf-8")
        target.chmod(0o600)
        outputs.append(target)
    return outputs


def renew_client(settings: DrosSettings, name: str, *, instance: str | None = None) -> list[Path]:
    selected = resolve_instance(settings, instance)
    config = load_instance_config(selected)
    if _auth_client_cert(config) == "none":
        raise ValueError("client certificates are disabled by auth.client_cert: none")
    profile = load_profile(selected, "client", name)
    _issue_cert(selected, config, profile, cn=_profile_cn(profile), subject=None, days=None)
    return update_client(settings, name, instance=selected.name)


def revoke_cert(
    settings: DrosSettings,
    *,
    server: str | None = None,
    client: str | None = None,
    instance: str | None = None,
) -> Path:
    if bool(server) == bool(client):
        raise ValueError("pass exactly one of --server or --client")
    kind = "server" if server else "client"
    name = server or client or ""
    return revoke_profile_cert(settings, instance=instance, kind=kind, name=name, cert_id="latest")


def revoke_profile_cert(
    settings: DrosSettings,
    *,
    instance: str | None = None,
    kind: str,
    name: str,
    cert_id: str,
) -> Path:
    selected = resolve_instance(settings, instance)
    config = load_instance_config(selected)
    _ensure_openssl_config(selected, config)
    profile = load_profile(selected, kind, name)
    cert_dir = _cert_dir_for_id(profile, cert_id)
    cert_path = cert_dir / f"{profile.name}.crt"
    if not cert_path.exists():
        raise ValueError(f"{profile.kind} cert not found: {profile.name}/{cert_dir.name}")
    if cert_dir.name not in _revoked_cert_ids(selected, profile):
        _run_openssl(["ca", "-batch", "-config", str(_openssl_config_path(selected)), "-revoke", str(cert_path)])
        _record_revocation(selected, profile, cert_path)
    return _generate_crl(selected, config)


def renew_crl(settings: DrosSettings, *, instance: str | None = None) -> Path:
    selected = resolve_instance(settings, instance)
    config = load_instance_config(selected)
    _ensure_ca(selected, config, force=False)
    return _generate_crl(selected, config)


def list_profiles_summary(settings: DrosSettings, *, instance: str | None = None) -> list[OvpnProfileSummary]:
    selected = resolve_instance(settings, instance)
    summaries: list[OvpnProfileSummary] = []
    for kind in ("server", "client"):
        for profile in list_profiles(selected, kind):
            summaries.append(
                OvpnProfileSummary(
                    kind=profile.kind,
                    name=profile.name,
                    path=str(profile.logical_root / "profile.yaml"),
                    latest_cert=_latest_cert_id(profile),
                    output_files=tuple(str(_logical_profile(profile, path)) for path in _output_files(profile)),
                )
            )
    return summaries


def list_profiles_payload(settings: DrosSettings, *, instance: str | None = None) -> dict[str, Any]:
    selected = resolve_instance(settings, instance)
    return {
        "instance": selected.name,
        "servers": [_profile_summary(item) for item in list_profiles(selected, "server")],
        "clients": [_profile_summary(item) for item in list_profiles(selected, "client")],
    }


def list_certs(settings: DrosSettings, *, instance: str | None = None) -> dict[str, Any]:
    selected = resolve_instance(settings, instance)
    payload: dict[str, Any] = {"instance": selected.name, "server": [], "client": []}
    for kind in ("server", "client"):
        for profile in list_profiles(selected, kind):
            latest = _latest_cert_link(profile)
            target = latest.resolve() if latest.exists() else None
            payload[kind].append(
                {
                    "name": profile.name,
                    "latest": target.name if target is not None else None,
                    "cert": str(target / f"{profile.name}.crt") if target is not None else None,
                }
            )
    return payload


def list_profile_certs(settings: DrosSettings, instance: str, kind: str, name: str) -> list[OvpnCertSummary]:
    selected = resolve_instance(settings, instance)
    profile = load_profile(selected, kind, name)
    return _profile_cert_summaries(profile, selected)


def download_profile_file(
    settings: DrosSettings,
    *,
    instance: str,
    kind: str,
    name: str,
    file_name: str | None = None,
) -> Path:
    selected = resolve_instance(settings, instance)
    profile = load_profile(selected, kind, name)
    selected_name = file_name
    if selected_name is None:
        selected_name = "server.conf" if kind == "server" else "client-auto.ovpn"
    if "/" in selected_name or "\\" in selected_name or selected_name in {"", ".", ".."}:
        raise ValueError(f"invalid OpenVPN output file name: {selected_name!r}")
    path = profile.root / selected_name
    if not path.exists() or not path.is_file():
        raise ValueError(f"OpenVPN output file not found: {selected_name}")
    return path


def doctor(settings: DrosSettings, *, instance: str | None = None) -> dict[str, Any]:
    selected = resolve_instance(settings, instance)
    config = load_instance_config(selected)
    ca_cert = _ca_cert_path(selected)
    ca_key = _ca_key_path(selected)
    crl = _crl_path(selected)
    return {
        "instance": selected.name,
        "root": str(selected.logical_root),
        "config": str(selected.logical_root / "config.yaml"),
        "ca": {
            "cert": ca_cert.exists(),
            "key": ca_key.exists(),
            "crl": crl.exists(),
            "cn": _ca_config(config).get("cn"),
        },
        "crl": {
            "path": str(selected.logical_root / "pki" / "ca" / "crl.pem"),
            "exists": crl.exists(),
            "days": int(_crl_config(config).get("days", 365)),
        },
        "auth": config.get("auth", {}),
        "options": config.get("options", {}),
        "servers": len(list_profiles(selected, "server")),
        "clients": len(list_profiles(selected, "client")),
    }


def load_instance_config(instance: OvpnInstance) -> dict[str, Any]:
    raw = yaml.safe_load(instance.config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"ovpn config must be a mapping: {instance.config_path}")
    return _merge_defaults(raw, _default_config(name=instance.name, ca_cn=None))


def load_profile(instance: OvpnInstance, kind: str, name: str) -> OvpnProfile:
    _validate_profile_kind(kind)
    _validate_safe_name(f"{kind} name", name)
    path = _profile_path(instance, kind, name)
    if not path.exists():
        raise ValueError(f"{kind} profile not found: {name}; run `gw ovpn {kind} create {name}`")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{kind} profile must be a mapping: {path}")
    raw.setdefault("name", name)
    return OvpnProfile(
        kind=kind,
        name=name,
        logical_root=instance.logical_root / _profile_plural(kind) / name,
        root=path.parent,
        path=path,
        data=raw,
    )


def list_profiles(instance: OvpnInstance, kind: str) -> list[OvpnProfile]:
    _validate_profile_kind(kind)
    root = instance.root / _profile_plural(kind)
    if not root.exists():
        return []
    profiles: list[OvpnProfile] = []
    for item in sorted(root.iterdir(), key=lambda path: path.name):
        if item.is_dir() and (item / "profile.yaml").exists():
            profiles.append(load_profile(instance, kind, item.name))
    return profiles


def render_server_config(instance: OvpnInstance, config: dict[str, Any], profile: OvpnProfile) -> str:
    options = _server_options(config, profile)
    cert_dir = _latest_cert_dir(profile)
    cert_path = cert_dir / f"{profile.name}.crt"
    key_path = cert_dir / f"{profile.name}.key"
    if not cert_path.exists() or not key_path.exists():
        raise ValueError(f"server cert not found: {profile.name}")
    lines = [
        f"port {int(options.get('port', 1194))}",
        *_server_proto_lines(options),
        f"dev-type {_dev_type(config)}",
    ]
    if _bool_option(options, "multihome"):
        lines.append("multihome")
    lines.extend(
        [
            f"server {profile.data.get('network', '10.8.0.0')} {profile.data.get('netmask', '255.255.255.0')}",
            f"topology {options.get('topology', 'subnet')}",
        ]
    )
    for value in _list_option(options, "push"):
        lines.append(f"push {_quoted(value)}")
    lines.extend(_inline_pem("ca", _ca_cert_path(instance)))
    lines.extend(_inline_pem("cert", cert_path))
    lines.extend(_inline_pem("key", key_path))
    lines.extend(["dh none", "persist-key", "persist-tun"])
    keepalive = options.get("keepalive")
    if isinstance(keepalive, list) and len(keepalive) == 2:
        lines.append(f"keepalive {int(keepalive[0])} {int(keepalive[1])}")
    _append_int_option(lines, options, "sndbuf")
    _append_int_option(lines, options, "rcvbuf")
    _append_int_option(lines, options, "txqueuelen")
    _append_auth(lines, config)
    allow_compression = str(options.get("allow_compression", "no"))
    lines.append(f"allow-compression {allow_compression}")
    if options.get("verb") is not None:
        lines.append(f"verb {int(options['verb'])}")
    for option in _list_option(options, "raw_server_options"):
        lines.append(str(option))
    lines.append("")
    return "\n".join(lines)


def render_client_config(
    instance: OvpnInstance,
    config: dict[str, Any],
    profile: OvpnProfile,
    servers: list[OvpnProfile],
    *,
    auto: bool,
) -> str:
    if not servers:
        raise ValueError("client config render requires at least one server profile")
    client_options = _client_options(config, profile)
    client_dev = _client_dev(config)
    lines = [
        "client",
        f"dev {client_dev}",
        f"dev-type {_dev_type(config)}",
        "nobind",
        "persist-key",
        "persist-tun",
        "remote-cert-tls server",
    ]
    if auto:
        for server in servers:
            server_options = _server_options(config, server)
            lines.extend(
                [
                    "<connection>",
                    (
                        f"remote {server.data['endpoint']} {int(server_options.get('port', 1194))} "
                        f"{_client_remote_proto(server_options, client_options)}"
                    ),
                    "</connection>",
                ]
            )
    else:
        server = servers[0]
        server_options = _server_options(config, server)
        lines.append(
            f"remote {server.data['endpoint']} {int(server_options.get('port', 1194))} "
            f"{_client_remote_proto(server_options, client_options)}"
        )

    user_auth = _auth_user_auth(config)
    client_cert = _auth_client_cert(config)
    if str(user_auth.get("type", "none")) != "none" or client_cert == "none":
        lines.append("auth-user-pass")
    lines.extend(_inline_pem("ca", _ca_cert_path(instance)))
    if client_cert != "none":
        cert_dir = _latest_cert_dir(profile)
        lines.extend(_inline_pem("cert", cert_dir / f"{profile.name}.crt"))
        lines.extend(_inline_pem("key", cert_dir / f"{profile.name}.key"))
    lines.append(f"allow-compression {client_options.get('allow_compression', 'no')}")
    if client_options.get("verb") is not None:
        lines.append(f"verb {int(client_options['verb'])}")
    if _bool_option(client_options, "auth_nocache"):
        lines.append("auth-nocache")
    for option in _list_option(client_options, "raw_client_options"):
        lines.append(str(option))
    lines.append("")
    return "\n".join(lines)


def _append_auth(lines: list[str], config: dict[str, Any]) -> None:
    client_cert = _auth_client_cert(config)
    if client_cert == "none":
        lines.append("verify-client-cert none")
        lines.append("username-as-common-name")
    elif client_cert == "optional":
        lines.append("verify-client-cert optional")
    user_auth = _auth_user_auth(config)
    auth_type = str(user_auth.get("type", "none"))
    if auth_type == "none":
        return
    plugin = user_auth.get("plugin")
    plugin_config = user_auth.get("config")
    verify_command = _get(user_auth, "verifyCommand", "verify_command", None)
    if plugin:
        line = f"plugin {plugin}"
        if plugin_config:
            line += f" {_quoted(str(plugin_config))}"
        lines.append(line)
    if verify_command:
        lines.append(f"auth-user-pass-verify {verify_command} via-env")
    if not plugin and not verify_command:
        raise ValueError(f"auth.user_auth.type={auth_type!r} requires plugin or verify_command")


def _auth_client_cert(config: dict[str, Any]) -> str:
    return str(_get(config.get("auth", {}), "clientCert", "client_cert", "required"))


def _auth_user_auth(config: dict[str, Any]) -> dict[str, Any]:
    value = _get(config.get("auth", {}), "userAuth", "user_auth", {})
    return value if isinstance(value, dict) else {}


def _server_options(config: dict[str, Any], profile: OvpnProfile) -> dict[str, Any]:
    _reject_profile_options(profile, {"client_dev", "dev_type"})
    options = dict(config.get("options", {}))
    profile_options = profile.data.get("options")
    if isinstance(profile_options, dict):
        options.update(profile_options)
    for key in _SERVER_OPTION_KEYS:
        if key in profile.data:
            options[key] = profile.data[key]
    return options


def _client_options(config: dict[str, Any], profile: OvpnProfile) -> dict[str, Any]:
    _reject_profile_options(profile, {"client_dev", "dev_type"})
    options = dict(config.get("options", {}))
    profile_options = profile.data.get("options")
    if isinstance(profile_options, dict):
        options.update(profile_options)
    return options


def _client_dev(config: dict[str, Any]) -> str:
    options = config.get("options", {})
    value = _get(options, "clientDev", "client_dev", None)
    if value is None:
        value = _get(options, "dev", "dev", None)
    if value is None:
        value = _dev_type(config)
    return str(value)


def _server_proto_lines(options: dict[str, Any]) -> list[str]:
    proto = _transport_proto(options)
    family = _address_family(options, "serverListenFamily", "server_listen_family", default="both")
    if proto == "udp":
        rendered = "udp4" if family == "v4only" else "udp6"
    else:
        rendered = "tcp4-server" if family == "v4only" else "tcp6-server"
    lines = [f"proto {rendered}"]
    if family == "v6only":
        lines.append("bind ipv6only")
    return lines


def _client_remote_proto(server_options: dict[str, Any], client_options: dict[str, Any]) -> str:
    proto = _transport_proto(server_options)
    family = _address_family(client_options, "clientConnectFamily", "client_connect_family", default=None)
    if family is None:
        family = _address_family(server_options, "clientConnectFamily", "client_connect_family", default="auto")
    suffix = {"auto": "", "v4only": "4", "v6only": "6"}[family]
    return f"{proto}{suffix}"


def _transport_proto(options: dict[str, Any]) -> str:
    proto = str(options.get("proto", "udp"))
    if proto not in {"udp", "tcp"}:
        raise ValueError(
            f"options.proto must be udp or tcp, got {proto!r}; "
            "use serverListenFamily/clientConnectFamily for IPv4/IPv6 selection"
        )
    return proto


def _address_family(
    options: dict[str, Any],
    camel_name: str,
    snake_name: str,
    *,
    default: str | None,
) -> str | None:
    value = _get(options, camel_name, snake_name, default)
    if value is None:
        return None
    family = str(value)
    if family not in {"v4only", "v6only", "both", "auto"}:
        raise ValueError(f"options.{snake_name} must be one of v4only, v6only, both, auto; got {family!r}")
    if camel_name == "serverListenFamily" and family == "auto":
        raise ValueError("options.serverListenFamily must be one of v4only, v6only, both")
    if camel_name == "clientConnectFamily" and family == "both":
        raise ValueError("options.clientConnectFamily must be one of v4only, v6only, auto")
    return family


def _dev_type(config: dict[str, Any]) -> str:
    value = str(config.get("options", {}).get("dev_type", "tun"))
    if value not in {"tun", "tap"}:
        raise ValueError(f"options.dev_type must be tun or tap, got {value!r}")
    return value


def _reject_profile_options(profile: OvpnProfile, option_names: set[str]) -> None:
    profile_options = profile.data.get("options")
    found = [name for name in sorted(option_names) if name in profile.data]
    if isinstance(profile_options, dict):
        found.extend(f"options.{name}" for name in sorted(option_names) if name in profile_options)
    if found:
        raise ValueError(
            f"{profile.kind} profile {profile.name} cannot override instance-wide option(s): {', '.join(found)}; "
            "set them in config.yaml"
        )


def _issue_cert(
    instance: OvpnInstance,
    config: dict[str, Any],
    profile: OvpnProfile,
    *,
    cn: str | None,
    subject: str | None,
    days: int | None,
) -> Path:
    if profile.kind == "client" and _auth_client_cert(config) == "none":
        raise ValueError("client certificates are disabled by auth.client_cert: none")
    _ensure_ca(instance, config, force=False)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    cert_dir = profile.root / "certs" / timestamp
    cert_dir.mkdir(parents=True, exist_ok=False)
    key_path = cert_dir / f"{profile.name}.key"
    csr_path = cert_dir / f"{profile.name}.csr"
    cert_path = cert_dir / f"{profile.name}.crt"
    common_name = cn or profile.name
    subject_value = subject or f"/CN={common_name}"
    _run_openssl(["genrsa", "-out", str(key_path), "2048"])
    key_path.chmod(0o600)
    _run_openssl(["req", "-new", "-key", str(key_path), "-out", str(csr_path), "-subj", subject_value])
    _run_openssl(
        [
            "ca",
            "-batch",
            "-config",
            str(_openssl_config_path(instance)),
            "-extensions",
            f"{profile.kind}_cert",
            "-days",
            str(days or int(_cert_config(config).get("days", 825))),
            "-notext",
            "-in",
            str(csr_path),
            "-out",
            str(cert_path),
        ]
    )
    _replace_latest_symlink(_latest_cert_link(profile), cert_dir)
    return cert_dir


def _ensure_ca(instance: OvpnInstance, config: dict[str, Any], *, force: bool) -> None:
    ca_key = _ca_key_path(instance)
    ca_cert = _ca_cert_path(instance)
    if (ca_key.exists() or ca_cert.exists()) and not force:
        if not ca_key.exists() or not ca_cert.exists():
            raise ValueError(f"incomplete CA under {ca_key.parent}")
        _ensure_openssl_config(instance, config)
        return
    if (ca_key.exists() or ca_cert.exists()) and force:
        raise ValueError("refusing to overwrite existing CA; remove the instance pki/ca directory manually")
    ca_key.parent.mkdir(parents=True, exist_ok=True)
    (ca_key.parent / "certs").mkdir(parents=True, exist_ok=True)
    (ca_key.parent / "index.txt").write_text("", encoding="utf-8")
    (ca_key.parent / "serial").write_text("1000\n", encoding="utf-8")
    (ca_key.parent / "crlnumber").write_text("1000\n", encoding="utf-8")
    ca = _ca_config(config)
    ca_cn = ca.get("cn") or f"{instance.name} OpenVPN CA"
    ca_days = int(ca.get("days", 3650))
    _run_openssl(["genrsa", "-out", str(ca_key), "4096"])
    ca_key.chmod(0o600)
    _run_openssl(
        [
            "req",
            "-x509",
            "-new",
            "-nodes",
            "-key",
            str(ca_key),
            "-sha256",
            "-days",
            str(ca_days),
            "-out",
            str(ca_cert),
            "-subj",
            f"/CN={ca_cn}",
        ]
    )
    _ensure_openssl_config(instance, config)
    _generate_crl(instance, config)


def _generate_crl(instance: OvpnInstance, config: dict[str, Any]) -> Path:
    _ensure_openssl_config(instance, config)
    crl_path = _crl_path(instance)
    _run_openssl(
        [
            "ca",
            "-batch",
            "-config",
            str(_openssl_config_path(instance)),
            "-gencrl",
            "-out",
            str(crl_path),
        ]
    )
    return crl_path


def _ensure_openssl_config(instance: OvpnInstance, config: dict[str, Any]) -> None:
    config_path = _openssl_config_path(instance)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_render_openssl_config(instance, config), encoding="utf-8")
    config_path.chmod(0o600)


def _render_openssl_config(instance: OvpnInstance, config: dict[str, Any]) -> str:
    ca_dir = instance.root / "pki" / "ca"
    crl_days = int(_crl_config(config).get("days", 365))
    return f"""[ ca ]
default_ca = CA_default

[ CA_default ]
dir = {ca_dir}
certs = $dir/certs
new_certs_dir = $dir/certs
database = $dir/index.txt
serial = $dir/serial
crlnumber = $dir/crlnumber
certificate = $dir/ca.crt
private_key = $dir/ca.key
default_md = sha256
default_crl_days = {crl_days}
unique_subject = no
policy = policy_any

[ policy_any ]
commonName = supplied

[ server_cert ]
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth

[ client_cert ]
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = clientAuth
"""


def _default_config(*, name: str, ca_cn: str | None) -> dict[str, Any]:
    return {
        "ca": {
            "cn": ca_cn or f"{name} OpenVPN CA",
            "days": 3650,
        },
        "cert": {
            "days": 825,
        },
        "crl": {
            "days": 365,
        },
        "auth": {
            "client_cert": "required",
            "user_auth": {
                "type": "none",
                "plugin": None,
                "config": None,
                "verify_command": None,
            },
        },
        "options": {
            "proto": "udp",
            "serverListenFamily": "both",
            "clientConnectFamily": "auto",
            "port": 1194,
            "dev_type": "tun",
            "topology": "subnet",
            "multihome": True,
            "allow_compression": "no",
            "keepalive": [10, 60],
            "verb": 3,
            "push": [],
            "raw_server_options": [],
            "raw_client_options": [],
        },
    }


def _merge_defaults(raw: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    result = dict(defaults)
    for key, value in raw.items():
        if isinstance(current := result.get(key), dict) and isinstance(value, dict):
            result[key] = _merge_defaults(value, current)
        else:
            result[key] = value
    return result


def _ca_config(config: dict[str, Any]) -> dict[str, Any]:
    if isinstance(config.get("ca"), dict):
        return config["ca"]
    pki = config.get("pki")
    if isinstance(pki, dict) and isinstance(pki.get("ca"), dict):
        return pki["ca"]
    return {}


def _cert_config(config: dict[str, Any]) -> dict[str, Any]:
    if isinstance(config.get("cert"), dict):
        return config["cert"]
    pki = config.get("pki")
    if isinstance(pki, dict):
        return {"days": _get(pki, "certDays", "cert_days", 825)}
    return {}


def _crl_config(config: dict[str, Any]) -> dict[str, Any]:
    if isinstance(config.get("crl"), dict):
        return config["crl"]
    return {"days": 365}


def _instance_for_name(settings: DrosSettings, name: str) -> OvpnInstance:
    logical_root = _ovpn_root(settings) / name
    root = _system_path(settings, logical_root)
    return OvpnInstance(name=name, logical_root=logical_root, root=root, config_path=root / "config.yaml")


def _ovpn_root(settings: DrosSettings) -> Path:
    return settings.paths.containers.parent / "ovpn"


def _system_path(settings: DrosSettings, logical_path: Path) -> Path:
    if not logical_path.is_absolute():
        raise ValueError(f"OpenVPN path must be absolute: {logical_path}")
    if settings.sys_root == Path("/"):
        return logical_path
    return settings.sys_root / logical_path.relative_to("/")


def _ensure_instance_dirs(instance: OvpnInstance) -> None:
    for relative in (
        "pki/ca",
        "servers",
        "clients",
        "state",
    ):
        (instance.root / relative).mkdir(parents=True, exist_ok=True)


def _profile_path(instance: OvpnInstance, kind: str, name: str) -> Path:
    return instance.root / _profile_plural(kind) / name / "profile.yaml"


def _profile_plural(kind: str) -> str:
    return "servers" if kind == "server" else "clients"


def _profile_summary(profile: OvpnProfile) -> dict[str, Any]:
    return {
        "name": profile.name,
        "path": str(profile.logical_root / "profile.yaml"),
        "cert": _latest_cert_id(profile),
    }


def _profile_cn(profile: OvpnProfile) -> str:
    return str(profile.data.get("cn") or profile.data.get("endpoint") or profile.name)


def _ca_key_path(instance: OvpnInstance) -> Path:
    return instance.root / "pki" / "ca" / "ca.key"


def _ca_cert_path(instance: OvpnInstance) -> Path:
    return instance.root / "pki" / "ca" / "ca.crt"


def _crl_path(instance: OvpnInstance) -> Path:
    return instance.root / "pki" / "ca" / "crl.pem"


def _openssl_config_path(instance: OvpnInstance) -> Path:
    return instance.root / "pki" / "openssl.cnf"


def _latest_cert_link(profile: OvpnProfile) -> Path:
    return profile.root / "certs" / "latest"


def _latest_cert_dir(profile: OvpnProfile) -> Path:
    latest = _latest_cert_link(profile)
    if not latest.exists():
        raise ValueError(f"{profile.kind} cert not found: {profile.name}")
    return latest.resolve()


def _latest_cert_id(profile: OvpnProfile) -> str | None:
    latest = _latest_cert_link(profile)
    if not latest.exists():
        return None
    return latest.resolve().name


def _cert_dir_for_id(profile: OvpnProfile, cert_id: str) -> Path:
    if cert_id == "latest":
        return _latest_cert_dir(profile)
    _validate_safe_name("cert id", cert_id)
    cert_dir = profile.root / "certs" / cert_id
    if not cert_dir.exists() or not cert_dir.is_dir():
        raise ValueError(f"{profile.kind} cert not found: {profile.name}/{cert_id}")
    return cert_dir


def _replace_latest_symlink(link: Path, target: Path) -> None:
    if link.exists() or link.is_symlink():
        link.unlink()
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target.name, target_is_directory=True)


def _inline_pem(tag: str, path: Path) -> list[str]:
    if not path.exists():
        raise ValueError(f"required PEM file not found: {path}")
    content = _pem_blocks(path.read_text(encoding="utf-8"), path)
    return [f"<{tag}>", content, f"</{tag}>"]


def _pem_blocks(value: str, path: Path) -> str:
    lines = value.splitlines()
    blocks: list[str] = []
    current: list[str] | None = None
    for line in lines:
        if line.startswith("-----BEGIN "):
            current = [line]
        elif current is not None:
            current.append(line)
            if line.startswith("-----END "):
                blocks.extend(current)
                current = None
    if not blocks:
        raise ValueError(f"required PEM block not found: {path}")
    return "\n".join(blocks)


def _logical_profile(profile: OvpnProfile, physical_path: Path) -> str:
    try:
        relative = physical_path.relative_to(profile.root)
    except ValueError:
        return str(physical_path)
    return str(profile.logical_root / relative)


def _write_yaml(path: Path, payload: dict[str, Any], *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")
    path.chmod(mode)


def _record_revocation(instance: OvpnInstance, profile: OvpnProfile, cert_path: Path) -> None:
    path = instance.root / "state" / "revoked.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    cert_id = cert_path.parent.name
    payload = {
        "revokedAt": datetime.now(timezone.utc).isoformat(),
        "kind": profile.kind,
        "name": profile.name,
        "certId": cert_id,
        "cert": str(cert_path),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")


def _revoked_cert_ids(instance: OvpnInstance, profile: OvpnProfile) -> set[str]:
    path = instance.root / "state" / "revoked.jsonl"
    if not path.exists():
        return set()
    result: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("kind") != profile.kind or payload.get("name") != profile.name:
            continue
        cert_id = payload.get("certId")
        if isinstance(cert_id, str):
            result.add(cert_id)
            continue
        cert_path = payload.get("cert")
        if isinstance(cert_path, str):
            result.add(Path(cert_path).parent.name)
    return result


def _profile_cert_summaries(
    profile: OvpnProfile,
    instance: OvpnInstance | None = None,
) -> list[OvpnCertSummary]:
    cert_root = profile.root / "certs"
    if not cert_root.exists():
        return []
    latest = _latest_cert_id(profile)
    revoked = _revoked_cert_ids(instance, profile) if instance is not None else set()
    result: list[OvpnCertSummary] = []
    for item in sorted(cert_root.iterdir(), key=lambda path: path.name, reverse=True):
        if not item.is_dir() or item.name == "latest":
            continue
        cert_path = item / f"{profile.name}.crt"
        if not cert_path.exists():
            continue
        result.append(
            OvpnCertSummary(
                kind=profile.kind,
                name=profile.name,
                cert_id=item.name,
                path=str(_logical_profile(profile, cert_path)),
                latest=item.name == latest,
                revoked=item.name in revoked,
            )
        )
    return result


def _output_files(profile: OvpnProfile) -> list[Path]:
    if profile.kind == "server":
        candidates = [profile.root / "server.conf"]
    else:
        candidates = sorted(profile.root.glob("client-*.ovpn"), key=lambda path: path.name)
    return [path for path in candidates if path.exists() and path.is_file()]


def _validate_profile_kind(kind: str) -> None:
    if kind not in {"server", "client"}:
        raise ValueError("profile kind must be one of: server, client")


def _validate_safe_name(label: str, value: str) -> None:
    if not value or not all(char.isalnum() or char in "._-" for char in value):
        raise ValueError(f"{label} may only contain letters, numbers, dot, underscore, and dash")


def _value_or_prompt(label: str, value: str | None, *, default: str) -> str:
    if value:
        return value
    if not sys.stdin.isatty():
        return default
    answer = input(f"{label} [{default}]: ").strip()
    return answer or default


def _get(mapping: dict[str, Any], camel: str, snake: str, default: Any) -> Any:
    if snake in mapping:
        return mapping[snake]
    if camel in mapping:
        return mapping[camel]
    return default


def _list_option(options: dict[str, Any], key: str) -> list[Any]:
    value = options.get(key)
    return value if isinstance(value, list) else []


def _bool_option(options: dict[str, Any], key: str) -> bool:
    value = options.get(key, False)
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _append_int_option(lines: list[str], options: dict[str, Any], key: str) -> None:
    if options.get(key) is not None:
        lines.append(f"{key} {int(options[key])}")


def _quoted(value: object) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _run_openssl(args: list[str]) -> None:
    command = ["openssl", *args]
    completed = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
        raise RuntimeError(f"openssl failed: {shlex.join(command)}: {message}")


_SERVER_OPTION_KEYS = {
    "allow_compression",
    "keepalive",
    "multihome",
    "port",
    "proto",
    "serverListenFamily",
    "server_listen_family",
    "clientConnectFamily",
    "client_connect_family",
    "push",
    "raw_server_options",
    "rcvbuf",
    "sndbuf",
    "topology",
    "txqueuelen",
    "verb",
}
