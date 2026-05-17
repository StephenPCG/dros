from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from dros.locks import exclusive_lock


def test_exclusive_lock_rejects_parallel_process(tmp_path: Path) -> None:
    lock_path = tmp_path / "gw-manual.lock"

    with exclusive_lock(lock_path, blocking=False):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "from dros.locks import LockBusyError, exclusive_lock; "
                    f"path = {str(lock_path)!r}; "
                    "\ntry:\n"
                    "    with exclusive_lock(path, blocking=False):\n"
                    "        raise SystemExit(0)\n"
                    "except LockBusyError:\n"
                    "    raise SystemExit(7)\n"
                ),
            ],
            check=False,
        )

    assert result.returncode == 7
