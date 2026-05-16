from __future__ import annotations

from pathlib import Path

from dros.cli import main as cli_main
from dros.cli.main import _extract_settings_path, _strip_global_options
from dros.settings import DrosSettings


def test_extract_settings_path_supports_separate_and_equals_forms() -> None:
    assert _extract_settings_path(["--settings", "/etc/dros/settings-test.yaml", "restart"]) == (
        "/etc/dros/settings-test.yaml"
    )
    assert _extract_settings_path(["--settings=/etc/dros/settings.yaml", "restart"]) == (
        "/etc/dros/settings.yaml"
    )


def test_strip_global_options_removes_settings_before_cyclopts() -> None:
    assert _strip_global_options(["--settings", "/etc/dros/settings-test.yaml", "restart", "web"]) == [
        "restart",
        "web",
    ]


def test_restart_command_passes_settings_path_to_service_helper(monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []

    def fake_restart_local_service(target: str, *, settings_path: str | None = None) -> str:
        calls.append((target, settings_path))
        return "dros-daemon-test.service"

    monkeypatch.setattr(cli_main, "restart_local_service", fake_restart_local_service)

    assert cli_main.main(["--settings", "/etc/dros/settings-test.yaml", "restart", "daemon"]) == 0
    assert calls == [("daemon", "/etc/dros/settings-test.yaml")]


def test_bootstrap_command_loads_settings_and_runs_bootstrap(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text(
        f"""
sysRoot: {tmp_path / "sysroot"}
paths:
  configs: {tmp_path / "configs"}
""".lstrip(),
        encoding="utf-8",
    )
    calls: list[tuple[DrosSettings, int]] = []

    def fake_ensure_bootstrap_privileges(settings: DrosSettings) -> None:
        assert settings.sys_root == tmp_path / "sysroot"

    def fake_run_bootstrap(settings: DrosSettings, *, verbose: int, console: object) -> None:
        calls.append((settings, verbose))

    monkeypatch.setattr(cli_main, "_ensure_bootstrap_privileges", fake_ensure_bootstrap_privileges)
    monkeypatch.setattr(cli_main, "run_bootstrap", fake_run_bootstrap)

    assert cli_main.main(["--settings", str(settings_file), "bootstrap", "--verbose", "2"]) == 0
    assert [(call[0].sys_root, call[1]) for call in calls] == [(tmp_path / "sysroot", 2)]


def test_config_create_prints_config_object_example(capsys) -> None:
    assert cli_main.main(["config", "create", "SystemNetworkConfig"]) == 0

    output = capsys.readouterr().out
    assert "kind: SystemNetworkConfig" in output
    assert "name: system" in output
    assert "domain: lan" in output
