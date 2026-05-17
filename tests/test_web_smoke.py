from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dros.settings import DrosPaths, DrosSettings, WebSettings
from dros.web.app import create_app
from dros.web.auth import WebAuthStore


def test_web_health_endpoint() -> None:
    client = TestClient(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_web_static_serves_spa_fallback_for_page_routes(tmp_path: Path) -> None:
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<main>DROS app</main>", encoding="utf-8")
    settings = DrosSettings(web=WebSettings(staticDir=static_dir))
    client = TestClient(create_app(settings))

    response = client.get("/openvpn")

    assert response.status_code == 200
    assert "DROS app" in response.text


def test_web_static_keeps_missing_assets_as_404(tmp_path: Path) -> None:
    static_dir = tmp_path / "dist"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<main>DROS app</main>", encoding="utf-8")
    settings = DrosSettings(web=WebSettings(staticDir=static_dir))
    client = TestClient(create_app(settings))

    response = client.get("/assets/missing.js")

    assert response.status_code == 404


def test_web_monitor_summary_requires_auth_and_reports_system_counters(tmp_path: Path) -> None:
    sysroot = tmp_path / "sysroot"
    (sysroot / "proc").mkdir(parents=True)
    (sysroot / "sys/class/net/eth0/statistics").mkdir(parents=True)
    (sysroot / "sys/class/net/eth0/operstate").write_text("up\n", encoding="utf-8")
    (sysroot / "sys/class/net/eth0/statistics/rx_bytes").write_text("1000\n", encoding="utf-8")
    (sysroot / "sys/class/net/eth0/statistics/tx_bytes").write_text("2000\n", encoding="utf-8")
    (sysroot / "proc/stat").write_text("cpu  100 0 0 100 0 0 0 0 0 0\n", encoding="utf-8")
    (sysroot / "proc/meminfo").write_text(
        "MemTotal:       1024 kB\nMemAvailable:    256 kB\n",
        encoding="utf-8",
    )
    (sysroot / "proc/loadavg").write_text("0.01 0.02 0.03 1/10 100\n", encoding="utf-8")
    (sysroot / "proc/uptime").write_text("120.0 100.0\n", encoding="utf-8")
    settings = DrosSettings(
        sysRoot=sysroot,
        paths=DrosPaths(configs=tmp_path / "configs"),
        web=WebSettings(authDb=tmp_path / "web-auth.sqlite3"),
    )
    WebAuthStore(settings.web.auth_db).create_user("alice", "secret")
    client = TestClient(create_app(settings))

    assert client.get("/api/monitor/summary").status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "secret", "remember": False},
    ).status_code == 200
    response = client.get("/api/monitor/summary")

    assert response.status_code == 200
    payload = response.json()
    assert payload["memory"]["totalBytes"] == 1024 * 1024
    assert payload["memory"]["usedBytes"] == 768 * 1024
    assert payload["interfaces"][0]["name"] == "eth0"
    assert payload["interfaces"][0]["rxBytes"] == 1000
