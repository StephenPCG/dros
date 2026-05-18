from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from dros.kind_aliases import resolve_kind_alias
from dros.settings import DrosSettings

API_VERSION = "dros/v1alpha1"
DEFAULT_OBJECT_NAME = "default"


class ConfigObjectLoadError(ValueError):
    pass


class SystemNetworkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    hostname: str = "gateway"
    domain: str = "lan"
    nf_conntrack_max: int = Field(524288, alias="nfConntrackMax")


class SystemMirrorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    apt_mirror: str = Field("https://mirrors.ustc.edu.cn/debian", alias="aptMirror")
    docker_apt_mirror: str = Field(
        "https://mirrors.ustc.edu.cn/docker-ce",
        alias="dockerAptMirror",
    )
    docker_registry_mirror: str = Field("", alias="dockerRegistryMirror")
    tailscale_apt_mirror: str = Field(
        "https://mirrors.ustc.edu.cn/tailscale",
        alias="tailscaleAptMirror",
    )


class DevGroupConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: int


class FwMarkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    mark: int | str = Field(validation_alias=AliasChoices("mark", "value"))
    mask: int | str = "0xffffffff"


class GatewayHopConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    dev: str
    via: str | None = None
    weight: int | None = None
    onlink: bool = False


class GatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    dev: str | None = None
    via: str | None = None
    onlink: bool = False
    metric: int | None = None
    nexthops: list[GatewayHopConfig] = Field(default_factory=list)


class RouteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    to: str | None = None
    gateway: str | None = None
    type: Literal["route", "unreachable", "blackhole", "prohibit"] = "route"
    metric: int | None = None
    raw: str | None = None


class RouteTableConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    family: Literal["ipv4", "ipv6"] = "ipv4"
    table: int | str = Field(validation_alias=AliasChoices("table", "id"))
    routes: list[RouteConfig] = Field(default_factory=list)


class ManagedPriorityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    start: int
    end: int


class RouteRuleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    priority: int
    lookup: int | str | None = None
    fw_mark: str | None = Field(
        None,
        validation_alias=AliasChoices("fw_mark", "fwMark"),
    )
    fwmark: str | None = None
    from_: str | None = Field(None, validation_alias=AliasChoices("from", "from_"))
    to: str | None = None
    iif: str | None = None
    oif: str | None = None
    uidrange: str | None = None
    suppress_prefixlength: int | None = Field(
        None,
        validation_alias=AliasChoices("suppress_prefixlength", "suppressPrefixlength"),
    )
    raw: str | None = None


class RouteRuleSetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    family: Literal["ipv4", "ipv6"] = "ipv4"
    managed_priority: ManagedPriorityConfig = Field(
        validation_alias=AliasChoices("managed_priority", "managedPriority"),
    )
    rules: list[RouteRuleConfig] = Field(default_factory=list)


class FirewallConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    defaults: dict[str, Any] = Field(default_factory=dict)
    interface_rules: list[dict[str, Any]] = Field(
        default_factory=list,
        validation_alias=AliasChoices("interface_rules", "interfaceRules"),
    )
    nat_rules: list[dict[str, Any]] = Field(
        default_factory=list,
        validation_alias=AliasChoices("nat_rules", "natRules"),
    )
    firewall_rules: list[dict[str, Any]] = Field(
        default_factory=list,
        validation_alias=AliasChoices("firewall_rules", "firewallRules"),
    )
    mark_rules: list[dict[str, Any]] = Field(
        default_factory=list,
        validation_alias=AliasChoices("mark_rules", "markRules"),
    )


class IpListUpdaterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = True
    schedule: str = Field("0 1 *", validation_alias=AliasChoices("schedule", "cron"))


class CronJobConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = True
    schedule: str
    user: str = "root"
    command: str
    environment: dict[str, str] = Field(default_factory=dict)
    comment: str | None = None


class ConfigMapConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    files: dict[str, str] = Field(default_factory=dict)


class DnsmasqDNSConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = True
    interfaces: list[str] = Field(default_factory=list)
    except_interfaces: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("except_interfaces", "exceptInterfaces"),
    )
    listen_addresses: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("listen_addresses", "listenAddresses"),
    )
    bind_interfaces: bool = Field(
        False,
        validation_alias=AliasChoices("bind_interfaces", "bindInterfaces"),
    )
    no_resolv: bool = Field(True, validation_alias=AliasChoices("no_resolv", "noResolv"))
    no_negcache: bool = Field(True, validation_alias=AliasChoices("no_negcache", "noNegcache"))
    no_hosts: bool = Field(True, validation_alias=AliasChoices("no_hosts", "noHosts"))
    all_servers: bool = Field(False, validation_alias=AliasChoices("all_servers", "allServers"))
    bogus_priv: bool = Field(True, validation_alias=AliasChoices("bogus_priv", "bogusPriv"))
    domain_needed: bool = Field(
        True,
        validation_alias=AliasChoices("domain_needed", "domainNeeded"),
    )
    expand_hosts: bool = Field(False, validation_alias=AliasChoices("expand_hosts", "expandHosts"))
    local_ttl: int | None = Field(None, validation_alias=AliasChoices("local_ttl", "localTtl"))
    cache_size: int | None = Field(None, validation_alias=AliasChoices("cache_size", "cacheSize"))
    log_queries: bool = Field(False, validation_alias=AliasChoices("log_queries", "logQueries"))
    log_async: int | None = Field(None, validation_alias=AliasChoices("log_async", "logAsync"))
    log_file: str | None = Field(None, validation_alias=AliasChoices("log_file", "logFile"))
    port: int | None = None
    conf_files: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("conf_files", "confFiles"),
    )
    conf_dirs: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("conf_dirs", "confDirs"),
    )
    addn_hosts: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("addn_hosts", "addnHosts"),
    )
    servers: list[str] = Field(default_factory=list)
    locals: list[str] = Field(default_factory=list)
    addresses: list[str] = Field(default_factory=list)
    host_records: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("host_records", "hostRecords"),
    )
    srv_hosts: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("srv_hosts", "srvHosts"),
    )
    cname_records: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("cname_records", "cnameRecords"),
    )
    raw_options: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("raw_options", "rawOptions"),
    )
    raw: list[str] = Field(default_factory=list)


class DnsmasqDHCPRangeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    tag: str
    start: str
    end: str | None = None
    netmask: str | None = None
    broadcast: str | None = None
    router: str | None = None
    mode: str | None = None
    lease: str = "24h"


class DnsmasqDHCPConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = True
    authoritative: bool = False
    domain: str | None = None
    dns_servers: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("dns_servers", "dnsServers"),
    )
    v6_dns_servers: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("v6_dns_servers", "v6DnsServers"),
    )
    options: list[str] = Field(default_factory=list)
    ranges: list[DnsmasqDHCPRangeConfig] = Field(default_factory=list)
    hosts: list[str] = Field(default_factory=list)
    raw_options: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("raw_options", "rawOptions"),
    )
    raw: list[str] = Field(default_factory=list)


class DnsmasqChinaNamesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = True
    servers: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    output_dir: str | None = Field(
        None,
        validation_alias=AliasChoices("output_dir", "outputDir"),
    )
    manual_names: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("manual_names", "manualNames"),
    )
    manual_name_files: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("manual_name_files", "manualNameFiles"),
    )
    cron_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("cron_enabled", "cronEnabled"),
    )
    schedule: str = "27 4 * * *"
    command: str | None = None


class CollectdInterfacePluginConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = True
    ignore: list[str] = Field(default_factory=lambda: ["/^veth/"])


class CollectdPingPluginConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = False
    hosts: list[str] = Field(default_factory=list)


class CollectdSensorsPluginConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = False


class CollectdPluginsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    interface: CollectdInterfacePluginConfig = Field(
        default_factory=CollectdInterfacePluginConfig
    )
    ping: CollectdPingPluginConfig = Field(default_factory=CollectdPingPluginConfig)
    sensors: CollectdSensorsPluginConfig = Field(
        default_factory=CollectdSensorsPluginConfig
    )


class CollectdConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = True
    interval: int = Field(10, ge=1)
    rrd_dir: str = Field(
        "/var/lib/collectd/rrd",
        validation_alias=AliasChoices("rrd_dir", "rrdDir"),
    )
    unix_sock: bool = Field(
        True,
        validation_alias=AliasChoices("unix_sock", "unixSock"),
    )
    unix_sock_path: str = Field(
        "/run/collectd-unixsock",
        validation_alias=AliasChoices("unix_sock_path", "unixSockPath"),
    )
    plugins: CollectdPluginsConfig = Field(default_factory=CollectdPluginsConfig)
    raw: list[str] = Field(default_factory=list)


class ResolvConfConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    nameservers: list[str] = Field(default_factory=list)
    search: list[str] = Field(default_factory=list)
    options: list[str] = Field(default_factory=list)


class IPv6PDDownstreamConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    iface: str
    subnet_id: int = Field(validation_alias=AliasChoices("subnet_id", "subnetId"))
    prefix_length: int = Field(
        64,
        validation_alias=AliasChoices("prefix_length", "prefixLength"),
    )
    address: str = "::1"
    delegated: bool = True
    ula_prefix: str | None = Field(
        None,
        validation_alias=AliasChoices("ula_prefix", "ulaPrefix"),
    )
    advertise: bool = True
    rdnss: list[str] = Field(default_factory=list)
    dnssl: list[str] = Field(default_factory=list)


class IPv6PDConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = True
    client: Literal["wide-dhcpv6"] = "wide-dhcpv6"
    duid: str | None = None
    uplink: str | None = None
    iaid: int = 1
    prefix_length_hint: int | None = Field(
        60,
        validation_alias=AliasChoices("prefix_length_hint", "prefixLengthHint"),
    )
    delegated_prefix_length: int | None = Field(
        None,
        validation_alias=AliasChoices("delegated_prefix_length", "delegatedPrefixLength"),
    )
    request_address: bool = Field(
        False,
        validation_alias=AliasChoices("request_address", "requestAddress"),
    )
    accept_ra: int | None = Field(
        2,
        validation_alias=AliasChoices("accept_ra", "acceptRA"),
    )
    dns_servers: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("dns_servers", "dnsServers"),
    )
    search_domains: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("search_domains", "searchDomains"),
    )
    downstream: list[IPv6PDDownstreamConfig] = Field(default_factory=list)


class DockerMountConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source_type: Literal["configMap", "inline", "file", "dir", "data-dir"] = Field(
        validation_alias=AliasChoices("source_type", "sourceType"),
    )
    source: str | None = None
    key: str | None = None
    target: str
    mode: Literal["ro", "rw"] = "rw"
    name: str | None = None


class DockerAppFileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source_type: Literal["configMap", "inline", "file", "dir"] = Field(
        validation_alias=AliasChoices("source_type", "sourceType"),
    )
    source: str
    key: str | None = None
    target: str | None = None
    mode: Literal["ro", "rw"] = "ro"
    name: str | None = None


class DockerAppNginxConfFileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    source_type: Literal["configMap", "inline", "file"] = Field(
        validation_alias=AliasChoices("source_type", "sourceType"),
    )
    source: str
    key: str | None = None
    mode: Literal["ro", "rw"] = "ro"
    name: str | None = None


class DockerContainerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = True
    image: str
    network: str = "default"
    restart: Literal["no", "always", "on-failure", "unless-stopped"] = "unless-stopped"
    environment: dict[str, str] = Field(default_factory=dict)
    mounts: list[DockerMountConfig] = Field(default_factory=list)
    cap_add: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("cap_add", "capAdd"),
    )
    cap_drop: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("cap_drop", "capDrop"),
    )
    devices: list[str] = Field(default_factory=list)
    privileged: bool = False
    command: str | list[str] | None = None
    dns_names: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("dns_names", "dnsNames"),
    )
    additional_domains: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("additional_domains", "additionalDomains"),
    )


class DockerAppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = True
    app: Literal["vlmcsd", "nginx", "ddns-go", "unifi", "certimate", "headscale"]
    variant: Literal["openresty", "nginx"] | None = None
    image: str | None = None
    image_name: str | None = Field(
        None,
        validation_alias=AliasChoices("image_name", "imageName"),
    )
    image_tag: str | None = Field(
        None,
        validation_alias=AliasChoices("image_tag", "imageTag"),
    )
    network: str = "default"
    nginx_conf_file: DockerAppNginxConfFileConfig | None = Field(
        None,
        validation_alias=AliasChoices("nginx_conf_file", "nginxConfFile"),
    )
    conf_files: list[DockerAppFileConfig] = Field(
        default_factory=list,
        validation_alias=AliasChoices("conf_files", "confFiles", "files"),
    )
    mounts: list[DockerMountConfig] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)
    dns_names: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("dns_names", "dnsNames"),
    )
    additional_domains: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("additional_domains", "additionalDomains"),
    )
    server_url: str | None = Field(
        None,
        validation_alias=AliasChoices("server_url", "serverUrl"),
    )
    listen_addr: str = Field(
        "0.0.0.0:8443",
        validation_alias=AliasChoices("listen_addr", "listenAddr"),
    )
    metrics_listen_addr: str = Field(
        "127.0.0.1:9090",
        validation_alias=AliasChoices("metrics_listen_addr", "metricsListenAddr"),
    )
    raw_config: str | None = Field(
        None,
        validation_alias=AliasChoices("raw_config", "rawConfig"),
    )


class DockerDNSConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = True
    suffix: str = "containers.lan"
    file: str = "/etc/dnsmasq.d/dros-40-containers.conf"
    host_network_address: str | None = Field(
        None,
        validation_alias=AliasChoices("host_network_address", "hostNetworkAddress"),
    )
    host_network_addresses: dict[str, str] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("host_network_addresses", "hostNetworkAddresses"),
    )


class XfrmTransportSelectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    proto: Literal["gre"] = "gre"


class XfrmTransportPartyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str | None = None
    public_ip: str = Field(validation_alias=AliasChoices("public_ip", "publicIp"))
    private_ip: str | None = Field(
        None,
        validation_alias=AliasChoices("private_ip", "privateIp"),
    )


class XfrmTransportDirectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    party_a_to_party_b: int | str = Field(
        validation_alias=AliasChoices("party_a_to_party_b", "partyAToPartyB"),
    )
    party_b_to_party_a: int | str = Field(
        validation_alias=AliasChoices("party_b_to_party_a", "partyBToPartyA"),
    )


class XfrmTransportKeysConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    party_a_to_party_b: str = Field(
        validation_alias=AliasChoices("party_a_to_party_b", "partyAToPartyB"),
    )
    party_b_to_party_a: str = Field(
        validation_alias=AliasChoices("party_b_to_party_a", "partyBToPartyA"),
    )


class XfrmTransportAeadConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: Literal["rfc4106(gcm(aes))"] = "rfc4106(gcm(aes))"
    icv_bits: int = Field(128, validation_alias=AliasChoices("icv_bits", "icvBits"))


class XfrmTransportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = True
    activation: Literal["manual", "system"] = "manual"
    local_party: Literal["partyA", "partyB"] = Field(
        validation_alias=AliasChoices("local_party", "localParty"),
    )
    selector: XfrmTransportSelectorConfig = Field(
        default_factory=XfrmTransportSelectorConfig
    )
    party_a: XfrmTransportPartyConfig = Field(
        validation_alias=AliasChoices("party_a", "partyA"),
    )
    party_b: XfrmTransportPartyConfig = Field(
        validation_alias=AliasChoices("party_b", "partyB"),
    )
    spi: XfrmTransportDirectionConfig
    reqid: XfrmTransportDirectionConfig
    keys: XfrmTransportKeysConfig
    aead: XfrmTransportAeadConfig = Field(default_factory=XfrmTransportAeadConfig)


class WireGuardWgsdClientConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = True
    dns: str
    zone: str
    schedule: str = "* * * * *"


class InterfaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal[
        "eth",
        "ethernet",
        "bridge",
        "vlan",
        "loopback",
        "external",
        "docker",
        "gre",
        "pppoe",
        "wireguard",
        "openvpn",
        "tailscale",
    ]
    auto: bool = True
    allow_hotplug: bool = Field(
        False,
        validation_alias=AliasChoices("allow_hotplug", "allowHotplug"),
    )
    dhcp: bool = False
    address: str | None = None
    addresses: list[str] = Field(default_factory=list)
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
    stp: bool = False
    forward_delay: int = Field(
        0,
        validation_alias=AliasChoices("forward_delay", "forwardDelay"),
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
    defaultroute6: bool = True
    nodefaultroute: bool = True
    nodefaultroute6: bool = True
    noreplacedefaultroute: bool = True
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
    wgsd_client: WireGuardWgsdClientConfig | None = Field(
        None,
        validation_alias=AliasChoices("wgsd_client", "wgsdClient"),
    )
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
    listen: dict[str, Any] | list[dict[str, Any]] | None = None
    login_server: str | None = Field(
        None,
        validation_alias=AliasChoices("login_server", "loginServer"),
    )
    hostname: str | None = None
    accept_routes: bool = Field(
        False,
        validation_alias=AliasChoices("accept_routes", "acceptRoutes"),
    )
    accept_dns: bool = Field(
        False,
        validation_alias=AliasChoices("accept_dns", "acceptDns"),
    )
    netfilter_mode: Literal["off", "nodivert", "on"] = Field(
        "off",
        validation_alias=AliasChoices("netfilter_mode", "netfilterMode"),
    )
    advertise_routes: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("advertise_routes", "advertiseRoutes"),
    )
    advertise_tags: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("advertise_tags", "advertiseTags"),
    )
    ssh: bool = False
    shields_up: bool = Field(
        False,
        validation_alias=AliasChoices("shields_up", "shieldsUp"),
    )
    snat_subnet_routes: bool | None = Field(
        None,
        validation_alias=AliasChoices("snat_subnet_routes", "snatSubnetRoutes"),
    )
    stateful_filtering: bool | None = Field(
        None,
        validation_alias=AliasChoices("stateful_filtering", "statefulFiltering"),
    )
    operator: str | None = None
    state_dir: str | None = Field(
        None,
        validation_alias=AliasChoices("state_dir", "stateDir"),
    )
    port: int | None = None
    no_logs_no_support: bool = Field(
        False,
        validation_alias=AliasChoices("no_logs_no_support", "noLogsNoSupport"),
    )
    extra_daemon_args: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("extra_daemon_args", "extraDaemonArgs"),
    )
    extra_up_args: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("extra_up_args", "extraUpArgs"),
    )
    up_timeout: str = Field(
        "10s",
        validation_alias=AliasChoices("up_timeout", "upTimeout"),
    )

    @field_validator("netfilter_mode", mode="before")
    @classmethod
    def _normalize_netfilter_mode(cls, value: object) -> object:
        if value is False:
            return "off"
        if value is True:
            return "on"
        return value


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
            raise ConfigObjectLoadError(f"ConfigObject path is not a directory: {config_dir}")

        seen_in_dir: dict[ConfigObjectKey, ConfigObject] = {}
        for yaml_file in sorted(_iter_yaml_files(config_dir)):
            try:
                documents = list(yaml.safe_load_all(yaml_file.read_text(encoding="utf-8")))
            except Exception as exc:
                raise ConfigObjectLoadError(f"{yaml_file}: failed to parse YAML: {exc}") from exc
            for raw_doc in documents:
                if raw_doc is None:
                    continue
                obj = _parse_config_object(raw_doc, yaml_file)
                if obj is None:
                    continue
                previous = seen_in_dir.get(obj.key)
                if previous is not None:
                    raise ConfigObjectLoadError(
                        f"{yaml_file}: duplicate {obj.kind}/{obj.name} already defined "
                        f"in same config directory at {previous.source}"
                    )
                seen_in_dir[obj.key] = obj
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
        raise ConfigObjectLoadError(f"ConfigObject document must be a mapping: {source}")

    api_version = raw_doc.get("apiVersion")
    if api_version is not None and api_version != API_VERSION:
        raise ConfigObjectLoadError(
            f"{source}: unsupported apiVersion {api_version!r}; expected {API_VERSION!r}"
        )

    kind = raw_doc.get("kind")
    if not isinstance(kind, str) or not kind:
        raise ConfigObjectLoadError(f"ConfigObject document is missing kind: {source}")
    kind = resolve_kind_alias(kind)

    metadata = raw_doc.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ConfigObjectLoadError(f"ConfigObject metadata must be a mapping: {source}")
    if metadata.get("disabled") is True:
        return None

    raw_name = metadata.get("name", DEFAULT_OBJECT_NAME)
    if not isinstance(raw_name, str) or not raw_name:
        raise ConfigObjectLoadError(
            f"ConfigObject metadata.name must be a non-empty string: {source}"
        )

    spec = raw_doc.get("spec") or {}
    if not isinstance(spec, dict):
        raise ConfigObjectLoadError(f"ConfigObject spec must be a mapping: {source}")

    return ConfigObject(
        kind=kind,
        name=raw_name,
        metadata=dict(metadata),
        spec=dict(spec),
        source=source,
    )
