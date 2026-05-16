from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_test_gw_sync_keeps_git_metadata_and_web_dist() -> None:
    exclude_rules = (ROOT / ".rsync-test-gw-exclude").read_text(encoding="utf-8").splitlines()

    assert ".git/" not in exclude_rules
    assert "web/dist/" not in exclude_rules
    assert "/dist/" in exclude_rules


def test_web_dist_is_not_git_ignored() -> None:
    dist_dir = ROOT / "web" / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    probe = dist_dir / ".gitignore-probe"
    probe.write_text("probe\n", encoding="utf-8")

    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(probe.relative_to(ROOT))],
            cwd=ROOT,
            check=False,
        )
    finally:
        probe.unlink(missing_ok=True)

    assert result.returncode == 1
