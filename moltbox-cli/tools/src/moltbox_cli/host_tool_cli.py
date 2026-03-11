from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from .deployment_assets import render_target
from .jsonio import emit_json, read_json_file, write_json_file
from .operation_ids import utc_now_iso
from .runtime_config import seed_runtime_root_config


def _result(
    operation: str,
    ok: bool,
    *,
    details: dict[str, Any] | None = None,
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "operation": operation,
        "started_at": utc_now_iso(),
        "finished_at": utc_now_iso(),
        "details": details or {},
        "warnings": warnings or [],
        "errors": errors or [],
    }


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _container_inspect(container_name: str) -> dict[str, Any] | None:
    completed = _run_command(["docker", "inspect", container_name])
    if completed.returncode != 0:
        return None
    payload = json.loads(completed.stdout)
    if not isinstance(payload, list) or not payload:
        return None
    return payload[0]


def _container_details(container_names: list[str]) -> list[dict[str, Any]]:
    containers: list[dict[str, Any]] = []
    for name in container_names:
        inspected = _container_inspect(name)
        if inspected is None:
            containers.append(
                {
                    "name": name,
                    "present": False,
                    "container_id": None,
                    "state": "missing",
                    "health": None,
                    "image": None,
                    "mounts": [],
                }
            )
            continue
        state = inspected.get("State", {})
        containers.append(
            {
                "name": name,
                "present": True,
                "container_id": inspected.get("Id"),
                "state": state.get("Status", "unknown"),
                "health": ((state.get("Health") or {}).get("Status")),
                "image": (inspected.get("Config") or {}).get("Image"),
                "mounts": inspected.get("Mounts") or [],
            }
        )
    return containers


def _validation_errors(containers: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for container in containers:
        if not container["present"]:
            errors.append(f"container '{container['name']}' is missing")
            continue
        if container["state"] != "running":
            errors.append(f"container '{container['name']}' is not running")
        health = container["health"]
        if health and health != "healthy":
            errors.append(f"container '{container['name']}' health is '{health}'")
    return errors


def _aggregate_state(containers: list[dict[str, Any]]) -> str:
    if not containers or all(not item["present"] for item in containers):
        return "not_found"
    states = {str(item["state"]) for item in containers if item["present"]}
    if states == {"running"}:
        return "running"
    if "running" in states:
        return "partial"
    if states:
        return sorted(states)[0]
    return "unknown"


def _compose_command(render_dir: Path, compose_project: str, args: list[str]) -> list[str]:
    command = ["docker", "compose", "-p", compose_project, "-f", str(render_dir / "compose.yml")]
    env_file = render_dir / ".env"
    if env_file.exists():
        command.extend(["--env-file", str(env_file)])
    command.extend(args)
    return command


def _compose_environment() -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "OPENCLAW_IMAGE",
        "OPENCLAW_PORT",
        "MOLTBOX_TOOLS_IMAGE",
        "MOLTBOX_TOOLS_PORT",
        "MOLTBOX_SSL_IMAGE",
        "MOLTBOX_SSL_HTTP_PORT",
        "MOLTBOX_SSL_HTTPS_PORT",
        "OLLAMA_IMAGE",
        "OLLAMA_BASE_IMAGE",
        "OPENSEARCH_IMAGE",
        "OPENSEARCH_BASE_IMAGE",
        "OPENSEARCH_JAVA_OPTS",
        "DISCOVERY_TYPE",
        "PLUGINS_SECURITY_DISABLED",
    ):
        env.pop(key, None)
    return env


def _run_compose_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, env=_compose_environment())


def _ensure_docker_network(network_name: str) -> None:
    if not network_name:
        return
    inspect_completed = _run_command(["docker", "network", "inspect", network_name])
    if inspect_completed.returncode == 0:
        return
    create_completed = _run_command(["docker", "network", "create", network_name])
    if create_completed.returncode != 0:
        raise RuntimeError(create_completed.stderr.strip() or f"failed to create docker network '{network_name}'")


def _copy_tree(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())


def _copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())


