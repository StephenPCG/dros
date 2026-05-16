from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

CommandRunner = Callable[..., object]


def build_privileged_command(
    command: Sequence[str],
    *,
    euid: int | None = None,
    sudo_path: str | None = None,
) -> list[str]:
    if euid is None:
        euid = os.geteuid()

    if euid == 0:
        return list(command)

    sudo = sudo_path or shutil.which("sudo")
    if sudo is None:
        raise RuntimeError("sudo was not found; run as root or install sudo")

    return [sudo, *command]


def run_privileged(
    command: Sequence[str],
    *,
    runner: CommandRunner = subprocess.run,
    euid: int | None = None,
    sudo_path: str | None = None,
) -> None:
    runner(build_privileged_command(command, euid=euid, sudo_path=sudo_path), check=True)


def path_writable_for_current_user(path: Path) -> bool:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return os.access(probe, os.W_OK)


def reexec_with_sudo(command: Sequence[str], *, sudo_path: str | None = None) -> None:
    sudo = sudo_path or shutil.which("sudo")
    if sudo is None:
        raise RuntimeError("sudo was not found; run as root or install sudo")
    os.execvp(sudo, [sudo, *command])
