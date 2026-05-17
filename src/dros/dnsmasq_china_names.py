from __future__ import annotations

import json
import subprocess
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console

from dros.executor import CommandRunner, SystemAction, SystemExecutor
from dros.settings import DrosSettings

AVAILABLE_DNSMASQ_CHINA_NAME_FILES = (
    "accelerated-domains.china.conf",
    "apple.china.conf",
    "bogus-nxdomain.china.conf",
    "google.china.conf",
)
UPSTREAM_BASE = "https://raw.githubusercontent.com/felixonmars/dnsmasq-china-list/master"
CHINA_NAMES_CONF = "/etc/dnsmasq.d/dros-30-china-names.conf"
CHINA_NAMES_MANUAL_CONF = "/etc/dnsmasq.d/dros-31-china-names-manual.conf"

FetchText = Callable[[str, float], str]


@dataclass(frozen=True)
class DnsmasqChinaNamesUpdateResult:
    changed: bool
    failures: list[dict[str, str]]
    warnings: list[str]
    actions: list[SystemAction]


class DnsmasqChinaNamesUpdater:
    def __init__(
        self,
        *,
        timeout: float = 30.0,
        fetch_text: FetchText | None = None,
    ) -> None:
        self.timeout = timeout
        self.fetch_text = fetch_text or _fetch_text

    def update(
        self,
        settings: DrosSettings,
        *,
        servers: list[str],
        selected_files: list[str] | None = None,
        manual_names: list[str] | None = None,
        manual_name_files: list[str] | None = None,
        verbose: int = 1,
        console: Console | None = None,
        runner: CommandRunner = subprocess.run,
    ) -> DnsmasqChinaNamesUpdateResult:
        executor = SystemExecutor(settings, verbose=verbose, console=console, runner=runner)
        cache_dir = china_names_cache_dir(settings)
        manifest_path = cache_dir / "sources.json"
        normalized_servers = normalize_servers(servers)
        file_names = normalize_selected_files(selected_files)
        normalized_manual_names = normalize_manual_names(manual_names or [])
        manual_paths = [Path(item) for item in manual_name_files or []]
        failures: list[dict[str, str]] = []
        warnings: list[str] = []
        manifest_entries: list[dict[str, Any]] = []
        changed = executor.ensure_dir(cache_dir)

        for file_name in file_names:
            url = f"{UPSTREAM_BASE}/{file_name}"
            try:
                content = self.fetch_text(url, self.timeout)
            except Exception as exc:
                failures.append({"file": file_name, "error": str(exc)})
                manifest_entries.append({"file": file_name, "ok": False, "error": str(exc)})
                continue
            changed = (
                executor.write_file(
                    cache_dir / file_name,
                    _ensure_trailing_newline(content),
                    show_diff=False,
                )
                or changed
            )
            manifest_entries.append(
                {
                    "file": file_name,
                    "ok": True,
                    "source": url,
                    "server_count": len(normalized_servers),
                    "line_count": len(content.splitlines()),
                }
            )

        manual_content, manual_results, manual_warnings = render_manual_names_conf(
            source="DnsmasqChinaNames/runtime",
            servers=normalized_servers,
            manual_names=normalized_manual_names,
            manual_name_files=manual_paths,
        )
        manifest_entries.extend(manual_results)
        warnings.extend(manual_warnings)

        manifest = {
            "generated_at": int(time.time()),
            "servers": normalized_servers,
            "selected_files": file_names,
            "manual_names_count": len(normalized_manual_names),
            "manual_name_files": [str(path) for path in manual_paths],
            "failure_count": len(failures),
            "warning_count": len(warnings),
            "warnings": warnings,
            "target_file": CHINA_NAMES_CONF,
            "manual_target_file": CHINA_NAMES_MANUAL_CONF,
            "results": manifest_entries,
        }
        changed = (
            executor.write_file(
                manifest_path,
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                show_diff=False,
            )
            or changed
        )

        conf_changed = executor.write_file(
            CHINA_NAMES_CONF,
            render_cached_china_names_conf(
                source="DnsmasqChinaNames/runtime",
                cache_dir=executor.target_path(cache_dir),
                servers=normalized_servers,
                selected_files=file_names,
            ),
            show_diff=False,
        )
        changed = conf_changed or changed
        manual_changed = executor.write_file(
            CHINA_NAMES_MANUAL_CONF,
            manual_content,
            show_diff=False,
        )
        changed = manual_changed or changed
        if conf_changed or manual_changed:
            executor.run(["systemctl", "restart", "dnsmasq"], real_only=True)

        return DnsmasqChinaNamesUpdateResult(
            changed=changed,
            failures=failures,
            warnings=warnings,
            actions=executor.actions,
        )


def china_names_cache_dir(settings: DrosSettings) -> Path:
    return settings.paths.run / "dnsmasq-china-names"


def normalize_selected_files(selected_files: list[str] | None) -> list[str]:
    if not selected_files:
        return list(AVAILABLE_DNSMASQ_CHINA_NAME_FILES)
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_name in selected_files:
        file_name = raw_name.strip()
        if file_name not in AVAILABLE_DNSMASQ_CHINA_NAME_FILES:
            available = ", ".join(AVAILABLE_DNSMASQ_CHINA_NAME_FILES)
            raise ValueError(f"unsupported dnsmasq china names file {raw_name!r}; available: {available}")
        if file_name in seen:
            continue
        normalized.append(file_name)
        seen.add(file_name)
    return normalized


