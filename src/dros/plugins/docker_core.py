from __future__ import annotations

import json

from dros.config_objects import SystemMirrorConfig
from dros.plugins.base import BootstrapContext, DrosPlugin

PACKAGES = frozenset({"docker-buildx-plugin", "docker-ce", "docker-compose-plugin"})
MANAGED_FILES = frozenset({"/etc/docker/daemon.json"})


def create_plugin() -> DrosPlugin:
    return DrosPlugin(
        name="docker.core",
        depends_on=("system.mirror",),
        packages=PACKAGES,
        managed_files=MANAGED_FILES,
        bootstrap_hook=bootstrap,
    )


def bootstrap(context: BootstrapContext) -> None:
    mirror = context.configs.resolve("SystemMirrorConfig", SystemMirrorConfig)
    config_changed = context.executor.write_file(
        "/etc/docker/daemon.json",
        _docker_daemon_config(mirror),
    )
    context.executor.install_missing_packages(PACKAGES)
    if config_changed:
        context.executor.run(["systemctl", "restart", "docker"], real_only=True)


def _docker_daemon_config(mirror: SystemMirrorConfig) -> str:
    config: dict[str, object] = {
        "ip6tables": False,
        "iptables": False,
    }
    if mirror.docker_registry_mirror:
        config["registry-mirrors"] = [mirror.docker_registry_mirror]
    return f"{json.dumps(config, indent=2, sort_keys=True)}\n"
