from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from moltbox_cli.config import resolve_config
from moltbox_cli.mcp_adapter import dispatch_host_action, dispatch_runtime_action, dispatch_tools_action
from moltbox_cli.mcp_policy import allowed_runtime_verbs, load_mcp_policy


class Args:
    config_path = None
    policy_path = None
    state_root = None
    runtime_artifacts_root = None
    internal_host = None
    internal_port = None
    cli_path = None


def test_runtime_policy_defaults_match_dev_test_prod_model(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    config = resolve_config(Args())
    policy, _ = load_mcp_policy(config)

    assert {"deploy", "rollback", "restart"}.issubset(allowed_runtime_verbs(policy, "dev"))
    assert allowed_runtime_verbs(policy, "test") == {"deploy", "status", "logs", "inspect"}
    assert allowed_runtime_verbs(policy, "prod") == {"deploy", "inspect", "logs"}


def test_runtime_action_denies_prod_restart(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    config = resolve_config(Args())

    payload = dispatch_runtime_action(config, "prod", "restart")

    assert payload["ok"] is False
    assert payload["error_type"] == "mcp_policy_denied"
    assert payload["details"]["environment"] == "prod"


def test_runtime_action_allows_test_deploy(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    config = resolve_config(Args())

    monkeypatch.setattr(
        "moltbox_cli.mcp_adapter.invoke_cli_json",
        lambda config_arg, args: {"ok": True, "args": args},
    )

    payload = dispatch_runtime_action(config, "test", "deploy")

    assert payload["ok"] is True
    assert payload["args"] == ["runtime", "test", "deploy"]


def test_host_action_allows_read_only_status(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    config = resolve_config(Args())

    monkeypatch.setattr(
        "moltbox_cli.mcp_adapter.invoke_cli_json",
        lambda config_arg, args: {"ok": True, "args": args},
    )

    payload = dispatch_host_action(config, "ssl", "status")

    assert payload["ok"] is True
    assert payload["args"] == ["host", "ssl", "status"]


def test_tools_action_denies_update(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    config = resolve_config(Args())

    payload = dispatch_tools_action(config, "update")

    assert payload["ok"] is False
    assert payload["error_type"] == "mcp_policy_denied"
