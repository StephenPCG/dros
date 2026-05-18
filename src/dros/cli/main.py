from __future__ import annotations

import os
import subprocess
import sys
import getpass
import sqlite3
import time
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import yaml
from cyclopts import App, Parameter
from rich.console import Console

from dros import __version__
from dros.bootstrap import run_bootstrap
from dros.cli.privilege import path_writable_for_current_user, reexec_with_sudo
from dros.cli.services import restart_local_service
from dros.config_objects import DnsmasqChinaNamesConfig, InterfaceConfig, load_config_objects
from dros.config_catalog import render_config_object_example
from dros.dnsmasq_china_names import DnsmasqChinaNamesUpdater
from dros.events import enqueue_event, process_event
from dros.invocation_log import append_invocation_log
from dros.ip_lists import AVAILABLE_IP_LIST_SOURCES, load_ip_lists, summarize_ip_lists, update_ip_lists
from dros.locks import APPLY_LOCK_PATH, LockBusyError, exclusive_lock
from dros.ovpn import (
    bootstrap_ca,
    create_client_profile,
    create_server_profile,
    doctor as ovpn_doctor,
    init_instance,
    list_certs,
    list_instances,
    list_profiles_payload,
    renew_client,
    renew_crl,
    renew_server,
    revoke_cert,
    update_client,
    update_server,
)
from dros.settings import DEFAULT_SETTINGS_PATH, DrosSettings, load_settings
from dros.update import UpdateValidationError, run_config_check, run_update
from dros.kind_aliases import resolve_kind_alias
from dros.executor import SystemExecutor
from dros.plugins import create_default_registry
from dros.plugins.base import UpdateContext
from dros.plugins.network_xfrm import start_xfrm, stop_xfrm
from dros.plugins.network_interfaces import tailscale_socket_path
from dros.config_objects import XfrmTransportConfig
from dros.web.auth import WebAuthStore, resolve_auth_db_path

console = Console()
error_console = Console(stderr=True)
_current_settings_path: str | None = None
_current_raw_args: list[str] = []

app = App(
    name="gw",
    help="DROS gateway management CLI.",
    version=__version__,
)

config_app = App(name="config", help="ConfigObject helpers.")
app.command(config_app)
web_app = App(name="web", help="Web user and session administration.")
app.command(web_app)
ip_list_app = App(name="ip-list", help="IP list utilities.")
app.command(ip_list_app)
dnsmasq_app = App(name="dnsmasq", help="dnsmasq utilities.")
app.command(dnsmasq_app)
dnsmasq_china_names_app = App(name="china-names", help="dnsmasq China names utilities.")
dnsmasq_app.command(dnsmasq_china_names_app)
ovpn_app = App(name="ovpn", help="OpenVPN PKI and profile utilities.")
app.command(ovpn_app)
ovpn_server_app = App(name="server", help="OpenVPN server profile operations.")
ovpn_app.command(ovpn_server_app)
ovpn_client_app = App(name="client", help="OpenVPN client profile operations.")
ovpn_app.command(ovpn_client_app)
ovpn_cert_app = App(name="cert", help="OpenVPN certificate operations.")
ovpn_app.command(ovpn_cert_app)
ovpn_crl_app = App(name="crl", help="OpenVPN CRL operations.")
ovpn_app.command(ovpn_crl_app)

TailscaleArgs = Annotated[
    list[str],
    Parameter(consume_multiple=True, allow_leading_hyphen=True),
]


def _not_ready(command: str) -> None:
    console.print(f"[yellow]{command}[/yellow] is reserved for the next implementation phase.")