def _sync_runtime_root(payload: dict[str, Any]) -> None:
    source_raw = str(payload.get("runtime_root_source_dir") or "")
    runtime_root_raw = str(payload.get("runtime_root") or "")
    if not source_raw or not runtime_root_raw:
        return
    source_dir = Path(source_raw)
    runtime_root = Path(runtime_root_raw)
    if not source_dir.exists():
        raise FileNotFoundError(f"rendered runtime root '{source_dir}' was not found")
    seed_runtime_root_config(
        source_dir,
        str(payload.get("gateway_port") or ""),
        command_runner=_run_command,
        existing_runtime_root_dir=runtime_root,
    )
    _copy_tree(source_dir, runtime_root)


def _sync_tools_config(payload: dict[str, Any]) -> None:
    rendered_config_raw = str(payload.get("rendered_config_path") or "")
    destination_raw = str(payload.get("control_plane_config_destination") or "")
    if not rendered_config_raw or not destination_raw:
        return
    rendered_config_path = Path(rendered_config_raw)
    destination_path = Path(destination_raw)
    if not rendered_config_path.exists():
        raise FileNotFoundError(f"rendered tools config '{rendered_config_path}' was not found")
    _copy_file(rendered_config_path, destination_path)


def _remove_existing_containers(container_names: list[str]) -> list[str]:
    removed: list[str] = []
    for name in container_names:
        if _container_inspect(name) is None:
            continue
        completed = _run_command(["docker", "rm", "-f", name])
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or f"failed to remove existing container '{name}'")
        removed.append(name)
    return removed


def _build_inline_config(payload: dict[str, Any]):  # noqa: ANN202
    from .config import AppConfig
    from .layout import build_host_layout

    return AppConfig(
        config_path=Path(payload["config_path"]),
        policy_path=Path(payload.get("policy_path") or (Path(payload["state_root"]) / "tools" / "control-plane-policy.yaml")),
        state_root=Path(payload["state_root"]),
        runtime_artifacts_root=Path(payload["runtime_artifacts_root"]),
        internal_host=str(payload["internal_host"]),
        internal_port=int(payload["internal_port"]),
        cli_command=["moltbox"],
        layout=build_host_layout(
            root=Path(payload["state_root"]),
            runtime_artifacts_root=Path(payload["runtime_artifacts_root"]),
            config_path=Path(payload["config_path"]),
            policy_path=Path(payload.get("policy_path") or (Path(payload["state_root"]) / "tools" / "control-plane-policy.yaml")),
        ),
    )


def _render_assets(payload: dict[str, Any]) -> dict[str, Any]:
    details = render_target(_build_inline_config(payload), str(payload["target"]), payload.get("profile"))
    return _result("render_assets", True, details=details)


def _inspect_target(payload: dict[str, Any]) -> dict[str, Any]:
    if not _docker_available():
        return _result("inspect_target", False, errors=["docker_not_available"])
    containers = _container_details([str(item) for item in payload.get("container_names", [])])
    return _result(
        "inspect_target",
        True,
        details={
            "target": payload.get("target"),
            "container_state": {
                "state": _aggregate_state(containers),
                "containers": containers,
            },
            "container_ids": [item["container_id"] for item in containers if item["container_id"]],
        },
    )


def _tail_target_logs(payload: dict[str, Any]) -> dict[str, Any]:
    if not _docker_available():
        return _result("tail_target_logs", False, errors=["docker_not_available"])
    tail_lines = int(payload.get("tail_lines", 50))
    chunks: list[str] = []
    for name in [str(item) for item in payload.get("container_names", [])]:
        completed = _run_command(["docker", "logs", "--tail", str(tail_lines), name])
        combined = completed.stdout.strip()
        if completed.stderr.strip():
            combined = "\n".join([part for part in [combined, completed.stderr.strip()] if part])
        if combined:
            chunks.append(f"[{name}]\n{combined}")
    return _result("tail_target_logs", True, details={"log_tail": "\n\n".join(chunks)})


def _compose_lifecycle(operation: str, payload: dict[str, Any], compose_args: list[str]) -> dict[str, Any]:
    if not _docker_available():
        return _result(operation, False, errors=["docker_not_available"])
    if operation in {"start_runtime", "restart_runtime"}:
        _sync_runtime_root(payload)
    _ensure_docker_network(str(payload.get("internal_network_name") or ""))
    render_dir = Path(str(payload["render_dir"]))
    command = _compose_command(render_dir, str(payload["compose_project"]), compose_args)
    completed = _run_compose_command(command)
    ok = completed.returncode == 0
    return _result(
        operation,
        ok,
        details={
            "compose_command": command,
            "compose_stdout": completed.stdout.strip(),
            "compose_stderr": completed.stderr.strip(),
        },
        errors=[] if ok else [completed.stderr.strip() or f"{operation}_failed"],
    )


