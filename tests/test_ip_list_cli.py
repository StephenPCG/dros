from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from dros.cli import main as cli_main


def test_ip_list_list_command_shows_detected_lists(capsys, tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.yaml"
    configs = tmp_path / "configs"
    run = tmp_path / "run"
    source = tmp_path / "source"
    (configs / "ip-lists").mkdir(parents=True)
    (run / "ip-lists").mkdir(parents=True)
    (source / "ip-lists").mkdir(parents=True)
    (configs / "ip-lists/china.v4.txt").write_text("10.0.0.0/8\n", encoding="utf-8")
    (run / "ip-lists/china.v6.txt").write_text("fd00::/8\n", encoding="utf-8")
    settings_file.write_text(
        f"""
paths:
  configs: {configs}
  run: {run}
  source: {source}
""".lstrip(),
        encoding="utf-8",
    )

    assert cli_main.main(["--settings", str(settings_file), "ip-list", "list"]) == 0

    output = capsys.readouterr().out
    assert "china" in output
    assert "v4=1" in output
    assert "v6=1" in output


def test_ip_list_update_command_uses_settings(monkeypatch, capsys, tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.yaml"
    run = tmp_path / "run"
    settings_file.write_text(
        f"""
paths:
  run: {run}
""".lstrip(),
        encoding="utf-8",
    )

    queued: list[tuple[str, str | None]] = []

    def fake_update_ip_lists(settings, *, verbose, console, timeout, selected_sources=None):  # type: ignore[no-untyped-def]
        assert settings.paths.run == run
        assert verbose == 2
        assert timeout == 10.0
        assert selected_sources == ["china"]
        return SimpleNamespace(failures=[])

    monkeypatch.setattr(cli_main, "update_ip_lists", fake_update_ip_lists)
    monkeypatch.setattr(
        cli_main,
        "enqueue_event",
        lambda _settings, event, iface=None: queued.append((event, iface)),
    )

    assert cli_main.main(
        [
            "--settings",
            str(settings_file),
            "ip-list",
            "update",
            "china",
            "--verbose",
            "2",
            "--timeout",
            "10",
        ]
    ) == 0
    assert "ip lists updated" in capsys.readouterr().out
    assert queued == [("route-refresh", None)]


def test_ip_list_sources_command_lists_builtin_sources(capsys) -> None:
    assert cli_main.main(["ip-list", "sources"]) == 0

    output = capsys.readouterr().out
    assert "china" in output
    assert "github" in output
    assert "tencent" in output
