from __future__ import annotations

from dataclasses import dataclass

from dros.kind_aliases import resolve_kind_alias


@dataclass(frozen=True)
class ConfigObjectCatalogEntry:
    kind: str
    example: str


CONFIG_OBJECT_CATALOG: dict[str, ConfigObjectCatalogEntry] = {
    "SystemNetworkConfig": ConfigObjectCatalogEntry(
        kind="SystemNetworkConfig",
        example="""apiVersion: dros/v1alpha1
kind: SystemNetworkConfig
metadata:
  name: system
spec:
  hostname: gateway
  domain: lan
  nfConntrackMax: 524288
""",
    ),
    "SystemMirrorConfig": ConfigObjectCatalogEntry(
        kind="SystemMirrorConfig",
        example="""apiVersion: dros/v1alpha1
kind: SystemMirrorConfig
metadata:
  name: system
spec:
  aptMirror: https://mirrors.ustc.edu.cn/debian
  dockerAptMirror: https://mirrors.ustc.edu.cn/docker-ce
  dockerRegistryMirror: ""
""",
    ),
    "DevGroup": ConfigObjectCatalogEntry(
        kind="DevGroup",
        example="""apiVersion: dros/v1alpha1
kind: DevGroup
metadata:
  name: lan
spec:
  id: 2
""",
    ),
    "Interface": ConfigObjectCatalogEntry(
        kind="Interface",
        example="""apiVersion: dros/v1alpha1
kind: Interface
metadata:
  name: br0
spec:
  type: bridge
  address: 10.0.0.1/24
  ports:
    - eth1
    - eth2
  vlanAware: true
  devGroup: lan
""",
    ),
    "FwMark": ConfigObjectCatalogEntry(
        kind="FwMark",
        example="""apiVersion: dros/v1alpha1
kind: FwMark
metadata:
  name: lab
spec:
  mark: "0x00000100"
  mask: "0x0000ff00"
""",
    ),
    "Gateway": ConfigObjectCatalogEntry(
        kind="Gateway",
        example="""apiVersion: dros/v1alpha1
kind: Gateway
metadata:
  name: wan
spec:
  dev: pppoe-wan
  via: 10.0.0.1
  onlink: true
  metric: 100
""",
    ),
    "RouteTable": ConfigObjectCatalogEntry(
        kind="RouteTable",
        example="""apiVersion: dros/v1alpha1
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
""",
    ),
    "RouteRuleSet": ConfigObjectCatalogEntry(
        kind="RouteRuleSet",
        example="""apiVersion: dros/v1alpha1
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
      lookup: wan
""",
    ),
    "Firewall": ConfigObjectCatalogEntry(
        kind="Firewall",
        example="""apiVersion: dros/v1alpha1
kind: Firewall
metadata:
  name: main
spec:
  defaults:
    inputPolicy: drop
    forwardPolicy: drop
    outputPolicy: accept
  interfaceRules:
    - subject: devgroup/lan
      input:
        ping: true
        services:
          - tcp/22
          - udp/53
      forward:
        policy: accept
""",
    ),
    "IpListUpdater": ConfigObjectCatalogEntry(
        kind="IpListUpdater",
        example="""apiVersion: dros/v1alpha1
kind: IpListUpdater
metadata:
  name: system
spec:
  enabled: true
  cron: "0 1 *"
""",
    ),
}


def render_config_object_example(kind: str) -> str:
    resolved_kind = resolve_kind_alias(kind)
    try:
        entry = CONFIG_OBJECT_CATALOG[resolved_kind]
    except KeyError as exc:
        allowed = ", ".join(sorted(CONFIG_OBJECT_CATALOG))
        raise ValueError(f"unknown ConfigObject kind: {kind}; expected one of: {allowed}") from exc
    return entry.example