def _docker_target_lifecycle(operation: str, payload: dict[str, Any], docker_verb: str) -> dict[str, Any]:
    if not _docker_available():
        return _result(operation, False, errors=["docker_not_available"])
    container_names = [str(item) for item in payload.get("container_names", [])]
    if not container_names:
        return _result(operation, False, errors=["target_has_no_containers"])
    command = ["docker", docker_verb, *container_names]
    completed = _run_command(command)
    ok = completed.returncode == 0
    return _result(
        operation,
        ok,
        details={
            "docker_command": command,
            "docker_stdout": completed.stdout.strip(),
            "docker_stderr": completed.stderr.strip(),
        },
        errors=[] if ok else [completed.stderr.strip() or f"{operation}_failed"],
    )


def _deploy_target(payload: dict[str, Any]) -> dict[str, Any]:
    if not _docker_available():
        return _result("deploy_target", False, errors=["docker_not_available"])
    _sync_runtime_root(payload)
    _sync_tools_config(payload)
    _ensure_docker_network(str(payload.get("internal_network_name") or ""))
    removed_containers: list[str] = []
    if bool(payload.get("replace_existing_containers", False)):
        removed_containers = _remove_existing_containers([str(item) for item in payload.get("container_names", [])])
    render_dir = Path(str(payload["render_dir"]))
    compose_args = ["up", "-d"]
    if bool(payload.get("build_images", False)):
        compose_args.append("--build")
    if bool(payload.get("force_recreate", False)):
        compose_args.append("--force-recreate")
    if bool(payload.get("remove_orphans", True)):
        compose_args.append("--remove-orphans")
    command = _compose_command(render_dir, str(payload["compose_project"]), compose_args)
    completed = _run_compose_command(command)
    ok = completed.returncode == 0
    containers = _container_details([str(item) for item in payload.get("container_names", [])])
    return _result(
        "deploy_target",
        ok,
        details={
            "compose_command": command,
            "compose_stdout": completed.stdout.strip(),
            "compose_stderr": completed.stderr.strip(),
            "removed_existing_containers": removed_containers,
            "new_container_ids": [item["container_id"] for item in containers if item["container_id"]],
        },
        errors=[] if ok else [completed.stderr.strip() or "docker_compose_up_failed"],
    )


def _start_runtime(payload: dict[str, Any]) -> dict[str, Any]:
    return _compose_lifecycle("start_runtime", payload, ["start"])


def _start_target(payload: dict[str, Any]) -> dict[str, Any]:
    return _docker_target_lifecycle("start_target", payload, "start")


def _stop_target(payload: dict[str, Any]) -> dict[str, Any]:
    return _docker_target_lifecycle("stop_target", payload, "stop")


def _restart_target(payload: dict[str, Any]) -> dict[str, Any]:
    return _docker_target_lifecycle("restart_target", payload, "restart")


def _stop_runtime(payload: dict[str, Any]) -> dict[str, Any]:
    return _compose_lifecycle("stop_runtime", payload, ["stop"])


def _restart_runtime(payload: dict[str, Any]) -> dict[str, Any]:
    return _compose_lifecycle("restart_runtime", payload, ["restart"])


def _snapshot_target(payload: dict[str, Any]) -> dict[str, Any]:
    snapshot_dir = Path(str(payload["snapshot_dir"]))
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    render_dir = Path(str(payload["render_dir"]))
    rendered_copy = snapshot_dir / "rendered"
    _copy_tree(render_dir, rendered_copy)
    containers = _container_details([str(item) for item in payload.get("container_names", [])]) if _docker_available() else []
    metadata = {
        "snapshot_id": payload.get("snapshot_id"),
        "target": payload.get("target"),
        "profile": payload.get("profile"),
        "created_at": utc_now_iso(),
        "source_deployment_id": payload.get("source_deployment_id"),
        "container_names": [item["name"] for item in containers],
        "image_refs": [item["image"] for item in containers if item["image"]],
        "render_manifest_path": str(rendered_copy / "render-manifest.json"),
        "volume_refs": [
            {
                "container_name": item["name"],
                "mounts": item["mounts"],
            }
            for item in containers
        ],
    }
    write_json_file(snapshot_dir / "metadata.json", metadata)
    if containers:
        write_json_file(snapshot_dir / "containers.json", containers)
    return _result(
        "snapshot_target",
        True,
        details={
            "snapshot_id": payload.get("snapshot_id"),
            "snapshot_path": str(snapshot_dir),
            "image_refs": metadata["image_refs"],
            "volume_refs": metadata["volume_refs"],
        },
    )


