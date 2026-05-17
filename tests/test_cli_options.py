from __future__ import annotations

import json
from pathlib import Path

from dros.cli import main as cli_main
from dros.cli.main import _extract_settings_path, _strip_global_options
from dros.settings import DrosSettings
from dros.update import UpdateValidationError
from dros.web.auth import WebAuthStore


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
  logs: {tmp_path / "logs"}
  run: {tmp_path / "run"}
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


def test_config_create_accepts_kind_alias(capsys) -> None:
    assert cli_main.main(["config", "create", "iface"]) == 0

    output = capsys.readouterr().out
    assert "kind: Interface" in output
    assert "type: bridge" in output


def test_config_create_accepts_ip_list_updater_alias(capsys) -> None:
    assert cli_main.main(["config", "create", "ip-list-updater"]) == 0

    output = capsys.readouterr().out
    assert "kind: IpListUpdater" in output
    assert 'schedule: "0 1 *"' in output


def test_config_create_accepts_dnsmasq_aliases(capsys) -> None:
    assert cli_main.main(["config", "create", "dns"]) == 0
    assert "kind: DnsmasqDNS" in capsys.readouterr().out

    assert cli_main.main(["config", "create", "dhcp"]) == 0
    assert "kind: DnsmasqDHCP" in capsys.readouterr().out

    assert cli_main.main(["config", "create", "dnsmasq-china-names"]) == 0
    assert "kind: DnsmasqChinaNames" in capsys.readouterr().out


def test_config_create_accepts_docker_aliases(capsys) -> None:
    assert cli_main.main(["config", "create", "docker-container"]) == 0
    assert "kind: DockerContainer" in capsys.readouterr().out

    assert cli_main.main(["config", "create", "docker-app"]) == 0
    output = capsys.readouterr().out
    assert "kind: DockerApp" in output
    assert "app: nginx" in output


