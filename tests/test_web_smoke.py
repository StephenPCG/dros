from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dros.settings import DrosPaths, DrosSettings, WebSettings
from dros.web.app import create_app
from dros.web.auth import WebAuthStore
from dros.web.rrd import collect_bandwidth_series, collect_metric_series, collect_ping_series


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


def test_monitor_dashboard_grid_uses_finer_resize_columns() -> None:
    source = (Path(__file__).resolve().parents[1] / "web/src/App.tsx").read_text(encoding="utf-8")

    assert "const DASHBOARD_COLUMNS = { lg: 12, md: 8, sm: 4 }" in source
    assert "function defaultDashboardItemWidth(columns: number): number" in source
    assert "const width = defaultDashboardItemWidth(columns)" in source
    assert "x: (index * width) % columns" in source
    assert "y: Math.floor((index * width) / columns) * height" in source


def test_web_frontend_dashboard_uses_server_storage_and_fixed_datetime_format() -> None:
    source = (Path(__file__).resolve().parents[1] / "web/src/App.tsx").read_text(encoding="utf-8")

    assert '"/api/monitor/dashboards"' in source
    assert "window.localStorage.setItem(DASHBOARD_STORAGE_KEY" not in source
    assert "布局和时间段保存在服务端" in source
    assert "function formatDateTime" in source
    assert ".toLocaleString(" not in source
    assert ".toLocaleDateString(" not in source


def test_web_frontend_dashboard_supports_metric_charts_and_vertical_shared_crosshair() -> None:
    source = (Path(__file__).resolve().parents[1] / "web/src/App.tsx").read_text(encoding="utf-8")

    assert '"cpu", "memory", "load", "disk", "df", "conntrack", "contextswitch", "irq"' in source
    assert "/api/monitor/rrd/metric" in source
    assert "metricChartOption" in source
    assert "metricChartPlotLabels" in source
    assert "metricSeriesColorClass(series, label)" in source
    assert "isHiddenMetricPlotLabel(series, label)" in source
    assert 'label === "free"' in source
    assert 'type: "line",' in source
    assert 'stack: stacked ? "total" : undefined' in source
    assert "areaStyle: stacked" in source
    assert 'axis: "x",' in source
    assert 'type: "cross"' not in source
    assert "echarts.connect(groupId)" in source


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


def test_web_monitor_dashboards_require_auth_and_persist_to_server_run_dir(tmp_path: Path) -> None:
    sysroot = tmp_path / "sysroot"
    sysroot.mkdir()
    settings = DrosSettings(
        sysRoot=sysroot,
        paths=DrosPaths(configs=tmp_path / "configs"),
        web=WebSettings(authDb=tmp_path / "web-auth.sqlite3"),
    )
    WebAuthStore(settings.web.auth_db).create_user("alice", "secret")
    client = TestClient(create_app(settings))
    payload = {
        "dashboards": [
            {
                "id": "dashboard-main",
                "name": "Main",
                "timespan": "4h",
                "charts": [{"id": "chart-wan", "type": "bandwidth", "target": "pppoe-telecom"}],
                "layouts": {"lg": [{"i": "chart-wan", "x": 0, "y": 0, "w": 4, "h": 12}]},
                "layoutVersion": 2,
            }
        ],
        "activeDashboardId": "dashboard-main",
    }

    assert client.get("/api/monitor/dashboards").status_code == 401
    assert client.put("/api/monitor/dashboards", json=payload).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "secret", "remember": False},
    ).status_code == 200

    assert client.get("/api/monitor/dashboards").json() == {
        "dashboards": [],
        "activeDashboardId": None,
    }
    response = client.put("/api/monitor/dashboards", json=payload)

    assert response.status_code == 200
    assert response.json() == payload
    stored = sysroot / "opt/gateway/run/web/dashboards.json"
    assert stored.exists()
    assert "dashboard-main" in stored.read_text(encoding="utf-8")
    client2 = TestClient(create_app(settings))
    assert client2.post(
        "/api/auth/login",
        json={"username": "alice", "password": "secret", "remember": False},
    ).status_code == 200
    assert client2.get("/api/monitor/dashboards").json() == payload


