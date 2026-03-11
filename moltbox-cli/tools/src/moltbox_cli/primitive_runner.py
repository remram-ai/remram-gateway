from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import AppConfig
from .errors import MoltboxCliError
from .layout import build_repo_layout
from .operation_ids import utc_now_iso


@dataclass(frozen=True)
class PrimitiveDefinition:
    name: str
    category: str
    summary: str
    relative_source_path: str
    mutates_state: bool
    allowed_payload_keys: tuple[str, ...]

    def source_path(self) -> Path:
        return build_repo_layout().moltbox_cli_dir / self.relative_source_path


PRIMITIVES: dict[str, PrimitiveDefinition] = {
    "render_assets": PrimitiveDefinition(
        "render_assets",
        "deploy",
        "Render canonical deployment assets into a deterministic output directory.",
        "host/commands/deploy/render-assets.py",
        False,
        ("target", "profile", "config_path", "policy_path", "state_root", "runtime_artifacts_root", "internal_host", "internal_port"),
    ),
    "inspect_target": PrimitiveDefinition(
        "inspect_target",
        "shared",
        "Inspect the current container state for a deploy-managed target.",
        "host/commands/shared/inspect-target.py",
        False,
        ("target", "container_names"),
    ),
    "tail_target_logs": PrimitiveDefinition(
        "tail_target_logs",
        "shared",
        "Return a bounded container log tail for a deploy-managed target.",
        "host/commands/shared/tail-target-logs.py",
        False,
        ("target", "container_names", "tail_lines"),
    ),
    "deploy_target": PrimitiveDefinition(
        "deploy_target",
        "deploy",
        "Deploy a rendered Docker Compose target.",
        "host/commands/deploy/deploy-target.py",
        True,
        (
            "target",
            "render_dir",
            "compose_project",
            "container_names",
            "remove_orphans",
            "build_images",
            "replace_existing_containers",
            "force_recreate",
            "runtime_root",
            "runtime_root_source_dir",
            "gateway_port",
            "internal_network_name",
            "rendered_config_path",
            "control_plane_config_destination",
        ),
    ),
    "start_target": PrimitiveDefinition(
        "start_target",
        "stack",
        "Start an existing host-service target container.",
        "host/commands/stack/start-target.py",
        True,
        ("target", "container_names"),
    ),
    "stop_target": PrimitiveDefinition(
        "stop_target",
        "stack",
        "Stop an existing host-service target container.",
        "host/commands/stack/stop-target.py",
        True,
        ("target", "container_names"),
    ),
    "restart_target": PrimitiveDefinition(
        "restart_target",
        "stack",
        "Restart an existing host-service target container.",
        "host/commands/stack/restart-target.py",
        True,
        ("target", "container_names"),
    ),
    "start_runtime": PrimitiveDefinition(
        "start_runtime",
        "stack",
        "Start a rendered runtime target.",
        "runtime/commands/stack/start-runtime.py",
        True,
        (
            "target",
            "render_dir",
            "compose_project",
            "container_names",
            "runtime_root",
            "runtime_root_source_dir",
            "gateway_port",
            "internal_network_name",
        ),
    ),
    "stop_runtime": PrimitiveDefinition(
        "stop_runtime",
        "stack",
        "Stop a rendered runtime target.",
        "runtime/commands/stack/stop-runtime.py",
        True,
        ("target", "render_dir", "compose_project", "container_names", "runtime_root", "runtime_root_source_dir", "internal_network_name"),
    ),
    "restart_runtime": PrimitiveDefinition(
        "restart_runtime",
        "stack",
        "Restart a rendered runtime target.",
        "runtime/commands/stack/restart-runtime.py",
        True,
        (
            "target",
            "render_dir",
            "compose_project",
            "container_names",
            "runtime_root",
            "runtime_root_source_dir",
            "gateway_port",
            "internal_network_name",
        ),
    ),
    "snapshot_target": PrimitiveDefinition(
        "snapshot_target",
        "snapshot",
        "Create a target-scoped rollback snapshot.",
        "host/commands/snapshot/snapshot-target.py",
        True,
        ("target", "profile", "snapshot_id", "snapshot_dir", "render_dir", "container_names", "source_deployment_id"),
    ),
    "restore_target_snapshot": PrimitiveDefinition(
        "restore_target_snapshot",
        "snapshot",
        "Restore a target from its most recent snapshot.",
        "host/commands/snapshot/restore-target-snapshot.py",
        True,
        ("target", "snapshot_id", "snapshot_dir", "compose_project", "container_names", "replace_existing_containers", "force_recreate", "internal_network_name"),
    ),
    "validate_target": PrimitiveDefinition(
        "validate_target",
        "validate",
        "Run baseline container validation for a deploy-managed target.",
        "host/commands/validate/validate-target.py",
        False,
        ("target", "validator_key", "container_names", "validation_timeout_seconds", "validation_poll_interval_seconds"),
    ),
}


def _command_for_primitive(definition: PrimitiveDefinition) -> list[str]:
    return [sys.executable, str(definition.source_path())]


def run_primitive(config: AppConfig, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    definition = PRIMITIVES.get(name)
    if definition is None:
        raise MoltboxCliError(
            error_type="unknown_primitive",
            error_message=f"primitive '{name}' is not allowlisted",
            recovery_message="use a supported deployment primitive and rerun the command",
            details={"primitive": name},
        )
    invalid_keys = sorted(set(payload).difference(definition.allowed_payload_keys))
    if invalid_keys:
        raise MoltboxCliError(
            error_type="invalid_primitive_payload",
            error_message=f"primitive '{name}' received unsupported payload keys",
            recovery_message="fix the MoltBox CLI primitive payload and rerun the command",
            details={"primitive": name, "invalid_keys": invalid_keys},
        )
    command = _command_for_primitive(definition) + ["--payload", json.dumps(payload)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    payload_text = completed.stdout.strip()
    parsed = json.loads(payload_text) if payload_text else {}
    if not isinstance(parsed, dict):
        parsed = {"ok": False, "errors": ["primitive_returned_non_object_json"]}
    result = {
        "primitive": name,
        "category": definition.category,
        "started_at": utc_now_iso(),
        "finished_at": utc_now_iso(),
        "argv": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }
    result.update(parsed)
    if "ok" not in result:
        result["ok"] = completed.returncode == 0
    return result
