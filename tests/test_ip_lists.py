from __future__ import annotations

from io import StringIO
from pathlib import Path

from rich.console import Console

from dros.ip_lists import (
    AZURE_SERVICE_TAGS_PUBLIC_URL,
    MICROSOFT_PUBLIC_IPS_URL,
    IpListUpdater,
    load_ip_lists,
    summarize_ip_lists,
    update_ip_lists,
)
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


def test_azure_source_fetches_azure_cloud_service_tag_prefixes(monkeypatch) -> None:
    requested_urls: list[str] = []

    def fake_get_json(self, url: str):  # type: ignore[no-untyped-def]
        requested_urls.append(url)
        return {
            "values": [
                {
                    "name": "Storage",
                    "properties": {"addressPrefixes": ["203.0.113.0/24"]},
                },
                {
                    "name": "AzureCloud",
                    "properties": {
                        "addressPrefixes": [
                            "20.0.0.0/8",
                            "2603:1000::/24",
                            "20.0.0.0/8",
                        ],
                    },
                },
            ]
        }

    monkeypatch.setattr(IpListUpdater, "_get_json", fake_get_json)

    result = IpListUpdater()._fetch_source("azure")

    assert result.name == "azure"
    assert result.source == f"{AZURE_SERVICE_TAGS_PUBLIC_URL}#AzureCloud"
    assert result.ipv4 == ["20.0.0.0/8"]
    assert result.ipv6 == ["2603:1000::/24"]
    assert requested_urls == [AZURE_SERVICE_TAGS_PUBLIC_URL]


def test_microsoft_source_fetches_public_ip_csv_prefixes(monkeypatch) -> None:
    requested_urls: list[str] = []

    def fake_get_text(self, url: str):  # type: ignore[no-untyped-def]
        requested_urls.append(url)
        return "\n".join(
            [
                "Prefix,Type",
                "4.128.0.0/12, MSFT Public IP Address Block",
                "2603:1000::/24, MSFT Public IP Address Block",
                "4.128.0.0/12, MSFT Public IP Address Block",
                "",
            ]
        )

    monkeypatch.setattr(IpListUpdater, "_get_text", fake_get_text)

    result = IpListUpdater()._fetch_source("microsoft")

    assert result.name == "microsoft"
    assert result.source == MICROSOFT_PUBLIC_IPS_URL
    assert result.ipv4 == ["4.128.0.0/12"]
    assert result.ipv6 == ["2603:1000::/24"]
    assert requested_urls == [MICROSOFT_PUBLIC_IPS_URL]


def test_tencent_source_fetches_collapsed_ipv6_prefixes_for_tencent_asns(monkeypatch) -> None:
    requested_urls: list[str] = []

    payloads = {
        "45090": [
            {"prefix": "2402:4e00::/32"},
            {"prefix": "2402:4e00:1::/48"},
            {"prefix": "1.12.0.0/14"},
        ],
        "137876": [
            {"prefix": "2001:df5:4500::/48"},
        ],
    }

    def fake_get_json(self, url: str):  # type: ignore[no-untyped-def]
        requested_urls.append(url)
        asn = url.split("resource=AS", 1)[1].split("&", 1)[0]
        return {"data": {"prefixes": payloads.get(asn, [])}}

    monkeypatch.setattr(IpListUpdater, "_get_json", fake_get_json)

    result = IpListUpdater()._fetch_source("tencent")

    assert result.name == "tencent"
    assert result.ipv4 == []
    assert result.ipv6 == ["2001:df5:4500::/48", "2402:4e00::/32"]
    assert requested_urls == [
        "https://stat.ripe.net/data/announced-prefixes/data.json?resource=AS45090&min_peers_seeing=1&sourceapp=dros_gateway",
        "https://stat.ripe.net/data/announced-prefixes/data.json?resource=AS132203&min_peers_seeing=1&sourceapp=dros_gateway",
        "https://stat.ripe.net/data/announced-prefixes/data.json?resource=AS133478&min_peers_seeing=1&sourceapp=dros_gateway",
        "https://stat.ripe.net/data/announced-prefixes/data.json?resource=AS132591&min_peers_seeing=1&sourceapp=dros_gateway",
        "https://stat.ripe.net/data/announced-prefixes/data.json?resource=AS139341&min_peers_seeing=1&sourceapp=dros_gateway",
        "https://stat.ripe.net/data/announced-prefixes/data.json?resource=AS9390&min_peers_seeing=1&sourceapp=dros_gateway",
        "https://stat.ripe.net/data/announced-prefixes/data.json?resource=AS137876&min_peers_seeing=1&sourceapp=dros_gateway",
    ]
