from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .commands.health import handle_health
from .commands.rollback import handle_rollback
from .commands.targets import handle_list_targets
from .component_resolution import ComponentSpec, ensure_component_command, resolve_component, try_resolve_component
from .config import AppConfig
from .deployment_service import (
    build_target_status,
    deploy_target,
    host_lifecycle,
    read_target_logs,
    rollback_target,
    runtime_chat,
    runtime_lifecycle,
)
from .errors import ValidationError
from .operation_ids import utc_now_iso
from .registry import get_target
from .repository_adapters import list_service_resources, load_skill_manifest, runtime_resource, service_resource
from .runtime_config import seed_runtime_root_config


def _component_payload(
    spec: ComponentSpec,
    *,
    command: str,
    requested_key: str = "requested_component",
    resolved_key: str = "resolved_component",
) -> dict[str, object]:
    return {
        requested_key: spec.requested_name,
        resolved_key: spec.component_name,
        "component": spec.as_dict(),
        "command": command,
    }


def _service_sources(spec: ComponentSpec, config: AppConfig) -> dict[str, object]:
    payload: dict[str, object] = {}
    if spec.service_repo_name is not None:
        payload["service_source"] = service_resource(config, spec.service_repo_name).as_dict()
    if spec.runtime_repo_name is not None:
        payload["runtime_source"] = runtime_resource(config, spec.runtime_repo_name).as_dict()
    return payload


def gateway_action(config: AppConfig, verb: str) -> dict[str, object]:
    if verb == "health":
        payload = handle_health(config)
        payload["command"] = "moltbox gateway health"
        return payload
    if verb == "inspect":
        return {
            "ok": True,
            "status": "success",
            "command": "moltbox gateway inspect",
            "timestamp": utc_now_iso(),
            "gateway": build_target_status(config, "tools"),
            "targets": handle_list_targets(config)["targets"],
        }
    if verb == "status":
        payload = build_target_status(config, "tools")
        payload["command"] = "moltbox gateway status"
        return payload
    if verb == "logs":
        payload = read_target_logs(config, "tools")
        payload["command"] = "moltbox gateway logs"
        return payload
    if verb == "update":
        payload = deploy_target(config, "tools")
        payload["command"] = "moltbox gateway update"
        return payload
    if verb == "rollback":
        payload = rollback_target(config, "tools")
        payload["command"] = "moltbox gateway rollback"
        return payload
    raise ValidationError(
        f"unsupported gateway command '{verb}'",
        "run `moltbox gateway --help` for supported commands",
        command=verb,
    )


def service_list_action(config: AppConfig) -> dict[str, object]:
    services: list[dict[str, object]] = []
    for resource in list_service_resources(config):
        supported_component = try_resolve_component(resource.path.name)
        services.append(
            {
                "name": resource.path.name,
                "supported_by_gateway": supported_component is not None,
                "component": supported_component.as_dict() if supported_component is not None else None,
                "service_source": resource.as_dict(),
            }
        )
    return {
        "ok": True,
        "status": "success",
        "command": "moltbox service list",
        "timestamp": utc_now_iso(),
        "services": services,
    }


def _resolve_deployable_service(name: str) -> ComponentSpec:
    spec = resolve_component(name)
    if spec.component_type == "gateway":
        raise ValidationError(
            "gateway cannot be deployed through the service pipeline",
            "use `moltbox gateway update` instead",
            service=name,
        )
    return spec


def service_inspect_action(config: AppConfig, name: str) -> dict[str, object]:
    spec = _resolve_deployable_service(name)
    payload = build_target_status(config, spec.backend_target)
    payload.update(
        _component_payload(spec, command=f"moltbox service inspect {name}", requested_key="requested_service", resolved_key="resolved_service")
    )
    payload.update(_service_sources(spec, config))
    return payload


def service_status_action(config: AppConfig, name: str) -> dict[str, object]:
    spec = _resolve_deployable_service(name)
    payload = build_target_status(config, spec.backend_target)
    payload.update(
        _component_payload(spec, command=f"moltbox service status {name}", requested_key="requested_service", resolved_key="resolved_service")
    )
    return payload


def service_logs_action(config: AppConfig, name: str) -> dict[str, object]:
    spec = _resolve_deployable_service(name)
    payload = read_target_logs(config, spec.backend_target)
    payload.update(
        _component_payload(spec, command=f"moltbox service logs {name}", requested_key="requested_service", resolved_key="resolved_service")
    )
    return payload


