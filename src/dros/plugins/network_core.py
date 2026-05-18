from __future__ import annotations

from pydantic import ValidationError

from dros.config_objects import ConfigObject, SystemNetworkConfig
from dros.plugins.base import BootstrapContext, DrosPlugin, UpdateContext

DNSMASQ_CONF = "/etc/dnsmasq.conf"
DNSMASQ_LOG_DIR = "/var/log/dnsmasq"
DNSMASQ_LOG_FILE = f"{DNSMASQ_LOG_DIR}/dnsmasq.log"
DNSMASQ_LOGROTATE_CONF = "/etc/logrotate.d/dros-dnsmasq"
CLOUD_INIT_HOSTNAME_CONF = "/etc/cloud/cloud.cfg.d/99-dros-hostname.cfg"
CLOUD_INIT_NETWORK_CONF = "/etc/cloud/cloud.cfg.d/99-dros-network.cfg"
CLOUD_INIT_NETWORK_FILES = (
    "/etc/network/interfaces.d/50-cloud-init",
    "/etc/network/interfaces.d/50-cloud-init.cfg",
    "/etc/netplan/50-cloud-init.yaml",
)
PACKAGES = frozenset(
    {
        "avahi-daemon",
        "bridge-utils",
        "dnsmasq",
        "ifupdown",
        "nftables",
        "openvpn",
        "openvpn-auth-ldap",
        "pppoe",
        "radvd",
        "tailscale",
        "vlan",
        "wide-dhcpv6-client",
        "wireguard-tools",
    }
)
MANAGED_FILES = frozenset(
    {
        "/etc/avahi/avahi-daemon.conf",
        CLOUD_INIT_HOSTNAME_CONF,
        CLOUD_INIT_NETWORK_CONF,
        *CLOUD_INIT_NETWORK_FILES,
        DNSMASQ_CONF,
        DNSMASQ_LOGROTATE_CONF,
        "/etc/hostname",
        "/etc/hosts",
        "/etc/sysctl.d/99-dros.conf",
    }
)


def create_plugin() -> DrosPlugin:
    return DrosPlugin(
        name="network.core",
        depends_on=("system.mirror",),
        config_kinds=frozenset({"SystemNetworkConfig"}),
        packages=PACKAGES,
        managed_files=MANAGED_FILES,
        bootstrap_hook=bootstrap,
        validation_hook=validate,
        update_hook=update,
    )


def bootstrap(context: BootstrapContext) -> None:
    network = context.configs.resolve("SystemNetworkConfig", SystemNetworkConfig)

    hostname_changed = context.executor.write_file("/etc/hostname", f"{network.hostname}\n")
    context.executor.write_file("/etc/hosts", _hosts_file(network))
    if _has_cloud_init(context):
        context.executor.write_file(CLOUD_INIT_HOSTNAME_CONF, _cloud_init_hostname_conf())
        if network.disable_cloud_init_network:
            context.executor.write_file(CLOUD_INIT_NETWORK_CONF, _cloud_init_network_conf())
            for path in CLOUD_INIT_NETWORK_FILES:
                context.executor.delete_file(path)
        else:
            context.executor.delete_file(CLOUD_INIT_NETWORK_CONF)

    sysctl_changed = context.executor.write_file("/etc/sysctl.d/99-dros.conf", _sysctl_file(network))
    context.executor.ensure_dir("/etc/dros/nftables.d")
    context.executor.ensure_dir(DNSMASQ_LOG_DIR)
    context.executor.write_file(DNSMASQ_CONF, _dnsmasq_conf())
    context.executor.write_file(DNSMASQ_LOGROTATE_CONF, _dnsmasq_logrotate())
    context.executor.write_file("/etc/avahi/avahi-daemon.conf", _avahi_daemon_conf())
    context.executor.install_missing_packages(PACKAGES)
    context.executor.run(
        ["systemctl", "disable", "--now", "tailscaled.service"],
        check=False,
        real_only=True,
    )
    if not network.wide_dhcpv6_client_service:
        context.executor.run(
            ["systemctl", "disable", "--now", "wide-dhcpv6-client.service"],
            check=False,
            real_only=True,
        )

    if hostname_changed and _runtime_hostname(context) != network.hostname:
        context.executor.run(["hostname", network.hostname], real_only=True)
    if sysctl_changed:
        context.executor.run(["sysctl", "--system"], real_only=True)


