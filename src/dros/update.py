from __future__ import annotations

import subprocess
from dataclasses import dataclass

from rich.console import Console

from dros.config_objects import ConfigObject, load_config_objects
from dros.executor import CommandRunner, SystemAction, SystemExecutor
from dros.kind_aliases import resolve_kind_alias
from dros.plugins import create_default_registry
from dros.plugins.base import PluginRegistry, UpdateContext
from dros.settings import DrosSettings


class UpdateValidationError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


@dataclass(frozen=True)
class UpdateResult:
    actions: list[SystemAction]


@dataclass(frozen=True)
class UpdateTarget:
    kind: str | None = None
    name: str | None = None


def run_update(
    settings: DrosSettings,
    *,
    target: str | None = None,
    verbose: int = 1,
    console: Console | None = None,
    runner: CommandRunner = subprocess.run,
    registry: PluginRegistry | None = None,
) -> UpdateResult:
    active_registry = registry or create_default_registry()
    configs = load_config_objects(settings)
    selected = _select_objects(configs.objects(), _parse_target(target))
    executor = SystemExecutor(settings, verbose=verbose, console=console, runner=runner)
    context = UpdateContext(
        settings=settings,
        configs=configs,
        executor=executor,
        registry=active_registry,
    )

    objects_by_plugin: dict[str, list[ConfigObject]] = {}
    validation_errors: list[str] = []
    for obj in selected:
        try:
            plugin = active_registry.plugin_for_kind(obj.kind)
        except KeyError:
            validation_errors.append(f"{obj.kind}/{obj.name}: no plugin registered for kind")
            continue
        objects_by_plugin.setdefault(plugin.name, []).append(obj)

    for plugin in active_registry.bootstrap_order():
        objects = objects_by_plugin.get(plugin.name)
        if objects:
            validation_errors.extend(plugin.validate(context, objects))

    if validation_errors:
        raise UpdateValidationError(validation_errors)

    for plugin in active_registry.bootstrap_order():
        objects = objects_by_plugin.get(plugin.name)
        if not objects:
            continue
        if verbose >= 2 and console is not None:
            console.print(f"update plugin: {plugin.name}")
        plugin.update(context, objects)

    return UpdateResult(actions=executor.actions)


def _parse_target(target: str | None) -> UpdateTarget:
    if target is None or not target.strip():
        return UpdateTarget()
    parts = target.split("/", 1)
    kind = resolve_kind_alias(parts[0])
    if len(parts) == 1:
        return UpdateTarget(kind=kind)
    name = parts[1].strip()
    if not name:
        raise ValueError(f"invalid update target: {target}")
    return UpdateTarget(kind=kind, name=name)


def _select_objects(objects: list[ConfigObject], target: UpdateTarget) -> list[ConfigObject]:
    if target.kind is None:
        return objects
    matches = [obj for obj in objects if obj.kind == target.kind]
    if target.name is None:
        return matches
    selected = [obj for obj in matches if obj.name == target.name]
    if not selected:
        raise ValueError(f"ConfigObject not found: {target.kind}/{target.name}")
    return selected
