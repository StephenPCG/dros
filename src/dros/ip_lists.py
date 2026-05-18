from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from rich.console import Console

from dros.settings import DrosSettings

CIDR_RE = re.compile(
    r"\b(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}\b|\b[0-9A-Fa-f:]+::?[0-9A-Fa-f:]*/\d{1,3}\b"
)

AVAILABLE_IP_LIST_SOURCES = (
    "amazon",
    "china",
    "cloudflare",
    "fastly",
    "github",
    "google",
    "telegram",
    "tencent",
    "wikipedia",
)

TENCENT_ASNS = (45090, 132203, 133478, 132591, 139341, 9390, 137876)
RIPESTAT_ANNOUNCED_PREFIXES_URL = "https://stat.ripe.net/data/announced-prefixes/data.json"

IpListFamily = Literal["mixed", "ipv4", "ipv6"]
RequestedFamily = Literal["auto", "all", "ipv4", "ipv6"]


@dataclass(frozen=True)
class FetchResult:
    name: str
    source: str
    ipv4: list[str]
    ipv6: list[str]


@dataclass(frozen=True)
class IpListEntry:
    name: str
    family: IpListFamily
    path: Path

    def networks(self) -> tuple[list[str], list[str]]:
        routes: list[str] = []
        warnings: list[str] = []
        for line_number, raw_line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            item = raw_line.split("#", 1)[0].strip()
            if not item:
                continue
            try:
                network = ipaddress.ip_network(item, strict=False)
            except ValueError as exc:
                warnings.append(f"{self.path}:{line_number}: invalid CIDR {item!r}: {exc}")
                continue
            routes.append(str(network))
        return routes, warnings


@dataclass(frozen=True)
class IpListSummary:
    name: str
    ipv4_count: int
    ipv6_count: int
    mixed_count: int
    sources: list[str]


@dataclass(frozen=True)
class IpListUpdateResult:
    output_dir: Path
    manifest_path: Path
    selected_sources: list[str]
    failures: list[dict[str, str]]
    results: list[dict[str, Any]]
    written_files: list[Path]


@dataclass(frozen=True)
class IpListStore:
    entries: dict[tuple[str, IpListFamily], IpListEntry]

    def resolve(self, reference: str, family: Literal["ipv4", "ipv6"]) -> tuple[list[str], list[str]]:
        name, requested_family = parse_ip_list_reference(reference)
        families: list[IpListFamily]
        if requested_family == "all":
            families = ["mixed", "ipv4", "ipv6"]
        elif requested_family in {"ipv4", "ipv6"}:
            families = [requested_family]
        else:
            families = [family, "mixed"]

        found = [self.entries[(name, item)] for item in families if (name, item) in self.entries]
        if not found:
            return [], [f"ip list {reference!r} not found"]

        routes: list[str] = []
        warnings: list[str] = []
        for entry in found:
            networks, entry_warnings = entry.networks()
            warnings.extend(entry_warnings)
            for network in networks:
                parsed = ipaddress.ip_network(network, strict=False)
                if (family == "ipv4" and parsed.version == 4) or (
                    family == "ipv6" and parsed.version == 6
                ):
                    routes.append(str(parsed))
        return routes, warnings


