from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PACKAGE_ROOT / "src"


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    merged_env["PYTHONPATH"] = str(SRC_DIR) + os.pathsep + merged_env.get("PYTHONPATH", "")
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "remram_dev_manager_control_pane", *args],
        cwd=str(PACKAGE_ROOT),
        capture_output=True,
        text=True,
        check=False,
        env=merged_env,
    )


def test_version_returns_json() -> None:
    completed = run_cli("version")
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert "version" in payload


def test_unknown_target_returns_code_4(tmp_path: Path) -> None:
    env = {
        "REMRAM_STATE_ROOT": str(tmp_path / ".remram"),
        "REMRAM_RUNTIME_ROOT": str(tmp_path / "Moltbox"),
    }
    completed = run_cli("status", "--target", "missing", env=env)
    assert completed.returncode == 4
    payload = json.loads(completed.stdout)
    assert payload["error_type"] == "target_not_found"
