from __future__ import annotations

import os
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from dros.config_objects import InterfaceConfig, XfrmTransportConfig, load_config_objects
from dros.events import process_event
from dros.executor import SystemExecutor
from dros.plugins import create_default_registry
from dros.plugins.base import UpdateContext
from dros.plugins.network_interfaces import _reload_ifupdown_interface
from dros.plugins.network_xfrm import start_xfrm
from dros.settings import DrosPaths, DrosSettings
from dros.update import UpdateValidationError, run_config_check, run_update


def _console(output: StringIO) -> Console:
    return Console(file=output, force_terminal=False, color_system=None, width=100)


def _settings(tmp_path: Path) -> DrosSettings:
    return DrosSettings(
        sysRoot=tmp_path / "sysroot",
        paths=DrosPaths(configs=tmp_path / "configs", run=tmp_path / "run"),
    )


def _openvpn_status_config(settings: DrosSettings, name: str) -> str:
    return f"status {settings.paths.run / f'openvpn.{name}.status'} 10\nstatus-version 3\n"


def test_config_check_validates_all_objects_without_applying(tmp_path: Path) -> None:
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
  name: br0.10
spec:
  type: vlan
  parent: br0
  id: 5000
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(UpdateValidationError) as exc_info:
        run_config_check(settings, console=_console(StringIO()))

    assert "vlan id must be between 1 and 4094" in "\n".join(exc_info.value.errors)
    assert "parent Interface/br0 is not defined" in "\n".join(exc_info.value.errors)
    assert not (settings.sys_root / "etc/network/interfaces.d").exists()


def test_config_check_reports_object_count_for_valid_configs(tmp_path: Path) -> None:
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

    result = run_config_check(settings, console=_console(StringIO()))

    assert result.object_count == 1
    assert not result.actions


def _interface_file(settings: DrosSettings, name: str) -> Path:
    path = settings.sys_root / "etc/network/interfaces.d" / f"*-dros-{name}.cfg"
    matches = sorted(path.parent.glob(path.name))
    assert len(matches) == 1
    return matches[0]


def _interface_file_exists(settings: DrosSettings, name: str) -> bool:
    path = settings.sys_root / "etc/network/interfaces.d" / f"*-dros-{name}.cfg"
    return bool(list(path.parent.glob(path.name)))


class _FakeReloadContext:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.executor = self

    def run(self, command: list[str], **kwargs: object) -> None:
        self.calls.append((command, kwargs))


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

    content = _interface_file(settings, "br0").read_text(encoding="utf-8")
    assert _interface_file(settings, "br0").name == "010-dros-br0.cfg"
    assert "auto br0\n" in content
    assert "iface br0 inet static\n" in content
    assert "  address 10.0.0.1/24\n" in content
    assert "  gateway 10.0.0.254\n" in content
    assert "  up ip addr add 10.0.0.2/24 dev $IFACE\n" in content
    assert "  up ip addr add fd00::1/64 dev $IFACE\n" in content
    assert "  bridge_ports eth1 eth2\n" in content
    assert "  bridge_stp off\n" in content
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

    eth_path = _interface_file(settings, "eth0")
    vlan_path = _interface_file(settings, "br0.10")
    eth = eth_path.read_text(encoding="utf-8")
    vlan = vlan_path.read_text(encoding="utf-8")
    assert eth_path.name == "010-dros-eth0.cfg"
    assert _interface_file(settings, "br0").name == "020-dros-br0.cfg"
    assert vlan_path.name == "030-dros-br0.10.cfg"
    assert "iface eth0 inet dhcp\n" in eth
    assert "  post-up ip link set dev eth0 group 1\n" in eth
    assert "iface br0.10 inet static\n" in vlan
    assert "  pre-up ip link show dev br0" in vlan
    assert "  pre-up ip link show dev $IFACE" in vlan
    assert " type vlan id 10\n" in vlan
    assert "  post-down ip link del dev $IFACE || true\n" in vlan
    assert any(action.command == ["ifup", "eth0"] for action in result.actions)
    assert any(action.command == ["ifup", "br0.10"] for action in result.actions)


