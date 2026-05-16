from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path


def test_gw_help_command_runs() -> None:
    src_dir = Path(__file__).resolve().parents[1] / "src"
    result = subprocess.run(
        [sys.executable, "-m", "dros.cli.main", "help"],
        check=False,
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(src_dir)},
    )

    assert result.returncode == 0
    assert "DROS gateway management CLI" in result.stdout
    assert "update" in result.stdout
