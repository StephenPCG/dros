from __future__ import annotations

import math
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from dros.config_objects import CollectdConfig, ConfigObject, load_config_objects
from dros.settings import DrosSettings

TIMESPANS: dict[str, tuple[str, int]] = {
    "1h": ("1h", 60 * 60),
    "4h": ("4h", 4 * 60 * 60),
    "12h": ("12h", 12 * 60 * 60),
    "1d": ("1d", 24 * 60 * 60),
    "1w": ("1w", 7 * 24 * 60 * 60),
    "1m": ("1m", 30 * 24 * 60 * 60),
}
RrdRunner = Callable[[list[str]], str]


@dataclass(frozen=True)
class RrdFileSet:
    root: Path
    bandwidth: dict[str, Path]
    ping_latency: dict[str, Path]
    ping_loss: dict[str, Path]
    configured_ping_hosts: list[str]


def collect_rrd_targets(settings: DrosSettings) -> dict[str, Any]:
    files = _rrd_files(settings)
    ping_names = sorted(
        {
            *files.configured_ping_hosts,
            *files.ping_latency.keys(),
            *files.ping_loss.keys(),
        }
    )
    return {
        "timespans": [
            {"id": timespan_id, "label": label, "seconds": seconds}
            for timespan_id, (label, seconds) in TIMESPANS.items()
        ],
        "bandwidth": [
            {"name": name, "hasData": True}
            for name in sorted(files.bandwidth)
        ],
        "ping": [
            {
                "name": name,
                "hasLatency": name in files.ping_latency,
                "hasLoss": name in files.ping_loss,
            }
            for name in ping_names
        ],
    }


def collect_bandwidth_series(
    settings: DrosSettings,
    *,
    target: str,
    timespan: str,
    runner: RrdRunner | None = None,
) -> dict[str, Any]:
    seconds = _timespan_seconds(timespan)
    files = _rrd_files(settings)
    rrd_path = files.bandwidth.get(target)
    if rrd_path is None:
        return {
            "target": target,
            "timespan": timespan,
            "unit": "bit/s",
            "points": [],
        }
    rows = _fetch_rrd(rrd_path, seconds, runner)
    points = []
    for row in rows:
        rx = _row_value(row, "rx", fallback_index=0)
        tx = _row_value(row, "tx", fallback_index=1)
        points.append(
            {
                "timestamp": row["timestamp"],
                "rxBitsPerSecond": None if rx is None else rx * 8.0,
                "txBitsPerSecond": None if tx is None else tx * 8.0,
            }
        )
    return {
        "target": target,
        "timespan": timespan,
        "unit": "bit/s",
        "points": points,
    }


def collect_ping_series(
    settings: DrosSettings,
    *,
    target: str,
    timespan: str,
    runner: RrdRunner | None = None,
) -> dict[str, Any]:
    seconds = _timespan_seconds(timespan)
    files = _rrd_files(settings)
    latency = _single_value_rows(files.ping_latency.get(target), seconds, runner)
    loss = _single_value_rows(files.ping_loss.get(target), seconds, runner)
    timestamps = sorted(set(latency) | set(loss))
    points = []
    for timestamp in timestamps:
        loss_value = loss.get(timestamp)
        points.append(
            {
                "timestamp": timestamp,
                "latencyMs": latency.get(timestamp),
                "lossPercent": None if loss_value is None else loss_value * 100.0,
            }
        )
    return {
        "target": target,
        "timespan": timespan,
        "latencyUnit": "ms",
        "lossUnit": "%",
        "points": points,
    }


