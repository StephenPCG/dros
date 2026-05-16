"""Plugin package for DROS-managed domains."""
from __future__ import annotations

from dros.plugins.base import PluginRegistry

from . import docker_core, network_core, network_interfaces, system_mirror, system_utilities


def create_default_registry() -> PluginRegistry:
    registry = PluginRegistry()
    registry.register(system_mirror.create_plugin())
    registry.register(network_core.create_plugin())
    registry.register(network_interfaces.create_plugin())
    registry.register(system_utilities.create_plugin())
    registry.register(docker_core.create_plugin())
    return registry


__all__ = ["create_default_registry"]