def _xfrm_lifecycle(action: str, target: str, verbose: int) -> None:
    if verbose not in {0, 1, 2}:
        error_console.print(f"[red]{action} failed:[/red] --verbose must be 0, 1, or 2")
        raise SystemExit(2)
    try:
        kind, sep, name = target.partition("/")
        if not sep or not name:
            raise ValueError(f"{action} expects kind/name target")
        resolved = resolve_kind_alias(kind)
        if resolved != "XfrmTransport":
            raise ValueError(f"{action} currently supports XfrmTransport only, got {resolved}/{name}")
        settings = _load_cli_settings()
        _ensure_bootstrap_privileges(settings)
        with _manual_cli_lock(settings):
            configs = load_config_objects(settings)
            obj = configs.require("XfrmTransport", name)
            config = configs.resolve_object(obj, XfrmTransportConfig)
            registry = create_default_registry()
            executor = SystemExecutor(settings, verbose=verbose, console=console)
            context = UpdateContext(
                settings=settings,
                configs=configs,
                executor=executor,
                registry=registry,
            )
            if action == "start":
                start_xfrm(context, name, config)
            else:
                stop_xfrm(context, name, config)
    except (KeyError, OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        error_console.print(f"[red]{action} failed:[/red] {exc}")
        raise SystemExit(1) from exc


@app.command
def bootstrap(verbose: int = 1) -> None:
    """Apply bootstrap hooks to the system."""
    if verbose not in {0, 1, 2}:
        error_console.print("[red]bootstrap failed:[/red] --verbose must be 0, 1, or 2")
        raise SystemExit(2)

    try:
        settings = _load_cli_settings()
        _ensure_bootstrap_privileges(settings)
        with _manual_cli_lock(settings):
            run_bootstrap(settings, verbose=verbose, console=console)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        error_console.print(f"[red]bootstrap failed:[/red] {exc}")
        raise SystemExit(1) from exc


@app.command
def update(target: str | None = None, verbose: int = 1) -> None:
    """Apply configuration to the running system."""
    if verbose not in {0, 1, 2}:
        error_console.print("[red]update failed:[/red] --verbose must be 0, 1, or 2")
        raise SystemExit(2)

    try:
        settings = _load_cli_settings()
        _ensure_bootstrap_privileges(settings)
        with _manual_cli_lock(settings):
            run_update(settings, target=target, verbose=verbose, console=console)
    except UpdateValidationError as exc:
        error_console.print("[red]update validation failed:[/red]")
        for error in exc.errors:
            error_console.print(f"- {error}", markup=False)
        raise SystemExit(1) from exc
    except (KeyError, OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        error_console.print(f"[red]update failed:[/red] {exc}")
        raise SystemExit(1) from exc


@app.command
def start(target: str, verbose: int = 1) -> None:
    """Start a runtime object that supports start semantics."""
    _xfrm_lifecycle("start", target, verbose)


@app.command
def stop(target: str, verbose: int = 1) -> None:
    """Stop a runtime object that supports stop semantics."""
    _xfrm_lifecycle("stop", target, verbose)


@app.command(name="tailscale", alias="ts")
def tailscale_command(iface_name: str, tailscale_args: TailscaleArgs) -> None:
    """Run tailscale CLI against a DROS-managed tailscale Interface."""
    try:
        settings = _load_cli_settings()
        configs = load_config_objects(settings)
        obj = configs.require("Interface", iface_name)
        config = configs.resolve_object(obj, InterfaceConfig)
        if config.type != "tailscale":
            raise ValueError(f"Interface/{iface_name} is not type tailscale")
        socket_path = tailscale_socket_path(settings, iface_name)
        result = subprocess.run(
            ["tailscale", f"--socket={socket_path}", *tailscale_args],
            check=False,
        )
    except (KeyError, OSError, RuntimeError, ValueError) as exc:
        error_console.print(f"[red]ts failed:[/red] {exc}")
        raise SystemExit(1) from exc
    raise SystemExit(result.returncode)


@app.command
def hook(event: str, iface: str | None = None, verbose: int = 0, process_now: bool = False) -> None:
    """Queue a system hook event for drosd."""
    if verbose not in {0, 1, 2}:
        error_console.print("[red]hook failed:[/red] --verbose must be 0, 1, or 2")
        raise SystemExit(2)

    try:
        settings = _load_cli_settings()
        enqueue_event(settings, event, iface)
        if process_now:
            process_event(settings, event, iface=iface, verbose=verbose, console=console)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        error_console.print(f"[red]hook failed:[/red] {exc}")
        raise SystemExit(1) from exc


@config_app.command(name="create")
def config_create(kind: str) -> None:
    """Print or create an example ConfigObject."""
    try:
        console.print(render_config_object_example(kind), markup=False)
    except ValueError as exc:
        error_console.print(f"[red]config create failed:[/red] {exc}")
        raise SystemExit(1) from exc


@config_app.command(name="check")
def config_check(verbose: int = 1) -> None:
    """Validate all ConfigObjects without applying them."""
    if verbose not in {0, 1, 2}:
        error_console.print("[red]config check failed:[/red] --verbose must be 0, 1, or 2")
        raise SystemExit(2)
    try:
        settings = _load_cli_settings()
        result = run_config_check(settings, verbose=verbose, console=console)
    except UpdateValidationError as exc:
        error_console.print("[red]config check failed:[/red]")
        for error in exc.errors:
            error_console.print(f"- {error}", markup=False)
        raise SystemExit(1) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        error_console.print(f"[red]config check failed:[/red] {exc}")
        raise SystemExit(1) from exc
    if verbose > 0:
        console.print(f"[green]ok:[/green] {result.object_count} ConfigObjects")


@ip_list_app.command(name="list")
def ip_list_list() -> None:
    """List detected IP lists."""
    try:
        settings = _load_cli_settings()
        summaries = summarize_ip_lists(load_ip_lists(settings))
    except (OSError, RuntimeError, ValueError) as exc:
        error_console.print(f"[red]ip-list list failed:[/red] {exc}")
        raise SystemExit(1) from exc

    if not summaries:
        console.print("no ip lists found")
        return
    for item in summaries:
        console.print(
            f"{item.name} v4={item.ipv4_count} v6={item.ipv6_count} "
            f"mixed={item.mixed_count}"
        )


@ip_list_app.command(name="sources")
def ip_list_sources() -> None:
    """List built-in downloadable IP list sources."""
    for source in AVAILABLE_IP_LIST_SOURCES:
        console.print(source)


@ip_list_app.command(name="update")
def ip_list_update(sources: list[str] | None = None, verbose: int = 1, timeout: float = 30.0) -> None:
    """Download runtime IP lists."""
    if verbose not in {0, 1, 2}:
        error_console.print("[red]ip-list update failed:[/red] --verbose must be 0, 1, or 2")
        raise SystemExit(2)
    try:
        settings = _load_cli_settings()
        _ensure_path_privileges(settings.paths.run)
        result = update_ip_lists(
            settings,
            selected_sources=sources,
            verbose=verbose,
            console=console,
            timeout=timeout,
        )
        if not result.failures:
            enqueue_event(settings, "route-refresh")
    except (OSError, RuntimeError, ValueError) as exc:
        error_console.print(f"[red]ip-list update failed:[/red] {exc}")
        raise SystemExit(1) from exc
    if result.failures:
        raise SystemExit(1)
    if verbose > 0:
        console.print("[green]ip lists updated[/green]")


@dnsmasq_china_names_app.command(name="update")
def dnsmasq_china_names_update(verbose: int = 1, timeout: float = 30.0) -> None:
    """Download dnsmasq China names files."""
    if verbose not in {0, 1, 2}:
        error_console.print("[red]dnsmasq china-names update failed:[/red] --verbose must be 0, 1, or 2")
        raise SystemExit(2)
    try:
        settings = _load_cli_settings()
        _ensure_bootstrap_privileges(settings)
        configs = load_config_objects(settings)
        obj = configs.get("DnsmasqChinaNames", "system")
        if obj is None:
            objects = configs.by_kind("DnsmasqChinaNames")
            obj = sorted(objects, key=lambda item: item.name)[-1] if objects else None
        if obj is None:
            raise ValueError("ConfigObject not found: DnsmasqChinaNames/system")
        config = configs.resolve_object(obj, DnsmasqChinaNamesConfig)
        if not config.enabled:
            if verbose > 0:
                console.print("dnsmasq China names updater is disabled")
            return
        manual_name_files = [
            str(_resolve_config_relative_path(obj.source.parent, item))
            for item in config.manual_name_files
        ]
        result = DnsmasqChinaNamesUpdater(timeout=timeout).update(
            settings,
            servers=config.servers,
            selected_files=config.files or None,
            manual_names=config.manual_names,
            manual_name_files=manual_name_files,
            verbose=verbose,
            console=console,
        )
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        error_console.print(f"[red]dnsmasq china-names update failed:[/red] {exc}")
        raise SystemExit(1) from exc
    for warning in result.warnings:
        if verbose > 0:
            console.print(f"warning: {warning}", markup=False)
    if result.failures:
        for failure in result.failures:
            if verbose > 0:
                console.print(f"failed: {failure['file']}: {failure['error']}", markup=False)
        raise SystemExit(1)
    if verbose > 0:
        console.print("[green]dnsmasq China names updated[/green]")


@ovpn_app.command(name="init")
def ovpn_init(name: str, ca_cn: str | None = None, force: bool = False, verbose: int = 1) -> None:
    """Create an OpenVPN instance config and CA."""
    try:
        settings = _load_cli_settings()
        _ensure_ovpn_privileges(settings)
        with _manual_cli_lock(settings):
            instance = init_instance(settings, name, ca_cn=ca_cn, force=force)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        error_console.print(f"[red]ovpn init failed:[/red] {exc}")
        raise SystemExit(1) from exc
    if verbose > 0:
        console.print(f"created ovpn instance {instance.name}: {instance.logical_root}", markup=False)


@ovpn_app.command(name="bootstrap")
def ovpn_bootstrap(instance: str | None = None, force: bool = False, verbose: int = 1) -> None:
    """Create or refresh the CA for an existing OpenVPN instance."""
    try:
        settings = _load_cli_settings()
        _ensure_ovpn_privileges(settings)
        with _manual_cli_lock(settings):
            selected = bootstrap_ca(settings, instance=instance, force=force)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        error_console.print(f"[red]ovpn bootstrap failed:[/red] {exc}")
        raise SystemExit(1) from exc
    if verbose > 0:
        console.print(f"bootstrapped ovpn CA: {selected.name}", markup=False)


@ovpn_app.command(name="list")
def ovpn_list(target: str = "instances", instance: str | None = None, verbose: int = 1) -> None:
    """List OpenVPN instances, profiles, or certs."""
    try:
        settings = _load_cli_settings()
        if target == "instances":
            payload = [{"name": item.name, "root": str(item.logical_root)} for item in list_instances(settings)]
        elif target == "profiles":
            payload = list_profiles_payload(settings, instance=instance)
        elif target == "certs":
            payload = list_certs(settings, instance=instance)
        else:
            raise ValueError("ovpn list target must be one of: instances, profiles, certs")
    except (OSError, RuntimeError, ValueError) as exc:
        error_console.print(f"[red]ovpn list failed:[/red] {exc}")
        raise SystemExit(1) from exc
    if verbose > 0:
        console.print(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), markup=False)


@ovpn_app.command(name="doctor")
def ovpn_doctor_command(instance: str | None = None, verbose: int = 1) -> None:
    """Print a compact OpenVPN instance health summary."""
    try:
        settings = _load_cli_settings()
        payload = ovpn_doctor(settings, instance=instance)
    except (OSError, RuntimeError, ValueError) as exc:
        error_console.print(f"[red]ovpn doctor failed:[/red] {exc}")
        raise SystemExit(1) from exc
    if verbose > 0:
        console.print(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), markup=False)


