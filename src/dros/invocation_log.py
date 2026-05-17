from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from dros.locks import exclusive_lock
from dros.settings import DrosSettings

INVOCATION_LOG = "gw-invocations.log"


def append_invocation_log(
    settings: DrosSettings,
    *,
    kind: str,
    phase: str | None = None,
    argv: list[str] | None = None,
    event: str | None = None,
    iface: str | None = None,
    exit_code: int | None = None,
    duration_ms: int | None = None,
    message: str | None = None,
) -> None:
    try:
        path = settings.paths.logs / INVOCATION_LOG
        path.parent.mkdir(parents=True, exist_ok=True)
        record: dict[str, Any] = {
            "ts": time.time(),
            "kind": kind,
            "pid": os.getpid(),
            "ppid": os.getppid(),
            "uid": os.geteuid(),
        }
        try:
            record["cwd"] = os.getcwd()
        except OSError:
            pass
        if phase is not None:
            record["phase"] = phase
        if argv is not None:
            record["argv"] = [str(item) for item in argv]
        if event is not None:
            record["event"] = event
        if iface is not None:
            record["iface"] = iface
        if exit_code is not None:
            record["exitCode"] = exit_code
        if duration_ms is not None:
            record["durationMs"] = duration_ms
        if message is not None:
            record["message"] = message
        with exclusive_lock(_lock_path(path)):
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{json.dumps(record, sort_keys=True)}\n")
    except OSError:
        return


def _lock_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.lock")