def validate(context: UpdateContext, objects: list[ConfigObject]) -> list[str]:
    errors: list[str] = []
    for obj in objects:
        if obj.kind != "SystemNetworkConfig":
            continue
        try:
            context.configs.resolve_object(obj, SystemNetworkConfig)
        except ValidationError as exc:
            for error in exc.errors():
                location = ".".join(str(part) for part in error["loc"])
                errors.append(f"{obj.kind}/{obj.name}: spec.{location}: {error['msg']}")
    return errors


def update(context: UpdateContext, _objects: object) -> None:
    bootstrap(context)


def _hosts_file(network: SystemNetworkConfig) -> str:
    fqdn = f"{network.hostname}.{network.domain}" if network.domain else network.hostname
    return "\n".join(
        [
            "127.0.0.1 localhost",
            f"127.0.1.1 {fqdn} {network.hostname}",
            "",
            "::1 localhost ip6-localhost ip6-loopback",
            "ff02::1 ip6-allnodes",
            "ff02::2 ip6-allrouters",
            "",
        ]
    )


def _sysctl_file(network: SystemNetworkConfig) -> str:
    return "\n".join(
        [
            "net.ipv4.ip_forward = 1",
            "net.ipv6.conf.all.forwarding = 1",
            f"net.netfilter.nf_conntrack_max = {network.nf_conntrack_max}",
            "net.core.default_qdisc = fq",
            "net.ipv4.tcp_congestion_control = bbr",
            "",
        ]
    )


def _cloud_init_hostname_conf() -> str:
    return "manage_etc_hosts: false\npreserve_hostname: true\n"


def _cloud_init_network_conf() -> str:
    return "\n".join(
        [
            "# Generated by DROS. Manual changes will be overwritten.",
            "network:",
            "  config: disabled",
            "disable_network_activation: true",
            "",
        ]
    )


def _has_cloud_init(context: BootstrapContext) -> bool:
    return context.executor.exists("/etc/cloud/cloud.cfg") or context.executor.exists(
        "/etc/cloud/cloud.cfg.d"
    )


def _runtime_hostname(context: BootstrapContext) -> str:
    return context.executor.output(["hostname"], default="")


def _avahi_daemon_conf() -> str:
    return """[server]
use-ipv4=yes
use-ipv6=yes
ratelimit-interval-usec=1000000
ratelimit-burst=1000

[wide-area]
enable-wide-area=yes

[publish]
publish-hinfo=no
publish-workstation=no

[reflector]
enable-reflector=yes

[rlimits]
rlimit-core=0
rlimit-data=8388608
rlimit-fsize=0
rlimit-nofile=768
rlimit-stack=8388608
rlimit-nproc=3
"""


def _dnsmasq_conf() -> str:
    return "\n".join(
        [
            "# Generated by DROS. Manual changes will be overwritten.",
            "log-async=25",
            "log-queries",
            f"log-facility={DNSMASQ_LOG_FILE}",
            "",
        ]
    )


def _dnsmasq_logrotate() -> str:
    return "\n".join(
        [
            "# Generated by DROS. Manual changes will be overwritten.",
            f"{DNSMASQ_LOG_FILE} {{",
            "    daily",
            "    missingok",
            "    rotate 90",
            "    compress",
            "    delaycompress",
            "    notifempty",
            "    create 0640 dnsmasq adm",
            "    sharedscripts",
            "    postrotate",
            "        if [ -f /run/dnsmasq/dnsmasq.pid ]; then",
            "            kill -USR2 \"$(cat /run/dnsmasq/dnsmasq.pid)\" 2>/dev/null || true",
            "        fi",
            "    endscript",
            "}",
            "",
        ]
    )
