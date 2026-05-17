from __future__ import annotations

import subprocess
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