def test_web_monitor_devices_require_auth_and_merge_dnsmasq_leases_with_arp(tmp_path: Path) -> None:
    sysroot = tmp_path / "sysroot"
    (sysroot / "proc/net").mkdir(parents=True)
    (sysroot / "var/lib/misc").mkdir(parents=True)
    (sysroot / "proc/net/arp").write_text(
        "IP address       HW type     Flags       HW address            Mask     Device\n"
        "192.168.8.10     0x1         0x2         00:11:22:33:44:55     *        br0\n"
        "192.168.8.20     0x1         0x2         aa:bb:cc:dd:ee:ff     *        br0\n",
        encoding="utf-8",
    )
    (sysroot / "var/lib/misc/dnsmasq.leases").write_text(
        "1900000000 00:11:22:33:44:55 192.168.8.10 nas 01:00:11:22:33:44:55\n",
        encoding="utf-8",
    )
    settings = DrosSettings(
        sysRoot=sysroot,
        paths=DrosPaths(configs=tmp_path / "configs"),
        web=WebSettings(authDb=tmp_path / "web-auth.sqlite3"),
    )
    WebAuthStore(settings.web.auth_db).create_user("alice", "secret")
    client = TestClient(create_app(settings))

    assert client.get("/api/monitor/devices").status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "secret", "remember": False},
    ).status_code == 200
    response = client.get("/api/monitor/devices")

    assert response.status_code == 200
    assert response.json()["devices"] == [
        {
            "hostname": "nas",
            "ipAddresses": ["192.168.8.10"],
            "macAddress": "00:11:22:33:44:55",
            "interface": "br0",
            "sources": ["arp", "dnsmasq"],
            "leaseExpiresAt": 1900000000,
        },
        {
            "hostname": None,
            "ipAddresses": ["192.168.8.20"],
            "macAddress": "aa:bb:cc:dd:ee:ff",
            "interface": "br0",
            "sources": ["arp"],
            "leaseExpiresAt": None,
        },
    ]


def test_web_monitor_openvpn_clients_require_auth_and_parse_status_v3(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "openvpn.ovpn-lan.status").write_text(
        "TITLE\tOpenVPN 2.6.14 x86_64-pc-linux-gnu\n"
        "TIME\t2026-05-20 00:53:03\t1779209583\n"
        "HEADER\tCLIENT_LIST\tCommon Name\tReal Address\tVirtual Address\t"
        "Virtual IPv6 Address\tBytes Received\tBytes Sent\tConnected Since\t"
        "Connected Since (time_t)\tUsername\tClient ID\tPeer ID\tData Channel Cipher\n"
        "CLIENT_LIST\tzhangcheng\t180.107.236.117:59300\t10.80.249.2\t\t"
        "2038896\t3753282\t2026-05-20 00:49:05\t1779209345\tUNDEF\t0\t0\tAES-256-GCM\n"
        "HEADER\tROUTING_TABLE\tVirtual Address\tCommon Name\tReal Address\tLast Ref\tLast Ref (time_t)\n"
        "ROUTING_TABLE\t10.80.249.2\tzhangcheng\t180.107.236.117:59300\t"
        "2026-05-20 00:53:02\t1779209582\n"
        "GLOBAL_STATS\tMax bcast/mcast queue length\t0\n"
        "GLOBAL_STATS\tdco_enabled\t0\n"
        "END\n",
        encoding="utf-8",
    )
    sysroot = tmp_path / "sysroot"
    sysroot.mkdir()
    settings = DrosSettings(
        sysRoot=sysroot,
        paths=DrosPaths(configs=tmp_path / "configs", run=run),
        web=WebSettings(authDb=tmp_path / "web-auth.sqlite3"),
    )
    WebAuthStore(settings.web.auth_db).create_user("alice", "secret")
    client = TestClient(create_app(settings))

    assert client.get("/api/monitor/openvpn-clients").status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "secret", "remember": False},
    ).status_code == 200
    response = client.get("/api/monitor/openvpn-clients")

    assert response.status_code == 200
    assert response.json()["clients"] == [
        {
            "interface": "ovpn-lan",
            "commonName": "zhangcheng",
            "realAddress": "180.107.236.117:59300",
            "publicIp": "180.107.236.117",
            "publicPort": 59300,
            "virtualAddress": "10.80.249.2",
            "virtualIpv6Address": None,
            "connectedSince": "2026-05-20 00:49:05",
            "connectedSinceTimestamp": 1779209345,
            "bytesReceived": 2038896,
            "bytesSent": 3753282,
        }
    ]