def service_lifecycle_action(config: AppConfig, name: str, action: str) -> dict[str, object]:
    spec = _resolve_deployable_service(name)
    if spec.component_type == "runtime":
        payload = runtime_lifecycle(config, spec.backend_target, action)
    else:
        payload = host_lifecycle(config, spec.backend_target, action)
    payload.update(
        _component_payload(spec, command=f"moltbox service {action} {name}", requested_key="requested_service", resolved_key="resolved_service")
    )
    return payload


def service_deploy_action(config: AppConfig, name: str) -> dict[str, object]:
    spec = _resolve_deployable_service(name)
    payload = deploy_target(config, spec.backend_target)
    payload.update(
        _component_payload(spec, command=f"moltbox service deploy {name}", requested_key="requested_service", resolved_key="resolved_service")
    )
    payload.update(_service_sources(spec, config))
    return payload


def service_rollback_action(config: AppConfig, name: str) -> dict[str, object]:
    spec = _resolve_deployable_service(name)
    payload = rollback_target(config, spec.backend_target)
    payload.update(
        _component_payload(spec, command=f"moltbox service rollback {name}", requested_key="requested_service", resolved_key="resolved_service")
    )
    return payload


def service_doctor_action(config: AppConfig, name: str) -> dict[str, object]:
    spec = _resolve_deployable_service(name)
    payload = build_target_status(config, spec.backend_target)
    payload.update(
        _component_payload(spec, command=f"moltbox service doctor {name}", requested_key="requested_service", resolved_key="resolved_service")
    )
    payload.update(_service_sources(spec, config))
    return payload


def _gateway_port_for_target(target_id: str) -> str:
    return {
        "dev": "18790",
        "test": "28789",
        "prod": "38789",
    }.get(target_id, "")


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    shutil.rmtree(path)


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


def component_config_sync_action(config: AppConfig, component_name: str) -> dict[str, object]:
    spec = resolve_component(component_name)
    if not spec.supports_config_sync or spec.runtime_repo_name is None:
        raise ValidationError(
            f"component '{component_name}' does not support runtime configuration sync",
            "use a runtime component such as openclaw-dev, openclaw-test, or openclaw",
            component=spec.as_dict(),
        )
    record = get_target(config, spec.backend_target)
    if not record.runtime_root:
        raise ValidationError(
            f"component '{component_name}' does not have a runtime root",
            "configure the runtime root before retrying the sync",
            component=spec.as_dict(),
        )
    source_resource = runtime_resource(config, spec.runtime_repo_name)
    if not source_resource.path.is_dir():
        raise ValidationError(
            f"runtime configuration source for '{component_name}' must be a directory",
            "store the runtime configuration as a directory in moltbox-runtime and rerun the command",
            component=spec.as_dict(),
            source=source_resource.as_dict(),
        )
    staging_dir = config.layout.deploy_dir / "runtime-sync" / spec.component_name
    _remove_tree(staging_dir)
    _copy_tree(source_resource.path, staging_dir)
    seed_runtime_root_config(
        staging_dir,
        _gateway_port_for_target(spec.backend_target),
        existing_runtime_root_dir=Path(record.runtime_root),
    )
    _copy_tree(staging_dir, Path(record.runtime_root))
    return {
        "ok": True,
        "status": "success",
        "command": f"moltbox {component_name} config sync",
        "timestamp": utc_now_iso(),
        "component": spec.as_dict(),
        "runtime_root": record.runtime_root,
        "runtime_source": source_resource.as_dict(),
        "staging_dir": str(staging_dir),
    }


def component_action(config: AppConfig, component_name: str, verb: str, *, message: str | None = None, timeout_seconds: int = 30) -> dict[str, object]:
    spec = resolve_component(component_name)
    ensure_component_command(spec, verb)
    command = f"moltbox {component_name} {verb}"
    if verb in {"status", "inspect"}:
        payload = build_target_status(config, spec.backend_target)
        payload.update(_component_payload(spec, command=command))
        payload.update(_service_sources(spec, config) if verb == "inspect" else {})
        return payload
    if verb == "logs":
        payload = read_target_logs(config, spec.backend_target)
        payload.update(_component_payload(spec, command=command))
        return payload
    if verb in {"start", "stop", "restart"}:
        if spec.component_type == "runtime":
            payload = runtime_lifecycle(config, spec.backend_target, verb)
        else:
            payload = host_lifecycle(config, spec.backend_target, verb)
        payload.update(_component_payload(spec, command=command))
        return payload
    if verb == "reload":
        payload = runtime_lifecycle(config, spec.backend_target, "restart")
        payload.update(_component_payload(spec, command=command))
        return payload
    if verb in {"doctor", "monitor"}:
        payload = build_target_status(config, spec.backend_target)
        payload.update(_component_payload(spec, command=command))
        payload.update(_service_sources(spec, config))
        return payload
    if verb == "chat":
        payload = runtime_chat(config, spec.backend_target, message, timeout_seconds)
        payload.update(_component_payload(spec, command=command))
        return payload
    raise ValidationError(
        f"unsupported component command '{verb}'",
        "run `moltbox --help` for supported commands",
        component=spec.as_dict(),
        command=verb,
    )


