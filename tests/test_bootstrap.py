from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from dros.bootstrap import run_bootstrap
from dros.plugins import create_default_registry
from dros.settings import DrosPaths, DrosSettings


def _console(output: StringIO) -> Console:
    return Console(file=output, force_terminal=False, color_system=None, width=100)


def _settings(tmp_path: Path) -> DrosSettings:
    return DrosSettings(
        sysRoot=tmp_path / "sysroot",
        paths=DrosPaths(configs=tmp_path / "configs"),
    )


def test_bootstrap_writes_managed_files_under_sysroot(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "site.yaml").write_text(
        """
kind: SystemNetworkConfig
metadata:
  name: default
spec:
  hostname: edge
  domain: home.arpa
  nfConntrackMax: 999999
---
kind: SystemMirrorConfig
metadata:
  name: default
spec:
  aptMirror: https://mirror.example/debian
  dockerAptMirror: https://mirror.example/docker-ce
  dockerRegistryMirror: https://registry.example
""".lstrip(),
        encoding="utf-8",
    )

    registry = create_default_registry()
    run_bootstrap(
        settings,
        console=_console(StringIO()),
        installed_packages=registry.owned_packages(),
    )

    sysroot = settings.sys_root
    assert (sysroot / "etc/hostname").read_text(encoding="utf-8") == "edge\n"
    assert "edge.home.arpa edge" in (sysroot / "etc/hosts").read_text(encoding="utf-8")
    assert "net.netfilter.nf_conntrack_max = 999999" in (
        sysroot / "etc/sysctl.d/99-dros.conf"
    ).read_text(encoding="utf-8")
    assert "https://mirror.example/debian" in (sysroot / "etc/apt/sources.list").read_text(
        encoding="utf-8"
    )
    assert "https://mirror.example/docker-ce/linux/debian" in (
        sysroot / "etc/apt/sources.list.d/docker-ce.list"
    ).read_text(encoding="utf-8")
    docker_config = (sysroot / "etc/docker/daemon.json").read_text(encoding="utf-8")
    assert '"iptables": false' in docker_config
    assert '"https://registry.example"' in docker_config
    docker_hook = (
        sysroot / "etc/systemd/system/docker.service.d/40-dros-hook.conf"
    ).read_text(encoding="utf-8")
    assert "ExecStartPost=-/usr/local/bin/gw hook docker-start --verbose 0" in docker_hook
    assert (sysroot / "etc/dnsmasq.conf").read_text(encoding="utf-8") == ""
    assert "enable-reflector=yes" in (
        sysroot / "etc/avahi/avahi-daemon.conf"
    ).read_text(encoding="utf-8")
    ppp_hook = (sysroot / "etc/ppp/ip-up.d/dros-hook").read_text(encoding="utf-8")
    assert 'gw hook ppp-up "$IFACE" --verbose 0' in ppp_hook
    route_hook = (sysroot / "etc/network/if-up.d/dros-route").read_text(
        encoding="utf-8"
    )
    assert 'case "$IFACE" in' in route_hook
    assert 'exec /usr/local/bin/gw hook route-refresh --verbose 0' in route_hook
    openvpn_helper = (sysroot / "usr/lib/dros/openvpn-iface").read_text(
        encoding="utf-8"
    )
    assert "usage: openvpn-iface start IFACE CONFIG PID UP_SCRIPT CRL_FILE LOG_FILE" in openvpn_helper
    assert "--log-append" in openvpn_helper
    assert (sysroot / "etc/dros/nftables.d").is_dir()
    assert not (sysroot / "etc/nftables.conf").exists()


def test_bootstrap_uses_singleton_config_named_system(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "SystemNetworkConfig.yaml").write_text(
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

    run_bootstrap(
        settings,
        console=_console(StringIO()),
        installed_packages=create_default_registry().owned_packages(),
    )

    assert "gateway.test.init2.me gateway" in (
        settings.sys_root / "etc/hosts"
    ).read_text(encoding="utf-8")


def test_bootstrap_file_writes_are_idempotent(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    installed_packages = create_default_registry().owned_packages()

    first_output = StringIO()
    first = run_bootstrap(
        settings,
        console=_console(first_output),
        installed_packages=installed_packages,
    )
    second_output = StringIO()
    second = run_bootstrap(
        settings,
        console=_console(second_output),
        installed_packages=installed_packages,
    )

    assert any(action.kind == "write_file" for action in first.actions)
    assert not [action for action in second.actions if action.kind == "write_file"]
    assert "updated /etc/hostname" in first_output.getvalue()
    assert "updated /etc/hostname" not in second_output.getvalue()


def test_bootstrap_installs_only_missing_packages(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    owned_packages = create_default_registry().owned_packages()
    installed_packages = owned_packages - {"bridge-utils", "dnsmasq", "docker-ce"}

    result = run_bootstrap(
        settings,
        console=_console(StringIO()),
        installed_packages=installed_packages,
    )

    install_commands = [action.command for action in result.actions if action.kind == "run_command"]
    flat_commands = [" ".join(command or []) for command in install_commands]
    assert any("apt-get install -y" in command and "bridge-utils" in command for command in flat_commands)
    assert any("apt-get install -y" in command and "dnsmasq" in command for command in flat_commands)
    assert any("apt-get install -y docker-ce" in command for command in flat_commands)
    assert not any("apt-get install -y curl" in command for command in flat_commands)


def test_bootstrap_disables_cloud_init_hosts_management_when_cloud_init_exists(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    cloud_dir = settings.sys_root / "etc/cloud"
    cloud_dir.mkdir(parents=True)
    (cloud_dir / "cloud.cfg").write_text("# cloud-init\n", encoding="utf-8")

    run_bootstrap(
        settings,
        console=_console(StringIO()),
        installed_packages=create_default_registry().owned_packages(),
    )

    drop_in = settings.sys_root / "etc/cloud/cloud.cfg.d/99-dros-hostname.cfg"
    assert drop_in.read_text(encoding="utf-8") == (
        "manage_etc_hosts: false\npreserve_hostname: true\n"
    )
