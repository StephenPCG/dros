from __future__ import annotations

from pathlib import Path

from dros.config_objects import (
    DevGroupConfig,
    InterfaceConfig,
    SystemMirrorConfig,
    SystemNetworkConfig,
    load_config_objects,
)
from dros.settings import DrosPaths, DrosSettings


def test_config_objects_later_directories_override_earlier_objects(tmp_path: Path) -> None:
    base = tmp_path / "base"
    site = tmp_path / "site"
    base.mkdir()
    site.mkdir()

    (base / "network.yaml").write_text(
        """
kind: SystemNetworkConfig
metadata:
  name: default
spec:
  hostname: base-gw
  domain: base.lan
""".lstrip(),
        encoding="utf-8",
    )
    (site / "network.yaml").write_text(
        """
kind: SystemNetworkConfig
metadata:
  name: default
spec:
  domain: site.lan
""".lstrip(),
        encoding="utf-8",
    )

    store = load_config_objects(DrosSettings(paths=DrosPaths(configs=[base, site])))

    assert store.require("SystemNetworkConfig", "default").spec == {"domain": "site.lan"}
    assert store.resolve("SystemNetworkConfig", SystemNetworkConfig).hostname == "gateway"
    assert store.resolve("SystemNetworkConfig", SystemNetworkConfig).domain == "site.lan"


def test_config_object_defaults_are_code_owned_when_object_is_missing(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    configs.mkdir()

    store = load_config_objects(DrosSettings(paths=DrosPaths(configs=configs)))

    assert store.resolve("SystemNetworkConfig", SystemNetworkConfig) == SystemNetworkConfig()
    assert store.resolve("SystemMirrorConfig", SystemMirrorConfig) == SystemMirrorConfig()


def test_single_yaml_file_can_contain_multiple_config_object_documents(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "network.yaml").write_text(
        """
apiVersion: dros/v1alpha1
kind: DevGroup
metadata:
  name: lan
spec:
  id: 2
---
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: br0
spec:
  type: bridge
  devGroup: lan
""".lstrip(),
        encoding="utf-8",
    )

    store = load_config_objects(DrosSettings(paths=DrosPaths(configs=configs)))

    assert store.require("DevGroup", "lan").source == configs / "network.yaml"
    assert store.require("Interface", "br0").source == configs / "network.yaml"


def test_singleton_config_object_resolves_unique_non_default_name(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "SystemNetworkConfig.yaml").write_text(
        """
apiVersion: dros/v1alpha1
kind: SystemNetworkConfig
metadata:
  name: system
spec:
  hostname: gateway
  domain: test.init2.me
""".lstrip(),
        encoding="utf-8",
    )

    store = load_config_objects(DrosSettings(paths=DrosPaths(configs=configs)))

    network = store.resolve("SystemNetworkConfig", SystemNetworkConfig)
    assert network.hostname == "gateway"
    assert network.domain == "test.init2.me"


def test_disabled_config_object_is_ignored(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "mirror.yaml").write_text(
        """
kind: SystemMirrorConfig
metadata:
  name: default
  disabled: true
spec:
  aptMirror: https://example.invalid/debian
""".lstrip(),
        encoding="utf-8",
    )

    store = load_config_objects(DrosSettings(paths=DrosPaths(configs=configs)))

    assert store.get("SystemMirrorConfig", "default") is None
    assert store.resolve("SystemMirrorConfig", SystemMirrorConfig).apt_mirror == (
        "https://mirrors.ustc.edu.cn/debian"
    )


def test_devgroup_and_interface_models_support_expected_field_aliases(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "network.yaml").write_text(
        """
kind: DevGroup
metadata:
  name: lan
spec:
  id: 2
---
kind: Interface
metadata:
  name: br0
spec:
  type: bridge
  devGroup: lan
  extraAddresses:
    - 10.0.0.2/24
  vlanAware: true
""".lstrip(),
        encoding="utf-8",
    )

    store = load_config_objects(DrosSettings(paths=DrosPaths(configs=configs)))

    assert store.resolve_object(store.require("DevGroup", "lan"), DevGroupConfig).id == 2
    interface = store.resolve_object(store.require("Interface", "br0"), InterfaceConfig)
    assert interface.devgroup == "lan"
    assert interface.extra_addresses == ["10.0.0.2/24"]
    assert interface.vlan_aware is True
