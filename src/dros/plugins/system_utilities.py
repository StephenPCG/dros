from __future__ import annotations

from dros.plugins.base import BootstrapContext, DrosPlugin

PACKAGES = frozenset(
    {
        "apt-file",
        "bind9-dnsutils",
        "ethtool",
        "iftop",
        "mtr-tiny",
        "net-tools",
        "netcat-openbsd",
        "sysstat",
        "tcpdump",
        "traceroute",
    }
)


def create_plugin() -> DrosPlugin:
    return DrosPlugin(
        name="system.utilities",
        depends_on=("system.mirror",),
        packages=PACKAGES,
        bootstrap_hook=bootstrap,
    )


def bootstrap(context: BootstrapContext) -> None:
    context.executor.install_missing_packages(PACKAGES)