def test_web_logs_require_auth_and_return_invocation_and_error_records(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "gw-invocations.log").write_text(
        '{"ts": 1700000000, "kind": "cli", "phase": "start", "argv": ["update"]}\n'
        '{"ts": 1700000001, "kind": "event.enqueue", "event": "xfrm-start", "iface": "office"}\n',
        encoding="utf-8",
    )
    (logs / "gw-errors.log").write_text(
        '{"ts": 1700000002, "channel": "event", "event": "xfrm-start", '
        '"iface": "office", "message": "boom"}\n',
        encoding="utf-8",
    )
    settings = DrosSettings(
        paths=DrosPaths(configs=tmp_path / "configs", logs=logs),
        web=WebSettings(authDb=tmp_path / "web-auth.sqlite3"),
    )
    WebAuthStore(settings.web.auth_db).create_user("alice", "secret")
    client = TestClient(create_app(settings))

    assert client.get("/api/logs/invocations").status_code == 401
    assert client.get("/api/logs/errors").status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "secret", "remember": False},
    ).status_code == 200

    invocations = client.get("/api/logs/invocations?limit=10")
    errors = client.get("/api/logs/errors?limit=10")

    assert invocations.status_code == 200
    assert invocations.json()["records"] == [
        {"ts": 1700000001, "kind": "event.enqueue", "event": "xfrm-start", "iface": "office"},
        {"ts": 1700000000, "kind": "cli", "phase": "start", "argv": ["update"]},
    ]
    assert errors.status_code == 200
    assert errors.json()["records"] == [
        {
            "ts": 1700000002,
            "channel": "event",
            "event": "xfrm-start",
            "iface": "office",
            "message": "boom",
        }
    ]


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
    (rrd_root / "cpu-0").mkdir(parents=True)
    (rrd_root / "memory").mkdir(parents=True)
    (rrd_root / "load").mkdir(parents=True)
    (rrd_root / "disk-sda").mkdir(parents=True)
    (rrd_root / "df-root").mkdir(parents=True)
    (rrd_root / "conntrack").mkdir(parents=True)
    (rrd_root / "contextswitch").mkdir(parents=True)
    (rrd_root / "irq").mkdir(parents=True)
    (rrd_root / "interface-eth0/if_octets.rrd").touch()
    (rrd_root / "interface-br0/if_octets.rrd").touch()
    (rrd_root / "ping/ping-1.1.1.1.rrd").touch()
    (rrd_root / "ping/ping_droprate-1.1.1.1.rrd").touch()
    (rrd_root / "cpu-0/cpu-user.rrd").touch()
    (rrd_root / "memory/percent-used.rrd").touch()
    (rrd_root / "memory/percent-free.rrd").touch()
    (rrd_root / "load/load.rrd").touch()
    (rrd_root / "disk-sda/disk_octets.rrd").touch()
    (rrd_root / "df-root/percent_bytes-used.rrd").touch()
    (rrd_root / "df-root/percent_bytes-free.rrd").touch()
    (rrd_root / "conntrack/conntrack.rrd").touch()
    (rrd_root / "contextswitch/contextswitch.rrd").touch()
    (rrd_root / "irq/irq-16.rrd").touch()
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
        {"id": "4h", "label": "4h", "seconds": 14400},
        {"id": "12h", "label": "12h", "seconds": 43200},
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
    assert payload["metrics"] == {
        "conntrack": [{"name": "system", "series": ["conntrack"]}],
        "contextswitch": [{"name": "system", "series": ["contextswitch"]}],
        "cpu": [
            {"name": "all", "series": ["user"]},
            {"name": "0", "series": ["user"]},
        ],
        "df": [{"name": "root", "series": ["used", "free"]}],
        "disk": [{"name": "sda", "series": ["read", "write"]}],
        "irq": [{"name": "16", "series": ["irq"]}],
        "load": [{"name": "system", "series": ["1m", "5m", "15m"]}],
        "memory": [{"name": "system", "series": ["used", "free"]}],
    }


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
1700000010: 2.5000000000e-01
"""

    payload = collect_ping_series(settings, target="1.1.1.1", timespan="1h", runner=runner)

    assert payload["points"] == [
        {"timestamp": 1700000000, "latencyMs": 12.0, "lossPercent": 0.0},
        {"timestamp": 1700000010, "latencyMs": 15.0, "lossPercent": 25.0},
    ]


def test_collect_metric_series_fetches_load_and_aggregates_cpu_points(tmp_path: Path) -> None:
    sysroot = tmp_path / "sysroot"
    load = sysroot / "var/lib/collectd/rrd/gateway/load/load.rrd"
    cpu0 = sysroot / "var/lib/collectd/rrd/gateway/cpu-0/cpu-user.rrd"
    cpu1 = sysroot / "var/lib/collectd/rrd/gateway/cpu-1/cpu-user.rrd"
    for path in [load, cpu0, cpu1]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    settings = DrosSettings(sysRoot=sysroot, paths=DrosPaths(configs=tmp_path / "configs"))

    def runner(command: list[str]) -> str:
        if command[2] == str(load):
            return """
                       shortterm       midterm        longterm
1700000000: 1.000000e-01 2.000000e-01 3.000000e-01
"""
        if command[2] == str(cpu0):
            return """
                           value
1700000000: 1.000000e+01
"""
        return """
                           value
1700000000: 3.000000e+01
"""

    load_payload = collect_metric_series(settings, kind="load", target="system", timespan="1h", runner=runner)
    cpu_payload = collect_metric_series(settings, kind="cpu", target="all", timespan="1h", runner=runner)

    assert load_payload == {
        "kind": "load",
        "target": "system",
        "timespan": "1h",
        "unit": "",
        "labels": ["1m", "5m", "15m"],
        "points": [{"timestamp": 1700000000, "values": {"1m": 0.1, "5m": 0.2, "15m": 0.3}}],
    }
    assert cpu_payload == {
        "kind": "cpu",
        "target": "all",
        "timespan": "1h",
        "unit": "%",
        "labels": ["user"],
        "points": [{"timestamp": 1700000000, "values": {"user": 20.0}}],
    }