@ovpn_server_app.command(name="create")
def ovpn_server_create(
    name: str,
    instance: str | None = None,
    endpoint: str | None = None,
    cn: str | None = None,
    network: str | None = None,
    netmask: str | None = None,
    force: bool = False,
    verbose: int = 1,
) -> None:
    """Create an OpenVPN server profile."""
    try:
        settings = _load_cli_settings()
        _ensure_ovpn_privileges(settings)
        with _manual_cli_lock(settings):
            profile = create_server_profile(
                settings,
                name,
                instance=instance,
                endpoint=endpoint,
                cn=cn,
                network=network,
                netmask=netmask,
                force=force,
            )
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        error_console.print(f"[red]ovpn server create failed:[/red] {exc}")
        raise SystemExit(1) from exc
    if verbose > 0:
        console.print(
            f"created ovpn server profile {profile.name}: {profile.logical_root / 'profile.yaml'}",
            markup=False,
        )


@ovpn_server_app.command(name="update")
def ovpn_server_update(name: str, instance: str | None = None, verbose: int = 1) -> None:
    """Upsert the server certificate and server.conf for a profile."""
    try:
        settings = _load_cli_settings()
        _ensure_ovpn_privileges(settings)
        with _manual_cli_lock(settings):
            paths = update_server(settings, name, instance=instance)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        error_console.print(f"[red]ovpn server update failed:[/red] {exc}")
        raise SystemExit(1) from exc
    if verbose > 0:
        _print_ovpn_paths(f"updated ovpn server {name}", paths)


