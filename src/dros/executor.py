from __future__ import annotations

import difflib
import os
import shutil
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from dros.settings import DrosSettings

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class SystemAction:
    kind: str
    message: str
    path: str | None = None
    command: list[str] | None = None
    skipped: bool = False


class SystemExecutor:
    def __init__(
        self,
        settings: DrosSettings,
        *,
        verbose: int = 1,
        console: Console | None = None,
        runner: CommandRunner = subprocess.run,
        installed_packages: set[str] | None = None,
    ) -> None:
        self.settings = settings
        self.verbose = verbose
        self.console = console or Console()
        self.runner = runner
        self.actions: list[SystemAction] = []
        self._installed_packages_override = installed_packages
        self._apt_updated = False

    @property
    def is_real_root(self) -> bool:
        return self.settings.sys_root == Path("/")

    def target_path(self, path: str | Path) -> Path:
        logical = Path(path)
        if not logical.is_absolute():
            raise ValueError(f"managed system path must be absolute: {path}")
        if self.is_real_root:
            return logical
        return self.settings.sys_root / logical.relative_to("/")

    def exists(self, path: str | Path) -> bool:
        return self.target_path(path).exists()

    def read_text(self, path: str | Path) -> str | None:
        target = self.target_path(path)
        if not target.exists():
            return None
        return target.read_text(encoding="utf-8")

    def ensure_dir(self, path: str | Path, *, mode: int = 0o755) -> bool:
        target = self.target_path(path)
        if target.is_dir():
            return False
        target.mkdir(parents=True, exist_ok=True)
        os.chmod(target, mode)
        logical = _logical_path(path)
        self.actions.append(SystemAction(kind="create_dir", path=logical, message=f"created {logical}"))
        if self.verbose >= 1:
            self._print(f"created {logical}")
        return True

    def write_file(self, path: str | Path, content: str, *, mode: int = 0o644) -> bool:
        target = self.target_path(path)
        logical = _logical_path(path)
        old_content = target.read_text(encoding="utf-8") if target.exists() else None
        if old_content == content:
            return False

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        os.chmod(target, mode)

        self.actions.append(SystemAction(kind="write_file", path=logical, message=f"updated {logical}"))
        if self.verbose >= 1:
            self._print(f"updated {logical}")
            self._print_diff(logical, old_content, content)
        return True

    def run(
        self,
        command: Sequence[str],
        *,
        check: bool = True,
        real_only: bool = False,
        quiet: bool = False,
    ) -> subprocess.CompletedProcess[str] | None:
        command_list = [str(part) for part in command]
        skipped = real_only and not self.is_real_root
        self.actions.append(
            SystemAction(
                kind="run_command",
                command=command_list,
                message=" ".join(command_list),
                skipped=skipped,
            )
        )

        if skipped:
            if self.verbose >= 2 and not quiet:
                self._print(f"skipped command outside real sysRoot: {' '.join(command_list)}")
            return None

        if self.verbose >= 1 and not quiet:
            self._print(f"run: {' '.join(command_list)}")

        capture_output = self.verbose < 2
        result = self.runner(
            command_list,
            check=False,
            capture_output=capture_output,
            text=True,
        )
        if self.verbose >= 2 and capture_output:
            self._print_command_output(result)
        if check and result.returncode != 0:
            if capture_output:
                self._print_command_output(result)
            raise subprocess.CalledProcessError(
                result.returncode,
                command_list,
                output=result.stdout,
                stderr=result.stderr,
            )
        if self.verbose >= 1 and not quiet:
            self._print(f"ok: {' '.join(command_list)}")
        return result

    def output(
        self,
        command: Sequence[str],
        *,
        default: str = "",
        real_only: bool = True,
    ) -> str:
        if real_only and not self.is_real_root:
            return default
        command_list = [str(part) for part in command]
        self.actions.append(
            SystemAction(
                kind="run_command",
                command=command_list,
                message=" ".join(command_list),
            )
        )
        result = self.runner(
            command_list,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return default
        return result.stdout.strip()

    def install_missing_packages(self, packages: Iterable[str]) -> list[str]:
        wanted = sorted(set(packages))
        if not wanted:
            return []

        installed = self.installed_packages()
        missing = [package for package in wanted if package not in installed]
        if not missing:
            return []

        if not self._apt_updated:
            self.run(["apt-get", "update"], real_only=True)
            self._apt_updated = True
        self.run(["apt-get", "install", "-y", *missing], real_only=True)
        return missing

    def installed_packages(self) -> set[str]:
        if self._installed_packages_override is not None:
            return set(self._installed_packages_override)
        if not self.is_real_root or shutil.which("dpkg-query") is None:
            return set()

        result = self.run(
            ["dpkg-query", "-W", "-f=${binary:Package}\\n"],
            check=False,
            quiet=True,
        )
        if result is None or result.returncode != 0:
            return set()
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}

    def _print_diff(self, logical: str, old_content: str | None, new_content: str) -> None:
        old_lines = [] if old_content is None else old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"current {logical}",
            tofile=f"desired {logical}",
        )
        for line in diff:
            self._print(line.rstrip("\n"))

    def _print_command_output(self, result: subprocess.CompletedProcess[str]) -> None:
        if result.stdout:
            self._print(result.stdout.rstrip())
        if result.stderr:
            self._print(result.stderr.rstrip())

    def _print(self, message: str) -> None:
        self.console.print(message, markup=False)


def _logical_path(path: str | Path) -> str:
    return str(Path(path))
