from __future__ import annotations

from pathlib import Path

from remram_dev_manager_control_pane.config import resolve_config
from remram_dev_manager_control_pane.registry_bootstrap import ensure_registry_bootstrap


class Args:
    config_path = None
    state_root = None
    runtime_artifacts_root = None
    internal_host = None
    internal_port = None
    cli_path = None


def test_registry_bootstrap_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("REMRAM_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("REMRAM_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    config = resolve_config(Args())
    first = ensure_registry_bootstrap(config)
    second = ensure_registry_bootstrap(config)
    assert [record.id for record in first] == [record.id for record in second]
    assert (tmp_path / ".remram" / "state" / "targets" / "control.json").exists()
    assert (tmp_path / "Moltbox" / "logs" / "control-plane").exists()
