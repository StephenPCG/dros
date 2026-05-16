from __future__ import annotations

from pathlib import Path

from dros.config_objects import (
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