class IpListUpdater:
    def __init__(self, *, timeout: float = 30.0) -> None:
        self.timeout = timeout
        self.headers = {
            "User-Agent": "dros-ip-list-updater/0.1",
            "Accept": "application/json, text/plain, */*",
        }
        github_token = _get_github_token()
        if github_token:
            self.headers["Authorization"] = f"Bearer {github_token}"

    def update(
        self,
        output_dir: Path,
        selected_sources: list[str] | None = None,
        *,
        manifest_path: Path | None = None,
    ) -> dict[str, Any]:
        source_names = normalize_selected_sources(selected_sources)
        written_files: list[str] = []
        failures: list[dict[str, str]] = []
        manifest_entries: list[dict[str, Any]] = []
        manifest_path = manifest_path or (output_dir / "sources.json")

        with _temporary_stage_dir(output_dir.parent) as raw_stage_dir:
            stage_dir = Path(raw_stage_dir)
            for source_name in source_names:
                try:
                    result = self._fetch_source(source_name)
                except Exception as exc:
                    failures.append({"name": source_name, "error": str(exc)})
                    manifest_entries.append({"name": source_name, "ok": False, "error": str(exc)})
                    continue

                staged4 = stage_dir / f"{result.name}.v4.txt"
                staged6 = stage_dir / f"{result.name}.v6.txt"
                _write_cidrs(staged4, result.ipv4)
                _write_cidrs(staged6, result.ipv6)
                target4 = output_dir / staged4.name
                target6 = output_dir / staged6.name
                _replace_from_staged(staged4, target4)
                _replace_from_staged(staged6, target6)
                written_files.extend([str(target4), str(target6)])
                manifest_entries.append(
                    {
                        "name": result.name,
                        "ok": True,
                        "source": result.source,
                        "ipv4_count": len(result.ipv4),
                        "ipv6_count": len(result.ipv6),
                    }
                )

            manifest = {
                "generated_at": int(time.time()),
                "selected_sources": source_names,
                "failure_count": len(failures),
                "results": manifest_entries,
            }
            staged_manifest = stage_dir / manifest_path.name
            _atomic_write_json(staged_manifest, manifest)
            _replace_from_staged(staged_manifest, manifest_path)
            written_files.append(str(manifest_path))
            _remove_stale_manifest(output_dir, manifest_path)

        return {
            "output_dir": str(output_dir),
            "manifest_path": str(manifest_path),
            "selected_sources": source_names,
            "written_files": written_files,
            "failures": failures,
            "results": manifest_entries,
        }

    def _fetch_source(self, source_name: str) -> FetchResult:
        fetcher = getattr(self, f"_fetch_{source_name}", None)
        if fetcher is None or not callable(fetcher):
            raise ValueError(f"unsupported source {source_name!r}")
        result = fetcher()
        if not isinstance(result, FetchResult):
            raise TypeError(f"fetcher for {source_name!r} returned unexpected result")
        return result

    def _fetch_china(self) -> FetchResult:
        base = "https://raw.githubusercontent.com/gaoyifan/china-operator-ip/ip-lists"
        ipv4 = _normalize_cidrs(self._get_text_lines(f"{base}/china.txt"))
        ipv6 = _normalize_cidrs(self._get_text_lines(f"{base}/china6.txt"))
        return FetchResult(name="china", source=base, ipv4=ipv4, ipv6=ipv6)

    def _fetch_amazon(self) -> FetchResult:
        payload = self._get_json("https://ip-ranges.amazonaws.com/ip-ranges.json")
        ipv4 = [
            str(ipaddress.ip_network(item["ip_prefix"], strict=False))
            for item in payload.get("prefixes", [])
            if item.get("service") == "AMAZON" and item.get("ip_prefix")
        ]
        ipv6 = [
            str(ipaddress.ip_network(item["ipv6_prefix"], strict=False))
            for item in payload.get("ipv6_prefixes", [])
            if item.get("service") == "AMAZON" and item.get("ipv6_prefix")
        ]
        return FetchResult(
            name="amazon",
            source="https://ip-ranges.amazonaws.com/ip-ranges.json",
            ipv4=_normalize_cidrs(ipv4),
            ipv6=_normalize_cidrs(ipv6),
        )

    def _fetch_cloudflare(self) -> FetchResult:
        ipv4 = _normalize_cidrs(self._get_text_lines("https://www.cloudflare.com/ips-v4"))
        ipv6 = _normalize_cidrs(self._get_text_lines("https://www.cloudflare.com/ips-v6"))
        return FetchResult(
            name="cloudflare",
            source="https://www.cloudflare.com/ips-v4 + https://www.cloudflare.com/ips-v6",
            ipv4=ipv4,
            ipv6=ipv6,
        )

    def _fetch_fastly(self) -> FetchResult:
        payload = self._get_json("https://api.fastly.com/public-ip-list")
        return FetchResult(
            name="fastly",
            source="https://api.fastly.com/public-ip-list",
            ipv4=_normalize_cidrs(payload.get("addresses", [])),
            ipv6=_normalize_cidrs(payload.get("ipv6_addresses", [])),
        )

    def _fetch_github(self) -> FetchResult:
        payload = self._get_json("https://api.github.com/meta")
        cidrs: list[str] = []
        for value in payload.values():
            if not isinstance(value, list):
                continue
            for item in value:
                if isinstance(item, str) and _looks_like_network(item):
                    cidrs.append(item)
        ipv4, ipv6 = _split_families(_normalize_cidrs(cidrs))
        return FetchResult(name="github", source="https://api.github.com/meta", ipv4=ipv4, ipv6=ipv6)

    def _fetch_google(self) -> FetchResult:
        payload = self._get_json("https://www.gstatic.com/ipranges/goog.json")
        cidrs = _extract_prefixes(payload.get("prefixes", []))
        ipv4, ipv6 = _split_families(_normalize_cidrs(cidrs))
        return FetchResult(name="google", source="https://www.gstatic.com/ipranges/goog.json", ipv4=ipv4, ipv6=ipv6)

    def _fetch_telegram(self) -> FetchResult:
        cidrs = _normalize_cidrs(self._get_text_lines("https://core.telegram.org/resources/cidr.txt"))
        ipv4, ipv6 = _split_families(cidrs)
        return FetchResult(name="telegram", source="https://core.telegram.org/resources/cidr.txt", ipv4=ipv4, ipv6=ipv6)

    def _fetch_tencent(self) -> FetchResult:
        cidrs: list[str] = []
        for asn in TENCENT_ASNS:
            url = (
                f"{RIPESTAT_ANNOUNCED_PREFIXES_URL}?resource=AS{asn}"
                "&min_peers_seeing=1&sourceapp=dros_gateway"
            )
            payload = self._get_json(url)
            prefixes = payload.get("data", {}).get("prefixes", [])
            if not isinstance(prefixes, list):
                raise ValueError(f"expected RIPEstat prefixes list for AS{asn}")
            for item in prefixes:
                if not isinstance(item, dict):
                    continue
                prefix = item.get("prefix")
                if isinstance(prefix, str) and ":" in prefix:
                    cidrs.append(prefix)
        source_asns = ", ".join(f"AS{asn}" for asn in TENCENT_ASNS)
        return FetchResult(
            name="tencent",
            source=f"RIPEstat announced-prefixes for {source_asns}",
            ipv4=[],
            ipv6=_collapse_cidrs(cidrs),
        )

    def _fetch_wikipedia(self) -> FetchResult:
        text = self._get_text("https://wikitech.wikimedia.org/wiki/IP_and_AS_allocations")
        ipv4, ipv6 = _split_families(_normalize_cidrs(CIDR_RE.findall(text)))
        return FetchResult(
            name="wikipedia",
            source="https://wikitech.wikimedia.org/wiki/IP_and_AS_allocations",
            ipv4=ipv4,
            ipv6=ipv6,
        )

    def _get_text(self, url: str) -> str:
        request = urllib.request.Request(url, headers=self.headers)
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read().decode("utf-8")

    def _get_text_lines(self, url: str) -> list[str]:
        return [line.strip() for line in self._get_text(url).splitlines() if line.strip()]

    def _get_json(self, url: str) -> dict[str, Any]:
        payload = json.loads(self._get_text(url))
        if not isinstance(payload, dict):
            raise ValueError(f"expected JSON object from {url}")
        return payload


