from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from dros.config_objects import ConfigStore
from dros.executor import SystemExecutor
from dros.settings import DrosSettings


class PluginRegistrationError(RuntimeError):
    pass


@dataclass
class BootstrapContext:
    settings: DrosSettings
    configs: ConfigStore
    executor: SystemExecutor
    registry: PluginRegistry


BootstrapHook = Callable[[BootstrapContext], None]


@dataclass(frozen=True)
class DrosPlugin:
    name: str
    depends_on: tuple[str, ...] = ()
    config_kinds: frozenset[str] = frozenset()
    packages: frozenset[str] = frozenset()
    managed_files: frozenset[str] = frozenset()
    bootstrap_hook: BootstrapHook | None = None
    event_hooks: frozenset[str] = frozenset()

    def bootstrap(self, context: BootstrapContext) -> None:
        if self.bootstrap_hook is not None:
            self.bootstrap_hook(context)


@dataclass
class PluginRegistry:
    _plugins: dict[str, DrosPlugin] = field(default_factory=dict)
    _package_owners: dict[str, str] = field(default_factory=dict)
    _file_owners: dict[str, str] = field(default_factory=dict)
    _kind_owners: dict[str, str] = field(default_factory=dict)

    def register(self, plugin: DrosPlugin) -> None:
        if plugin.name in self._plugins:
            raise PluginRegistrationError(f"plugin already registered: {plugin.name}")

        for package in plugin.packages:
            owner = self._package_owners.get(package)
            if owner is not None:
                raise PluginRegistrationError(
                    f"system package {package} is owned by both {owner} and {plugin.name}"
                )

        for managed_file in plugin.managed_files:
            owner = self._file_owners.get(managed_file)
            if owner is not None:
                raise PluginRegistrationError(
                    f"managed file {managed_file} is owned by both {owner} and {plugin.name}"
                )

        for kind in plugin.config_kinds:
            owner = self._kind_owners.get(kind)
            if owner is not None:
                raise PluginRegistrationError(
                    f"ConfigObject kind {kind} is owned by both {owner} and {plugin.name}"
                )

        self._plugins[plugin.name] = plugin
        self._package_owners.update({package: plugin.name for package in plugin.packages})
        self._file_owners.update({path: plugin.name for path in plugin.managed_files})
        self._kind_owners.update({kind: plugin.name for kind in plugin.config_kinds})

    def plugin_for_kind(self, kind: str) -> DrosPlugin:
        owner = self._kind_owners.get(kind)
        if owner is None:
            raise KeyError(f"no plugin registered for ConfigObject kind: {kind}")
        return self._plugins[owner]

    def bootstrap_order(self) -> list[DrosPlugin]:
        ordered: list[DrosPlugin] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            if name in visiting:
                raise PluginRegistrationError(f"plugin dependency cycle includes {name}")
            plugin = self._plugins.get(name)
            if plugin is None:
                raise PluginRegistrationError(f"plugin dependency is not registered: {name}")

            visiting.add(name)
            for dependency in plugin.depends_on:
                visit(dependency)
            visiting.remove(name)
            visited.add(name)
            ordered.append(plugin)

        for name in self._plugins:
            visit(name)
        return ordered

    def owned_packages(self) -> set[str]:
        return set(self._package_owners)

    def managed_files(self) -> set[str]:
        return set(self._file_owners)
