from __future__ import annotations

from dataclasses import dataclass


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
}


def render_config_object_example(kind: str) -> str:
    try:
        entry = CONFIG_OBJECT_CATALOG[kind]
    except KeyError as exc:
        allowed = ", ".join(sorted(CONFIG_OBJECT_CATALOG))
        raise ValueError(f"unknown ConfigObject kind: {kind}; expected one of: {allowed}") from exc
    return entry.example
