from __future__ import annotations

KIND_ALIASES: dict[str, str] = {
    "devgroup": "DevGroup",
    "devgroups": "DevGroup",
    "dhcp": "DnsmasqDHCP",
    "dns": "DnsmasqDNS",
    "dnsmasq": "Dnsmasq",
    "chinaname": "DnsmasqChinaNames",
    "chinanames": "DnsmasqChinaNames",
    "dnsmasqchinaname": "DnsmasqChinaNames",
    "dnsmasqchinanames": "DnsmasqChinaNames",
    "dnsmasqdhcp": "DnsmasqDHCP",
    "dnsmasqdhcps": "DnsmasqDHCP",
    "dnsmasqdns": "DnsmasqDNS",
    "dnsmasqdnss": "DnsmasqDNS",
    "docker": "Docker",
    "dockers": "Docker",
    "dockerapp": "DockerApp",
    "dockerapps": "DockerApp",
    "dockercontainer": "DockerContainer",
    "dockercontainers": "DockerContainer",
    "firewall": "Firewall",
    "firewalls": "Firewall",
    "fwmark": "FwMark",
    "fwmarks": "FwMark",
    "gateway": "Gateway",
    "gateways": "Gateway",
    "group": "DevGroup",
    "groups": "DevGroup",
    "iface": "Interface",
    "ifaces": "Interface",
    "interface": "Interface",
    "interfaces": "Interface",
    "iplistupdater": "IpListUpdater",
    "iplistupdaters": "IpListUpdater",
    "mirror": "SystemMirrorConfig",
    "mirrors": "SystemMirrorConfig",
    "nft": "Firewall",
    "nftruleset": "Firewall",
    "nftrulesets": "Firewall",
    "network": "SystemNetworkConfig",
    "route": "Route",
    "routeruleset": "RouteRuleSet",
    "routerulesets": "RouteRuleSet",
    "routes": "Route",
    "routetable": "RouteTable",
    "routetables": "RouteTable",
    "ruleset": "RouteRuleSet",
    "rulesets": "RouteRuleSet",
    "systemmirrorconfig": "SystemMirrorConfig",
    "systemnetworkconfig": "SystemNetworkConfig",
}

KIND_GROUPS: dict[str, frozenset[str]] = {
    "Docker": frozenset({"DockerContainer", "DockerApp"}),
    "Dnsmasq": frozenset({"DnsmasqDNS", "DnsmasqDHCP", "DnsmasqChinaNames"}),
    "Route": frozenset({"FwMark", "Gateway", "RouteTable", "RouteRuleSet"}),
}


def resolve_kind_alias(kind: str) -> str:
    normalized = kind.replace("_", "").replace("-", "").lower()
    return KIND_ALIASES.get(normalized, kind)


def resolve_kind_group(kind: str) -> frozenset[str] | None:
    return KIND_GROUPS.get(kind)
