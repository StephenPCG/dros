from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from dros.events import process_event
from dros.settings import DrosPaths, DrosSettings
from dros.update import UpdateValidationError, run_update


def _console(output: StringIO) -> Console:
    return Console(file=output, force_terminal=False, color_system=None, width=100)


def _settings(tmp_path: Path) -> DrosSettings:
    return DrosSettings(
        sysRoot=tmp_path / "sysroot",
        paths=DrosPaths(configs=tmp_path / "configs", run=tmp_path / "run"),
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


def test_update_target_only_validates_selected_objects(tmp_path: Path) -> None:
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
  name: bad-vlan
spec:
  type: vlan
  parent: eth0
  id: 5000
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="iface/eth0", console=_console(StringIO()))

    assert (settings.sys_root / "etc/network/interfaces.d/dros-eth0.cfg").exists()
    assert not (settings.paths.run / "configs").exists()


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


def test_update_does_not_mirror_config_objects_under_run(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "network.yaml").write_text(
        """
kind: Interface
metadata:
  name: eth0
spec:
  type: eth
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, console=_console(StringIO()))

    assert not (settings.paths.run / "configs").exists()


def test_update_interface_writes_loopback_extra_addresses(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "loopback.yaml").write_text(
        """
kind: Interface
metadata:
  name: lo
spec:
  type: loopback
  extraAddresses:
    - 10.255.0.1/32
    - fd00::1/128
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="iface/lo", console=_console(StringIO()))

    content = (
        settings.sys_root / "etc/network/interfaces.d/dros-lo.cfg"
    ).read_text(encoding="utf-8")
    assert "iface lo inet loopback\n" in content
    assert "  up ip addr add 10.255.0.1/32 dev $IFACE\n" in content
    assert "  up ip addr add fd00::1/128 dev $IFACE\n" in content


def test_update_interface_creates_docker_bridge_networks(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "docker.yaml").write_text(
        """
kind: DevGroup
metadata:
  name: docker
spec:
  id: 20
---
kind: Interface
metadata:
  name: docker0
spec:
  type: docker
  devGroup: docker
---
kind: Interface
metadata:
  name: br-app
spec:
  type: docker
  subnet: 172.30.0.0/24
  devGroup: docker
""".lstrip(),
        encoding="utf-8",
    )

    result = run_update(settings, target="ifaces", console=_console(StringIO()))

    commands = [" ".join(action.command or []) for action in result.actions]
    assert any("docker network create" in command for command in commands)
    assert any("--subnet" in command and "172.30.0.0/24" in command for command in commands)
    assert any(
        "com.docker.network.bridge.name" in command and "br-app" in command
        for command in commands
    )
    assert not any("docker network create" in command and "docker0" in command for command in commands)
    assert any("ip link set dev br-app group 20" in command for command in commands)
    assert any("ip link set dev docker0 group 20" in command for command in commands)


