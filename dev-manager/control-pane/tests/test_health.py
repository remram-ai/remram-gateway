from __future__ import annotations

from pathlib import Path

from remram_dev_manager_control_pane.config import resolve_config
from remram_dev_manager_control_pane.health import build_cli_health_payload


class Args:
    config_path = None
    state_root = None
    runtime_artifacts_root = None
    internal_host = None
    internal_port = None
    cli_path = None


def test_health_reports_down_when_serve_not_running(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REMRAM_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("REMRAM_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    config = resolve_config(Args())
    payload = build_cli_health_payload(config, "test-version")
    assert payload["serve_state"] == "down"
    assert payload["ok"] is False
    log_path = Path(payload["logs"][0]["path"].replace("~", str(Path.home())))
    assert log_path.name == "serve.log"
    assert log_path.parent.name == "control-plane"
    assert "remram serve" in payload["recovery_message"]
