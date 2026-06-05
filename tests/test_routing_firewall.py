from __future__ import annotations

import subprocess
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

import dros.plugins.network_routing as network_routing
from dros.events import process_event
from dros.plugins.network_routing import GatewayRuntimeState, gateway_is_available
from dros.settings import DrosPaths, DrosSettings
from dros.update import UpdateValidationError, run_update


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


def _system_path(settings: DrosSettings, path: Path | str) -> Path:
    logical = Path(path)
    return settings.sys_root / logical.relative_to("/")


def _update_route_script(settings: DrosSettings) -> str:
    return _system_path(settings, settings.paths.run / "tmp/update-route.sh").read_text(
        encoding="utf-8"
    )


def test_update_route_table_writes_rt_tables_and_batch_script(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "routes.yaml").write_text(
        """
apiVersion: dros/v1alpha1
kind: Gateway
metadata:
  name: wan
spec:
  dev: pppoe-wan
  via: 10.0.0.1
  onlink: true
  metric: 100
---
apiVersion: dros/v1alpha1
kind: RouteTable
metadata:
  name: wan
spec:
  family: ipv4
  table: 100
  routes:
    - to: default
      gateway: wan
    - to: 192.0.2.0/24
      type: unreachable
""".lstrip(),
        encoding="utf-8",
    )

    result = run_update(settings, target="route-table/wan", console=_console(StringIO()))

    rt_tables = (
        settings.sys_root / "etc/iproute2/rt_tables.d/dros.conf"
    ).read_text(encoding="utf-8")
    assert "100 wan" in rt_tables
    commands = _commands(result)
    assert ["sh", str(settings.paths.run / "tmp/update-route.sh")] in commands
    assert not any(command[:4] == ["ip", "-4", "route", "replace"] for command in commands)
    script = _update_route_script(settings)
    assert "ip -4 route flush table 100 2>/dev/null || true" in script
    assert 'ip -4 -batch "$batch"' in script
    assert (
        "route replace default via 10.0.0.1 dev pppoe-wan onlink metric 100 table 100"
        in script
    )
    assert "route replace unreachable 192.0.2.0/24 table 100" in script


