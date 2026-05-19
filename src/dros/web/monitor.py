from __future__ import annotations

import csv
import ipaddress
import platform
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dros.settings import DrosSettings


@dataclass(frozen=True)
class CounterSample:
    timestamp: float
    cpu_total: int
    cpu_idle: int
    interfaces: dict[str, tuple[int, int]]


@dataclass
class DeviceRecord:
    hostname: str | None
    ip_addresses: set[str]
    mac_address: str | None
    interface: str | None
    sources: set[str]
    lease_expires_at: int | None


def collect_monitor_summary(
    settings: DrosSettings,
    *,
    sample_seconds: float = 0.05,
) -> dict[str, Any]:
    first = _sample(settings)
    if sample_seconds > 0:
        time.sleep(sample_seconds)
    second = _sample(settings)
    return {
        "system": _system_status(settings, first, second),
        "memory": _memory_status(settings),
        "interfaces": _interface_status(settings, first, second),
    }


def collect_network_devices(settings: DrosSettings) -> dict[str, Any]:
    devices: dict[str, DeviceRecord] = {}
    for item in _read_dnsmasq_leases(settings):
        _add_device(devices, source="dnsmasq", **item)
    for item in _read_arp_table(settings):
        _add_device(devices, source="arp", **item)
    return {
        "devices": [
            {
                "hostname": record.hostname,
                "ipAddresses": sorted(record.ip_addresses, key=_ip_sort_key),
                "macAddress": record.mac_address,
                "interface": record.interface,
                "sources": sorted(record.sources),
                "leaseExpiresAt": record.lease_expires_at,
            }
            for record in sorted(devices.values(), key=_device_sort_key)
        ]
    }


def collect_openvpn_clients(settings: DrosSettings) -> dict[str, Any]:
    clients: list[dict[str, Any]] = []
    for path in _openvpn_status_paths(settings):
        interface = _openvpn_interface_from_status_path(path)
        for client in _parse_openvpn_status(path):
            clients.append({"interface": interface, **client})
    return {
        "clients": sorted(
            clients,
            key=lambda item: (
                str(item.get("interface") or ""),
                str(item.get("commonName") or ""),
                int(item.get("connectedSinceTimestamp") or 0),
            ),
        )
    }


def _sample(settings: DrosSettings) -> CounterSample:
    cpu_total, cpu_idle = _read_cpu_times(settings)
    return CounterSample(
        timestamp=time.monotonic(),
        cpu_total=cpu_total,
        cpu_idle=cpu_idle,
        interfaces=_read_interface_counters(settings),
    )


def _system_status(
    settings: DrosSettings,
    first: CounterSample,
    second: CounterSample,
) -> dict[str, Any]:
    total_delta = second.cpu_total - first.cpu_total
    idle_delta = second.cpu_idle - first.cpu_idle
    cpu_percent = None
    if total_delta > 0:
        cpu_percent = round(max(0.0, min(100.0, 100.0 * (1.0 - idle_delta / total_delta))), 1)
    return {
        "hostname": platform.node() or "unknown",
        "kernel": platform.release(),
        "cpuPercent": cpu_percent,
        "loadavg": _read_loadavg(settings),
        "uptimeSeconds": _read_uptime(settings),
    }


def _memory_status(settings: DrosSettings) -> dict[str, Any]:
    meminfo = _read_meminfo(settings)
    total = meminfo.get("MemTotal")
    available = meminfo.get("MemAvailable")
    used = total - available if total is not None and available is not None else None
    percent = round(100.0 * used / total, 1) if total and used is not None else None
    return {
        "totalBytes": total * 1024 if total is not None else None,
        "availableBytes": available * 1024 if available is not None else None,
        "usedBytes": used * 1024 if used is not None else None,
        "usedPercent": percent,
    }


def _interface_status(
    settings: DrosSettings,
    first: CounterSample,
    second: CounterSample,
) -> list[dict[str, Any]]:
    interval = max(second.timestamp - first.timestamp, 0.001)
    result: list[dict[str, Any]] = []
    names = sorted(set(first.interfaces) | set(second.interfaces))
    for name in names:
        rx1, tx1 = first.interfaces.get(name, (0, 0))
        rx2, tx2 = second.interfaces.get(name, (rx1, tx1))
        iface_path = _target_path(settings, "/sys/class/net") / name
        result.append(
            {
                "name": name,
                "operstate": _read_text(iface_path / "operstate") or "unknown",
                "rxBytes": rx2,
                "txBytes": tx2,
                "rxBytesPerSecond": max(0, int((rx2 - rx1) / interval)),
                "txBytesPerSecond": max(0, int((tx2 - tx1) / interval)),
            }
        )
    return result


