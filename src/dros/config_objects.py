from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from dros.settings import DrosSettings

DEFAULT_OBJECT_NAME = "default"


class SystemNetworkConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    hostname: str = "gateway"
    domain: str = "lan"
    nf_conntrack_max: int = Field(524288, alias="nfConntrackMax")


class SystemMirrorConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    apt_mirror: str = Field("https://mirrors.ustc.edu.cn/debian", alias="aptMirror")
    docker_apt_mirror: str = Field(
        "https://mirrors.ustc.edu.cn/docker-ce",
        alias="dockerAptMirror",
    )
    docker_registry_mirror: str = Field("", alias="dockerRegistryMirror")


class DevGroupConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int


class InterfaceConfig(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    type: Literal[
        "eth",
        "bridge",
        "vlan",
        "loopback",
        "docker",
        "gre",
        "pppoe",
        "wireguard",
        "openvpn",
    ]
    dhcp: bool = False
    address: str | None = None
    gateway: str | None = None
    extra_addresses: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("extra_addresses", "extraAddresses"),
    )
    devgroup: str | None = Field(
        None,
        validation_alias=AliasChoices("devgroup", "devGroup"),
    )
    ports: list[str] = Field(default_factory=list)
    vlan_aware: bool = Field(
        False,
        validation_alias=AliasChoices("vlan_aware", "vlanAware"),
    )
    parent: str | None = None
    id: int | None = None
    subnet: str | None = None
    local_vip: str | None = Field(
        None,
        validation_alias=AliasChoices("local_vip", "localVip"),
    )
    remote_vip: str | None = Field(
        None,
        validation_alias=AliasChoices("remote_vip", "remoteVip"),
    )
    local_public_ip: str | None = Field(
        None,
        validation_alias=AliasChoices("local_public_ip", "localPublicIp"),
    )
    remote_public_ip: str | None = Field(
        None,
        validation_alias=AliasChoices("remote_public_ip", "remotePublicIp"),
    )
    xfrm_transport: str | None = Field(
        None,
        validation_alias=AliasChoices("xfrm_transport", "xfrmTransport"),
    )
    ttl: int = 255
    device: str | None = None
    user: str | None = None
    password: str | None = None
    hide_password: bool = Field(
        True,
        validation_alias=AliasChoices("hide_password", "hidePassword"),
    )
    noauth: bool = True
    maxfail: int = 0
    persist: bool = True
    debug: bool = True
    holdoff: int = 5
    manage_device: bool = Field(
        True,
        validation_alias=AliasChoices("manage_device", "manageDevice"),
    )
    noipdefault: bool = True
    defaultroute: bool = True
    replacedefaultroute: bool = False
    noproxyarp: bool = True
    ipv6: bool = True
    ipv6cp_use_ipaddr: bool = Field(
        True,
        validation_alias=AliasChoices("ipv6cp_use_ipaddr", "ipv6cpUseIpaddr"),
    )
    use_peer_dns: bool = Field(
        False,
        validation_alias=AliasChoices("use_peer_dns", "usePeerDNS"),
    )
    private_key: str | None = Field(
        None,
        validation_alias=AliasChoices("private_key", "privateKey"),
    )
    private_key_file: str | None = Field(
        None,
        validation_alias=AliasChoices("private_key_file", "privateKeyFile"),
    )
    listen_port: int | None = Field(
        None,
        validation_alias=AliasChoices("listen_port", "listenPort"),
    )
    peers: list[dict[str, Any]] = Field(default_factory=list)
    config: str | None = None
    config_file: str | None = Field(
        None,
        validation_alias=AliasChoices("config_file", "configFile"),
    )
    crl_file: str | None = Field(
        None,
        validation_alias=AliasChoices("crl_file", "crlFile"),
    )
    up: str | None = None


@dataclass(frozen=True)
class ConfigObjectKey:
    kind: str
    name: str = DEFAULT_OBJECT_NAME