def test_update_command_loads_settings_and_runs_update(monkeypatch, tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text(
        f"""
sysRoot: {tmp_path / "sysroot"}
paths:
  configs: {tmp_path / "configs"}
  logs: {tmp_path / "logs"}
  run: {tmp_path / "run"}
""".lstrip(),
        encoding="utf-8",
    )
    calls: list[tuple[DrosSettings, str | None, int]] = []

    def fake_ensure_bootstrap_privileges(settings: DrosSettings) -> None:
        assert settings.sys_root == tmp_path / "sysroot"

    def fake_run_update(
        settings: DrosSettings,
        *,
        target: str | None,
        verbose: int,
        console: object,
    ) -> None:
        calls.append((settings, target, verbose))

    monkeypatch.setattr(cli_main, "_ensure_bootstrap_privileges", fake_ensure_bootstrap_privileges)
    monkeypatch.setattr(cli_main, "run_update", fake_run_update)

    assert cli_main.main(["--settings", str(settings_file), "update", "iface/br0"]) == 0
    assert [(call[0].sys_root, call[1], call[2]) for call in calls] == [
        (tmp_path / "sysroot", "iface/br0", 1)
    ]
    log_records = [
        json.loads(line)
        for line in (tmp_path / "logs/gw-invocations.log").read_text(encoding="utf-8").splitlines()
    ]
    assert [record["phase"] for record in log_records] == ["start", "finish"]
    assert log_records[0]["kind"] == "cli"
    assert log_records[0]["argv"] == ["--settings", str(settings_file), "update", "iface/br0"]
    assert log_records[1]["exitCode"] == 0


def test_update_command_prints_all_validation_errors(monkeypatch, tmp_path: Path, capsys) -> None:
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text(
        f"""
sysRoot: {tmp_path / "sysroot"}
paths:
  configs: {tmp_path / "configs"}
  logs: {tmp_path / "logs"}
  run: {tmp_path / "run"}
""".lstrip(),
        encoding="utf-8",
    )

    def fake_ensure_bootstrap_privileges(settings: DrosSettings) -> None:
        assert settings.sys_root == tmp_path / "sysroot"

    def fake_run_update(
        settings: DrosSettings,
        *,
        target: str | None,
        verbose: int,
        console: object,
    ) -> None:
        raise UpdateValidationError(["Interface/a: bad a", "Interface/b: bad b"])

    monkeypatch.setattr(cli_main, "_ensure_bootstrap_privileges", fake_ensure_bootstrap_privileges)
    monkeypatch.setattr(cli_main, "run_update", fake_run_update)

    assert cli_main.main(["--settings", str(settings_file), "update", "ifaces"]) == 1
    output = capsys.readouterr().err
    assert "update validation failed:" in output
    assert "Interface/a: bad a" in output
    assert "Interface/b: bad b" in output


def test_hook_command_only_enqueues_event_and_logs_invocation(monkeypatch, tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text(
        f"""
sysRoot: {tmp_path / "sysroot"}
paths:
  configs: {tmp_path / "configs"}
  logs: {tmp_path / "logs"}
  run: {tmp_path / "run"}
""".lstrip(),
        encoding="utf-8",
    )

    def fail_process_event(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("gw hook should not process events inline by default")

    monkeypatch.setattr(cli_main, "process_event", fail_process_event)

    assert cli_main.main(["--settings", str(settings_file), "hook", "route-refresh", "pppoe-wan"]) == 0

    event_records = [
        json.loads(line)
        for line in (tmp_path / "run/events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert event_records == [
        {
            "event": "route-refresh",
            "iface": "pppoe-wan",
            "createdAt": event_records[0]["createdAt"],
        }
    ]
    log_records = [
        json.loads(line)
        for line in (tmp_path / "logs/gw-invocations.log").read_text(encoding="utf-8").splitlines()
    ]
    assert [record["kind"] for record in log_records] == ["cli", "event.enqueue", "cli"]
    assert [record["phase"] for record in log_records if record["kind"] == "cli"] == ["start", "finish"]


def test_web_create_user_command_creates_login_account(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.yaml"
    auth_db = tmp_path / "web-auth.sqlite3"
    settings_file.write_text(
        f"""
web:
  authDb: {auth_db}
""".lstrip(),
        encoding="utf-8",
    )

    assert cli_main.main(
        ["--settings", str(settings_file), "web", "create-user", "alice", "--password", "secret"]
    ) == 0

    store = WebAuthStore(auth_db)
    assert store.verify_password("alice", "secret")


def test_web_create_user_prompts_for_password_when_option_missing(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings_file = tmp_path / "settings.yaml"
    auth_db = tmp_path / "web-auth.sqlite3"
    settings_file.write_text(
        f"""
web:
  authDb: {auth_db}
""".lstrip(),
        encoding="utf-8",
    )
    prompts: list[str] = []
    passwords = iter(["secret", "secret"])

    def fake_getpass(prompt: str) -> str:
        prompts.append(prompt)
        return next(passwords)

    monkeypatch.setattr(cli_main.getpass, "getpass", fake_getpass)

    assert cli_main.main(["--settings", str(settings_file), "web", "create-user", "alice"]) == 0

    assert prompts == ["Password: ", "Confirm password: "]
    assert WebAuthStore(auth_db).verify_password("alice", "secret")


def test_web_passwd_command_changes_password(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.yaml"
    auth_db = tmp_path / "web-auth.sqlite3"
    settings_file.write_text(
        f"""
web:
  authDb: {auth_db}
""".lstrip(),
        encoding="utf-8",
    )
    store = WebAuthStore(auth_db)
    store.create_user("alice", "secret")

    assert cli_main.main(
        ["--settings", str(settings_file), "web", "passwd", "alice", "--password", "better"]
    ) == 0

    assert not store.verify_password("alice", "secret")
    assert store.verify_password("alice", "better")
