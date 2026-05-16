from __future__ import annotations

KIND_ALIASES: dict[str, str] = {
    "devgroup": "DevGroup",
    "devgroups": "DevGroup",
    "group": "DevGroup",
    "groups": "DevGroup",
    "iface": "Interface",
    "ifaces": "Interface",
    "interface": "Interface",
    "interfaces": "Interface",
    "mirror": "SystemMirrorConfig",
    "mirrors": "SystemMirrorConfig",
    "network": "SystemNetworkConfig",
    "systemmirrorconfig": "SystemMirrorConfig",
    "systemnetworkconfig": "SystemNetworkConfig",
}


def resolve_kind_alias(kind: str) -> str:
    normalized = kind.replace("_", "").replace("-", "").lower()
    return KIND_ALIASES.get(normalized, kind)