def load_ip_lists(settings: DrosSettings) -> IpListStore:
    entries: dict[tuple[str, IpListFamily], IpListEntry] = {}
    for directory in ip_list_dirs(settings):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.txt")):
            parsed = parse_ip_list_filename(path.name)
            if parsed is None:
                continue
            name, family = parsed
            key = (name, family)
            if key not in entries:
                entries[key] = IpListEntry(name=name, family=family, path=path)
    return IpListStore(entries=entries)


def ip_list_dirs(settings: DrosSettings) -> list[Path]:
    config_dirs = [path / "ip-lists" for path in reversed(settings.paths.config_dirs())]
    return [*config_dirs, settings.paths.run / "ip-lists", settings.paths.source / "ip-lists"]


def summarize_ip_lists(store: IpListStore) -> list[IpListSummary]:
    names = sorted({name for name, _family in store.entries})
    summaries: list[IpListSummary] = []
    for name in names:
        counts: dict[IpListFamily, int] = {"mixed": 0, "ipv4": 0, "ipv6": 0}
        sources: list[str] = []
        for family in ("ipv4", "ipv6", "mixed"):
            entry = store.entries.get((name, family))
            if entry is None:
                continue
            networks, _warnings = entry.networks()
            counts[family] = len(networks)
            sources.append(str(entry.path))
        summaries.append(
            IpListSummary(
                name=name,
                ipv4_count=counts["ipv4"],
                ipv6_count=counts["ipv6"],
                mixed_count=counts["mixed"],
                sources=sources,
            )
        )
    return summaries


def update_ip_lists(
    settings: DrosSettings,
    *,
    updater: IpListUpdater | None = None,
    selected_sources: list[str] | None = None,
    verbose: int = 1,
    console: Console | None = None,
    timeout: float = 30.0,
) -> IpListUpdateResult:
    active_console = console or Console()
    active_updater = updater or IpListUpdater(timeout=timeout)
    output_dir = settings.paths.run / "ip-lists"
    manifest_path = settings.paths.run / "ip-lists.sources.json"
    result = active_updater.update(
        output_dir=output_dir,
        selected_sources=selected_sources,
        manifest_path=manifest_path,
    )
    parsed = IpListUpdateResult(
        output_dir=Path(result["output_dir"]),
        manifest_path=Path(result["manifest_path"]),
        selected_sources=list(result["selected_sources"]),
        failures=list(result["failures"]),
        results=list(result["results"]),
        written_files=[Path(path) for path in result["written_files"]],
    )
    if verbose > 0:
        active_console.print(f"output_dir: {parsed.output_dir}")
        for item in parsed.results:
            if item.get("ok"):
                active_console.print(
                    f"ok: {item['name']} v4={item['ipv4_count']} v6={item['ipv6_count']}"
                )
            else:
                active_console.print(f"error: {item['name']}: {item['error']}")
    return parsed


