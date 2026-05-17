from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class LockBusyError(RuntimeError):
    pass


APPLY_LOCK_PATH = "locks/gw-apply.lock"


@contextmanager
def exclusive_lock(path: str | Path, *, blocking: bool = True) -> Iterator[None]:
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    flags = fcntl.LOCK_EX
    if not blocking:
        flags |= fcntl.LOCK_NB
    try:
        try:
            fcntl.flock(handle.fileno(), flags)
        except BlockingIOError as exc:
            raise LockBusyError(f"lock is busy: {lock_path}") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\n")
        handle.flush()
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
