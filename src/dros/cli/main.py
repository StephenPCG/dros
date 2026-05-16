from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from cyclopts import App
from rich.console import Console

from dros import __version__
from dros.bootstrap import run_bootstrap
from dros.cli.privilege import path_writable_for_current_user, reexec_with_sudo
from dros.cli.services import restart_local_service
from dros.config_catalog import render_config_object_example
from dros.settings import DEFAULT_SETTINGS_PATH, DrosSettings, load_settings

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
def update(target: str | None = None) -> None:
    """Apply configuration to the running system."""
    label = f"gw update {target}" if target else "gw update"
    _not_ready(label)


@config_app.command(name="create")
def config_create(kind: str) -> None:
    """Print or create an example ConfigObject."""
    try:
        console.print(render_config_object_example(kind), markup=False)
    except ValueError as exc:
        error_console.print(f"[red]config create failed:[/red] {exc}")
        raise SystemExit(1) from exc


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