@ovpn_server_app.command(name="renew")
def ovpn_server_renew(name: str, instance: str | None = None, verbose: int = 1) -> None:
    """Issue a fresh server certificate and refresh server.conf."""
    try:
        settings = _load_cli_settings()
        _ensure_ovpn_privileges(settings)
        with _manual_cli_lock(settings):
            paths = renew_server(settings, name, instance=instance)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        error_console.print(f"[red]ovpn server renew failed:[/red] {exc}")
        raise SystemExit(1) from exc
    if verbose > 0:
        _print_ovpn_paths(f"renewed ovpn server {name}", paths)


@ovpn_client_app.command(name="create")
def ovpn_client_create(
    name: str,
    instance: str | None = None,
    cn: str | None = None,
    force: bool = False,
    verbose: int = 1,
) -> None:
    """Create an OpenVPN client profile."""
    try:
        settings = _load_cli_settings()
        _ensure_ovpn_privileges(settings)
        with _manual_cli_lock(settings):
            profile = create_client_profile(settings, name, instance=instance, cn=cn, force=force)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        error_console.print(f"[red]ovpn client create failed:[/red] {exc}")
        raise SystemExit(1) from exc
    if verbose > 0:
        console.print(
            f"created ovpn client profile {profile.name}: {profile.logical_root / 'profile.yaml'}",
            markup=False,
        )