def normalize_selected_sources(selected_sources: list[str] | None) -> list[str]:
    if not selected_sources:
        return list(AVAILABLE_IP_LIST_SOURCES)

    normalized: list[str] = []
    seen: set[str] = set()
    for source in selected_sources:
        name = source.strip().lower()
        if name not in AVAILABLE_IP_LIST_SOURCES:
            available = ", ".join(AVAILABLE_IP_LIST_SOURCES)
            raise ValueError(f"unsupported source {source!r}; available: {available}")
        if name not in seen:
            normalized.append(name)
            seen.add(name)
    return normalized


def parse_ip_list_reference(reference: str) -> tuple[str, RequestedFamily]:
    if "@" not in reference:
        return reference, "auto"
    name, family = reference.rsplit("@", 1)
    if family == "v4":
        return name, "ipv4"
    if family == "v6":
        return name, "ipv6"
    if family == "all":
        return name, "all"
    return reference, "auto"


def parse_ip_list_filename(filename: str) -> tuple[str, IpListFamily] | None:
    if filename.endswith(".v4.txt"):
        return filename.removesuffix(".v4.txt"), "ipv4"
    if filename.endswith(".v6.txt"):
        return filename.removesuffix(".v6.txt"), "ipv6"
    if filename.endswith(".txt"):
        return filename.removesuffix(".txt"), "mixed"
    return None


def looks_like_ip_list(value: str) -> bool:
    if value in {"default", "default6"}:
        return False
    try:
        ipaddress.ip_network(value, strict=False)
        return False
    except ValueError:
        return True


def _extract_prefixes(items: Iterable[dict[str, Any]]) -> list[str]:
    cidrs: list[str] = []
    for item in items:
        ipv4 = item.get("ipv4Prefix")
        ipv6 = item.get("ipv6Prefix")
        if isinstance(ipv4, str):
            cidrs.append(ipv4)
        if isinstance(ipv6, str):
            cidrs.append(ipv6)
    return cidrs


def _looks_like_network(value: str) -> bool:
    try:
        ipaddress.ip_network(value, strict=False)
    except ValueError:
        return False
    return True


def _normalize_cidrs(values: Iterable[str]) -> list[str]:
    networks: set[ipaddress._BaseNetwork] = set()
    for line_number, raw in enumerate(values, start=1):
        candidate = raw.split("#", 1)[0].strip()
        if not candidate:
            continue
        try:
            network = ipaddress.ip_network(candidate, strict=False)
        except ValueError:
            print(f"warning: skipped invalid CIDR in upstream:{line_number}: {raw.rstrip()}", file=sys.stderr)
            continue
        networks.add(network)
    sorted_networks = sorted(networks, key=lambda item: (item.version, int(item.network_address), item.prefixlen))
    return [str(network) for network in sorted_networks]


def _collapse_cidrs(values: Iterable[str]) -> list[str]:
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for line_number, raw in enumerate(values, start=1):
        candidate = raw.split("#", 1)[0].strip()
        if not candidate:
            continue
        try:
            network = ipaddress.ip_network(candidate, strict=False)
        except ValueError:
            print(f"warning: skipped invalid CIDR in upstream:{line_number}: {raw.rstrip()}", file=sys.stderr)
            continue
        networks.append(network)
    collapsed = ipaddress.collapse_addresses(networks)
    return [str(network) for network in collapsed]


def _split_families(values: Iterable[str]) -> tuple[list[str], list[str]]:
    ipv4: list[str] = []
    ipv6: list[str] = []
    for value in values:
        network = ipaddress.ip_network(value, strict=False)
        if network.version == 4:
            ipv4.append(str(network))
        else:
            ipv6.append(str(network))
    return ipv4, ipv6


def _write_cidrs(path: Path, cidrs: Iterable[str]) -> None:
    _atomic_write_text(path, "".join(f"{cidr}\n" for cidr in cidrs))


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def _atomic_write_json(path: Path, data: object) -> None:
    _atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def _temporary_stage_dir(parent: Path):
    parent.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix="dros-ip-lists-", dir=parent)


def _replace_from_staged(staged: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target.parent / f".{target.name}.tmp"
    shutil.copyfile(staged, temp_target)
    temp_target.replace(target)


def _remove_stale_manifest(output_dir: Path, manifest_path: Path) -> None:
    stale_path = output_dir / "sources.json"
    if stale_path != manifest_path and stale_path.exists():
        stale_path.unlink()


def _get_github_token() -> str | None:
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None
