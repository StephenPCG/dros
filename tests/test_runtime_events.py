from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import dros.events as events
from dros.locks import APPLY_LOCK_PATH, LockBusyError
from dros.settings import DrosPaths, DrosSettings


def _settings(tmp_path: Path) -> DrosSettings:
    return DrosSettings(
        sysRoot=tmp_path / "sysroot",
        paths=DrosPaths(
            configs=tmp_path / "configs",
            logs=tmp_path / "logs",
            run=tmp_path / "run",
        ),
    )


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_enqueue_event_is_logged_and_process_queue_coalesces_duplicates(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    handled: list[tuple[str, str | None]] = []

    def fake_process_event(
        _settings: DrosSettings,
        event: str,
        *,
        iface: str | None = None,
        **_kwargs: object,
    ) -> object:
        handled.append((event, iface))
        return object()

    monkeypatch.setattr(events, "process_event", fake_process_event)

    events.enqueue_event(settings, "route-refresh", "pppoe-wan")
    events.enqueue_event(settings, "route-refresh", "pppoe-wan")
    events.enqueue_event(settings, "ppp-up", "pppoe-wan")

    offset = events.process_event_queue(settings)

    assert offset > 0
    assert handled == [("route-refresh", "pppoe-wan"), ("ppp-up", "pppoe-wan")]
    log_records = _jsonl(settings.paths.logs / "gw-invocations.log")
    assert [item["kind"] for item in log_records] == [
        "event.enqueue",
        "event.enqueue",
        "event.enqueue",
        "event.process",
        "event.process",
        "event.process",
        "event.process",
    ]
    assert [item.get("phase") for item in log_records if item["kind"] == "event.process"] == [
        "start",
        "finish",
        "start",
        "finish",
    ]
    assert log_records[-1]["event"] == "ppp-up"


def test_process_event_queue_waits_when_apply_lock_is_busy(monkeypatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    events.enqueue_event(settings, "route-refresh", "pppoe-wan")

    @contextmanager
    def fake_exclusive_lock(path: Path, *, blocking: bool = True):
        if str(path).endswith(APPLY_LOCK_PATH):
            raise LockBusyError("apply lock busy")
        yield

    def fail_process_event(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("event processing must wait for the apply lock")

    monkeypatch.setattr(events, "exclusive_lock", fake_exclusive_lock)
    monkeypatch.setattr(events, "process_event", fail_process_event)

    assert events.process_event_queue(settings, offset=0) == 0