def _read_cpu_times(settings: DrosSettings) -> tuple[int, int]:
    content = _read_text(_target_path(settings, "/proc/stat"))
    if not content:
        return (0, 0)
    first_line = content.splitlines()[0].split()
    if not first_line or first_line[0] != "cpu":
        return (0, 0)
    values = [int(item) for item in first_line[1:] if item.isdigit()]
    idle = (values[3] if len(values) > 3 else 0) + (values[4] if len(values) > 4 else 0)
    return (sum(values), idle)


def _read_interface_counters(settings: DrosSettings) -> dict[str, tuple[int, int]]:
    sys_net = _target_path(settings, "/sys/class/net")
    if not sys_net.exists():
        return {}
    counters: dict[str, tuple[int, int]] = {}
    for entry in sys_net.iterdir():
        if not entry.is_dir() or entry.name == "lo":
            continue
        counters[entry.name] = (
            _read_int(entry / "statistics" / "rx_bytes"),
            _read_int(entry / "statistics" / "tx_bytes"),
        )
    return counters


def _read_dnsmasq_leases(settings: DrosSettings) -> list[dict[str, Any]]:
    leases: list[dict[str, Any]] = []
    for path in _dnsmasq_lease_paths(settings):
        content = _read_text(path)
        if not content:
            continue
        for line in content.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            expires_raw, mac_raw, ip_address, hostname_raw = parts[:4]
            try:
                expires_at = int(expires_raw)
            except ValueError:
                expires_at = None
            leases.append(
                {
                    "hostname": None if hostname_raw == "*" else hostname_raw,
                    "ip_address": ip_address,
                    "mac_address": _normalize_mac(mac_raw),
                    "interface": None,
                    "lease_expires_at": expires_at,
                }
            )
    return leases


def _read_arp_table(settings: DrosSettings) -> list[dict[str, Any]]:
    content = _read_text(_target_path(settings, "/proc/net/arp"))
    if not content:
        return []
    rows: list[dict[str, Any]] = []
    for line in content.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        ip_address, _hw_type, flags, mac_raw, _mask, interface = parts[:6]
        mac_address = _normalize_mac(mac_raw)
        if not mac_address or flags == "0x0":
            continue
        rows.append(
            {
                "hostname": None,
                "ip_address": ip_address,
                "mac_address": mac_address,
                "interface": interface,
                "lease_expires_at": None,
            }
        )
    return rows


def _add_device(
    devices: dict[str, DeviceRecord],
    *,
    source: str,
    hostname: str | None,
    ip_address: str,
    mac_address: str | None,
    interface: str | None,
    lease_expires_at: int | None,
) -> None:
    key = mac_address or ip_address
    record = devices.get(key)
    if record is None:
        record = DeviceRecord(
            hostname=hostname,
            ip_addresses=set(),
            mac_address=mac_address,
            interface=interface,
            sources=set(),
            lease_expires_at=lease_expires_at,
        )
        devices[key] = record
    if hostname:
        record.hostname = hostname
    if ip_address:
        record.ip_addresses.add(ip_address)
    if mac_address:
        record.mac_address = mac_address
    if interface:
        record.interface = interface
    record.sources.add(source)
    if lease_expires_at is not None:
        record.lease_expires_at = lease_expires_at


def _dnsmasq_lease_paths(settings: DrosSettings) -> list[Path]:
    candidates = [
        Path("/var/lib/misc/dnsmasq.leases"),
        Path("/var/lib/dnsmasq/dnsmasq.leases"),
        Path("/run/dnsmasq/dnsmasq.leases"),
    ]
    seen: set[Path] = set()
    paths: list[Path] = [_configured_path(settings, settings.paths.run) / "dnsmasq.leases"]
    seen.add(paths[0])
    for candidate in candidates:
        path = _target_path(settings, str(candidate))
        if path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def _openvpn_status_paths(settings: DrosSettings) -> list[Path]:
    run_dir = _configured_path(settings, settings.paths.run)
    if not run_dir.exists():
        return []
    return sorted(run_dir.glob("openvpn.*.status"))


def _parse_openvpn_status(path: Path) -> list[dict[str, Any]]:
    content = _read_text(path)
    if not content:
        return []
    headers: dict[str, list[str]] = {}
    clients: list[dict[str, Any]] = []
    for row in _openvpn_status_rows(content):
        if not row:
            continue
        section = row[0]
        if section == "HEADER" and len(row) >= 3:
            headers[row[1]] = row[2:]
        elif section == "CLIENT_LIST":
            client = _parse_openvpn_client_row(row[1:], headers.get("CLIENT_LIST"))
            if client is not None:
                clients.append(client)
    return clients


