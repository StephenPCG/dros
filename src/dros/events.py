from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from dros.config_objects import load_config_objects
from dros.executor import CommandRunner, SystemAction, SystemExecutor
from dros.plugins import create_default_registry
from dros.plugins.base import PluginRegistry, UpdateContext
from dros.plugins.network_interfaces import handle_event as handle_interface_event
from dros.settings import DrosSettings

EVENTS_PATH = "events.jsonl"


@dataclass(frozen=True)
class HookResult:
    actions: list[SystemAction]


def enqueue_event(settings: DrosSettings, event: str, iface: str | None = None) -> Path:
    path = settings.paths.run / EVENTS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "event": event,
        "iface": iface,
        "createdAt": time.time(),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{json.dumps(payload, sort_keys=True)}\n")
    return path


def process_event(
    settings: DrosSettings,
    event: str,
    *,
    iface: str | None = None,
    verbose: int = 0,
    console: Console | None = None,
    runner: CommandRunner = subprocess.run,
    registry: PluginRegistry | None = None,
) -> HookResult:
    active_registry = registry or create_default_registry()
    configs = load_config_objects(settings)
    executor = SystemExecutor(settings, verbose=verbose, console=console, runner=runner)
    context = UpdateContext(
        settings=settings,
        configs=configs,
        executor=executor,
        registry=active_registry,
    )
    handle_interface_event(context, event, iface)
    return HookResult(actions=executor.actions)


def process_event_queue(
    settings: DrosSettings,
    *,
    offset: int = 0,
    verbose: int = 0,
    console: Console | None = None,
    runner: CommandRunner = subprocess.run,
    registry: PluginRegistry | None = None,
) -> int:
    path = settings.paths.run / EVENTS_PATH
    if not path.exists():
        return 0
    if path.stat().st_size < offset:
        offset = 0

    with path.open("r", encoding="utf-8") as handle:
        handle.seek(offset)
        lines = handle.readlines()
        next_offset = handle.tell()

    for line in lines:
        try:
            payload = json.loads(line)
            event = payload.get("event")
            iface = payload.get("iface")
            if not isinstance(event, str):
                continue
            process_event(
                settings,
                event,
                iface=iface if isinstance(iface, str) else None,
                verbose=verbose,
                console=console,
                runner=runner,
                registry=registry,
            )
        except Exception as exc:
            if console is not None:
                console.print(f"[red]event failed:[/red] {exc}")
    return next_offset
