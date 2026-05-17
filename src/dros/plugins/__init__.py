"""Plugin package for DROS-managed domains."""
from __future__ import annotations

from dros.plugins.base import PluginRegistry

from . import (
    collectd,
    dnsmasq,
    docker_core,
    docker_resources,
    ip_lists,
    network_core,
    network_firewall,
    network_interfaces,
    network_routing,
    system_mirror,
    system_utilities,
)


def create_default_registry() -> PluginRegistry:
    registry = PluginRegistry()
    registry.register(system_mirror.create_plugin())
    registry.register(network_core.create_plugin())
    registry.register(network_interfaces.create_plugin())
    registry.register(network_routing.create_plugin())
    registry.register(network_firewall.create_plugin())
    registry.register(dnsmasq.create_plugin())
    registry.register(ip_lists.create_plugin())
    registry.register(system_utilities.create_plugin())
    registry.register(collectd.create_plugin())
    registry.register(docker_core.create_plugin())
    registry.register(docker_resources.create_plugin())
    return registry


__all__ = ["create_default_registry"]