def test_hook_events_read_current_config_objects(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    config_path = settings.paths.configs / "docker.yaml"
    config_path.write_text(
        """
kind: Interface
metadata:
  name: br-app
spec:
  type: docker
  subnet: 172.30.0.0/24
""".lstrip(),
        encoding="utf-8",
    )

    result = process_event(settings, "docker-start", console=_console(StringIO()))

    commands = [" ".join(action.command or []) for action in result.actions]
    assert any("172.30.0.0/24" in command for command in commands)


def test_update_interface_writes_gre_pppoe_and_wireguard_files(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "tunnels.yaml").write_text(
        """
kind: Interface
metadata:
  name: gre-office
spec:
  type: gre
  address: 10.10.10.1/30
  localPublicIp: 198.51.100.1
  remotePublicIp: 203.0.113.1
  ttl: 255
---
kind: Interface
metadata:
  name: pppoe-wan
spec:
  type: pppoe
  device: eth0.35
  user: home@example.net
  password: secret
---
kind: Interface
metadata:
  name: wg0
spec:
  type: wireguard
  address: 10.20.0.1/24
  privateKey: private-key
  listenPort: 51820
  peers:
    - publicKey: peer-key
      allowedIPs:
        - 10.20.0.2/32
        - fd00:20::2/128
      endpoint: peer.example.net:51820
      persistentKeepalive: 25
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="ifaces", console=_console(StringIO()))

    gre = (
        settings.sys_root / "etc/network/interfaces.d/dros-gre-office.cfg"
    ).read_text(encoding="utf-8")
    assert "iface gre-office inet static\n" in gre
    assert "pre-up ip tunnel add $IFACE mode gre" in gre
    assert "local 198.51.100.1 remote 203.0.113.1 ttl 255" in gre
    assert "post-down ip tunnel del $IFACE || true\n" in gre

    pppoe_iface = (
        settings.sys_root / "etc/network/interfaces.d/dros-pppoe-wan.cfg"
    ).read_text(encoding="utf-8")
    pppoe_peer = (settings.sys_root / "etc/ppp/peers/pppoe-wan").read_text(
        encoding="utf-8"
    )
    assert "iface pppoe-wan inet ppp\n" in pppoe_iface
    assert "  provider pppoe-wan\n" in pppoe_iface
    assert "plugin rp-pppoe.so eth0.35\n" in pppoe_peer
    assert 'user "home@example.net"\n' in pppoe_peer
    assert 'password "secret"\n' in pppoe_peer

    wg_iface = (
        settings.sys_root / "etc/network/interfaces.d/dros-wg0.cfg"
    ).read_text(encoding="utf-8")
    wg_conf = (settings.sys_root / "etc/wireguard/wg0.conf").read_text(
        encoding="utf-8"
    )
    assert "iface wg0 inet static\n" in wg_iface
    assert "pre-up ip link add dev $IFACE type wireguard\n" in wg_iface
    assert "pre-up wg setconf $IFACE /etc/wireguard/wg0.conf\n" in wg_iface
    assert "PrivateKey = private-key\n" in wg_conf
    assert "ListenPort = 51820\n" in wg_conf
    assert "PublicKey = peer-key\n" in wg_conf
    assert "AllowedIPs = 10.20.0.2/32, fd00:20::2/128\n" in wg_conf


def test_update_interface_writes_openvpn_files(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "openvpn.yaml").write_text(
        """
kind: DevGroup
metadata:
  name: vpn
spec:
  id: 9
---
kind: Interface
metadata:
  name: ovpn-lab
spec:
  type: openvpn
  config: |
    dev-type tun
    proto udp
    remote vpn.example.net 1194
  crlFile: /etc/openvpn/pki/crl.pem
  up: echo openvpn-up
  devGroup: vpn
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="iface/ovpn-lab", console=_console(StringIO()))

    iface = (
        settings.sys_root / "etc/network/interfaces.d/dros-ovpn-lab.cfg"
    ).read_text(encoding="utf-8")
    ovpn = (settings.sys_root / "etc/dros/openvpn/ovpn-lab.ovpn").read_text(
        encoding="utf-8"
    )
    up = (settings.sys_root / "etc/dros/openvpn/ovpn-lab.up").read_text(
        encoding="utf-8"
    )

    assert "iface ovpn-lab inet manual\n" in iface
    assert "/usr/lib/dros/openvpn-iface start ovpn-lab" in iface
    assert "/etc/dros/openvpn/ovpn-lab.ovpn" in iface
    assert str(settings.paths.run / "openvpn.ovpn-lab.pid") in iface
    assert "/etc/dros/openvpn/ovpn-lab.up" in iface
    assert "/etc/openvpn/pki/crl.pem" in iface
    assert str(settings.paths.logs / "openvpn-ovpn-lab.log") in iface
    assert "/usr/lib/dros/openvpn-iface stop ovpn-lab" in iface
    assert "dev-type tun\n" in ovpn
    assert "remote vpn.example.net 1194\n" in ovpn
    assert 'IFACE="ovpn-lab"\n' in up
    assert 'ip link set dev "$IFACE" group 9\n' in up
    assert "sh -c 'echo openvpn-up'\n" in up


def test_update_interface_openvpn_can_read_relative_config_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "client.conf").write_text(
        "client\nremote vpn.example.net 1194\n",
        encoding="utf-8",
    )
    (settings.paths.configs / "openvpn.yaml").write_text(
        """
kind: Interface
metadata:
  name: ovpn-client
spec:
  type: openvpn
  configFile: client.conf
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="iface/ovpn-client", console=_console(StringIO()))

    ovpn = (settings.sys_root / "etc/dros/openvpn/ovpn-client.ovpn").read_text(
        encoding="utf-8"
    )
    assert ovpn == "client\nremote vpn.example.net 1194\n"


def test_update_rejects_openvpn_without_single_config_source(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "openvpn.yaml").write_text(
        """
kind: Interface
metadata:
  name: missing-config
spec:
  type: openvpn
---
kind: Interface
metadata:
  name: duplicate-config
spec:
  type: openvpn
  config: inline
  configFile: client.conf
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(UpdateValidationError) as exc_info:
        run_update(settings, target="ifaces", console=_console(StringIO()))

    message = str(exc_info.value)
    assert "Interface/missing-config" in message
    assert "requires exactly one of spec.config or spec.configFile" in message
    assert "Interface/duplicate-config" in message
