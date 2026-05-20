from __future__ import annotations

import io
import stat
import tarfile
from pathlib import Path

import pytest
from rich.console import Console

from dros.settings import DrosPaths, DrosSettings
from dros.wgsd_install import install_wgsd_binary, resolve_wgsd_binary


def _settings(tmp_path: Path) -> DrosSettings:
    return DrosSettings(
        sysRoot=tmp_path / "sysroot",
        paths=DrosPaths(configs=tmp_path / "configs"),
    )


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, color_system=None, width=100)


def _elf_payload(label: bytes) -> bytes:
    header = bytearray(64)
    header[:4] = b"\x7fELF"
    header[4] = 2
    header[5] = 1
    header[18:20] = b"\x3e\x00"
    return bytes(header) + label


def test_install_wgsd_client_from_local_binary(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    source = tmp_path / "wgsd-client"
    payload = _elf_payload(b" local client")
    source.write_bytes(payload)

    target = install_wgsd_binary(
        settings,
        resolve_wgsd_binary("wgsd-client"),
        source=str(source),
        source_is_archive=False,
        console=_console(),
    )

    installed = settings.sys_root / "usr/local/bin/wgsd-client"
    assert target == Path("/usr/local/bin/wgsd-client")
    assert installed.read_bytes() == payload
    assert installed.stat().st_mode & stat.S_IXUSR


def test_resolve_wgsd_binary_accepts_kind_like_target() -> None:
    assert resolve_wgsd_binary("wgsd-client/wgsd-client").name == "wgsd-client"
    assert resolve_wgsd_binary("wgsd-coredns/coredns").name == "wgsd-coredns"


def test_install_wgsd_coredns_from_default_archive_url(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    archive = io.BytesIO()
    payload = _elf_payload(b" coredns")
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        info = tarfile.TarInfo("coredns")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    def fake_download(url: str, timeout: float) -> bytes:
        assert url == resolve_wgsd_binary("wgsd-coredns").default_download_url
        assert timeout == 30.0
        return archive.getvalue()

    install_wgsd_binary(
        settings,
        resolve_wgsd_binary("coredns"),
        source=resolve_wgsd_binary("wgsd-coredns").default_download_url,
        source_is_archive=True,
        console=_console(),
        downloader=fake_download,
    )

    assert (settings.sys_root / "usr/local/bin/coredns").read_bytes() == payload


def test_install_rejects_custom_archive_source(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    source = tmp_path / "wgsd-client.tar.gz"
    source.write_bytes(b"\x1f\x8b archive")

    with pytest.raises(ValueError, match="must be an extracted linux/amd64 ELF binary"):
        install_wgsd_binary(
            settings,
            resolve_wgsd_binary("wgsd-client"),
            source=str(source),
            source_is_archive=False,
            console=_console(),
        )
