from __future__ import annotations

from pathlib import Path

import pytest

from dros.cli.services import resolve_service_name, restart_local_service


def test_restart_local_service_uses_sudo_when_not_root() -> None:
    calls: list[tuple[list[str], bool]] = []

    def runner(command: list[str], *, check: bool) -> None:
        calls.append((command, check))

    service = restart_local_service(
        "daemon",
        runner=runner,
        systemctl_path="/bin/systemctl",
        sudo_path="/usr/bin/sudo",
        euid=1000,
    )

    assert service == "dros-daemon.service"
    assert calls == [(["/usr/bin/sudo", "/bin/systemctl", "restart", "dros-daemon.service"], True)]


def test_restart_local_service_skips_sudo_when_root() -> None:
    calls: list[tuple[list[str], bool]] = []

    def runner(command: list[str], *, check: bool) -> None:
        calls.append((command, check))

    service = restart_local_service(
        "web",
        runner=runner,
        systemctl_path="/bin/systemctl",
        sudo_path="/usr/bin/sudo",
        euid=0,
    )

    assert service == "dros-web.service"
    assert calls == [(["/bin/systemctl", "restart", "dros-web.service"], True)]


def test_restart_local_service_uses_test_services_for_test_settings() -> None:
    calls: list[tuple[list[str], bool]] = []

    def runner(command: list[str], *, check: bool) -> None:
        calls.append((command, check))

    service = restart_local_service(
        "daemon",
        settings_path=Path("/etc/dros/settings-test.yaml"),
        runner=runner,
        systemctl_path="/bin/systemctl",
        sudo_path="/usr/bin/sudo",
        euid=1000,
    )

    assert service == "dros-daemon-test.service"
    assert calls == [
        (["/usr/bin/sudo", "/bin/systemctl", "restart", "dros-daemon-test.service"], True)
    ]


def test_resolve_service_name_rejects_unknown_targets() -> None:
    with pytest.raises(ValueError, match="unsupported restart target"):
        resolve_service_name("database")

