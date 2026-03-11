from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import AppConfig
from .deployment_state import (
    deployment_rendered_dir,
    latest_deployment_record,
    latest_snapshot_metadata,
    target_snapshots_dir,
    write_deployment_record,
)
from .errors import ValidationError
from .operation_ids import new_operation_id, utc_now_iso
from .primitive_runner import run_primitive
from .registry import get_target
from .target_resolution import canonical_cli_command, resolve_target_identifier


def _primitive_config_payload(config: AppConfig) -> dict[str, object]:
    return {
        "config_path": str(config.config_path),
        "policy_path": str(config.policy_path),
        "state_root": str(config.state_root),
        "runtime_artifacts_root": str(config.runtime_artifacts_root),
        "internal_host": config.internal_host,
        "internal_port": config.internal_port,
    }


def _log_tail(config: AppConfig, record) -> str:
    tail_result = run_primitive(
        config,
        "tail_target_logs",
        {"target": record.id, "container_names": record.container_names, "tail_lines": 50},
    )
    return str((tail_result.get("details") or {}).get("log_tail") or "")


def _latest_validator_result(config: AppConfig, target: str) -> dict[str, Any] | None:
    latest = latest_deployment_record(config, target, record_type="deployment")
    if latest and "rollback_validator_result" in latest:
        return latest["rollback_validator_result"]
    if latest and "validator_result" in latest:
        return latest["validator_result"]
    rollback = latest_deployment_record(config, target, record_type="rollback")
    if rollback and "validator_result" in rollback:
        return rollback["validator_result"]
    return None


def _runtime_root_payload(record, render_details: dict[str, Any]) -> dict[str, str]:
    rendered_runtime_root_dir = str(render_details.get("rendered_runtime_root_dir") or "")
    if record.target_class != "runtime" or not rendered_runtime_root_dir:
        return {}
    return {
        "runtime_root": str(record.runtime_root or ""),
        "runtime_root_source_dir": rendered_runtime_root_dir,
        "gateway_port": str(render_details.get("gateway_port") or ""),
    }


def _shared_network_payload(record, render_details: dict[str, Any]) -> dict[str, str]:
    network_name = str(render_details.get("internal_network_name") or "")
    if not network_name and record.target_class in {"runtime", "shared_service"}:
        network_name = "moltbox_moltbox_internal"
    return {"internal_network_name": network_name} if network_name else {}


def _tools_config_payload(config: AppConfig, record, render_details: dict[str, Any]) -> dict[str, str]:
    rendered_config_path = str(render_details.get("rendered_config_path") or "")
    if record.id != "tools" or not rendered_config_path:
        return {}
    return {
        "rendered_config_path": rendered_config_path,
        "control_plane_config_destination": str(config.layout.policy_path),
    }


def _render_requires_build(render_dir: str) -> bool:
    return (Path(render_dir) / "Dockerfile").exists()


def render_assets(config: AppConfig, target: str, profile: str | None = None) -> dict[str, Any]:
    record = get_target(config, target)
    effective_profile = profile or record.profile
    payload = _primitive_config_payload(config)
    payload.update({"target": record.id, "profile": effective_profile})
    result = run_primitive(config, "render_assets", payload)
    return {
        "ok": bool(result.get("ok")),
        "status": "success" if result.get("ok") else "failure",
        "command": canonical_cli_command(record.id, "deploy"),
        "target": record.id,
        "profile": effective_profile,
        "exit_code": int(result.get("exit_code", 0 if result.get("ok") else 1)),
        "stdout": str(result.get("stdout") or ""),
        "stderr": str(result.get("stderr") or ""),
        "timestamp": utc_now_iso(),
        "duration_ms": 0,
        "render": result.get("details", {}),
    }


