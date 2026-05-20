from __future__ import annotations

import math
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from dros.config_objects import CollectdConfig, ConfigObject, load_config_objects
from dros.settings import DrosSettings

TIMESPANS: dict[str, tuple[str, int]] = {
    "10min": ("10min", 10 * 60),
    "30min": ("30min", 30 * 60),
    "1h": ("1h", 60 * 60),
    "4h": ("4h", 4 * 60 * 60),
    "12h": ("12h", 12 * 60 * 60),
    "1d": ("1d", 24 * 60 * 60),
    "1w": ("1w", 7 * 24 * 60 * 60),
    "1m": ("1m", 30 * 24 * 60 * 60),
}
RrdRunner = Callable[[list[str]], str]


@dataclass(frozen=True)
class MetricSource:
    path: Path
    ds: str
    fallback_index: int = 0
    scale: float = 1.0


@dataclass(frozen=True)
class RrdFileSet:
    root: Path
    bandwidth: dict[str, Path]
    ping_latency: dict[str, Path]
    ping_loss: dict[str, Path]
    configured_ping_hosts: list[str]
    metrics: dict[str, dict[str, dict[str, list[MetricSource]]]]


METRIC_KINDS = (
    "cpu",
    "memory",
    "load",
    "disk",
    "df",
    "conntrack",
    "contextswitch",
    "irq",
)


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
        "metrics": {
            kind: [
                {"name": target, "series": sorted(series, key=_metric_label_sort_key)}
                for target, series in sorted(targets.items(), key=lambda item: _metric_target_sort_key(kind, item[0]))
            ]
            for kind, targets in sorted(files.metrics.items())
        },
    }