def normalize_servers(servers: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_server in servers:
        server = raw_server.strip()
        if not server or server in seen:
            continue
        normalized.append(server)
        seen.add(server)
    if not normalized:
        raise ValueError("DnsmasqChinaNames spec.servers must not be empty")
    return normalized


def normalize_manual_names(names: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_name in names:
        name = _normalize_domain_line(raw_name)
        if not name or name in seen:
            continue
        normalized.append(name)
        seen.add(name)
    return normalized


def render_manual_names(
    manual_names: list[str],
    manual_name_files: list[Path],
    servers: list[str],
) -> tuple[str, list[dict[str, Any]], list[str]]:
    lines: list[str] = []
    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    domains = list(manual_names)
    for path in manual_name_files:
        if not path.exists():
            warnings.append(f"manual china names file not found: {path}")
            results.append({"file": str(path), "ok": False, "warning": "file not found"})
            continue
        before = len(domains)
        server_lines = 0
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("server=/"):
                prefix = _extract_server_prefix(line)
                for server in servers:
                    lines.append(f"{prefix}{server}")
                server_lines += 1
                continue
            domain = _normalize_domain_line(line)
            if domain:
                domains.append(domain)
        results.append(
            {
                "file": str(path),
                "ok": True,
                "manual_domain_count": len(domains) - before,
                "manual_server_line_count": server_lines,
            }
        )

    seen: set[str] = set()
    for domain in domains:
        if domain in seen:
            continue
        seen.add(domain)
        for server in servers:
            lines.append(f"server=/{domain}/{server}")
    return "".join(f"{line}\n" for line in lines), results, warnings


def render_manual_names_conf(
    *,
    source: str,
    servers: list[str],
    manual_names: list[str],
    manual_name_files: list[Path],
) -> tuple[str, list[dict[str, Any]], list[str]]:
    manual_content, results, warnings = render_manual_names(
        normalize_manual_names(manual_names),
        manual_name_files,
        normalize_servers(servers),
    )
    return _manual_file_content(manual_content, source=source), results, warnings


def render_cached_china_names_conf(
    *,
    source: str,
    cache_dir: Path,
    servers: list[str],
    selected_files: list[str] | None,
) -> str:
    normalized_servers = normalize_servers(servers)
    rendered_parts: list[tuple[str, str]] = []
    for file_name in normalize_selected_files(selected_files):
        cache_path = cache_dir / file_name
        if not cache_path.exists():
            continue
        rendered_parts.append(
            (
                file_name,
                rewrite_dnsmasq_servers(
                    file_name,
                    cache_path.read_text(encoding="utf-8"),
                    normalized_servers,
                ),
            )
        )
    return _merge_rendered_files(
        rendered_parts,
        source=source,
        empty_message="# no cached china names available",
    )


def rewrite_dnsmasq_servers(file_name: str, content: str, servers: list[str]) -> str:
    if file_name == "bogus-nxdomain.china.conf":
        return _ensure_trailing_newline(content)
    rendered_lines: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or not line.startswith("server=/"):
            rendered_lines.append(raw_line)
            continue
        prefix = _extract_server_prefix(line)
        for server in servers:
            rendered_lines.append(f"{prefix}{server}")
    return "".join(f"{line}\n" for line in rendered_lines)


def _merge_rendered_files(
    rendered_parts: list[tuple[str, str]],
    *,
    source: str | None = None,
    empty_message: str | None = None,
) -> str:
    lines = ["# Generated by DROS. Manual changes will be overwritten."]
    if source:
        lines.append(f"# Source: {source}")
    lines.append("")
    if not rendered_parts and empty_message:
        lines.extend([empty_message, ""])
        return "\n".join(lines)
    for file_name, content in rendered_parts:
        lines.append(f"## {file_name}")
        lines.append(_ensure_trailing_newline(content).rstrip("\n"))
        lines.append("")
    return "\n".join(lines)


def _manual_file_content(content: str, *, source: str | None = None) -> str:
    if content.strip():
        return _merge_rendered_files(
            [("chinanames-manual.conf", content)],
            source=source,
        )
    return _merge_rendered_files(
        [],
        source=source,
        empty_message="# no manual china names configured",
    )


def _extract_server_prefix(line: str) -> str:
    parts = line.rsplit("/", 1)
    if len(parts) != 2:
        raise ValueError(f"unexpected dnsmasq server line: {line!r}")
    return f"{parts[0]}/"


def _normalize_domain_line(value: str) -> str:
    line = value.split("#", 1)[0].strip()
    if not line:
        return ""
    if line.startswith("server=/"):
        line = _extract_server_prefix(line).removeprefix("server=/").rstrip("/")
    return line.strip().strip("/")


def _ensure_trailing_newline(value: str) -> str:
    return value if value.endswith("\n") else f"{value}\n"


def _fetch_text(url: str, timeout: float) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "dros-dnsmasq-china-names/0.1",
            "Accept": "text/plain, */*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")
