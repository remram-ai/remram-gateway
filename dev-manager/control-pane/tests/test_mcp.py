from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from remram_dev_manager_control_pane.config import resolve_config
from remram_dev_manager_control_pane.mcp_adapter import invoke_cli_json


class Args:
    config_path = None
    state_root = None
    runtime_artifacts_root = None
    internal_host = None
    internal_port = None
    cli_path = "python"


def test_mcp_adapter_cli_wrapper_returns_health(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REMRAM_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("REMRAM_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    config = resolve_config(Args())
    config = replace(config, cli_command=["python", "-m", "remram_dev_manager_control_pane"])
    payload = invoke_cli_json(config, ["health"])
    assert payload["serve_state"] == "down"
    assert "logs" in payload


def test_mcp_adapter_preserves_resolved_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("REMRAM_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("REMRAM_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    config = resolve_config(Args())
    config = replace(
        config,
        cli_command=["python", "-m", "remram_dev_manager_control_pane"],
        internal_host="127.0.0.2",
        internal_port=8555,
    )

    recorded: dict[str, object] = {}

    class Completed:
        returncode = 0
        stderr = ""
        stdout = json.dumps({"ok": True, "status": "down"})

    def fake_run(command, capture_output, text, check):  # noqa: ANN001
        recorded["command"] = command
        return Completed()

    monkeypatch.setattr("remram_dev_manager_control_pane.mcp_adapter.subprocess.run", fake_run)

    payload = invoke_cli_json(config, ["health"])
    assert payload["ok"] is True
    command = recorded["command"]
    assert "--config-path" in command
    assert "--state-root" in command
    assert "--runtime-artifacts-root" in command
    assert "--internal-host" in command
    assert "--internal-port" in command
    assert "8555" in command