def test_interface_files_are_numbered_by_dependency_order(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "network.yaml").write_text(
        """
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
  name: br-wan
spec:
  type: bridge
  ports:
    - eth0.35
---
kind: Interface
metadata:
  name: eth0.35
spec:
  type: vlan
  parent: eth0
  id: 35
---
kind: Interface
metadata:
  name: eth0
spec:
  type: eth
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="ifaces", console=_console(StringIO()))

    names = sorted(path.name for path in (settings.sys_root / "etc/network/interfaces.d").glob("*.cfg"))
    assert names == [
        "010-dros-eth0.cfg",
        "020-dros-eth0.35.cfg",
        "030-dros-br-wan.cfg",
        "040-dros-pppoe-wan.cfg",
    ]


def test_update_interface_migrates_legacy_unprefixed_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    old_path = settings.sys_root / "etc/network/interfaces.d/dros-eth0.cfg"
    old_path.parent.mkdir(parents=True)
    old_path.write_text("# old generated file\n", encoding="utf-8")
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

    result = run_update(settings, target="iface/eth0", console=_console(StringIO()))

    new_path = settings.sys_root / "etc/network/interfaces.d/010-dros-eth0.cfg"
    assert not old_path.exists()
    assert new_path.exists()
    assert any(action.kind == "rename_file" for action in result.actions)


def test_update_interface_rejects_dependency_cycles(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "network.yaml").write_text(
        """
kind: Interface
metadata:
  name: a
spec:
  type: vlan
  parent: b
  id: 10
---
kind: Interface
metadata:
  name: b
spec:
  type: vlan
  parent: a
  id: 11
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(UpdateValidationError, match="dependency cycle"):
        run_update(settings, target="ifaces", console=_console(StringIO()))


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

    assert not _interface_file_exists(settings, "br0")


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
    assert not _interface_file_exists(settings, "ok")


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

    assert _interface_file_exists(settings, "eth0")
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

    assert _interface_file_exists(settings, "br0.1")
    assert _interface_file_exists(settings, "br0.4094")


def test_update_rejects_unknown_config_fields(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "network.yaml").write_text(
        """
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: eth0
spec:
  type: eth
  typoField: true
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(UpdateValidationError) as exc_info:
        run_update(settings, target="iface/eth0", console=_console(StringIO()))

    assert "typoField" in str(exc_info.value)


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

    assert not _interface_file_exists(settings, "eth0")
    assert _interface_file_exists(settings, "eth1")


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

    content = _interface_file(settings, "lo").read_text(encoding="utf-8")
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
  xfrmTransport: office
  ttl: 255
---
apiVersion: dros/v1alpha1
kind: XfrmTransport
metadata:
  name: office
spec:
  localParty: partyA
  selector:
    proto: udp
  partyA:
    publicIp: 198.51.100.1
  partyB:
    publicIp: 203.0.113.1
  spi:
    partyAToPartyB: "0x100"
    partyBToPartyA: "0x101"
  reqid:
    partyAToPartyB: 100
    partyBToPartyA: 101
  keys:
    partyAToPartyB: "0x00112233445566778899aabbccddeeff00112233"
    partyBToPartyA: "0xffeeddccbbaa99887766554433221100ffeeddcc"
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
  xfrmTransport: office
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

    gre = _interface_file(settings, "gre-office").read_text(encoding="utf-8")
    assert "iface gre-office inet static\n" in gre
    assert "post-up gw hook xfrm-start office --verbose 0\n" in gre
    assert "pre-up ip tunnel add $IFACE mode gre" in gre
    assert "local 198.51.100.1 remote 203.0.113.1 ttl 255" in gre
    assert "post-down ip tunnel del $IFACE || true\n" in gre
    assert "post-down gw hook xfrm-stop office --verbose 0 || true\n" in gre
    assert "gw start xfrm/office" not in gre
    assert "gw stop xfrm/office" not in gre

    pppoe_iface = _interface_file(settings, "pppoe-wan").read_text(encoding="utf-8")
    pppoe_peer = (settings.sys_root / "etc/ppp/peers/pppoe-wan").read_text(
        encoding="utf-8"
    )
    assert "iface pppoe-wan inet ppp\n" in pppoe_iface
    assert "  pre-up ifup eth0.35 2>/dev/null || true\n" in pppoe_iface
    assert "  provider pppoe-wan\n" in pppoe_iface
    assert "plugin rp-pppoe.so eth0.35\n" in pppoe_peer
    assert 'user "home@example.net"\n' in pppoe_peer
    assert 'password "secret"\n' in pppoe_peer

    wg_iface = _interface_file(settings, "wg0").read_text(encoding="utf-8")
    wg_conf = (settings.sys_root / "etc/wireguard/wg0.conf").read_text(
        encoding="utf-8"
    )
    assert "iface wg0 inet static\n" in wg_iface
    assert "post-up gw hook xfrm-start office --verbose 0\n" in wg_iface
    assert "pre-up ip link add dev $IFACE type wireguard\n" in wg_iface
    assert "pre-up wg setconf $IFACE /etc/wireguard/wg0.conf\n" in wg_iface
    assert "post-down gw hook xfrm-stop office --verbose 0 || true\n" in wg_iface
    assert "gw start xfrm/office" not in wg_iface
    assert "gw stop xfrm/office" not in wg_iface
    assert "PrivateKey = private-key\n" in wg_conf
    assert "ListenPort = 51820\n" in wg_conf
    assert "PublicKey = peer-key\n" in wg_conf
    assert "AllowedIPs = 10.20.0.2/32, fd00:20::2/128\n" in wg_conf


def test_update_interface_supports_gwtool_compatible_fields(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "network.yaml").write_text(
        """
apiVersion: dros/v1alpha1
kind: DevGroup
metadata:
  name: wan
spec:
  id: 1
---
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: eth0
spec:
  type: ethernet
  auto: false
  allowHotplug: true
  addresses:
    - 192.0.2.2/24
    - 192.0.2.3/24
  gateway: 192.0.2.1
  devGroup: wan
---
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: br0
spec:
  type: bridge
  stp: true
  forwardDelay: 2
---
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: external0
spec:
  type: external
  extraAddresses:
    - 198.51.100.10/32
  devGroup: wan
""".lstrip(),
        encoding="utf-8",
    )

    result = run_update(settings, target="ifaces", console=_console(StringIO()))

    eth = _interface_file(settings, "eth0").read_text(encoding="utf-8")
    br = _interface_file(settings, "br0").read_text(encoding="utf-8")
    commands = [" ".join(action.command or []) for action in result.actions]
    assert "allow-hotplug eth0\n" in eth
    assert "auto eth0\n" not in eth
    assert "  address 192.0.2.2/24\n" in eth
    assert "  up ip addr add 192.0.2.3/24 dev $IFACE\n" in eth
    assert "  gateway 192.0.2.1\n" in eth
    assert "  bridge_stp on\n" in br
    assert "  bridge_fd 2\n" in br
    assert not _interface_file_exists(settings, "external0")
    assert any("ip link set dev external0 group 1" in command for command in commands)
    assert any("ip addr replace 198.51.100.10/32 dev external0" in command for command in commands)


def test_update_interface_writes_tailscale_service_config_and_up_command(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "tailscale.yaml").write_text(
        """
kind: DevGroup
metadata:
  name: vpn
spec:
  id: 4
---
kind: Interface
metadata:
  name: tailscale0
spec:
  type: tailscale
  loginServer: https://hs.example.net:8443
  hostname: homelab-gw
  acceptRoutes: false
  acceptDns: false
  netfilterMode: off
  advertiseRoutes:
    - 10.6.0.0/16
    - fd02::/48
  advertiseTags:
    - tag:gateway
  snatSubnetRoutes: false
  devGroup: vpn
""".lstrip(),
        encoding="utf-8",
    )

    result = run_update(settings, target="iface/tailscale0", console=_console(StringIO()))

    service = (
        settings.sys_root / "etc/systemd/system/dros-tailscaled-tailscale0.service"
    ).read_text(encoding="utf-8")
    socket_path = settings.paths.run / "tailscale/tailscale0.sock"
    assert "--tun=tailscale0" in service
    assert f"--socket={socket_path}" in service
    assert "--statedir=/var/lib/tailscale/tailscale0" in service
    assert not _interface_file_exists(settings, "tailscale0")
    commands = [" ".join(action.command or []) for action in result.actions]
    assert any("systemctl daemon-reload" in command for command in commands)
    assert any(
        "systemctl enable --now dros-tailscaled-tailscale0.service" in command
        for command in commands
    )
    assert any(
        "systemctl restart dros-tailscaled-tailscale0.service" in command
        for command in commands
    )
    assert any(
        f"tailscale --socket={socket_path} up --reset --login-server=https://hs.example.net:8443 "
        "--hostname=homelab-gw --accept-routes=false --accept-dns=false "
        "--netfilter-mode=off --timeout=10s --advertise-routes=10.6.0.0/16,fd02::/48 "
        "--advertise-tags=tag:gateway --snat-subnet-routes=false" in command
        for command in commands
    )
    assert any("ip link set dev tailscale0 group 4" in command for command in commands)


def test_update_supports_multiple_tailscale_interfaces(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "tailscale.yaml").write_text(
        """
kind: Interface
metadata:
  name: tailscale0
spec:
  type: tailscale
---
kind: Interface
metadata:
  name: ts1
spec:
  type: tailscale
""".lstrip(),
        encoding="utf-8",
    )

    result = run_update(settings, target="ifaces", console=_console(StringIO()))

    commands = [" ".join(action.command or []) for action in result.actions]
    assert (
        settings.sys_root / "etc/systemd/system/dros-tailscaled-tailscale0.service"
    ).exists()
    assert (settings.sys_root / "etc/systemd/system/dros-tailscaled-ts1.service").exists()
    assert any(
        f"tailscale --socket={settings.paths.run / 'tailscale/tailscale0.sock'} up" in command
        for command in commands
    )
    assert any(
        f"tailscale --socket={settings.paths.run / 'tailscale/ts1.sock'} up" in command
        for command in commands
    )


def test_update_pppoe_renders_gwtool_route_flags(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "pppoe.yaml").write_text(
        """
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: pppoe-wan
spec:
  type: pppoe
  device: eth0
  user: home@example.net
  password: secret
  nodefaultroute: true
  nodefaultroute6: true
  noreplacedefaultroute: true
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="iface/pppoe-wan", console=_console(StringIO()))

    peer = (settings.sys_root / "etc/ppp/peers/pppoe-wan").read_text(encoding="utf-8")
    lines = set(peer.splitlines())
    assert "nodefaultroute" in lines
    assert "defaultroute" not in lines
    assert "nodefaultroute6" in lines
    assert "defaultroute6" not in lines
    assert "noreplacedefaultroute" in lines


def test_update_pppoe_defaults_disable_default_routes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "pppoe.yaml").write_text(
        """
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: pppoe-wan
spec:
  type: pppoe
  device: eth0
  user: home@example.net
  password: secret
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="iface/pppoe-wan", console=_console(StringIO()))

    peer = (settings.sys_root / "etc/ppp/peers/pppoe-wan").read_text(encoding="utf-8")
    lines = set(peer.splitlines())
    assert "noipdefault" in lines
    assert "nodefaultroute" in lines
    assert "noreplacedefaultroute" in lines
    assert "nodefaultroute6" in lines
    assert "defaultroute" not in lines
    assert "defaultroute6" not in lines
    assert "replacedefaultroute" not in lines


def test_update_xfrm_transport_manual_activation_does_not_start_runtime(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "xfrm.yaml").write_text(
        """
apiVersion: dros/v1alpha1
kind: XfrmTransport
metadata:
  name: office
spec:
  localParty: partyA
  partyA:
    publicIp: 198.51.100.1
    privateIp: 10.0.0.1
  partyB:
    publicIp: 203.0.113.1
  spi:
    partyAToPartyB: "0x100"
    partyBToPartyA: "0x101"
  reqid:
    partyAToPartyB: 100
    partyBToPartyA: 101
  keys:
    partyAToPartyB: "0x00112233445566778899aabbccddeeff00112233"
    partyBToPartyA: "0xffeeddccbbaa99887766554433221100ffeeddcc"
""".lstrip(),
        encoding="utf-8",
    )

    result = run_update(settings, target="xfrm/office", console=_console(StringIO()))

    commands = [" ".join(action.command or []) for action in result.actions]
    assert not any("ip xfrm state add" in command for command in commands)
    assert not any("ip xfrm policy add" in command for command in commands)


def test_update_xfrm_transport_system_activation_writes_systemd_service(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "xfrm.yaml").write_text(
        """
apiVersion: dros/v1alpha1
kind: XfrmTransport
metadata:
  name: office
spec:
  activation: system
  localParty: partyA
  partyA:
    publicIp: 198.51.100.1
    privateIp: 10.0.0.1
  partyB:
    publicIp: 203.0.113.1
  spi:
    partyAToPartyB: "0x100"
    partyBToPartyA: "0x101"
  reqid:
    partyAToPartyB: 100
    partyBToPartyA: 101
  keys:
    partyAToPartyB: "0x00112233445566778899aabbccddeeff00112233"
    partyBToPartyA: "0xffeeddccbbaa99887766554433221100ffeeddcc"
""".lstrip(),
        encoding="utf-8",
    )

    result = run_update(settings, target="xfrm/office", console=_console(StringIO()))

    service = (
        settings.sys_root / "etc/systemd/system/dros-xfrm-office.service"
    ).read_text(encoding="utf-8")
    commands = [" ".join(action.command or []) for action in result.actions]
    assert "Description=DROS XFRM transport office" in service
    assert "Type=oneshot" in service
    assert "RemainAfterExit=yes" in service
    assert "ExecStart=/usr/local/bin/gw start xfrm/office --verbose 0" in service
    assert "ExecStop=/usr/local/bin/gw stop xfrm/office --verbose 0" in service
    assert "systemctl daemon-reload" in commands
    assert "systemctl enable --now dros-xfrm-office.service" in commands
    assert "systemctl restart dros-xfrm-office.service" in commands
    assert not any("ip xfrm state add" in command for command in commands)
    assert not any("ip xfrm policy add" in command for command in commands)


def test_update_xfrm_transport_supports_udp_selector(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "xfrm.yaml").write_text(
        """
apiVersion: dros/v1alpha1
kind: XfrmTransport
metadata:
  name: office
spec:
  localParty: partyA
  selector:
    proto: udp
  partyA:
    publicIp: 198.51.100.1
    privateIp: 10.0.0.1
  partyB:
    publicIp: 203.0.113.1
  spi:
    partyAToPartyB: "0x100"
    partyBToPartyA: "0x101"
  reqid:
    partyAToPartyB: 100
    partyBToPartyA: 101
  keys:
    partyAToPartyB: "0x00112233445566778899aabbccddeeff00112233"
    partyBToPartyA: "0xffeeddccbbaa99887766554433221100ffeeddcc"
""".lstrip(),
        encoding="utf-8",
    )

    result = run_update(settings, target="xfrm/office", console=_console(StringIO()))

    commands = [" ".join(action.command or []) for action in result.actions]
    assert not any("sel src 10.0.0.1/32 dst 203.0.113.1/32 proto udp" in command for command in commands)
    assert not any("ip xfrm policy add dir out src 10.0.0.1/32 dst 203.0.113.1/32 proto udp" in command for command in commands)


def test_start_xfrm_transport_supports_udp_selector(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "xfrm.yaml").write_text(
        """
apiVersion: dros/v1alpha1
kind: XfrmTransport
metadata:
  name: office
spec:
  localParty: partyA
  selector:
    proto: udp
  partyA:
    publicIp: 198.51.100.1
    privateIp: 10.0.0.1
  partyB:
    publicIp: 203.0.113.1
  spi:
    partyAToPartyB: "0x100"
    partyBToPartyA: "0x101"
  reqid:
    partyAToPartyB: 100
    partyBToPartyA: 101
  keys:
    partyAToPartyB: "0x00112233445566778899aabbccddeeff00112233"
    partyBToPartyA: "0xffeeddccbbaa99887766554433221100ffeeddcc"
""".lstrip(),
        encoding="utf-8",
    )
    configs = load_config_objects(settings)
    obj = configs.require("XfrmTransport", "office")
    config = configs.resolve_object(obj, XfrmTransportConfig)
    registry = create_default_registry()
    executor = SystemExecutor(settings, console=_console(StringIO()))
    context = UpdateContext(settings=settings, configs=configs, executor=executor, registry=registry)

    start_xfrm(context, "office", config)

    commands = [" ".join(action.command or []) for action in executor.actions]
    assert any("sel src 10.0.0.1/32 dst 203.0.113.1/32 proto udp" in command for command in commands)
    assert any("ip xfrm policy add dir out src 10.0.0.1/32 dst 203.0.113.1/32 proto udp" in command for command in commands)


def test_xfrm_hook_events_start_and_stop_transport(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "xfrm.yaml").write_text(
        """
apiVersion: dros/v1alpha1
kind: XfrmTransport
metadata:
  name: office
spec:
  localParty: partyA
  selector:
    proto: gre
  partyA:
    publicIp: 198.51.100.1
    privateIp: 10.0.0.1
  partyB:
    publicIp: 203.0.113.1
  spi:
    partyAToPartyB: "0x100"
    partyBToPartyA: "0x101"
  reqid:
    partyAToPartyB: 100
    partyBToPartyA: 101
  keys:
    partyAToPartyB: "0x00112233445566778899aabbccddeeff00112233"
    partyBToPartyA: "0xffeeddccbbaa99887766554433221100ffeeddcc"
""".lstrip(),
        encoding="utf-8",
    )

    start = process_event(settings, "xfrm-start", iface="office", console=_console(StringIO()))
    stop = process_event(settings, "xfrm-stop", iface="office", console=_console(StringIO()))

    start_commands = [" ".join(action.command or []) for action in start.actions]
    stop_command_lists = [action.command or [] for action in stop.actions]
    assert any("ip xfrm state add src 10.0.0.1 dst 203.0.113.1" in command for command in start_commands)
    assert any("ip xfrm policy add dir out src 10.0.0.1/32 dst 203.0.113.1/32" in command for command in start_commands)
    assert any(
        command[:4]
        == [
            "sh",
            "-c",
            'ip xfrm state delete src "$1" dst "$2" proto esp spi "$3" 2>/dev/null || true',
            "sh",
        ]
        and command[4:6] == ["10.0.0.1", "203.0.113.1"]
        for command in stop_command_lists
    )
    assert any(
        command[:4]
        == [
            "sh",
            "-c",
            'ip xfrm policy delete dir "$1" src "$2" dst "$3" proto "$4" 2>/dev/null || true',
            "sh",
        ]
        and command[4:7] == ["out", "10.0.0.1/32", "203.0.113.1/32"]
        for command in stop_command_lists
    )


def test_xfrm_hook_events_ignore_system_activation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "xfrm.yaml").write_text(
        """
apiVersion: dros/v1alpha1
kind: XfrmTransport
metadata:
  name: office
spec:
  activation: system
  localParty: partyA
  selector:
    proto: gre
  partyA:
    publicIp: 198.51.100.1
  partyB:
    publicIp: 203.0.113.1
  spi:
    partyAToPartyB: "0x100"
    partyBToPartyA: "0x101"
  reqid:
    partyAToPartyB: 100
    partyBToPartyA: 101
  keys:
    partyAToPartyB: "0x00112233445566778899aabbccddeeff00112233"
    partyBToPartyA: "0xffeeddccbbaa99887766554433221100ffeeddcc"
""".lstrip(),
        encoding="utf-8",
    )

    start = process_event(settings, "xfrm-start", iface="office", console=_console(StringIO()))
    stop = process_event(settings, "xfrm-stop", iface="office", console=_console(StringIO()))

    assert start.actions == []
    assert stop.actions == []


def test_interface_rejects_system_activated_xfrm_transport(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "network.yaml").write_text(
        """
apiVersion: dros/v1alpha1
kind: XfrmTransport
metadata:
  name: office
spec:
  activation: system
  localParty: partyA
  partyA:
    publicIp: 198.51.100.1
  partyB:
    publicIp: 203.0.113.1
  spi:
    partyAToPartyB: "0x100"
    partyBToPartyA: "0x101"
  reqid:
    partyAToPartyB: 100
    partyBToPartyA: 101
  keys:
    partyAToPartyB: "0x00112233445566778899aabbccddeeff00112233"
    partyBToPartyA: "0xffeeddccbbaa99887766554433221100ffeeddcc"
---
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: gre-office
spec:
  type: gre
  address: 10.10.10.1/30
  localPublicIp: 198.51.100.1
  remotePublicIp: 203.0.113.1
  xfrmTransport: office
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(UpdateValidationError) as exc_info:
        run_update(settings, target="iface/gre-office", console=_console(StringIO()))

    assert "XfrmTransport/office uses activation system" in "\n".join(exc_info.value.errors)


def test_update_wireguard_expands_allowed_ips_and_writes_wgsd_cron(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "ip-lists").mkdir()
    (settings.paths.configs / "ip-lists/china.v4.txt").write_text(
        "10.0.0.0/8\n",
        encoding="utf-8",
    )
    (settings.paths.configs / "ip-lists/china.v6.txt").write_text(
        "fd00::/8\n",
        encoding="utf-8",
    )
    (settings.paths.configs / "wg.yaml").write_text(
        """
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: wg0
spec:
  type: wireguard
  privateKey: private-key
  peers:
    - publicKey: peer-key
      allowedIPs:
        - china@v4
        - china@v6
        - 192.0.2.0/24
        - china@all
  wgsdClient:
    dns: 127.0.0.1:53
    zone: wg.example.
    schedule: "*/5 * * * *"
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="iface/wg0", console=_console(StringIO()))

    wg_conf = (settings.sys_root / "etc/wireguard/wg0.conf").read_text(encoding="utf-8")
    cron = (settings.sys_root / "etc/cron.d/dros-wgsd-client-wg0").read_text(
        encoding="utf-8"
    )
    assert "AllowedIPs = 10.0.0.0/8, fd00::/8, 192.0.2.0/24\n" in wg_conf
    assert "*/5 * * * * root /usr/local/bin/wgsd-client" in cron
    assert "-device wg0 -dns 127.0.0.1:53 -zone wg.example." in cron


def test_update_wireguard_uses_addconf_when_only_wireguard_conf_changes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    config_path = settings.paths.configs / "wg.yaml"
    config_path.write_text(
        """
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: wg0
spec:
  type: wireguard
  address: 10.20.0.1/24
  privateKey: private-key
  peers:
    - publicKey: peer-key
      allowedIPs:
        - 10.20.0.2/32
""".lstrip(),
        encoding="utf-8",
    )
    run_update(settings, target="iface/wg0", console=_console(StringIO()))
    config_path.write_text(
        """
apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: wg0
spec:
  type: wireguard
  address: 10.20.0.1/24
  privateKey: private-key
  peers:
    - publicKey: peer-key
      allowedIPs:
        - 10.20.0.3/32
""".lstrip(),
        encoding="utf-8",
    )

    result = run_update(settings, target="iface/wg0", console=_console(StringIO()))

    commands = [" ".join(action.command or []) for action in result.actions if action.command]
    assert any("wg addconf wg0 /etc/wireguard/wg0.conf" in command for command in commands)
    assert not any(command == "ifdown --force wg0" for command in commands)


def test_pppoe_interface_reload_uses_timeouts() -> None:
    context = _FakeReloadContext()
    config = InterfaceConfig(
        type="pppoe",
        device="eth0.35",
        user="home@example.net",
        password="secret",
    )

    _reload_ifupdown_interface(context, "pppoe-wan", config)  # type: ignore[arg-type]

    assert context.calls == [
        (
            ["ifdown", "--force", "pppoe-wan"],
            {"check": False, "real_only": True, "timeout": 60},
        ),
        (["ifup", "pppoe-wan"], {"real_only": True, "timeout": 120}),
    ]


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

    iface = _interface_file(settings, "ovpn-lab").read_text(encoding="utf-8")
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
    assert _openvpn_status_config(settings, "ovpn-lab") in ovpn
    assert 'IFACE="ovpn-lab"\n' in up
    assert 'ip link set dev "$IFACE" group 9\n' in up
    assert "sh -c 'echo openvpn-up'\n" in up
    assert '/usr/local/bin/gw hook route-refresh "$IFACE" --verbose 0 || true\n' in up


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
    assert ovpn == (
        "client\nremote vpn.example.net 1194\n"
        + _openvpn_status_config(settings, "ovpn-client")
    )


def test_update_interface_openvpn_writes_route_refresh_up_script_by_default(
    tmp_path: Path,
) -> None:
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

    iface = _interface_file(settings, "ovpn-client").read_text(encoding="utf-8")
    up = (settings.sys_root / "etc/dros/openvpn/ovpn-client.up").read_text(
        encoding="utf-8"
    )
    assert "/etc/dros/openvpn/ovpn-client.up" in iface
    assert '/usr/local/bin/gw hook route-refresh "$IFACE" --verbose 0 || true\n' in up


def test_update_interface_openvpn_appends_extra_config_lines_to_config_file(
    tmp_path: Path,
) -> None:
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
  extraConfigLines:
    - route-noexec
    - pull-filter ignore redirect-gateway
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="iface/ovpn-client", console=_console(StringIO()))

    ovpn = (settings.sys_root / "etc/dros/openvpn/ovpn-client.ovpn").read_text(
        encoding="utf-8"
    )
    assert (
        ovpn
        == "client\n"
        "remote vpn.example.net 1194\n"
        "route-noexec\n"
        "pull-filter ignore redirect-gateway\n"
        + _openvpn_status_config(settings, "ovpn-client")
    )


def test_update_interface_openvpn_reload_when_config_file_content_changes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    config_file = settings.paths.configs / "client.conf"
    config_file.write_text("client\nremote vpn-a.example.net 1194\n", encoding="utf-8")
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
    config_file.write_text("client\nremote vpn-b.example.net 1194\n", encoding="utf-8")

    result = run_update(settings, target="iface/ovpn-client", console=_console(StringIO()))

    ovpn = (settings.sys_root / "etc/dros/openvpn/ovpn-client.ovpn").read_text(
        encoding="utf-8"
    )
    commands = [" ".join(action.command or []) for action in result.actions]
    assert ovpn == (
        "client\nremote vpn-b.example.net 1194\n"
        + _openvpn_status_config(settings, "ovpn-client")
    )
    assert "ifdown --force ovpn-client" in commands
    assert "ifup ovpn-client" in commands


def test_update_interface_openvpn_reload_when_absolute_config_file_content_changes(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    config_file = settings.sys_root / "opt/gateway/ovpn/lab/clients/alice/client-auto.ovpn"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("client\nremote vpn-a.example.net 1194\n", encoding="utf-8")
    (settings.paths.configs / "openvpn.yaml").write_text(
        """
kind: Interface
metadata:
  name: ovpn-client
spec:
  type: openvpn
  configFile: /opt/gateway/ovpn/lab/clients/alice/client-auto.ovpn
""".lstrip(),
        encoding="utf-8",
    )
    run_update(settings, target="iface/ovpn-client", console=_console(StringIO()))
    config_file.write_text("client\nremote vpn-b.example.net 1194\n", encoding="utf-8")

    result = run_update(settings, target="iface/ovpn-client", console=_console(StringIO()))

    ovpn = (settings.sys_root / "etc/dros/openvpn/ovpn-client.ovpn").read_text(
        encoding="utf-8"
    )
    commands = [" ".join(action.command or []) for action in result.actions]
    assert ovpn == (
        "client\nremote vpn-b.example.net 1194\n"
        + _openvpn_status_config(settings, "ovpn-client")
    )
    assert "ifdown --force ovpn-client" in commands
    assert "ifup ovpn-client" in commands


def test_update_interface_openvpn_reload_when_external_config_file_is_newer(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    config_file = settings.paths.configs / "client.conf"
    config_file.write_text("client\nremote vpn.example.net 1194\n", encoding="utf-8")
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
    target = settings.sys_root / "etc/dros/openvpn/ovpn-client.ovpn"
    old_target_mtime = 1_000_000_000
    os.utime(target, ns=(old_target_mtime, old_target_mtime))
    source_mtime = config_file.stat().st_mtime_ns

    result = run_update(settings, target="iface/ovpn-client", console=_console(StringIO()))

    commands = [" ".join(action.command or []) for action in result.actions]
    assert target.read_text(encoding="utf-8") == (
        "client\nremote vpn.example.net 1194\n" + _openvpn_status_config(settings, "ovpn-client")
    )
    assert source_mtime > old_target_mtime
    assert target.stat().st_mtime_ns >= source_mtime
    assert "ifdown --force ovpn-client" in commands
    assert "ifup ovpn-client" in commands


def test_update_interface_openvpn_reload_when_inline_config_changes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    config_path = settings.paths.configs / "openvpn.yaml"
    config_path.write_text(
        """
kind: Interface
metadata:
  name: ovpn-client
spec:
  type: openvpn
  config: |
    client
    remote vpn-a.example.net 1194
""".lstrip(),
        encoding="utf-8",
    )
    run_update(settings, target="iface/ovpn-client", console=_console(StringIO()))
    config_path.write_text(
        """
kind: Interface
metadata:
  name: ovpn-client
spec:
  type: openvpn
  config: |
    client
    remote vpn-b.example.net 1194
""".lstrip(),
        encoding="utf-8",
    )

    result = run_update(settings, target="iface/ovpn-client", console=_console(StringIO()))

    ovpn = (settings.sys_root / "etc/dros/openvpn/ovpn-client.ovpn").read_text(
        encoding="utf-8"
    )
    commands = [" ".join(action.command or []) for action in result.actions]
    assert ovpn == (
        "client\nremote vpn-b.example.net 1194\n"
        + _openvpn_status_config(settings, "ovpn-client")
    )
    assert "ifdown --force ovpn-client" in commands
    assert "ifup ovpn-client" in commands


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