def test_update_route_table_expands_ip_list_destinations(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    source_ip_lists = settings.paths.source / "ip-lists"
    source_ip_lists.mkdir(parents=True)
    (source_ip_lists / "private.v4.txt").write_text(
        "10.0.0.0/8\n172.16.0.0/12\n",
        encoding="utf-8",
    )
    (settings.paths.configs / "routes.yaml").write_text(
        """
kind: Gateway
metadata:
  name: wan
spec:
  dev: pppoe-wan
---
kind: RouteTable
metadata:
  name: wan
spec:
  family: ipv4
  table: 100
  routes:
    - to: private
      gateway: wan
""".lstrip(),
        encoding="utf-8",
    )

    result = run_update(settings, target="route-table/wan", console=_console(StringIO()))

    commands = _commands(result)
    assert ["sh", str(settings.paths.run / "tmp/update-route.sh")] in commands
    script = _update_route_script(settings)
    assert "route replace 10.0.0.0/8 dev pppoe-wan table 100" in script
    assert "route replace 172.16.0.0/12 dev pppoe-wan table 100" in script


def test_update_route_table_suppresses_update_script_diff(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "routes.yaml").write_text(
        """
kind: Gateway
metadata:
  name: wan
spec:
  dev: pppoe-wan
---
kind: RouteTable
metadata:
  name: wan
spec:
  family: ipv4
  table: 100
  routes:
    - to: default
      gateway: wan
""".lstrip(),
        encoding="utf-8",
    )
    output = StringIO()

    run_update(settings, target="route-table/wan", console=_console(output))

    rendered = output.getvalue()
    assert "updated" in rendered
    assert "update-route.sh" in rendered
    assert f"desired {settings.paths.run / 'tmp/update-route.sh'}" not in rendered
    assert "route replace default dev pppoe-wan table 100" not in rendered
    assert "ip -4 -batch" not in rendered


def test_update_route_table_skips_missing_ip_list_with_warning(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "routes.yaml").write_text(
        """
kind: Gateway
metadata:
  name: wan
spec:
  dev: pppoe-wan
---
kind: RouteTable
metadata:
  name: wan
spec:
  family: ipv4
  table: 100
  routes:
    - to: missing-list
      gateway: wan
""".lstrip(),
        encoding="utf-8",
    )
    output = StringIO()

    run_update(settings, target="route-table/wan", console=_console(output))

    script = _update_route_script(settings)
    assert "ip -4 route flush table 100 2>/dev/null || true" in script
    assert "route replace" not in script
    assert "warning: RouteTable/wan: spec.routes[0]: ip list 'missing-list' not found" in (
        output.getvalue()
    )


def test_update_route_table_aggregates_unavailable_gateway_warnings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = DrosSettings(
        sysRoot=Path("/"),
        paths=DrosPaths(
            configs=tmp_path / "configs",
            run=tmp_path / "run",
            source=tmp_path / "source",
        ),
    )
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "routes.yaml").write_text(
        """
kind: Gateway
metadata:
  name: china
spec:
  dev: pppoe-china
---
kind: RouteTable
metadata:
  name: auto-gfw
spec:
  table: 200
  routes:
    - to: default
      gateway: china
---
kind: RouteTable
metadata:
  name: forced-routes
spec:
  table: 201
  routes:
    - to: 192.0.2.0/24
      gateway: china
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(network_routing, "_write_rt_tables", lambda _context: None)

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        stdout = ""
        if command == ["ip", "-o", "link", "show"]:
            stdout = "1: pppoe-wan: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500\n"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    output = StringIO()

    run_update(settings, target="route-table", console=_console(output), runner=runner)

    rendered = output.getvalue()
    assert rendered.count("Gateway/china is unavailable") == 1
    assert "skipped 2 route(s)" in rendered
    assert "RouteTable/auto-gfw[0]" in rendered
    assert "RouteTable/forced-routes[0]" in rendered


def test_route_refresh_event_skips_missing_ip_list_without_warning(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "routes.yaml").write_text(
        """
kind: Gateway
metadata:
  name: wan
spec:
  dev: pppoe-wan
---
kind: RouteTable
metadata:
  name: wan
spec:
  family: ipv4
  table: 100
  routes:
    - to: missing-list
      gateway: wan
""".lstrip(),
        encoding="utf-8",
    )
    output = StringIO()

    process_event(settings, "route-refresh", verbose=0, console=_console(output))

    script = _update_route_script(settings)
    assert "route replace" not in script
    assert output.getvalue() == ""


def test_gateway_is_available_requires_link_up_for_known_interfaces() -> None:
    assert gateway_is_available(
        ("wan",),
        {"wan": GatewayRuntimeState(exists=True, up=True)},
    )
    assert not gateway_is_available(
        ("wan",),
        {"wan": GatewayRuntimeState(exists=True, up=False)},
    )
    assert not gateway_is_available(
        ("missing",),
        {"wan": GatewayRuntimeState(exists=True, up=True)},
    )
    assert gateway_is_available(("wan",), {})


def test_update_route_rule_set_resolves_fwmark_and_lookup_table(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "policy.yaml").write_text(
        """
kind: FwMark
metadata:
  name: lab
spec:
  mark: "0x00000100"
  mask: "0x0000ff00"
---
kind: RouteTable
metadata:
  name: lab
spec:
  table: 100
  routes: []
---
kind: RouteRuleSet
metadata:
  name: policy
spec:
  family: ipv4
  managedPriority:
    start: 10000
    end: 10999
  rules:
    - priority: 10010
      fwMark: lab
      lookup: lab
    - priority: 10020
      from: 10.8.0.0/16
      lookup: 100
""".lstrip(),
        encoding="utf-8",
    )

    result = run_update(settings, target="ruleset/policy", console=_console(StringIO()))

    commands = _commands(result)
    assert [
        "ip",
        "-4",
        "rule",
        "add",
        "priority",
        "10010",
        "fwmark",
        "0x00000100/0x0000ff00",
        "lookup",
        "100",
    ] in commands
    assert [
        "ip",
        "-4",
        "rule",
        "add",
        "priority",
        "10020",
        "from",
        "10.8.0.0/16",
        "lookup",
        "100",
    ] in commands


def test_update_route_runs_internal_commands_quietly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = DrosSettings(
        sysRoot=Path("/"),
        paths=DrosPaths(
            configs=tmp_path / "configs",
            run=tmp_path / "run",
            source=tmp_path / "source",
        ),
    )
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "policy.yaml").write_text(
        """
kind: Gateway
metadata:
  name: wan
spec:
  dev: pppoe-wan
---
kind: FwMark
metadata:
  name: lab
spec:
  mark: "0x00000100"
---
kind: RouteTable
metadata:
  name: lab
spec:
  table: 100
  routes:
    - to: default
      gateway: wan
---
kind: RouteRuleSet
metadata:
  name: policy
spec:
  family: ipv4
  managedPriority:
    start: 10000
    end: 10999
  rules:
    - priority: 10010
      fwMark: lab
      lookup: lab
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(network_routing, "_write_rt_tables", lambda _context: None)

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        stdout = ""
        if command == ["ip", "-o", "link", "show"]:
            stdout = "1: pppoe-wan: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500\n"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    output = StringIO()

    result = run_update(settings, target="route", console=_console(output), runner=runner)

    commands = _commands(result)
    assert ["sh", str(settings.paths.run / "tmp/update-route.sh")] in commands
    assert any(command[:3] == ["ip", "-4", "rule"] for command in commands)
    rendered = output.getvalue()
    assert "run: sh" not in rendered
    assert "ok: sh" not in rendered
    assert "run: ip -4 rule" not in rendered
    assert "ok: ip -4 rule" not in rendered
    assert "family=\"$1\"" not in rendered


def test_update_firewall_writes_nftables_entrypoint_and_base_rules(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "firewall.yaml").write_text(
        """
kind: DevGroup
metadata:
  name: wan
spec:
  id: 1
---
kind: Interface
metadata:
  name: wg0
spec:
  type: wireguard
  privateKey: test-private-key
  listenPort: 51820
---
kind: Interface
metadata:
  name: ovpn-lab
spec:
  type: openvpn
  config: |
    client
    dev tun
  listen:
    proto: udp
    port: 1194
    from:
      devGroups:
        - wan
---
kind: Firewall
metadata:
  name: main
spec:
  defaults:
    inputPolicy: drop
    forwardPolicy: drop
    outputPolicy: accept
  firewallRules:
    - chain: input
      rule: tcp dport 22 accept
  interfaceRules:
    - subject: devgroup/wan
      input:
        services:
          - udp/500
          - gre
          - esp
          - ah
""".lstrip(),
        encoding="utf-8",
    )

    result = run_update(settings, target="firewall/main", console=_console(StringIO()))

    entrypoint = (settings.sys_root / "etc/nftables.conf").read_text(encoding="utf-8")
    assert "flush ruleset" in entrypoint
    assert 'include "/etc/dros/nftables.d/*.nft"' in entrypoint
    nft = (settings.sys_root / "etc/dros/nftables.d/10-firewall.nft").read_text(
        encoding="utf-8"
    )
    assert "table inet dros_filter" in nft
    assert "type filter hook input priority filter; policy drop;" in nft
    assert "add rule inet dros_filter input_user tcp dport 22 accept" in nft
    assert "add rule inet dros_filter input_auto iifgroup 1 udp dport 500 accept" in nft
    assert "add rule inet dros_filter input_auto iifgroup 1 meta l4proto gre accept" in nft
    assert "add rule inet dros_filter input_auto iifgroup 1 meta l4proto esp accept" in nft
    assert "add rule inet dros_filter input_auto iifgroup 1 meta l4proto ah accept" in nft
    assert "udp dport 51820" not in nft
    assert "udp dport 1194" not in nft
    assert ["nft", "-f", "/etc/nftables.conf"] in _commands(result)


def test_update_firewall_renders_portmap_and_ipmap_hairpin_nat(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "firewall.yaml").write_text(
        """
kind: Firewall
metadata:
  name: main
spec:
  natRules:
    - type: portmap
      daddr: 10.20.255.8
      proto: tcp
      dport: 6443
      to: 10.20.3.83
      toPort: 6443
      hairpin:
        sourceNet: 10.20.0.0/16
    - type: ipmap
      daddr: 10.30.255.8
      to: 10.30.3.83
      hairpin:
        sourceNet: 10.30.0.0/16
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="firewall/main", console=_console(StringIO()))

    nft = (settings.sys_root / "etc/dros/nftables.d/10-firewall.nft").read_text(
        encoding="utf-8"
    )
    assert (
        "add rule inet dros_nat snat_postrouting "
        "ip saddr 10.20.0.0/16 ip daddr 10.20.3.83 tcp dport 6443 "
        "snat to ip saddr & 0.255.255.255 | 255.0.0.0"
    ) in nft
    assert (
        "add rule inet dros_nat snat_postrouting "
        "ip saddr 10.30.0.0/16 ip daddr 10.30.3.83 "
        "snat to ip saddr & 0.255.255.255 | 255.0.0.0"
    ) in nft


def test_update_firewall_portmap_forward_matches_post_dnat_destination(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "firewall.yaml").write_text(
        """
kind: Firewall
metadata:
  name: main
spec:
  natRules:
    - type: portmap
      family: ip
      proto: tcp
      daddr: 180.97.199.39
      dport: 34522
      to: 10.80.30.11
      toPort: 22
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="firewall/main", console=_console(StringIO()))

    nft = (settings.sys_root / "etc/dros/nftables.d/10-firewall.nft").read_text(
        encoding="utf-8"
    )
    assert (
        "add rule inet dros_nat dnat_prerouting "
        "meta nfproto ipv4 ip daddr 180.97.199.39 tcp dport 34522 "
        "dnat to 10.80.30.11:22"
    ) in nft
    assert (
        "add rule inet dros_filter portmap_forward "
        "meta nfproto ipv4 ip daddr 10.80.30.11 tcp dport 22 accept"
    ) in nft
    assert (
        "add rule inet dros_filter portmap_forward "
        "meta nfproto ipv4 ip daddr 180.97.199.39 "
        "ip daddr 10.80.30.11 tcp dport 22 accept"
    ) not in nft


def test_update_firewall_portmap_accepts_dport_list(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "firewall.yaml").write_text(
        """
kind: Firewall
metadata:
  name: main
spec:
  natRules:
    - type: portmap
      family: ip
      proto: tcp
      daddr: 180.97.199.39
      dport:
        - 80
        - 443
      to: 10.80.30.11
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="firewall/main", console=_console(StringIO()))

    nft = (settings.sys_root / "etc/dros/nftables.d/10-firewall.nft").read_text(
        encoding="utf-8"
    )
    assert (
        "add rule inet dros_nat dnat_prerouting "
        "meta nfproto ipv4 ip daddr 180.97.199.39 tcp dport { 80, 443 } "
        "dnat to 10.80.30.11"
    ) in nft
    assert (
        "add rule inet dros_filter portmap_forward "
        "meta nfproto ipv4 ip daddr 10.80.30.11 tcp dport { 80, 443 } accept"
    ) in nft


def test_update_firewall_can_apply_dnat_nat_rules_to_local_output(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "firewall.yaml").write_text(
        """
kind: Firewall
metadata:
  name: main
spec:
  natRules:
    - type: portmap
      daddr: 198.51.100.10
      proto: tcp
      dport: 443
      to: 10.20.3.83
      toPort: 6443
      localOutput: true
    - type: ipmap
      daddr: 198.51.100.20
      to: 10.30.3.83
      localOutput: true
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="firewall/main", console=_console(StringIO()))

    nft = (settings.sys_root / "etc/dros/nftables.d/10-firewall.nft").read_text(
        encoding="utf-8"
    )
    portmap_rule = (
        "meta nfproto ipv4 ip daddr 198.51.100.10 tcp dport 443 "
        "dnat to 10.20.3.83:6443"
    )
    ipmap_rule = "meta nfproto ipv4 ip daddr 198.51.100.20 dnat to 10.30.3.83"
    assert f"add rule inet dros_nat dnat_prerouting {portmap_rule}" in nft
    assert f"add rule inet dros_nat dnat_output {portmap_rule}" in nft
    assert f"add rule inet dros_nat dnat_prerouting {ipmap_rule}" in nft
    assert f"add rule inet dros_nat dnat_output {ipmap_rule}" in nft


def test_update_firewall_renders_fixed_clamp_mss_size(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "firewall.yaml").write_text(
        """
kind: Firewall
metadata:
  name: main
spec:
  defaults:
    clampMssSize: 1300
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="firewall/main", console=_console(StringIO()))

    nft = (settings.sys_root / "etc/dros/nftables.d/10-firewall.nft").read_text(
        encoding="utf-8"
    )
    assert (
        "tcp flags & (syn | rst) == syn tcp option maxseg size set 1300"
        in nft
    )
    assert "tcp option maxseg size set rt mtu" not in nft


def test_update_firewall_rejects_invalid_clamp_mss_size(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "firewall.yaml").write_text(
        """
kind: Firewall
metadata:
  name: main
spec:
  defaults:
    clampMssSize: 100
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(UpdateValidationError) as exc_info:
        run_update(settings, target="firewall/main", console=_console(StringIO()))

    assert "spec.defaults.clampMssSize must be an integer between 536 and 65495" in "\n".join(
        exc_info.value.errors
    )


def test_update_firewall_rejects_local_output_nat_rule_with_ingress_match(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "firewall.yaml").write_text(
        """
kind: Firewall
metadata:
  name: main
spec:
  natRules:
    - type: portmap
      iif: pppoe-wan
      daddr: 198.51.100.10
      proto: tcp
      dport: 443
      to: 10.20.3.83
      localOutput: true
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(UpdateValidationError) as exc_info:
        run_update(settings, target="firewall/main", console=_console(StringIO()))

    assert (
        "Firewall/main: spec.natRules[0].localOutput cannot be used with iif"
        in "\n".join(exc_info.value.errors)
    )


def test_update_firewall_validates_hairpin_nat(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "firewall.yaml").write_text(
        """
kind: Firewall
metadata:
  name: main
spec:
  natRules:
    - type: portmap
      proto: tcp
      dport: 443
      to: 10.0.0.2
      hairpin:
        snat: to-address
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(UpdateValidationError) as exc_info:
        run_update(settings, target="firewall/main", console=_console(StringIO()))

    message = "\n".join(exc_info.value.errors)
    assert "spec.natRules[0].hairpin.sourceNet is required" in message
    assert "spec.natRules[0].hairpin.snatTo is required for to-address hairpin SNAT" in message


def test_update_firewall_mark_rules_match_icmp(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "firewall.yaml").write_text(
        """
kind: FwMark
metadata:
  name: lab
spec:
  mark: "0x00000100"
  mask: "0x0000ff00"
---
kind: Firewall
metadata:
  name: main
spec:
  markRules:
    - fwMark: lab
      chains:
        - prerouting
        - output
      match:
        icmp: true
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="firewall/main", console=_console(StringIO()))

    nft = (settings.sys_root / "etc/dros/nftables.d/10-firewall.nft").read_text(
        encoding="utf-8"
    )
    mark_expr = "meta mark set meta mark & 0xffff00ff | 0x00000100"
    assert (
        "add rule inet dros_route mark_prerouting "
        f"meta nfproto ipv4 meta l4proto icmp {mark_expr}"
    ) in nft
    assert (
        "add rule inet dros_route mark_output "
        f"meta nfproto ipv4 meta l4proto icmp {mark_expr}"
    ) in nft


def test_update_interface_writes_openvpn_listen_snippet_without_firewall_reload(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "openvpn.yaml").write_text(
        """
kind: DevGroup
metadata:
  name: wan
spec:
  id: 1
---
kind: Interface
metadata:
  name: ovpn-lab
spec:
  type: openvpn
  config: |
    client
    dev tun
  listen:
    proto: udp
    port: 1194
    from:
      devGroups:
        - wan
""".lstrip(),
        encoding="utf-8",
    )

    result = run_update(settings, target="iface/ovpn-lab", console=_console(StringIO()))

    snippet = (
        settings.sys_root / "etc/dros/nftables.d/30-interface-ovpn-lab.nft"
    ).read_text(encoding="utf-8")
    assert "add rule inet dros_filter input_pre iifgroup 1 udp dport 1194 accept" in snippet
    assert ["nft", "-f", "/etc/nftables.conf"] not in _commands(result)


def test_update_interface_reload_nftables_when_firewall_has_been_applied(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    firewall_dir = settings.sys_root / "etc/dros/nftables.d"
    firewall_dir.mkdir(parents=True)
    (firewall_dir / "10-firewall.nft").write_text(
        "table inet dros_filter { chain input_pre { } }\n",
        encoding="utf-8",
    )
    (settings.sys_root / "etc/nftables.conf").parent.mkdir(parents=True, exist_ok=True)
    (settings.sys_root / "etc/nftables.conf").write_text(
        'flush ruleset\ninclude "/etc/dros/nftables.d/*.nft"\n',
        encoding="utf-8",
    )
    (settings.paths.configs / "wireguard.yaml").write_text(
        """
kind: Interface
metadata:
  name: wg0
spec:
  type: wireguard
  privateKey: test-private-key
  listenPort: 51820
""".lstrip(),
        encoding="utf-8",
    )

    result = run_update(settings, target="iface/wg0", console=_console(StringIO()))

    snippet = (
        settings.sys_root / "etc/dros/nftables.d/30-interface-wg0.nft"
    ).read_text(encoding="utf-8")
    assert "add rule inet dros_filter input_pre udp dport 51820 accept" in snippet
    assert ["nft", "-f", "/etc/nftables.conf"] in _commands(result)


def test_update_interface_rejects_invalid_openvpn_listen(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "openvpn.yaml").write_text(
        """
kind: Interface
metadata:
  name: ovpn-lab
spec:
  type: openvpn
  config: |
    client
    dev tun
  listen:
    proto: icmp
    port: 70000
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(UpdateValidationError) as exc_info:
        run_update(settings, target="iface/ovpn-lab", console=_console(StringIO()))

    message = str(exc_info.value)
    assert "spec.listen[0].proto must be tcp or udp" in message
    assert "spec.listen[0].port must be between 1 and 65535" in message


def test_update_routes_collects_all_validation_errors(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    (settings.paths.configs / "bad-routes.yaml").write_text(
        """
kind: FwMark
metadata:
  name: bad
spec:
  mark: "nope"
  mask: "0xff"
---
kind: Gateway
metadata:
  name: broken
spec:
  dev: eth0
  nexthops:
    - dev: eth1
---
kind: RouteTable
metadata:
  name: missing-gw
spec:
  table: 100
  routes:
    - to: default
      gateway: nowhere
---
kind: RouteRuleSet
metadata:
  name: bad-policy
spec:
  managedPriority:
    start: 10000
    end: 10010
  rules:
    - priority: 10020
      fwMark: missing
      lookup: missing-gw
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(UpdateValidationError) as exc_info:
        run_update(settings, target="route", console=_console(StringIO()))

    message = str(exc_info.value)
    assert "FwMark/bad" in message
    assert "Gateway/broken" in message
    assert "RouteTable/missing-gw" in message
    assert "Gateway/nowhere is not defined" in message
    assert "RouteRuleSet/bad-policy" in message
    assert "FwMark/missing is not defined" in message
    assert not (settings.sys_root / "etc/iproute2/rt_tables.d/dros.conf").exists()


def test_update_ip_list_updater_writes_and_deletes_cron_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.paths.configs.mkdir(parents=True)
    config_file = settings.paths.configs / "ip-list-updater.yaml"
    config_file.write_text(
        """
kind: IpListUpdater
metadata:
  name: system
spec:
  enabled: true
  schedule: "0 1 *"
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="ip-list-updater/system", console=_console(StringIO()))

    cron = (settings.sys_root / "etc/cron.d/dros-ip-list-updater").read_text(encoding="utf-8")
    assert "0 1 * * * root /usr/local/bin/gw ip-list update --verbose 1" in cron

    config_file.write_text(
        """
kind: IpListUpdater
metadata:
  name: system
spec:
  enabled: false
""".lstrip(),
        encoding="utf-8",
    )

    run_update(settings, target="ip-list-updater/system", console=_console(StringIO()))

    assert not (settings.sys_root / "etc/cron.d/dros-ip-list-updater").exists()