@ovpn_client_app.command(name="update")
def ovpn_client_update(name: str, instance: str | None = None, verbose: int = 1) -> None:
    """Upsert the client certificate and render ovpn files."""
    try:
        settings = _load_cli_settings()
        _ensure_ovpn_privileges(settings)
        with _manual_cli_lock(settings):
            paths = update_client(settings, name, instance=instance)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        error_console.print(f"[red]ovpn client update failed:[/red] {exc}")
        raise SystemExit(1) from exc
    if verbose > 0:
        _print_ovpn_paths(f"updated ovpn client {name}", paths)


@ovpn_client_app.command(name="renew")
def ovpn_client_renew(name: str, instance: str | None = None, verbose: int = 1) -> None:
    """Issue a fresh client certificate and refresh ovpn files."""
    try:
        settings = _load_cli_settings()
        _ensure_ovpn_privileges(settings)
        with _manual_cli_lock(settings):
            paths = renew_client(settings, name, instance=instance)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        error_console.print(f"[red]ovpn client renew failed:[/red] {exc}")
        raise SystemExit(1) from exc
    if verbose > 0:
        _print_ovpn_paths(f"renewed ovpn client {name}", paths)


@ovpn_cert_app.command(name="revoke")
def ovpn_cert_revoke(
    server: str | None = None,
    client: str | None = None,
    instance: str | None = None,
    verbose: int = 1,
) -> None:
    """Revoke the latest server or client certificate and refresh CRL."""
    try:
        settings = _load_cli_settings()
        _ensure_ovpn_privileges(settings)
        with _manual_cli_lock(settings):
            path = revoke_cert(settings, server=server, client=client, instance=instance)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        error_console.print(f"[red]ovpn cert revoke failed:[/red] {exc}")
        raise SystemExit(1) from exc
    if verbose > 0:
        role = "server" if server else "client"
        console.print(f"revoked ovpn {role} {server or client}; crl: {path}", markup=False)


