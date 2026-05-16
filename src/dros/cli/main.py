from __future__ import annotations

import os
import subprocess
import sys
import getpass
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from cyclopts import App
from rich.console import Console

from dros import __version__
from dros.bootstrap import run_bootstrap
from dros.cli.privilege import path_writable_for_current_user, reexec_with_sudo
from dros.cli.services import restart_local_service
from dros.config_catalog import render_config_object_example
from dros.events import enqueue_event, process_event
from dros.settings import DEFAULT_SETTINGS_PATH, DrosSettings, load_settings
from dros.update import UpdateValidationError, run_update
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


def _not_ready(command: str) -> None:
    console.print(f"[yellow]{command}[/yellow] is reserved for the next implementation phase.")


@app.command
def bootstrap(verbose: int = 1) -> None:
    """Apply bootstrap hooks to the system."""
    if verbose not in {0, 1, 2}:
        error_console.print("[red]bootstrap failed:[/red] --verbose must be 0, 1, or 2")
        raise SystemExit(2)

    try:
        settings = _load_cli_settings()
        _ensure_bootstrap_privileges(settings)
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


def _load_cli_settings() -> DrosSettings:
    if _current_settings_path is not None:
        return load_settings(_current_settings_path)
    if DEFAULT_SETTINGS_PATH.exists():
        return load_settings(DEFAULT_SETTINGS_PATH)
    raise ValueError(f"default settings file not found: {DEFAULT_SETTINGS_PATH}; pass --settings")


def _ensure_bootstrap_privileges(settings: DrosSettings) -> None:
    if os.geteuid() == 0:
        return
    if settings.sys_root == Path("/") or not path_writable_for_current_user(settings.sys_root):
        reexec_with_sudo([sys.executable, "-m", "dros.cli.main", *_current_raw_args])


def _ensure_web_auth_privileges(settings: DrosSettings) -> None:
    if os.geteuid() == 0:
        return
    auth_db = resolve_auth_db_path(settings)
    if not path_writable_for_current_user(auth_db.parent):
        reexec_with_sudo([sys.executable, "-m", "dros.cli.main", *_current_raw_args])


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
    try:
        app(args)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
