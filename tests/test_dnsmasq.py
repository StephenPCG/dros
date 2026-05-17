from __future__ import annotations

import subprocess
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

import dros.dnsmasq_china_names as china_names
from dros.dnsmasq_china_names import DnsmasqChinaNamesUpdater
from dros.settings import DrosPaths, DrosSettings
from dros.update import run_update


def _console(output: StringIO) -> Console:
    return Console(file=output, force_terminal=False, color_system=None, width=120)


def _settings(tmp_path: Path) -> DrosSettings:
    return DrosSettings(
        sysRoot=tmp_path / "sysroot",
        paths=DrosPaths(
            configs=tmp_path / "configs",
            run=tmp_path / "run",
            source=tmp_path / "source",
        ),
    )


def _commands(result: object) -> list[list[str]]:
    return [action.command for action in result.actions if action.command is not None]


def test_update_dnsmasq_dns_writes_core_and_dns_options(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "dnsmasq.yaml").write_text(
        """
kind: DnsmasqDNS
metadata:
  name: system
spec:
  interfaces:
    - br-lan
  listenAddresses:
    - 10.0.0.1
    - 127.0.0.1
  noResolv: true
  bogusPriv: true
  domainNeeded: true
  cacheSize: 10000
  logQueries: true
  logAsync: 25
  logFile: /var/log/dnsmasq/dnsmasq.log
  servers:
    - 223.5.5.5
    - /corp.example.com/10.0.0.53
  locals:
    - lan
  addresses:
    - /gateway.lan/10.0.0.1
  hostRecords:
    - nas.lan,10.0.0.10
  cnameRecords:
    - unifi.lan,gateway.lan
  raw:
    - stop-dns-rebind
""".lstrip(),
        encoding="utf-8",
    )

    result = run_update(settings, target="dnsmasq", console=_console(StringIO()))

    content = (settings.sys_root / "etc/dnsmasq.d/dros-10-dns.conf").read_text(
        encoding="utf-8"
    )
    assert "interface=br-lan" in content
    assert "listen-address=10.0.0.1" in content
    assert "no-resolv" in content
    assert "bogus-priv" in content
    assert "domain-needed" in content
    assert "cache-size=10000" in content
    assert "log-queries" in content
    assert "log-async=25" in content
    assert "log-facility=/var/log/dnsmasq/dnsmasq.log" in content
    assert "server=223.5.5.5" in content
    assert "server=/corp.example.com/10.0.0.53" in content
    assert "local=/lan/" in content
    assert "address=/gateway.lan/10.0.0.1" in content
    assert "host-record=nas.lan,10.0.0.10" in content
    assert "cname=unifi.lan,gateway.lan" in content
    assert "stop-dns-rebind" in content
    logrotate = (settings.sys_root / "etc/logrotate.d/dros-dnsmasq").read_text(
        encoding="utf-8"
    )
    assert "/var/log/dnsmasq/dnsmasq.log" in logrotate
    assert ["systemctl", "restart", "dnsmasq"] in _commands(result)


def test_update_dnsmasq_dhcp_writes_dhcp_options(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "dhcp.yaml").write_text(
        """
kind: DnsmasqDHCP
metadata:
  name: system
spec:
  authoritative: true
  domain: lan
  dnsServers:
    - 10.0.0.1
  v6DnsServers:
    - fd00::1
  ranges:
    - tag: lan
      start: 10.0.0.100
      end: 10.0.0.200
      lease: 12h
    - tag: guest
      start: 10.10.0.100
      end: 10.10.0.200
      router: 10.10.0.1
  hosts:
    - 00:11:22:33:44:55,10.0.0.10,nas
  raw:
    - dhcp-lease-max=1000
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="dnsmasq-dhcp/system", console=_console(StringIO()))

    content = (settings.sys_root / "etc/dnsmasq.d/dros-20-dhcp.conf").read_text(
        encoding="utf-8"
    )
    assert "dhcp-authoritative" in content
    assert "domain=lan" in content
    assert "dhcp-option=option:dns-server,10.0.0.1" in content
    assert "dhcp-option=option6:dns-server,[fd00::1]" in content
    assert "dhcp-range=lan,10.0.0.100,10.0.0.200,12h" in content
    assert "dhcp-option=lan,option:router,10.0.0.1" in content
    assert "dhcp-option=guest,option:router,10.10.0.1" in content
    assert "dhcp-host=00:11:22:33:44:55,10.0.0.10,nas" in content
    assert "dhcp-lease-max=1000" in content


def test_update_dnsmasq_china_names_writes_manual_conf_empty_cached_conf_and_cron(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "manual-domains.txt").write_text(
        """
# comments are ignored
from-file.example.cn
server=/preformatted.example.cn/1.1.1.1
""".lstrip(),
        encoding="utf-8",
    )
    (settings.paths.configs / "china-names.yaml").write_text(
        """
kind: DnsmasqChinaNames
metadata:
  name: system
spec:
  servers:
    - 114.114.114.114
    - 223.5.5.5
  files:
    - accelerated-domains.china.conf
    - bogus-nxdomain.china.conf
  manualNames:
    - internal.example.cn
  manualNameFiles:
    - manual-domains.txt
  schedule: "27 4 * * *"
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="dnsmasq-china-names/system", console=_console(StringIO()))

    china = (settings.sys_root / "etc/dnsmasq.d/dros-30-china-names.conf").read_text(
        encoding="utf-8"
    )
    manual = (
        settings.sys_root / "etc/dnsmasq.d/dros-31-china-names-manual.conf"
    ).read_text(encoding="utf-8")
    cron = (settings.sys_root / "etc/cron.d/dros-dnsmasq-china-names").read_text(
        encoding="utf-8"
    )
    assert "no cached china names" in china
    assert "gw dnsmasq china-names update" not in china
    assert "server=/internal.example.cn/114.114.114.114" in manual
    assert "server=/internal.example.cn/223.5.5.5" in manual
    assert "server=/from-file.example.cn/114.114.114.114" in manual
    assert "server=/from-file.example.cn/223.5.5.5" in manual
    assert "server=/preformatted.example.cn/114.114.114.114" in manual
    assert "server=/preformatted.example.cn/223.5.5.5" in manual
    assert (
        "27 4 * * * root /usr/local/bin/gw dnsmasq china-names update --verbose 1"
        in cron
    )