def deploy_target(config: AppConfig, target: str) -> dict[str, Any]:
    record = get_target(config, target)
    deployment_id = new_operation_id(record.id)
    rendered = run_primitive(
        config,
        "render_assets",
        {**_primitive_config_payload(config), "target": record.id, "profile": record.profile},
    )
    if not rendered.get("ok"):
        failure = {
            "ok": False,
            "record_type": "deployment",
            "command": canonical_cli_command(record.id, "deploy"),
            "deployment_id": deployment_id,
            "target": record.id,
            "profile": record.profile,
            "timestamp": utc_now_iso(),
            "status": "failure",
            "exit_code": 1,
            "stdout": str(rendered.get("stdout") or ""),
            "stderr": str(rendered.get("stderr") or ""),
            "previous_container_ids": [],
            "snapshot_id": None,
            "new_container_ids": [],
            "deployment_status": "render_failed",
            "rollback_performed": False,
            "validator_result": None,
            "render_manifest_path": None,
            "log_tail": "",
            "error_type": "render_failure",
            "error_message": "failed to render deployment assets",
            "recovery_message": f"inspect deployment assets for target '{record.id}' and rerun the command",
        }
        write_deployment_record(config, record.id, deployment_id, failure)
        return failure

    render_details = rendered.get("details") or {}
    render_dir = str(render_details.get("output_dir") or deployment_rendered_dir(config, record.id, record.profile))
    render_manifest_path = str(render_details.get("render_manifest_path") or "")
    inspect = run_primitive(config, "inspect_target", {"target": record.id, "container_names": record.container_names})
    state = ((inspect.get("details") or {}).get("container_state") or {}).get("state")
    previous_container_ids = list((inspect.get("details") or {}).get("container_ids") or [])
    snapshot_id: str | None = None
    if state == "running":
        snapshot_id = new_operation_id(record.id)
        snapshot = run_primitive(
            config,
            "snapshot_target",
            {
                "target": record.id,
                "profile": record.profile,
                "snapshot_id": snapshot_id,
                "snapshot_dir": str(target_snapshots_dir(config, record.id) / snapshot_id),
                "render_dir": render_dir,
                "container_names": record.container_names,
                "source_deployment_id": deployment_id,
            },
        )
        if not snapshot.get("ok"):
            failure = {
                "ok": False,
                "record_type": "deployment",
                "command": canonical_cli_command(record.id, "deploy"),
                "deployment_id": deployment_id,
                "target": record.id,
                "profile": record.profile,
                "timestamp": utc_now_iso(),
                "status": "failure",
                "exit_code": 1,
                "stdout": str(snapshot.get("stdout") or ""),
                "stderr": str(snapshot.get("stderr") or ""),
                "previous_container_ids": previous_container_ids,
                "snapshot_id": snapshot_id,
                "new_container_ids": [],
                "deployment_status": "snapshot_failed",
                "rollback_performed": False,
                "validator_result": None,
                "render_manifest_path": render_manifest_path,
                "log_tail": "",
                "error_type": "snapshot_failure",
                "error_message": "failed to capture a target snapshot before deployment",
                "recovery_message": f"resolve the snapshot failure for target '{record.id}' and rerun the command",
            }
            write_deployment_record(config, record.id, deployment_id, failure)
            return failure

    deploy_result = run_primitive(
        config,
        "deploy_target",
        {
            "target": record.id,
            "render_dir": render_dir,
            "compose_project": record.compose_project,
            "container_names": record.container_names,
            "remove_orphans": record.target_class != "shared_service",
            "replace_existing_containers": True,
            "force_recreate": record.target_class == "tools",
            **_runtime_root_payload(record, render_details),
            **_shared_network_payload(record, render_details),
            **_tools_config_payload(config, record, render_details),
            "build_images": _render_requires_build(render_dir),
        },
    )
    validate_result = run_primitive(
        config,
        "validate_target",
        {
            "target": record.id,
            "validator_key": record.validator_key,
            "container_names": record.container_names,
        },
    )
    rollback_performed = False
    rollback_result: dict[str, Any] | None = None
    rollback_validator_result: dict[str, Any] | None = None
    if snapshot_id and (not deploy_result.get("ok") or not validate_result.get("ok")):
        rollback_performed = True
        rollback_result = run_primitive(
            config,
            "restore_target_snapshot",
            {
                "target": record.id,
                "snapshot_id": snapshot_id,
                "snapshot_dir": str(target_snapshots_dir(config, record.id) / snapshot_id),
                "compose_project": record.compose_project,
                "container_names": record.container_names,
                "replace_existing_containers": True,
                "force_recreate": True,
                **_shared_network_payload(record, render_details),
            },
        )
        if rollback_result.get("ok"):
            rollback_validate_result = run_primitive(
                config,
                "validate_target",
                {
                    "target": record.id,
                    "validator_key": record.validator_key,
                    "container_names": record.container_names,
                },
            )
            rollback_validator_result = rollback_validate_result.get("details")
    log_tail = _log_tail(config, record)
    ok = bool(deploy_result.get("ok") and validate_result.get("ok"))
    deployment_validator_result = validate_result.get("details")
    payload = {
        "ok": ok,
        "record_type": "deployment",
        "command": canonical_cli_command(record.id, "deploy"),
        "deployment_id": deployment_id,
        "target": record.id,
        "profile": record.profile,
        "timestamp": utc_now_iso(),
        "status": "success" if ok else "failure",
        "exit_code": 0 if ok else 1,
        "stdout": str(deploy_result.get("stdout") or ""),
        "stderr": str(deploy_result.get("stderr") or ""),
        "previous_container_ids": previous_container_ids,
        "snapshot_id": snapshot_id,
        "new_container_ids": list((deploy_result.get("details") or {}).get("new_container_ids") or []),
        "deployment_status": "success" if ok else "failed",
        "rollback_performed": rollback_performed,
        "validator_result": rollback_validator_result or deployment_validator_result,
        "deployment_validator_result": deployment_validator_result,
        "render_manifest_path": render_manifest_path,
        "log_tail": log_tail,
    }
    if not ok:
        payload["error_type"] = "deployment_failed"
        payload["error_message"] = "deployment did not complete successfully"
        payload["recovery_message"] = f"inspect deployment logs for target '{record.id}' and retry after resolving the failure"
        if rollback_result is not None:
            payload["rollback_result"] = rollback_result
        if rollback_validator_result is not None:
            payload["rollback_validator_result"] = rollback_validator_result
    write_deployment_record(config, record.id, deployment_id, payload)
    return payload