@ovpn_crl_app.command(name="renew")
def ovpn_crl_renew(instance: str | None = None, verbose: int = 1) -> None:
    """Refresh the OpenVPN CRL file without revoking a certificate."""
    try:
        settings = _load_cli_settings()
        _ensure_ovpn_privileges(settings)
        with _manual_cli_lock(settings):
            path = renew_crl(settings, instance=instance)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        error_console.print(f"[red]ovpn crl renew failed:[/red] {exc}")
        raise SystemExit(1) from exc
    if verbose > 0:
        console.print(f"renewed ovpn crl: {path}", markup=False)


@web_app.command(name="create-user")
def web_create_user(username: str, password: str | None = None) -> None:
    """Create a DROS Web login user."""
    try:
        settings = _load_cli_settings()
        _ensure_web_auth_privileges(settings)
        store = WebAuthStore(resolve_auth_db_path(settings))
        store.create_user(username, _read_password(password))
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        error_console.print(f"[red]web create-user failed:[/red] {exc}")
        raise SystemExit(1) from exc

    console.print(f"[green]created[/green] web user {username.strip()}")


@web_app.command(name="passwd")
def web_passwd(username: str, password: str | None = None) -> None:
    """Change a DROS Web login password."""
    try:
        settings = _load_cli_settings()
        _ensure_web_auth_privileges(settings)
        store = WebAuthStore(resolve_auth_db_path(settings))
        store.set_password(username, _read_password(password))
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        error_console.print(f"[red]web passwd failed:[/red] {exc}")
        raise SystemExit(1) from exc

    console.print(f"[green]updated[/green] password for web user {username.strip()}")


@app.command
def remove(target: str) -> None:
    """Remove a managed object from the system."""
    _not_ready(f"gw remove {target}")


@app.command
def reload(target: str) -> None:
    """Reload a managed object when supported by its plugin."""
    _not_ready(f"gw reload {target}")


@app.command
def restart(target: str) -> None:
    """Restart a managed object when supported by its plugin."""
    try:
        service = restart_local_service(target, settings_path=_current_settings_path)
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        error_console.print(f"[red]restart failed:[/red] {exc}")
        raise SystemExit(1) from exc

    console.print(f"[green]restarted[/green] {service}")


@app.command
def status(target: str | None = None) -> None:
    """Show object or system status."""
    label = f"gw status {target}" if target else "gw status"
    _not_ready(label)


def _normalize_help(args: list[str]) -> list[str]:
    if not args or args[0] != "help":
        return args
    if len(args) == 1:
        return ["--help"]
    return [*args[1:], "--help"]


def _extract_settings_path(args: list[str]) -> str | None:
    index = 0
    while index < len(args):
        item = args[index]
        if item == "--settings":
            if index + 1 < len(args):
                return args[index + 1]
            return None
        if item.startswith("--settings="):
            return item.split("=", 1)[1]
        index += 1
    return None


def _strip_global_options(args: list[str]) -> list[str]:
    normalized: list[str] = []
    index = 0
    while index < len(args):
        item = args[index]
        if item == "--settings":
            index += 2
            continue
        if item.startswith("--settings="):
            index += 1
            continue
        normalized.append(item)
        index += 1
    return normalized


