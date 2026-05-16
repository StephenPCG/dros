from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from dros.ip_lists import IpListUpdater, load_ip_lists, summarize_ip_lists, update_ip_lists
from dros.settings import DrosPaths, DrosSettings


def _console(output: StringIO) -> Console:
    return Console(file=output, force_terminal=False, color_system=None, width=120)


def _settings(tmp_path: Path) -> DrosSettings:
    return DrosSettings(
        sysRoot=tmp_path / "sysroot",
        paths=DrosPaths(
            configs=tmp_path / "configs",
            run=tmp_path / "run",
            source=tmp_path / "source",
        ),
    )


def test_load_ip_lists_uses_configs_then_run_then_source_priority(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    configs = settings.paths.config_dirs()[0] / "ip-lists"
    run = settings.paths.run / "ip-lists"
    source = settings.paths.source / "ip-lists"
    configs.mkdir(parents=True)
    run.mkdir(parents=True)
    source.mkdir(parents=True)
    (source / "china.v4.txt").write_text("10.0.0.0/8\n", encoding="utf-8")
    (run / "china.v4.txt").write_text("172.16.0.0/12\n", encoding="utf-8")
    (configs / "china.v4.txt").write_text("192.168.0.0/16\n", encoding="utf-8")
    (run / "china.v6.txt").write_text("fd00::/8\n", encoding="utf-8")

    store = load_ip_lists(settings)

    assert store.resolve("china", "ipv4")[0] == ["192.168.0.0/16"]
    assert store.resolve("china", "ipv6")[0] == ["fd00::/8"]
    summary = summarize_ip_lists(store)
    assert summary[0].name == "china"
    assert summary[0].ipv4_count == 1
    assert summary[0].ipv6_count == 1


def test_ip_list_updater_writes_sources_to_run_directory(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    class FakeUpdater(IpListUpdater):
        def _fetch_source(self, source_name: str):  # type: ignore[no-untyped-def]
            from dros.ip_lists import FetchResult

            return FetchResult(
                name=source_name,
                source="test",
                ipv4=["192.0.2.0/24"],
                ipv6=["2001:db8::/32"],
            )

    output = StringIO()
    result = update_ip_lists(
        settings,
        updater=FakeUpdater(),
        selected_sources=["china"],
        console=_console(output),
    )

    assert result.failures == []
    assert (settings.paths.run / "ip-lists/china.v4.txt").read_text(encoding="utf-8") == (
        "192.0.2.0/24\n"
    )
    assert (settings.paths.run / "ip-lists/china.v6.txt").read_text(encoding="utf-8") == (
        "2001:db8::/32\n"
    )
    assert "ok: china v4=1 v6=1" in output.getvalue()