def _rrd_files(settings: DrosSettings) -> RrdFileSet:
    config = _collectd_config(settings)
    root = _target_path(settings, Path(config.rrd_dir))
    bandwidth: dict[str, Path] = {}
    ping_latency: dict[str, Path] = {}
    ping_loss: dict[str, Path] = {}
    if root.exists():
        for path in root.glob("*/interface-*/if_octets.rrd"):
            name = path.parent.name.removeprefix("interface-")
            bandwidth.setdefault(name, path)
        for path in root.glob("interface-*/if_octets.rrd"):
            name = path.parent.name.removeprefix("interface-")
            bandwidth.setdefault(name, path)
        for path in root.glob("*/ping/ping-*.rrd"):
            name = path.stem.removeprefix("ping-")
            ping_latency.setdefault(name, path)
        for path in root.glob("ping/ping-*.rrd"):
            name = path.stem.removeprefix("ping-")
            ping_latency.setdefault(name, path)
        for path in root.glob("*/ping/ping_droprate-*.rrd"):
            name = path.stem.removeprefix("ping_droprate-")
            ping_loss.setdefault(name, path)
        for path in root.glob("ping/ping_droprate-*.rrd"):
            name = path.stem.removeprefix("ping_droprate-")
            ping_loss.setdefault(name, path)
    return RrdFileSet(
        root=root,
        bandwidth=bandwidth,
        ping_latency=ping_latency,
        ping_loss=ping_loss,
        configured_ping_hosts=list(config.plugins.ping.hosts),
    )


def _collectd_config(settings: DrosSettings) -> CollectdConfig:
    try:
        configs = load_config_objects(settings)
    except (OSError, ValueError, ValidationError):
        return CollectdConfig()
    objects = configs.by_kind("Collectd")
    if not objects:
        return CollectdConfig()
    selected = _select_collectd_object(objects)
    try:
        return configs.resolve_object(selected, CollectdConfig)
    except ValidationError:
        return CollectdConfig()


def _select_collectd_object(objects: list[ConfigObject]) -> ConfigObject:
    for obj in reversed(objects):
        if obj.name == "system":
            return obj
    return objects[-1]


def _target_path(settings: DrosSettings, path: Path) -> Path:
    if not path.is_absolute() or settings.sys_root == Path("/"):
        return path
    return settings.sys_root / path.relative_to("/")


def _timespan_seconds(timespan: str) -> int:
    try:
        return TIMESPANS[timespan][1]
    except KeyError as exc:
        raise ValueError(f"unsupported timespan: {timespan}") from exc


def _fetch_rrd(
    path: Path,
    seconds: int,
    runner: RrdRunner | None,
) -> list[dict[str, Any]]:
    output = (runner or _run_rrdtool)(
        [
            "rrdtool",
            "fetch",
            str(path),
            "AVERAGE",
            "--start",
            f"now-{seconds}",
            "--end",
            "now",
        ]
    )
    return _parse_fetch_output(output)


def _run_rrdtool(command: list[str]) -> str:
    result = subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout


def _parse_fetch_output(output: str) -> list[dict[str, Any]]:
    headers: list[str] | None = None
    rows: list[dict[str, Any]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if ":" not in line:
            headers = line.split()
            continue
        if headers is None:
            continue
        timestamp_text, values_text = line.split(":", 1)
        try:
            timestamp = int(float(timestamp_text.strip()))
        except ValueError:
            continue
        values = [_parse_float(item) for item in values_text.split()]
        row: dict[str, Any] = {"timestamp": timestamp, "_values": values}
        for index, header in enumerate(headers):
            row[header] = values[index] if index < len(values) else None
        if any(value is not None for value in values):
            rows.append(row)
    return rows


def _parse_float(value: str) -> float | None:
    try:
        parsed = float(value)
    except ValueError:
        return None
    if math.isnan(parsed) or math.isinf(parsed):
        return None
    return parsed


def _row_value(row: dict[str, Any], key: str, *, fallback_index: int) -> float | None:
    value = row.get(key)
    if isinstance(value, float):
        return value
    values = row.get("_values")
    if isinstance(values, list) and fallback_index < len(values):
        fallback = values[fallback_index]
        return fallback if isinstance(fallback, float) else None
    return None


def _single_value_rows(
    path: Path | None,
    seconds: int,
    runner: RrdRunner | None,
) -> dict[int, float | None]:
    if path is None:
        return {}
    result: dict[int, float | None] = {}
    for row in _fetch_rrd(path, seconds, runner):
        value = _row_value(row, "value", fallback_index=0)
        if value is not None:
            result[row["timestamp"]] = value
    return result