def _resolve_config_relative_path(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def _load_cli_settings() -> DrosSettings:
    if _current_settings_path is not None:
        return load_settings(_current_settings_path)
    if DEFAULT_SETTINGS_PATH.exists():
        return load_settings(DEFAULT_SETTINGS_PATH)
    raise ValueError(f"default settings file not found: {DEFAULT_SETTINGS_PATH}; pass --settings")


def _try_load_cli_settings_for_logging() -> DrosSettings | None:
    try:
        if _current_settings_path is not None:
            return load_settings(_current_settings_path)
        if DEFAULT_SETTINGS_PATH.exists():
            return load_settings(DEFAULT_SETTINGS_PATH)
    except (OSError, ValueError):
        return None
    return None


@contextmanager
def _manual_cli_lock(settings: DrosSettings):
    try:
        with exclusive_lock(_settings_target_path(settings, settings.paths.run / APPLY_LOCK_PATH), blocking=False):
            yield
    except LockBusyError as exc:
        raise RuntimeError("another manual gw command is already running") from exc


def _ensure_bootstrap_privileges(settings: DrosSettings) -> None:
    if os.geteuid() == 0:
        return
    if settings.sys_root == Path("/") or not path_writable_for_current_user(settings.sys_root):
        reexec_with_sudo([sys.executable, "-m", "dros.cli.main", *_current_raw_args])


def _ensure_path_privileges(path: Path) -> None:
    if os.geteuid() == 0:
        return
    if not path_writable_for_current_user(path):
        reexec_with_sudo([sys.executable, "-m", "dros.cli.main", *_current_raw_args])


def _ensure_web_auth_privileges(settings: DrosSettings) -> None:
    if os.geteuid() == 0:
        return
    auth_db = resolve_auth_db_path(settings)
    if not path_writable_for_current_user(auth_db.parent):
        reexec_with_sudo([sys.executable, "-m", "dros.cli.main", *_current_raw_args])


def _ensure_ovpn_privileges(settings: DrosSettings) -> None:
    if os.geteuid() == 0:
        return
    _ensure_path_privileges(_settings_target_path(settings, settings.paths.containers.parent / "ovpn"))


def _print_ovpn_paths(message: str, paths: list[Path]) -> None:
    console.print(f"{message}:", markup=False)
    for path in paths:
        console.print(f"  - {path}", markup=False)


def _settings_target_path(settings: DrosSettings, path: Path) -> Path:
    if not path.is_absolute() or settings.sys_root == Path("/"):
        return path
    return settings.sys_root / path.relative_to("/")


def _read_password(password: str | None) -> str:
    if password is not None:
        if not password:
            raise ValueError("password cannot be empty")
        return password

    first = getpass.getpass("Password: ")
    if not first:
        raise ValueError("password cannot be empty")
    second = getpass.getpass("Confirm password: ")
    if first != second:
        raise ValueError("passwords do not match")
    return first


def main(argv: Sequence[str] | None = None) -> int:
    global _current_raw_args, _current_settings_path
    raw_args = list(sys.argv[1:] if argv is None else argv)
    _current_raw_args = raw_args
    _current_settings_path = _extract_settings_path(raw_args)
    args = _normalize_help(_strip_global_options(raw_args))
    log_settings = _try_load_cli_settings_for_logging()
    started_at = time.monotonic()
    if log_settings is not None:
        append_invocation_log(log_settings, kind="cli", phase="start", argv=raw_args)
    try:
        app(args)
    except SystemExit as exc:
        exit_code = int(exc.code or 0)
    else:
        exit_code = 0
    if log_settings is not None:
        append_invocation_log(
            log_settings,
            kind="cli",
            phase="finish",
            argv=raw_args,
            exit_code=exit_code,
            duration_ms=int((time.monotonic() - started_at) * 1000),
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
