from __future__ import annotations

import sys
from collections.abc import Sequence

from cyclopts import App
from rich.console import Console

from dros import __version__

console = Console()

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
def bootstrap() -> None:
    """Apply all bootstrap-scoped ConfigObjects."""
    _not_ready("gw bootstrap")


@app.command
def update(target: str | None = None) -> None:
    """Apply configuration to the running system."""
    label = f"gw update {target}" if target else "gw update"
    _not_ready(label)


@config_app.command(name="create")
def config_create(kind: str) -> None:
    """Print or create an example ConfigObject."""
    _not_ready(f"gw config create {kind}")


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
    _not_ready(f"gw restart {target}")


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


def main(argv: Sequence[str] | None = None) -> int:
    args = _normalize_help(_strip_global_options(list(sys.argv[1:] if argv is None else argv)))
    try:
        app(args)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