def rollback_target(config: AppConfig, target: str) -> dict[str, Any]:
    record = get_target(config, target)
    rollback_id = new_operation_id(record.id)
    snapshot = latest_snapshot_metadata(config, record.id)
    if snapshot is None:
        payload = {
            "ok": False,
            "record_type": "rollback",
            "command": canonical_cli_command(record.id, "rollback"),
            "rollback_id": rollback_id,
            "target": record.id,
            "timestamp": utc_now_iso(),
            "status": "failure",
            "exit_code": 1,
            "stdout": "",
            "stderr": "",
            "snapshot_id": None,
            "restored_container_ids": [],
            "rollback_status": "missing_snapshot",
            "validator_result": None,
            "log_tail": "",
            "error_type": "snapshot_not_found",
            "error_message": f"no snapshot was found for target '{record.id}'",
            "recovery_message": "deploy the target successfully at least once before running rollback",
        }
        write_deployment_record(config, record.id, rollback_id, payload)
        return payload

    snapshot_id = str(snapshot["snapshot_id"])
    restore_result = run_primitive(
        config,
        "restore_target_snapshot",
        {
            "target": record.id,
            "snapshot_id": snapshot_id,
            "snapshot_dir": str(target_snapshots_dir(config, record.id) / snapshot_id),
            "compose_project": record.compose_project,
            "container_names": record.container_names,
            "replace_existing_containers": True,
            "force_recreate": True,
            **_shared_network_payload(record, {"internal_network_name": "moltbox_moltbox_internal"}),
        },
    )
    validate_result = run_primitive(
        config,
        "validate_target",
        {
            "target": record.id,
            "validator_key": record.validator_key,
            "container_names": record.container_names,
        },
    )
    log_tail = _log_tail(config, record)
    ok = bool(restore_result.get("ok") and validate_result.get("ok"))
    payload = {
        "ok": ok,
        "record_type": "rollback",
        "command": canonical_cli_command(record.id, "rollback"),
        "rollback_id": rollback_id,
        "target": record.id,
        "timestamp": utc_now_iso(),
        "status": "success" if ok else "failure",
        "exit_code": 0 if ok else 1,
        "stdout": str(restore_result.get("stdout") or ""),
        "stderr": str(restore_result.get("stderr") or ""),
        "snapshot_id": snapshot_id,
        "restored_container_ids": list((restore_result.get("details") or {}).get("restored_container_ids") or []),
        "rollback_status": "success" if ok else "failed",
        "validator_result": validate_result.get("details"),
        "log_tail": log_tail,
    }
    if not ok:
        payload["error_type"] = "rollback_failed"
        payload["error_message"] = "rollback did not complete successfully"
        payload["recovery_message"] = f"inspect the rollback record for target '{record.id}' and resolve the restore failure"
    write_deployment_record(config, record.id, rollback_id, payload)
    return payload


def build_target_status(config: AppConfig, requested_target: str) -> dict[str, Any]:
    record = get_target(config, requested_target)
    inspect = run_primitive(config, "inspect_target", {"target": record.id, "container_names": record.container_names})
    latest_deploy = latest_deployment_record(config, record.id, record_type="deployment")
    snapshot = latest_snapshot_metadata(config, record.id)
    return {
        "ok": bool(inspect.get("ok")),
        "status": "success" if inspect.get("ok") else "failure",
        "requested_target": requested_target,
        "resolved_target": resolve_target_identifier(requested_target),
        "target": record.as_dict(),
        "container_state": (inspect.get("details") or {}).get("container_state"),
        "last_deployment_id": (latest_deploy or {}).get("deployment_id"),
        "last_snapshot_id": (snapshot or {}).get("snapshot_id"),
        "validator_result": _latest_validator_result(config, record.id),
        "log_tail": _log_tail(config, record),
    }