def test_update_dnsmasq_china_names_uses_cached_downloads(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    cache_dir = settings.sys_root / (settings.paths.run / "dnsmasq-china-names").relative_to("/")
    cache_dir.mkdir(parents=True)
    (cache_dir / "accelerated-domains.china.conf").write_text(
        "server=/example.cn/1.1.1.1\n",
        encoding="utf-8",
    )
    (settings.paths.configs / "china-names.yaml").write_text(
        """
kind: DnsmasqChinaNames
metadata:
  name: system
spec:
  servers:
    - 114.114.114.114
    - 223.5.5.5
  files:
    - accelerated-domains.china.conf
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="dnsmasq-china-names/system", console=_console(StringIO()))

    china = (settings.sys_root / "etc/dnsmasq.d/dros-30-china-names.conf").read_text(
        encoding="utf-8"
    )
    assert "server=/example.cn/114.114.114.114" in china
    assert "server=/example.cn/223.5.5.5" in china


def test_update_dnsmasq_china_names_uses_fixed_cache_dir(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    default_cache_dir = settings.sys_root / (settings.paths.run / "dnsmasq-china-names").relative_to("/")
    default_cache_dir.mkdir(parents=True)
    (default_cache_dir / "accelerated-domains.china.conf").write_text(
        "server=/example.cn/1.1.1.1\n",
        encoding="utf-8",
    )
    (settings.paths.configs / "china-names.yaml").write_text(
        """
kind: DnsmasqChinaNames
metadata:
  name: system
spec:
  outputDir: /tmp/custom-dnsmasq-china-names
  servers:
    - 114.114.114.114
  files:
    - accelerated-domains.china.conf
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="dnsmasq-china-names/system", console=_console(StringIO()))

    china = (settings.sys_root / "etc/dnsmasq.d/dros-30-china-names.conf").read_text(
        encoding="utf-8"
    )
    assert "server=/example.cn/114.114.114.114" in china
    assert not (settings.sys_root / "tmp/custom-dnsmasq-china-names").exists()


def test_dnsmasq_china_names_updater_caches_downloads_renders_conf_and_restarts(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def fetch_text(url: str, _timeout: float) -> str:
        assert url.endswith("/accelerated-domains.china.conf")
        return "server=/example.cn/1.1.1.1\n"

    updater = DnsmasqChinaNamesUpdater(fetch_text=fetch_text)

    result = updater.update(
        settings,
        servers=["114.114.114.114", "223.5.5.5"],
        selected_files=["accelerated-domains.china.conf"],
        manual_names=["internal.example.cn"],
        runner=runner,
        console=_console(StringIO()),
    )

    assert result.changed
    china = (settings.sys_root / "etc/dnsmasq.d/dros-30-china-names.conf").read_text(
        encoding="utf-8"
    )
    manual = (
        settings.sys_root / "etc/dnsmasq.d/dros-31-china-names-manual.conf"
    ).read_text(encoding="utf-8")
    assert "server=/example.cn/114.114.114.114" in china
    assert "server=/example.cn/223.5.5.5" in china
    assert "server=/internal.example.cn/114.114.114.114" in manual
    assert (
        settings.sys_root
        / (settings.paths.run / "dnsmasq-china-names/accelerated-domains.china.conf").relative_to("/")
    ).read_text(encoding="utf-8") == "server=/example.cn/1.1.1.1\n"
    assert (
        settings.sys_root
        / (settings.paths.run / "dnsmasq-china-names/sources.json").relative_to("/")
    ).exists()
    assert ["systemctl", "restart", "dnsmasq"] in _commands(result)


def test_dnsmasq_china_names_updater_does_not_restart_for_manifest_only_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    def fetch_text(_url: str, _timeout: float) -> str:
        return "server=/example.cn/1.1.1.1\n"

    updater = DnsmasqChinaNamesUpdater(fetch_text=fetch_text)
    kwargs = {
        "settings": settings,
        "servers": ["114.114.114.114"],
        "selected_files": ["accelerated-domains.china.conf"],
        "runner": runner,
        "console": _console(StringIO()),
    }

    monkeypatch.setattr(china_names.time, "time", lambda: 1)
    updater.update(**kwargs)
    monkeypatch.setattr(china_names.time, "time", lambda: 2)

    result = updater.update(**kwargs)

    assert result.changed
    assert ["systemctl", "restart", "dnsmasq"] not in _commands(result)