def collect_bandwidth_series(
    settings: DrosSettings,
    *,
    target: str,
    timespan: str,
    runner: RrdRunner | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    seconds = _timespan_seconds(timespan)
    start, end = _series_window(seconds, now)
    files = _rrd_files(settings)
    rrd_path = files.bandwidth.get(target)
    if rrd_path is None:
        return {
            "target": target,
            "timespan": timespan,
            "unit": "bit/s",
            "startTimestamp": start,
            "endTimestamp": end,
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
        "startTimestamp": start,
        "endTimestamp": end,
        "points": points,
    }


def collect_ping_series(
    settings: DrosSettings,
    *,
    target: str,
    timespan: str,
    runner: RrdRunner | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    seconds = _timespan_seconds(timespan)
    start, end = _series_window(seconds, now)
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
        "startTimestamp": start,
        "endTimestamp": end,
        "points": points,
    }


def collect_metric_series(
    settings: DrosSettings,
    *,
    kind: str,
    target: str,
    timespan: str,
    runner: RrdRunner | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    if kind not in METRIC_KINDS:
        raise ValueError(f"unsupported metric kind: {kind}")
    seconds = _timespan_seconds(timespan)
    start, end = _series_window(seconds, now)
    files = _rrd_files(settings)
    series_sources = files.metrics.get(kind, {}).get(target, {})
    labels = sorted(series_sources, key=_metric_label_sort_key)
    series_values = {
        label: _combined_metric_source_rows(sources, seconds, runner)
        for label, sources in series_sources.items()
    }
    timestamps = sorted({timestamp for values in series_values.values() for timestamp in values})
    return {
        "kind": kind,
        "target": target,
        "timespan": timespan,
        "unit": _metric_unit(kind, series_sources),
        "labels": labels,
        "startTimestamp": start,
        "endTimestamp": end,
        "points": [
            {
                "timestamp": timestamp,
                "values": {
                    label: series_values.get(label, {}).get(timestamp)
                    for label in labels
                },
            }
            for timestamp in timestamps
        ],
    }


def _rrd_files(settings: DrosSettings) -> RrdFileSet:
    config = _collectd_config(settings)
    root = _target_path(settings, Path(config.rrd_dir))
    bandwidth: dict[str, Path] = {}
    ping_latency: dict[str, Path] = {}
    ping_loss: dict[str, Path] = {}
    metrics: dict[str, dict[str, dict[str, list[MetricSource]]]] = {}
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
        _scan_collectd_metrics(root, metrics)
    return RrdFileSet(
        root=root,
        bandwidth=bandwidth,
        ping_latency=ping_latency,
        ping_loss=ping_loss,
        configured_ping_hosts=list(config.plugins.ping.hosts),
        metrics=metrics,
    )


def _scan_collectd_metrics(
    root: Path,
    metrics: dict[str, dict[str, dict[str, list[MetricSource]]]],
) -> None:
    for path in _iter_rrd_paths(root, "cpu-*/cpu-*.rrd"):
        cpu = path.parent.name.removeprefix("cpu-")
        state = path.stem.removeprefix("cpu-")
        source = MetricSource(path=path, ds="value")
        _add_metric(metrics, "cpu", cpu, state, source)
        _add_metric(metrics, "cpu", "all", state, source)

    memory_percent = list(_iter_rrd_paths(root, "memory/percent-*.rrd"))
    memory_paths = memory_percent or list(_iter_rrd_paths(root, "memory/memory-*.rrd"))
    for path in memory_paths:
        prefix = "percent-" if path in memory_percent else "memory-"
        _add_metric(
            metrics,
            "memory",
            "system",
            path.stem.removeprefix(prefix),
            MetricSource(path=path, ds="value"),
        )

    for path in _iter_rrd_paths(root, "load/load.rrd"):
        _add_metric(metrics, "load", "system", "1m", MetricSource(path=path, ds="shortterm", fallback_index=0))
        _add_metric(metrics, "load", "system", "5m", MetricSource(path=path, ds="midterm", fallback_index=1))
        _add_metric(metrics, "load", "system", "15m", MetricSource(path=path, ds="longterm", fallback_index=2))

    for path in _iter_rrd_paths(root, "disk-*/disk_octets.rrd"):
        target = path.parent.name.removeprefix("disk-")
        _add_metric(metrics, "disk", target, "read", MetricSource(path=path, ds="read", fallback_index=0))
        _add_metric(metrics, "disk", target, "write", MetricSource(path=path, ds="write", fallback_index=1))

    df_percent = list(_iter_rrd_paths(root, "df-*/percent_bytes-*.rrd"))
    df_percent_targets = {path.parent.name for path in df_percent}
    for path in df_percent:
        target = path.parent.name.removeprefix("df-")
        label = path.stem.removeprefix("percent_bytes-")
        _add_metric(metrics, "df", target, label, MetricSource(path=path, ds="value"))
    for path in _iter_rrd_paths(root, "df-*/df_complex-*.rrd"):
        if path.parent.name in df_percent_targets:
            continue
        target = path.parent.name.removeprefix("df-")
        label = path.stem.removeprefix("df_complex-")
        _add_metric(metrics, "df", target, label, MetricSource(path=path, ds="value"))

    for path in _iter_rrd_paths(root, "conntrack/conntrack.rrd"):
        _add_metric(metrics, "conntrack", "system", "conntrack", MetricSource(path=path, ds="value"))

    for path in _iter_rrd_paths(root, "contextswitch/contextswitch.rrd"):
        _add_metric(metrics, "contextswitch", "system", "contextswitch", MetricSource(path=path, ds="value"))

    for path in _iter_rrd_paths(root, "irq/irq-*.rrd"):
        target = path.stem.removeprefix("irq-")
        _add_metric(metrics, "irq", target, "irq", MetricSource(path=path, ds="value"))


def _iter_rrd_paths(root: Path, pattern: str) -> list[Path]:
    paths = [*root.glob(pattern), *root.glob(f"*/{pattern}")]
    return sorted(set(paths))


def _add_metric(
    metrics: dict[str, dict[str, dict[str, list[MetricSource]]]],
    kind: str,
    target: str,
    label: str,
    source: MetricSource,
) -> None:
    metrics.setdefault(kind, {}).setdefault(target, {}).setdefault(label, []).append(source)


def _metric_target_sort_key(kind: str, target: str) -> tuple[int, str]:
    if kind == "cpu" and target == "all":
        return (0, target)
    return (1, target)


def _metric_label_sort_key(label: str) -> tuple[int, str]:
    order = {
        "1m": 0,
        "5m": 1,
        "15m": 2,
        "used": 10,
        "free": 11,
        "cached": 12,
        "buffered": 13,
        "read": 20,
        "write": 21,
        "user": 30,
        "system": 31,
        "idle": 32,
        "wait": 33,
    }
    return (order.get(label, 100), label)


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
    if timespan in TIMESPANS:
        return TIMESPANS[timespan][1]
    match = re.fullmatch(r"([1-9][0-9]*)(min|h|d|w|m|y)", timespan.strip())
    if match is None:
        raise ValueError(f"unsupported timespan: {timespan}")
    amount = int(match.group(1))
    unit = match.group(2)
    multipliers = {
        "min": 60,
        "h": 60 * 60,
        "d": 24 * 60 * 60,
        "w": 7 * 24 * 60 * 60,
        "m": 30 * 24 * 60 * 60,
        "y": 365 * 24 * 60 * 60,
    }
    return amount * multipliers[unit]


def _series_window(seconds: int, now: int | None) -> tuple[int, int]:
    end = int(time.time()) if now is None else now
    return end - seconds, end


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


def _combined_metric_source_rows(
    sources: list[MetricSource],
    seconds: int,
    runner: RrdRunner | None,
) -> dict[int, float | None]:
    values_by_timestamp: dict[int, list[float]] = {}
    for source in sources:
        for row in _fetch_rrd(source.path, seconds, runner):
            value = _row_value(row, source.ds, fallback_index=source.fallback_index)
            if value is None:
                continue
            values_by_timestamp.setdefault(row["timestamp"], []).append(value * source.scale)
    return {
        timestamp: sum(values) / len(values)
        for timestamp, values in values_by_timestamp.items()
        if values
    }


def _metric_unit(
    kind: str,
    series_sources: dict[str, list[MetricSource]],
) -> str:
    if kind in {"cpu", "df"}:
        if _series_paths_have_prefix(series_sources, "percent"):
            return "%"
        return "B" if kind == "df" else "%"
    if kind == "memory":
        return "%" if _series_paths_have_prefix(series_sources, "percent") else "B"
    if kind == "disk":
        return "B/s"
    if kind in {"contextswitch", "irq"}:
        return "/s"
    return ""


def _series_paths_have_prefix(
    series_sources: dict[str, list[MetricSource]],
    prefix: str,
) -> bool:
    for sources in series_sources.values():
        for source in sources:
            if source.path.stem.startswith(prefix):
                return True
    return False
