from __future__ import annotations

import subprocess
from dataclasses import dataclass

from rich.console import Console

from dros.config_objects import load_config_objects
from dros.executor import CommandRunner, SystemAction, SystemExecutor
from dros.plugins import create_default_registry
from dros.plugins.base import BootstrapContext, PluginRegistry
from dros.settings import DrosSettings


@dataclass(frozen=True)
class BootstrapResult:
    actions: list[SystemAction]


def run_bootstrap(
    settings: DrosSettings,
    *,
    verbose: int = 1,
    console: Console | None = None,
    runner: CommandRunner = subprocess.run,
    installed_packages: set[str] | None = None,
    registry: PluginRegistry | None = None,
) -> BootstrapResult:
    active_registry = registry or create_default_registry()
    executor = SystemExecutor(
        settings,
        verbose=verbose,
        console=console,
        runner=runner,
        installed_packages=installed_packages,
    )
    context = BootstrapContext(
        settings=settings,
        configs=load_config_objects(settings),
        executor=executor,
        registry=active_registry,
    )

    for plugin in active_registry.bootstrap_order():
        if verbose >= 2 and console is not None:
            console.print(f"bootstrap plugin: {plugin.name}")
        plugin.bootstrap(context)

    return BootstrapResult(actions=executor.actions)
