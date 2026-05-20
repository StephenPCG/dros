from __future__ import annotations

import io
import tarfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

from dros.executor import SystemExecutor
from dros.settings import DrosSettings

WGSD_VERSION = "0.3.6"
WGSD_RELEASE_PAGE = f"https://github.com/jwhited/wgsd/releases/tag/v{WGSD_VERSION}"
WGSD_DOWNLOAD_BASE = f"https://github.com/jwhited/wgsd/releases/download/v{WGSD_VERSION}"

Downloader = Callable[[str, float], bytes]


@dataclass(frozen=True)
class WgsdBinarySpec:
    name: str
    install_path: str
    archive_member: str
    default_download_url: str
    release_page: str = WGSD_RELEASE_PAGE


WGSD_BINARIES = {
    "wgsd-client": WgsdBinarySpec(
        name="wgsd-client",
        install_path="/usr/local/bin/wgsd-client",
        archive_member="wgsd-client",
        default_download_url=f"{WGSD_DOWNLOAD_BASE}/wgsd-client_{WGSD_VERSION}_linux_amd64.tar.gz",
    ),
    "wgsd-coredns": WgsdBinarySpec(
        name="wgsd-coredns",
        install_path="/usr/local/bin/coredns",
        archive_member="coredns",
        default_download_url=f"{WGSD_DOWNLOAD_BASE}/wgsd-coredns_{WGSD_VERSION}_linux_amd64.tar.gz",
    ),
}

WGSD_BINARY_ALIASES = {
    "client": "wgsd-client",
    "wgsd-client": "wgsd-client",
    "coredns": "wgsd-coredns",
    "wgsd-coredns": "wgsd-coredns",
}


def resolve_wgsd_binary(target: str) -> WgsdBinarySpec:
    normalized = target.strip().lower().replace("_", "-")
    if "/" in normalized:
        normalized = normalized.rsplit("/", 1)[-1]
    canonical = WGSD_BINARY_ALIASES.get(normalized)
    if canonical is None:
        choices = ", ".join(sorted(WGSD_BINARIES))
        raise ValueError(f"unknown wgsd install target {target!r}; expected one of: {choices}")
    return WGSD_BINARIES[canonical]


def install_wgsd_binary(
    settings: DrosSettings,
    spec: WgsdBinarySpec,
    *,
    source: str,
    source_is_archive: bool,
    console: Console,
    downloader: Downloader | None = None,
    timeout: float = 30.0,
) -> Path:
    payload = _read_source(source, downloader=downloader or _download_url, timeout=timeout)
    if source_is_archive:
        payload = _extract_archive_member(payload, spec.archive_member)
    else:
        _reject_archive_payload(payload)
    _validate_linux_amd64_elf(payload, spec.name)

    executor = SystemExecutor(settings, verbose=1, console=console)
    executor.write_binary_file(spec.install_path, payload, mode=0o755)
    executor.target_path(spec.install_path).chmod(0o755)
    return Path(spec.install_path)


def _read_source(source: str, *, downloader: Downloader, timeout: float) -> bytes:
    if _is_http_url(source):
        return downloader(source, timeout)
    path = Path(source).expanduser()
    if not path.exists():
        raise ValueError(f"binary source not found: {source}")
    if not path.is_file():
        raise ValueError(f"binary source is not a file: {source}")
    return path.read_bytes()


def _download_url(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - user-confirmed URL
        return response.read()


def _extract_archive_member(payload: bytes, member_name: str) -> bytes:
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile() or Path(member.name).name != member_name:
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    break
                return extracted.read()
    except tarfile.TarError as exc:
        raise ValueError("default GitHub download is not a valid tar.gz archive") from exc
    raise ValueError(f"default GitHub download does not contain {member_name!r}")


def _reject_archive_payload(payload: bytes) -> None:
    if payload.startswith(b"\x1f\x8b") or payload.startswith(b"PK\x03\x04"):
        raise ValueError("custom source must be an extracted linux/amd64 ELF binary, not an archive")


def _validate_linux_amd64_elf(payload: bytes, name: str) -> None:
    if len(payload) < 20 or not payload.startswith(b"\x7fELF"):
        raise ValueError(f"{name} source must be an extracted linux/amd64 ELF binary")
    if payload[4] != 2:
        raise ValueError(f"{name} source must be a 64-bit linux/amd64 ELF binary")
    if payload[18:20] != b"\x3e\x00":
        raise ValueError(f"{name} source must be a linux/amd64 ELF binary")


def _is_http_url(value: str) -> bool:
    return value.startswith("https://") or value.startswith("http://")