@dataclass(frozen=True)
class ConfigObject:
    kind: str
    name: str
    metadata: dict[str, object]
    spec: dict[str, object]
    source: Path

    @property
    def key(self) -> ConfigObjectKey:
        return ConfigObjectKey(self.kind, self.name)


ModelT = TypeVar("ModelT", bound=BaseModel)


class ConfigStore:
    def __init__(self, objects: dict[ConfigObjectKey, ConfigObject] | None = None) -> None:
        self._objects = objects or {}

    def get(self, kind: str, name: str = DEFAULT_OBJECT_NAME) -> ConfigObject | None:
        return self._objects.get(ConfigObjectKey(kind, name))

    def require(self, kind: str, name: str = DEFAULT_OBJECT_NAME) -> ConfigObject:
        obj = self.get(kind, name)
        if obj is None:
            raise KeyError(f"ConfigObject not found: {kind}/{name}")
        return obj

    def resolve(
        self,
        kind: str,
        model_type: type[ModelT],
        name: str = DEFAULT_OBJECT_NAME,
    ) -> ModelT:
        obj = self.get(kind, name)
        if obj is None and name == DEFAULT_OBJECT_NAME:
            obj = self._unique_object_for_kind(kind)
        data = obj.spec if obj is not None else {}
        return model_type.model_validate(data)

    def resolve_object(self, obj: ConfigObject, model_type: type[ModelT]) -> ModelT:
        return model_type.model_validate(obj.spec)

    def by_kind(self, kind: str) -> list[ConfigObject]:
        return [obj for key, obj in self._objects.items() if key.kind == kind]

    def objects(self) -> list[ConfigObject]:
        return list(self._objects.values())

    def _unique_object_for_kind(self, kind: str) -> ConfigObject | None:
        matches = [obj for key, obj in self._objects.items() if key.kind == kind]
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        names = ", ".join(sorted(obj.name for obj in matches))
        raise ValueError(f"multiple ConfigObjects found for singleton {kind}: {names}")


def load_config_objects(settings: DrosSettings) -> ConfigStore:
    objects: dict[ConfigObjectKey, ConfigObject] = {}
    for config_dir in settings.paths.config_dirs():
        if not config_dir.exists():
            continue
        if not config_dir.is_dir():
            raise ValueError(f"ConfigObject path is not a directory: {config_dir}")

        for yaml_file in sorted(_iter_yaml_files(config_dir)):
            for raw_doc in yaml.safe_load_all(yaml_file.read_text(encoding="utf-8")):
                if raw_doc is None:
                    continue
                obj = _parse_config_object(raw_doc, yaml_file)
                if obj is None:
                    continue
                objects[obj.key] = obj
    return ConfigStore(objects)


def _iter_yaml_files(config_dir: Path) -> list[Path]:
    return [
        path
        for path in config_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
    ]


def _parse_config_object(raw_doc: object, source: Path) -> ConfigObject | None:
    if not isinstance(raw_doc, dict):
        raise ValueError(f"ConfigObject document must be a mapping: {source}")

    kind = raw_doc.get("kind")
    if not isinstance(kind, str) or not kind:
        raise ValueError(f"ConfigObject document is missing kind: {source}")

    metadata = raw_doc.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"ConfigObject metadata must be a mapping: {source}")
    if metadata.get("disabled") is True:
        return None

    raw_name = metadata.get("name", DEFAULT_OBJECT_NAME)
    if not isinstance(raw_name, str) or not raw_name:
        raise ValueError(f"ConfigObject metadata.name must be a non-empty string: {source}")

    spec = raw_doc.get("spec") or {}
    if not isinstance(spec, dict):
        raise ValueError(f"ConfigObject spec must be a mapping: {source}")

    return ConfigObject(
        kind=kind,
        name=raw_name,
        metadata=dict(metadata),
        spec=dict(spec),
        source=source,
    )
