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
  tailscaleAptMirror: https://mirrors.ustc.edu.cn/tailscale
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
    "XfrmTransport": ConfigObjectCatalogEntry(
        kind="XfrmTransport",
        example="""apiVersion: dros/v1alpha1
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
""",
    ),
    "ConfigMap": ConfigObjectCatalogEntry(
        kind="ConfigMap",
        example="""apiVersion: dros/v1alpha1
kind: ConfigMap
metadata:
  name: nginx-config
spec:
  files:
    default.conf: |
      server {
        listen 80;
      }
""",
    ),
    "CronJob": ConfigObjectCatalogEntry(
        kind="CronJob",
        example="""apiVersion: dros/v1alpha1
kind: CronJob
metadata:
  name: refresh-routes
spec:
  schedule: "*/10 * * * *"
  user: root
  command: /usr/local/bin/gw hook route-refresh --verbose 0
  environment:
    PATH: /usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
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
  schedule: "0 1 *"
""",
    ),
    "DnsmasqDNS": ConfigObjectCatalogEntry(
        kind="DnsmasqDNS",
        example="""apiVersion: dros/v1alpha1
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
  servers:
    - 223.5.5.5
    - /corp.example.com/10.0.0.53
  locals:
    - lan
  addresses:
    - /gateway.lan/10.0.0.1
""",
    ),
    "DnsmasqDHCP": ConfigObjectCatalogEntry(
        kind="DnsmasqDHCP",
        example="""apiVersion: dros/v1alpha1
kind: DnsmasqDHCP
metadata:
  name: system
spec:
  authoritative: true
  domain: lan
  dnsServers:
    - 10.0.0.1
  ranges:
    - tag: lan
      start: 10.0.0.100
      end: 10.0.0.200
      lease: 24h
  hosts:
    - 00:11:22:33:44:55,10.0.0.10,nas
""",
    ),
    "DnsmasqChinaNames": ConfigObjectCatalogEntry(
        kind="DnsmasqChinaNames",
        example="""apiVersion: dros/v1alpha1
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
  schedule: "27 4 * * *"
""",
    ),
    "Collectd": ConfigObjectCatalogEntry(
        kind="Collectd",
        example="""apiVersion: dros/v1alpha1
kind: Collectd
metadata:
  name: system
spec:
  interval: 10
  plugins:
    ping:
      enabled: true
      hosts:
        - 1.1.1.1
        - 8.8.8.8
""",
    ),
    "IPv6PD": ConfigObjectCatalogEntry(
        kind="IPv6PD",
        example="""apiVersion: dros/v1alpha1
kind: IPv6PD
metadata:
  name: system
spec:
  uplink: pppoe-wan
  prefixLengthHint: 60
  delegatedPrefixLength: 60
  acceptRA: 2
  dnsServers:
    - fd02::1
  searchDomains:
    - lan
  downstream:
    - iface: br-lan
      subnetId: 2
      ulaPrefix: fd02::/64
""",
    ),
    "ResolvConf": ConfigObjectCatalogEntry(
        kind="ResolvConf",
        example="""apiVersion: dros/v1alpha1
kind: ResolvConf
metadata:
  name: system
spec:
  nameservers:
    - 10.0.0.1
    - 223.5.5.5
  search:
    - lan
  options:
    - timeout:2
    - attempts:2
""",
    ),
    "DockerContainer": ConfigObjectCatalogEntry(
        kind="DockerContainer",
        example="""apiVersion: dros/v1alpha1
kind: DockerContainer
metadata:
  name: web-demo
spec:
  image: nginx:stable-alpine
  network: default
  restart: unless-stopped
  environment:
    TZ: Asia/Shanghai
  mounts:
    - sourceType: inline
      name: default.conf
      target: /etc/nginx/conf.d/default.conf
      mode: ro
      source: |
        server {
          listen 80;
        }
""",
    ),
    "DockerApp": ConfigObjectCatalogEntry(
        kind="DockerApp",
        example="""apiVersion: dros/v1alpha1
kind: DockerApp
metadata:
  name: nginx
spec:
  app: nginx
  variant: openresty
  network: default
  nginxConfFile:
    sourceType: inline
    name: nginx.conf
    source: |
      events {}
      http {
        include /etc/nginx/conf.d/*.conf;
      }
""",
    ),
    "DockerDNS": ConfigObjectCatalogEntry(
        kind="DockerDNS",
        example="""apiVersion: dros/v1alpha1
kind: DockerDNS
metadata:
  name: system
spec:
  enabled: true
  suffix: containers.lan
  file: /etc/dnsmasq.d/dros-40-containers.conf
  hostNetworkAddress: 10.0.0.1
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
