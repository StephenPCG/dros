from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dros.settings import DrosPaths, DrosSettings, WebSettings
from dros.web.app import create_app
from dros.web.auth import WebAuthStore
from dros.web.rrd import collect_bandwidth_series, collect_ping_series


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


def test_web_monitor_rrd_targets_require_auth_and_scan_collectd_data(tmp_path: Path) -> None:
    sysroot = tmp_path / "sysroot"
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "collectd.yaml").write_text(
        """
kind: Collectd
metadata:
  name: system
spec:
  rrdDir: /var/lib/collectd/rrd
  plugins:
    ping:
      enabled: true
      hosts:
        - 1.1.1.1
        - 9.9.9.9
""".lstrip(),
        encoding="utf-8",
    )
    rrd_root = sysroot / "var/lib/collectd/rrd/gateway"
    (rrd_root / "interface-eth0").mkdir(parents=True)
    (rrd_root / "interface-br0").mkdir(parents=True)
    (rrd_root / "ping").mkdir(parents=True)
    (rrd_root / "interface-eth0/if_octets.rrd").touch()
    (rrd_root / "interface-br0/if_octets.rrd").touch()
    (rrd_root / "ping/ping-1.1.1.1.rrd").touch()
    (rrd_root / "ping/ping_droprate-1.1.1.1.rrd").touch()
    settings = DrosSettings(
        sysRoot=sysroot,
        paths=DrosPaths(configs=configs),
        web=WebSettings(authDb=tmp_path / "web-auth.sqlite3"),
    )
    WebAuthStore(settings.web.auth_db).create_user("alice", "secret")
    client = TestClient(create_app(settings))

    assert client.get("/api/monitor/rrd/targets").status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "secret", "remember": False},
    ).status_code == 200
    response = client.get("/api/monitor/rrd/targets")

    assert response.status_code == 200
    payload = response.json()
    assert payload["timespans"] == [
        {"id": "1h", "label": "1h", "seconds": 3600},
        {"id": "1d", "label": "1d", "seconds": 86400},
        {"id": "1w", "label": "1w", "seconds": 604800},
        {"id": "1m", "label": "1m", "seconds": 2592000},
    ]
    assert payload["bandwidth"] == [
        {"name": "br0", "hasData": True},
        {"name": "eth0", "hasData": True},
    ]
    assert payload["ping"] == [
        {"name": "1.1.1.1", "hasLatency": True, "hasLoss": True},
        {"name": "9.9.9.9", "hasLatency": False, "hasLoss": False},
    ]


def test_collect_bandwidth_series_fetches_rrd_points(tmp_path: Path) -> None:
    sysroot = tmp_path / "sysroot"
    rrd = sysroot / "var/lib/collectd/rrd/gateway/interface-eth0/if_octets.rrd"
    rrd.parent.mkdir(parents=True)
    rrd.touch()
    settings = DrosSettings(sysRoot=sysroot, paths=DrosPaths(configs=tmp_path / "configs"))
    commands: list[list[str]] = []

    def runner(command: list[str]) -> str:
        commands.append(command)
        return """
                              rx                 tx
1700000000: 1.2500000000e+02 2.5000000000e+02
1700000010: nan 5.0000000000e+02
"""

    payload = collect_bandwidth_series(settings, target="eth0", timespan="1h", runner=runner)

    assert commands == [
        [
            "rrdtool",
            "fetch",
            str(rrd),
            "AVERAGE",
            "--start",
            "now-3600",
            "--end",
            "now",
        ]
    ]
    assert payload["target"] == "eth0"
    assert payload["timespan"] == "1h"
    assert payload["points"] == [
        {"timestamp": 1700000000, "rxBitsPerSecond": 1000.0, "txBitsPerSecond": 2000.0},
        {"timestamp": 1700000010, "rxBitsPerSecond": None, "txBitsPerSecond": 4000.0},
    ]


def test_collect_ping_series_fetches_latency_and_loss_points(tmp_path: Path) -> None:
    sysroot = tmp_path / "sysroot"
    latency = sysroot / "var/lib/collectd/rrd/gateway/ping/ping-1.1.1.1.rrd"
    loss = sysroot / "var/lib/collectd/rrd/gateway/ping/ping_droprate-1.1.1.1.rrd"
    latency.parent.mkdir(parents=True)
    latency.touch()
    loss.touch()
    settings = DrosSettings(sysRoot=sysroot, paths=DrosPaths(configs=tmp_path / "configs"))

    def runner(command: list[str]) -> str:
        if command[2] == str(latency):
            return """
                           value
1700000000: 1.2000000000e+01
1700000010: 1.5000000000e+01
"""
        return """
                           value
1700000000: 0.0000000000e+00
1700000010: 2.5000000000e+01
"""

    payload = collect_ping_series(settings, target="1.1.1.1", timespan="1h", runner=runner)

    assert payload["points"] == [
        {"timestamp": 1700000000, "latencyMs": 12.0, "lossPercent": 0.0},
        {"timestamp": 1700000010, "latencyMs": 15.0, "lossPercent": 25.0},
    ]
