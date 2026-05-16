from __future__ import annotations

from pathlib import Path

from dros.settings import load_settings


def test_load_settings_reads_web_host_and_port(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.yaml"
    settings_file.write_text(
        """
sysRoot: /tmp/sysroot
web:
  host: 0.0.0.0
  port: 8765
""".strip()
    )

    settings = load_settings(settings_file)

    assert settings.sys_root == Path("/tmp/sysroot")
    assert settings.web.host == "0.0.0.0"
    assert settings.web.port == 8765


def test_load_settings_reads_web_auth_database_path(tmp_path: Path) -> None:
    settings_file = tmp_path / "settings.yaml"
    auth_db = tmp_path / "auth.sqlite3"
    settings_file.write_text(
        f"""
web:
  authDb: {auth_db}
""".strip()
    )

    settings = load_settings(settings_file)

    assert settings.web.auth_db == auth_db