def _list_values(payload: Any) -> list[str]:
    if payload is None:
        return []
    if isinstance(payload, str):
        return [payload]
    if not isinstance(payload, list):
        raise ValidationError(
            "skill deployment manifest entries must be lists of strings",
            "rewrite the skill manifest with list entries and rerun the command",
            manifest_value=payload,
        )
    values: list[str] = []
    for item in payload:
        if not isinstance(item, str) or not item.strip():
            raise ValidationError(
                "skill deployment manifest entries must be non-empty strings",
                "remove invalid manifest entries and rerun the command",
                manifest_value=payload,
            )
        values.append(item.strip())
    return values


def _normalize_skill_plan(manifest: dict[str, Any]) -> dict[str, list[str]]:
    services_section = manifest.get("services")
    runtime_section = manifest.get("runtime")
    return {
        "service_deploy": _list_values(manifest.get("service_deploy"))
        + _list_values(services_section.get("deploy") if isinstance(services_section, dict) else None),
        "runtime_sync": _list_values(manifest.get("runtime_sync"))
        + _list_values(runtime_section.get("sync") if isinstance(runtime_section, dict) else None),
        "runtime_reload": _list_values(manifest.get("runtime_reload"))
        + _list_values(runtime_section.get("reload") if isinstance(runtime_section, dict) else None),
        "component_restart": _list_values(manifest.get("component_restart")) + _list_values(manifest.get("restart")),
    }


def skill_deploy_action(config: AppConfig, skill_name: str) -> dict[str, object]:
    manifest_resource, manifest = load_skill_manifest(config, skill_name)
    plan = _normalize_skill_plan(manifest)
    operations: list[dict[str, object]] = []

    for service_name in plan["service_deploy"]:
        result = service_deploy_action(config, service_name)
        operations.append({"operation": "service_deploy", "target": service_name, "result": result})
        if not result.get("ok"):
            return {
                "ok": False,
                "status": "failure",
                "command": f"moltbox skill deploy {skill_name}",
                "timestamp": utc_now_iso(),
                "skill": skill_name,
                "skill_manifest": manifest_resource.as_dict(),
                "plan": plan,
                "operations": operations,
            }

    for component_name in plan["runtime_sync"]:
        result = component_config_sync_action(config, component_name)
        operations.append({"operation": "runtime_sync", "target": component_name, "result": result})
        if not result.get("ok"):
            return {
                "ok": False,
                "status": "failure",
                "command": f"moltbox skill deploy {skill_name}",
                "timestamp": utc_now_iso(),
                "skill": skill_name,
                "skill_manifest": manifest_resource.as_dict(),
                "plan": plan,
                "operations": operations,
            }

    for component_name in plan["runtime_reload"]:
        result = component_action(config, component_name, "reload")
        operations.append({"operation": "runtime_reload", "target": component_name, "result": result})
        if not result.get("ok"):
            return {
                "ok": False,
                "status": "failure",
                "command": f"moltbox skill deploy {skill_name}",
                "timestamp": utc_now_iso(),
                "skill": skill_name,
                "skill_manifest": manifest_resource.as_dict(),
                "plan": plan,
                "operations": operations,
            }

    for component_name in plan["component_restart"]:
        result = component_action(config, component_name, "restart")
        operations.append({"operation": "component_restart", "target": component_name, "result": result})
        if not result.get("ok"):
            return {
                "ok": False,
                "status": "failure",
                "command": f"moltbox skill deploy {skill_name}",
                "timestamp": utc_now_iso(),
                "skill": skill_name,
                "skill_manifest": manifest_resource.as_dict(),
                "plan": plan,
                "operations": operations,
            }

    return {
        "ok": True,
        "status": "success",
        "command": f"moltbox skill deploy {skill_name}",
        "timestamp": utc_now_iso(),
        "skill": skill_name,
        "skill_manifest": manifest_resource.as_dict(),
        "plan": plan,
        "operations": operations,
    }
