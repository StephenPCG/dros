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
}


def render_config_object_example(kind: str) -> str:
    resolved_kind = resolve_kind_alias(kind)
    try:
        entry = CONFIG_OBJECT_CATALOG[resolved_kind]
    except KeyError as exc:
        allowed = ", ".join(sorted(CONFIG_OBJECT_CATALOG))
        raise ValueError(f"unknown ConfigObject kind: {kind}; expected one of: {allowed}") from exc
    return entry.example
