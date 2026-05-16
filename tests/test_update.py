from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from dros.settings import DrosPaths, DrosSettings
from dros.update import UpdateValidationError, run_update


def _console(output: StringIO) -> Console:
    return Console(file=output, force_terminal=False, color_system=None, width=100)


def _settings(tmp_path: Path) -> DrosSettings:
    return DrosSettings(
        sysRoot=tmp_path / "sysroot",
        paths=DrosPaths(configs=tmp_path / "configs"),
    )


def test_update_interface_writes_ifupdown_bridge_with_devgroups(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "network.yaml").write_text(
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
  address: 10.0.0.1/24
  gateway: 10.0.0.254
  extra_addresses:
    - 10.0.0.2/24
    - fd00::1/64
  ports:
    - eth1
    - eth2
  vlan_aware: true
  devgroup: lan
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="iface/br0", console=_console(StringIO()))

    content = (
        settings.sys_root / "etc/network/interfaces.d/dros-br0.cfg"
    ).read_text(encoding="utf-8")
    assert "auto br0\n" in content
    assert "iface br0 inet static\n" in content
    assert "  address 10.0.0.1/24\n" in content
    assert "  gateway 10.0.0.254\n" in content
    assert "  up ip addr add 10.0.0.2/24 dev $IFACE\n" in content
    assert "  up ip addr add fd00::1/64 dev $IFACE\n" in content
    assert "  bridge_ports eth1 eth2\n" in content
    assert "  bridge_fd 0\n" in content
    assert "  bridge_vlan_aware yes\n" in content
    assert "  post-up ip link set dev br0 group 2\n" in content


def test_update_interface_writes_dhcp_eth_and_vlan(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "network.yaml").write_text(
        """
kind: DevGroup
metadata:
  name: wan
spec:
  id: 1
---
kind: Interface
metadata:
  name: eth0
spec:
  type: eth
  dhcp: true
  devGroup: wan
---
kind: Interface
metadata:
  name: br0
spec:
  type: bridge
---
kind: Interface
metadata:
  name: br0.10
spec:
  type: vlan
  parent: br0
  id: 10
  address: 10.10.0.1/24
""".lstrip(),
        encoding="utf-8",
    )

    result = run_update(settings, target="ifaces", console=_console(StringIO()))

    eth = (settings.sys_root / "etc/network/interfaces.d/dros-eth0.cfg").read_text(
        encoding="utf-8"
    )
    vlan = (settings.sys_root / "etc/network/interfaces.d/dros-br0.10.cfg").read_text(
        encoding="utf-8"
    )
    assert "iface eth0 inet dhcp\n" in eth
    assert "  post-up ip link set dev eth0 group 1\n" in eth
    assert "iface br0.10 inet static\n" in vlan
    assert "  pre-up ip link show dev br0" in vlan
    assert "  pre-up ip link show dev $IFACE" in vlan
    assert " type vlan id 10\n" in vlan
    assert "  post-down ip link del dev $IFACE || true\n" in vlan
    assert any(action.command == ["ifup", "eth0"] for action in result.actions)
    assert any(action.command == ["ifup", "br0.10"] for action in result.actions)


def test_update_rejects_interface_with_missing_devgroup(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "iface.yaml").write_text(
        """
kind: Interface
metadata:
  name: br0
spec:
  type: bridge
  devgroup: lan
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="DevGroup/lan"):
        run_update(settings, target="iface/br0", console=_console(StringIO()))

    assert not (settings.sys_root / "etc/network/interfaces.d/dros-br0.cfg").exists()


def test_update_collects_all_config_errors_before_writing_files(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "network.yaml").write_text(
        """
kind: Interface
metadata:
  name: ok
spec:
  type: eth
---
kind: Interface
metadata:
  name: vlan-bad-id
spec:
  type: vlan
  parent: ok
  id: 4095
---
kind: Interface
metadata:
  name: vlan-missing-parent
spec:
  type: vlan
  parent: missing0
  id: 10
---
kind: Interface
metadata:
  name: br-missing-group
spec:
  type: bridge
  devgroup: lan
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(UpdateValidationError) as exc_info:
        run_update(settings, target="ifaces", console=_console(StringIO()))

    message = str(exc_info.value)
    assert "Interface/vlan-bad-id" in message
    assert "vlan id must be between 1 and 4094" in message
    assert "Interface/vlan-missing-parent" in message
    assert "parent Interface/missing0 is not defined" in message
    assert "Interface/br-missing-group" in message
    assert "undefined DevGroup/lan" in message
    assert not (settings.sys_root / "etc/network/interfaces.d/dros-ok.cfg").exists()


def test_update_rejects_vlan_without_parent_config_object(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "network.yaml").write_text(
        """
kind: Interface
metadata:
  name: br0.10
spec:
  type: vlan
  parent: br0
  id: 10
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(UpdateValidationError, match="parent Interface/br0 is not defined"):
        run_update(settings, target="iface/br0.10", console=_console(StringIO()))


def test_update_accepts_vlan_id_boundaries(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "network.yaml").write_text(
        """
kind: Interface
metadata:
  name: br0
spec:
  type: bridge
---
kind: Interface
metadata:
  name: br0.1
spec:
  type: vlan
  parent: br0
  id: 1
---
kind: Interface
metadata:
  name: br0.4094
spec:
  type: vlan
  parent: br0
  id: 4094
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="ifaces", console=_console(StringIO()))

    assert (settings.sys_root / "etc/network/interfaces.d/dros-br0.1.cfg").exists()
    assert (settings.sys_root / "etc/network/interfaces.d/dros-br0.4094.cfg").exists()


def test_update_filters_target_aliases(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "network.yaml").write_text(
        """
kind: Interface
metadata:
  name: eth0
spec:
  type: eth
---
kind: Interface
metadata:
  name: eth1
spec:
  type: eth
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="interface/eth1", console=_console(StringIO()))

    assert not (settings.sys_root / "etc/network/interfaces.d/dros-eth0.cfg").exists()
    assert (settings.sys_root / "etc/network/interfaces.d/dros-eth1.cfg").exists()


def test_update_devgroup_target_is_valid_but_writes_no_files(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "devgroup.yaml").write_text(
        """
kind: DevGroup
metadata:
  name: lan
spec:
  id: 2
""".lstrip(),
        encoding="utf-8",
    )

    result = run_update(settings, target="devgroups", console=_console(StringIO()))

    assert not (settings.sys_root / "etc").exists()
    assert result.actions == []
