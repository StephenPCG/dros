from __future__ import annotations

import platform
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