def runtime_lifecycle(config: AppConfig, env: str, action: str) -> dict[str, Any]:
    if env not in {"dev", "test", "prod"}:
        raise ValidationError(
            f"unsupported runtime environment '{env}'",
            "use one of dev, test, or prod and rerun the command",
            environment=env,
        )
    record = get_target(config, env)
    render_result = render_assets(config, env)
    if not render_result.get("ok"):
        return render_result
    render_details = render_result.get("render") or {}
    primitive_name = {"start": "start_runtime", "stop": "stop_runtime", "restart": "restart_runtime"}[action]
    result = run_primitive(
        config,
        primitive_name,
        {
            "target": record.id,
            "render_dir": str(render_details.get("output_dir") or deployment_rendered_dir(config, record.id, record.profile)),
            "compose_project": record.compose_project,
            "container_names": record.container_names,
            **(_runtime_root_payload(record, render_details) if action in {"start", "restart"} else {}),
            **_shared_network_payload(record, render_details),
        },
    )
    return {
        "ok": bool(result.get("ok")),
        "status": "success" if result.get("ok") else "failure",
        "command": canonical_cli_command(record.id, action),
        "target": record.id,
        "exit_code": int(result.get("exit_code", 0 if result.get("ok") else 1)),
        "stdout": str(result.get("stdout") or ""),
        "stderr": str(result.get("stderr") or ""),
        "timestamp": utc_now_iso(),
        "duration_ms": 0,
        "log_tail": _log_tail(config, record),
    }


def runtime_chat(config: AppConfig, env: str, message: str | None, timeout_seconds: int = 30) -> dict[str, Any]:
    if env not in {"dev", "test", "prod"}:
        raise ValidationError(
            f"unsupported runtime environment '{env}'",
            "use one of dev, test, or prod and rerun the command",
            environment=env,
        )
    if not isinstance(message, str) or not message.strip():
        raise ValidationError(
            "runtime chat requires a prompt",
            "pass --message with a non-empty prompt and rerun the command",
            environment=env,
        )
    if timeout_seconds <= 0:
        raise ValidationError(
            "runtime chat timeout must be greater than zero",
            "pass --timeout-seconds with a positive integer and rerun the command",
            environment=env,
            timeout_seconds=timeout_seconds,
        )
    record = get_target(config, env)
    runtime_root = Path(record.runtime_root) if record.runtime_root else None
    result = run_primitive(
        config,
        "runtime_chat",
        {
            "target": record.id,
            "container_names": record.container_names,
            "message": message.strip(),
            "timeout_seconds": timeout_seconds,
            "runtime_root": str(runtime_root) if runtime_root is not None else "",
        },
    )
    details = result.get("details") or {}
    return {
        "ok": bool(result.get("ok")),
        "status": "success" if result.get("ok") else "failure",
        "command": canonical_cli_command(record.id, "chat"),
        "target": record.id,
        "exit_code": int(result.get("exit_code", 0 if result.get("ok") else 1)),
        "stdout": str(result.get("stdout") or ""),
        "stderr": str(result.get("stderr") or ""),
        "timestamp": utc_now_iso(),
        "duration_ms": 0,
        "log_tail": _log_tail(config, record),
        "chat": details,
    }


def host_lifecycle(config: AppConfig, target: str, action: str) -> dict[str, Any]:
    record = get_target(config, target)
    if record.target_class != "shared_service":
        raise ValidationError(
            f"unsupported host lifecycle target '{record.id}'",
            "use a shared host service target and rerun the command",
            target=record.id,
        )
    primitive_name = {"start": "start_target", "stop": "stop_target", "restart": "restart_target"}[action]
    result = run_primitive(
        config,
        primitive_name,
        {
            "target": record.id,
            "container_names": record.container_names,
        },
    )
    return {
        "ok": bool(result.get("ok")),
        "status": "success" if result.get("ok") else "failure",
        "command": canonical_cli_command(record.id, action),
        "target": record.id,
        "exit_code": int(result.get("exit_code", 0 if result.get("ok") else 1)),
        "stdout": str(result.get("stdout") or ""),
        "stderr": str(result.get("stderr") or ""),
        "timestamp": utc_now_iso(),
        "duration_ms": 0,
        "log_tail": _log_tail(config, record),
    }


def read_target_logs(config: AppConfig, requested_target: str, tail_lines: int = 50) -> dict[str, Any]:
    record = get_target(config, requested_target)
    result = run_primitive(
        config,
        "tail_target_logs",
        {"target": record.id, "container_names": record.container_names, "tail_lines": tail_lines},
    )
    return {
        "ok": bool(result.get("ok")),
        "status": "success" if result.get("ok") else "failure",
        "command": canonical_cli_command(record.id, "logs"),
        "target": record.id,
        "exit_code": int(result.get("exit_code", 0 if result.get("ok") else 1)),
        "stdout": str(result.get("stdout") or ""),
        "stderr": str(result.get("stderr") or ""),
        "timestamp": utc_now_iso(),
        "duration_ms": 0,
        "log_tail": str((result.get("details") or {}).get("log_tail") or ""),
    }