def _openvpn_status_rows(content: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in content.splitlines():
        if not line.strip():
            continue
        if "\t" in line:
            rows.append(line.split("\t"))
        else:
            rows.extend(csv.reader([line]))
    return rows


def _parse_openvpn_client_row(values: list[str], header: list[str] | None) -> dict[str, Any] | None:
    if header:
        row = {
            key: values[index] if index < len(values) else ""
            for index, key in enumerate(header)
        }
    else:
        keys = ["Common Name", "Real Address", "Bytes Received", "Bytes Sent", "Connected Since"]
        row = {key: values[index] if index < len(values) else "" for index, key in enumerate(keys)}
    common_name = _none_if_empty(row.get("Common Name"))
    real_address = _none_if_empty(row.get("Real Address"))
    if common_name is None and real_address is None:
        return None
    public_ip, public_port = _split_public_address(real_address)
    return {
        "commonName": common_name,
        "realAddress": real_address,
        "publicIp": public_ip,
        "publicPort": public_port,
        "virtualAddress": _none_if_empty(row.get("Virtual Address")),
        "virtualIpv6Address": _none_if_empty(row.get("Virtual IPv6 Address")),
        "connectedSince": _none_if_empty(row.get("Connected Since")),
        "connectedSinceTimestamp": _int_or_none(row.get("Connected Since (time_t)")),
        "bytesReceived": _int_or_none(row.get("Bytes Received")),
        "bytesSent": _int_or_none(row.get("Bytes Sent")),
    }


def _read_loadavg(settings: DrosSettings) -> list[float] | None:
    content = _read_text(_target_path(settings, "/proc/loadavg"))
    if not content:
        return None
    try:
        return [float(item) for item in content.split()[:3]]
    except ValueError:
        return None


def _read_uptime(settings: DrosSettings) -> float | None:
    content = _read_text(_target_path(settings, "/proc/uptime"))
    if not content:
        return None
    try:
        return float(content.split()[0])
    except (IndexError, ValueError):
        return None


def _read_meminfo(settings: DrosSettings) -> dict[str, int]:
    content = _read_text(_target_path(settings, "/proc/meminfo"))
    values: dict[str, int] = {}
    for line in content.splitlines():
        key, sep, rest = line.partition(":")
        if not sep:
            continue
        parts = rest.strip().split()
        if not parts:
            continue
        try:
            values[key] = int(parts[0])
        except ValueError:
            continue
    return values


def _read_int(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, OSError, ValueError):
        return 0


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return ""


def _target_path(settings: DrosSettings, path: str) -> Path:
    logical = Path(path)
    if settings.sys_root == Path("/"):
        return logical
    return settings.sys_root / logical.relative_to("/")


def _configured_path(settings: DrosSettings, path: Path) -> Path:
    if path.is_absolute():
        if settings.sys_root != Path("/") and path.exists():
            return path
        return _target_path(settings, str(path))
    return path


def _normalize_mac(value: str) -> str | None:
    normalized = value.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", normalized):
        return None
    if normalized == "00:00:00:00:00:00":
        return None
    return normalized


def _openvpn_interface_from_status_path(path: Path) -> str:
    name = path.name
    prefix = "openvpn."
    suffix = ".status"
    if name.startswith(prefix) and name.endswith(suffix):
        return name[len(prefix) : -len(suffix)]
    return path.stem


def _split_public_address(value: str | None) -> tuple[str | None, int | None]:
    if not value:
        return None, None
    bracketed = re.fullmatch(r"\[([^\]]+)\]:(\d+)", value)
    if bracketed:
        return bracketed.group(1), _int_or_none(bracketed.group(2))
    host, sep, port = value.rpartition(":")
    if sep and host and _int_or_none(port) is not None and ":" not in host:
        return host, _int_or_none(port)
    return value, None


def _none_if_empty(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or normalized == "UNDEF":
        return None
    return normalized


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _device_sort_key(record: DeviceRecord) -> tuple[tuple[int, ...], str]:
    first_ip = min(record.ip_addresses, key=_ip_sort_key) if record.ip_addresses else ""
    return (_ip_sort_key(first_ip), record.mac_address or "")


def _ip_sort_key(value: str) -> tuple[int, ...]:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return (2, *tuple(ord(char) for char in value))
    return (ip.version, *ip.packed)
