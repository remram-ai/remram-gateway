from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from moltbox_cli.config import resolve_config
from moltbox_cli.deployment_service import _latest_validator_result
from moltbox_cli.deployment_state import write_deployment_record


class Args:
    config_path = None
    policy_path = None
    state_root = None
    runtime_artifacts_root = None
    internal_host = None
    internal_port = None
    cli_path = None


def test_latest_validator_result_prefers_post_rollback_health(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    config = resolve_config(Args())

    write_deployment_record(
        config,
        "dev",
        "deploy-1",
        {
            "record_type": "deployment",
            "validator_result": {"result": "fail"},
            "rollback_validator_result": {"result": "pass"},
        },
    )

    assert _latest_validator_result(config, "dev") == {"result": "pass"}
