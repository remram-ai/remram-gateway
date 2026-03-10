from __future__ import annotations

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
