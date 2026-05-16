from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from dros.cli.privilege import run_privileged

CommandRunner = Callable[..., object]

SERVICE_TARGETS = {
    "daemon": ("dros-daemon.service", "dros-daemon-test.service"),
    "web": ("dros-web.service", "dros-web-test.service"),
}


def service_profile_from_settings(settings_path: Path | str | None) -> str:
    if settings_path is None:
        return "release"
    if Path(settings_path).name == "settings-test.yaml":
        return "test"
    return "release"


def resolve_service_name(target: str, settings_path: Path | str | None = None) -> str:
    try:
        release_service, test_service = SERVICE_TARGETS[target]
    except KeyError as exc:
        allowed = ", ".join(sorted(SERVICE_TARGETS))
        raise ValueError(f"unsupported restart target: {target}; expected one of: {allowed}") from exc

    if service_profile_from_settings(settings_path) == "test":
        return test_service
    return release_service


def restart_local_service(
    target: str,
    *,
    settings_path: Path | str | None = None,
    runner: CommandRunner = subprocess.run,
    systemctl_path: str | None = None,
    sudo_path: str | None = None,
    euid: int | None = None,
) -> str:
    systemctl = systemctl_path or shutil.which("systemctl")
    if systemctl is None:
        raise RuntimeError("systemctl was not found; gw restart is only available on a systemd host")

    service = resolve_service_name(target, settings_path)
    run_privileged(
        [systemctl, "restart", service],
        runner=runner,
        euid=euid,
        sudo_path=sudo_path,
    )
    return service

