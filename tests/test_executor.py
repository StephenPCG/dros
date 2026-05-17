from __future__ import annotations

import subprocess
import os
from pathlib import Path

import pytest

from dros.executor import SystemExecutor
from dros.settings import DrosPaths, DrosSettings


def _settings(tmp_path: Path) -> DrosSettings:
    return DrosSettings(
        sysRoot=tmp_path / "sysroot",
        paths=DrosPaths(
            configs=tmp_path / "configs",
            run=tmp_path / "run",
            source=tmp_path / "source",
        ),
    )


def test_run_verbose_one_discards_command_output_instead_of_capturing(tmp_path: Path) -> None:
    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert "capture_output" not in kwargs
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        return subprocess.CompletedProcess(command, 0, stdout=None, stderr=None)

    executor = SystemExecutor(_settings(tmp_path), verbose=1, runner=runner)

    executor.run(["noisy-command"])


def test_run_reports_command_timeout(tmp_path: Path) -> None:
    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 7)

    executor = SystemExecutor(_settings(tmp_path), verbose=1, runner=runner)

    with pytest.raises(RuntimeError, match="timed out after 7s"):
        executor.run(["slow-command"], timeout=7)


def test_install_missing_packages_uses_noninteractive_apt(tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    settings = DrosSettings(sysRoot=Path("/"))
    executor = SystemExecutor(
        settings,
        verbose=1,
        runner=runner,
        installed_packages=set(),
    )

    executor.install_missing_packages({"dnsmasq"})

    install_call = next(item for item in calls if "install" in item[0])
    command, kwargs = install_call
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert env["DEBIAN_FRONTEND"] == "noninteractive"
    assert env["APT_LISTCHANGES_FRONTEND"] == "none"
    assert env["NEEDRESTART_MODE"] == "a"
    assert env["UCF_FORCE_CONFOLD"] == "1"
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert command[:6] == [
        "apt-get",
        "-o",
        "Dpkg::Options::=--force-confdef",
        "-o",
        "Dpkg::Options::=--force-confold",
        "install",
    ]
    assert command[-1] == "dnsmasq"
    assert os.environ.get("DEBIAN_FRONTEND") != "noninteractive"