def _restore_target_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    if not _docker_available():
        return _result("restore_target_snapshot", False, errors=["docker_not_available"])
    snapshot_dir = Path(str(payload["snapshot_dir"]))
    render_dir = snapshot_dir / "rendered"
    metadata = read_json_file(snapshot_dir / "metadata.json", default={}) or {}
    _ensure_docker_network(str(payload.get("internal_network_name") or ""))
    removed_containers: list[str] = []
    if bool(payload.get("replace_existing_containers", False)):
        removed_containers = _remove_existing_containers([str(item) for item in payload.get("container_names", [])])
    compose_args = ["up", "-d"]
    if bool(payload.get("force_recreate", True)):
        compose_args.append("--force-recreate")
    command = _compose_command(render_dir, str(payload["compose_project"]), compose_args)
    completed = _run_compose_command(command)
    ok = completed.returncode == 0
    containers = _container_details([str(item) for item in metadata.get("container_names", payload.get("container_names", []))])
    return _result(
        "restore_target_snapshot",
        ok,
        details={
            "snapshot_id": metadata.get("snapshot_id", payload.get("snapshot_id")),
            "snapshot_path": str(snapshot_dir),
            "restored_container_ids": [item["container_id"] for item in containers if item["container_id"]],
            "removed_existing_containers": removed_containers,
            "compose_command": command,
            "compose_stdout": completed.stdout.strip(),
            "compose_stderr": completed.stderr.strip(),
        },
        errors=[] if ok else [completed.stderr.strip() or "docker_compose_restore_failed"],
    )
def _validation_pending(containers: list[dict[str, Any]]) -> bool:
    for container in containers:
        if not container["present"]:
            return True
        if container["state"] in {"created", "restarting"}:
            return True
        if container["state"] == "running" and container["health"] == "starting":
            return True
    return False


def _validate_target(payload: dict[str, Any]) -> dict[str, Any]:
    if not _docker_available():
        return _result("validate_target", False, errors=["docker_not_available"])
    container_names = [str(item) for item in payload.get("container_names", [])]
    timeout_seconds = max(
        int(payload.get("validation_timeout_seconds", os.environ.get("MOLTBOX_VALIDATE_TIMEOUT_SECONDS", 90))),
        0,
    )
    poll_interval_seconds = max(
        float(payload.get("validation_poll_interval_seconds", os.environ.get("MOLTBOX_VALIDATE_POLL_INTERVAL_SECONDS", 2))),
        0,
    )
    deadline = time.monotonic() + timeout_seconds
    containers = _container_details(container_names)
    errors = _validation_errors(containers)
    while errors and _validation_pending(containers) and time.monotonic() < deadline:
        if poll_interval_seconds:
            time.sleep(poll_interval_seconds)
        containers = _container_details(container_names)
        errors = _validation_errors(containers)
    return _result(
        "validate_target",
        not errors,
        details={
            "validator_key": payload.get("validator_key"),
            "containers": containers,
            "result": "pass" if not errors else "fail",
        },
        errors=errors,
    )


HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "render_assets": _render_assets,
    "inspect_target": _inspect_target,
    "tail_target_logs": _tail_target_logs,
    "deploy_target": _deploy_target,
    "start_target": _start_target,
    "stop_target": _stop_target,
    "restart_target": _restart_target,
    "start_runtime": _start_runtime,
    "stop_runtime": _stop_runtime,
    "restart_runtime": _restart_runtime,
    "snapshot_target": _snapshot_target,
    "restore_target_snapshot": _restore_target_snapshot,
    "validate_target": _validate_target,
}


def main(operation: str) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True)
    args = parser.parse_args()
    payload = json.loads(args.payload)
    result = HANDLERS[operation](payload)
    emit_json(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
