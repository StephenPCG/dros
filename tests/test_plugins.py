from __future__ import annotations

import pytest

from dros.plugins import create_default_registry
from dros.plugins.base import DrosPlugin, PluginRegistrationError, PluginRegistry


def test_default_plugins_are_ordered_by_dependencies() -> None:
    registry = create_default_registry()

    names = [plugin.name for plugin in registry.bootstrap_order()]

    assert names.index("system.mirror") < names.index("network.core")
    assert names.index("system.mirror") < names.index("system.utilities")
    assert names.index("system.mirror") < names.index("docker.core")
    assert names.index("network.core") < names.index("network.interfaces")
    assert registry.plugin_for_kind("SystemNetworkConfig").name == "network.core"
    assert registry.plugin_for_kind("SystemMirrorConfig").name == "system.mirror"
    assert registry.plugin_for_kind("DevGroup").name == "network.interfaces"
    assert registry.plugin_for_kind("Interface").name == "network.interfaces"


def test_registry_rejects_duplicate_package_owners() -> None:
    registry = PluginRegistry()
    registry.register(DrosPlugin(name="one", packages=frozenset({"curl"})))

    with pytest.raises(PluginRegistrationError, match="curl"):
        registry.register(DrosPlugin(name="two", packages=frozenset({"curl"})))


def test_registry_rejects_duplicate_managed_file_owners() -> None:
    registry = PluginRegistry()
    registry.register(DrosPlugin(name="one", managed_files=frozenset({"/etc/hostname"})))

    with pytest.raises(PluginRegistrationError, match="/etc/hostname"):
        registry.register(DrosPlugin(name="two", managed_files=frozenset({"/etc/hostname"})))
