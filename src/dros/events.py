from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from dros.config_objects import load_config_objects
from dros.executor import CommandRunner, SystemAction, SystemExecutor
from dros.invocation_log import append_error_log, append_invocation_log
from dros.locks import APPLY_LOCK_PATH, LockBusyError, exclusive_lock
from dros.plugins import create_default_registry
from dros.plugins.base import PluginRegistry, UpdateContext
from dros.plugins.docker_resources import handle_event as handle_docker_event
from dros.plugins.network_interfaces import handle_event as handle_interface_event
from dros.plugins.network_ipv6pd import handle_event as handle_ipv6pd_event
from dros.plugins.network_routing import handle_event as handle_routing_event
from dros.plugins.network_xfrm import handle_event as handle_xfrm_event
from dros.settings import DrosSettings

EVENTS_PATH = "events.jsonl"
EVENTS_APPEND_LOCK_PATH = "locks/events-append.lock"
EVENTS_PROCESS_LOCK_PATH = "locks/events-process.lock"


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
    with exclusive_lock(settings.paths.run / EVENTS_APPEND_LOCK_PATH):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{json.dumps(payload, sort_keys=True)}\n")
    append_invocation_log(settings, kind="event.enqueue", event=event, iface=iface)
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
    handle_xfrm_event(context, event, iface)
    handle_docker_event(context, event, iface)
    handle_ipv6pd_event(context, event, iface)
    if event in {"route-refresh", "iface-up", "iface-down", "ppp-up", "ppp-down"}:
        handle_routing_event(context, event, iface)
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
    try:
        with exclusive_lock(settings.paths.run / EVENTS_PROCESS_LOCK_PATH, blocking=False):
            try:
                apply_lock = exclusive_lock(settings.paths.run / APPLY_LOCK_PATH, blocking=False)
                with apply_lock:
                    return _process_event_queue_locked(
                        settings,
                        verbose=verbose,
                        console=console,
                        runner=runner,
                        registry=registry,
                    )
            except LockBusyError:
                return 0
    except LockBusyError:
        return 0


def _process_event_queue_locked(
    settings: DrosSettings,
    *,
    verbose: int,
    console: Console | None,
    runner: CommandRunner,
    registry: PluginRegistry | None,
) -> int:
    batch = _read_event_batch(settings)
    lines = batch.splitlines()
    processed = 0
    for payload in _coalesce_event_payloads(lines):
        event = payload["event"]
        iface = payload["iface"]
        append_invocation_log(settings, kind="event.process", phase="start", event=event, iface=iface)
        processed += 1
        try:
            process_event(
                settings,
                event,
                iface=iface,
                verbose=verbose,
                console=console,
                runner=runner,
                registry=registry,
            )
            append_invocation_log(settings, kind="event.process", phase="finish", event=event, iface=iface)
        except Exception as exc:
            append_invocation_log(
                settings,
                kind="event.process",
                phase="error",
                event=event,
                iface=iface,
                message=str(exc),
            )
            append_error_log(
                settings,
                channel="event",
                error_type=type(exc).__name__,
                event=event,
                iface=iface,
                message=str(exc),
            )
            if console is not None:
                console.print(f"[red]event failed:[/red] {exc}")
    _delete_event_batch(settings, batch)
    return processed


def _read_event_batch(settings: DrosSettings) -> str:
    path = settings.paths.run / EVENTS_PATH
    with exclusive_lock(settings.paths.run / EVENTS_APPEND_LOCK_PATH):
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""


def _delete_event_batch(settings: DrosSettings, batch: str) -> None:
    if not batch:
        return
    path = settings.paths.run / EVENTS_PATH
    with exclusive_lock(settings.paths.run / EVENTS_APPEND_LOCK_PATH):
        try:
            current = path.read_text(encoding="utf-8")
        except OSError:
            return
        if current.startswith(batch):
            path.write_text(current[len(batch) :], encoding="utf-8")
            return
        # This should only be reached if an external editor touched the queue.
        current_lines = current.splitlines()
        batch_line_count = len(batch.splitlines())
        remaining = current_lines[batch_line_count:]
        path.write_text("".join(f"{line}\n" for line in remaining), encoding="utf-8")


def _coalesce_event_payloads(lines: list[str]) -> list[dict[str, str | None]]:
    result: dict[tuple[str, str | None], dict[str, str | None]] = {}
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = payload.get("event")
        iface = payload.get("iface")
        if not isinstance(event, str):
            continue
        normalized_iface = iface if isinstance(iface, str) else None
        key = (event, normalized_iface)
        if key in result:
            del result[key]
        result[key] = {"event": event, "iface": normalized_iface}
    return list(result.values())
