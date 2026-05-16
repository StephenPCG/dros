from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_install_script_is_shell_syntax_clean_and_executable() -> None:
    script = ROOT / "install-dros.sh"

    assert os.access(script, os.X_OK)
    result = subprocess.run(["bash", "-n", str(script)], check=False, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr


def test_install_script_installs_profile_specific_units() -> None:
    script_text = (ROOT / "install-dros.sh").read_text()

    assert "--test" in script_text
    assert "--profile" in script_text
    assert "dros-daemon.service" in script_text
    assert "dros-web.service" in script_text
    assert "dros-daemon-test.service" in script_text
    assert "dros-web-test.service" in script_text
    assert "settings-test.yaml" in script_text
    assert "socketPath: $GATEWAY_DIR/test/run/drosd-test.sock" in script_text
    assert "port: 8766" in script_text
    assert "cleanup_legacy_test_profile_unit" in script_text
    assert "reset-failed" in script_text
    assert "wait_for_web_health" in script_text
    assert 'VENV_BIN="$SOURCE_DIR/.venv/bin"' in script_text
    assert "ExecStart=$VENV_BIN/drosd" in script_text
    assert "ExecStart=$VENV_BIN/dros-web" in script_text
    assert "settings-test.yaml" in script_text
    assert "systemctl enable" in script_text
    assert "systemctl restart" in script_text
