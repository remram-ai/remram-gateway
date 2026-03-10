from __future__ import annotations

from typing import Any

from .config import AppConfig
from .diagnostics import build_log_ref
from .health import build_cli_health_payload
from .log_paths import service_log_file, target_log_service_name
from .registry import get_target
from .target_resolution import resolve_target_identifier
from .versioning import resolve_version_info


def build_target_status(config: AppConfig, requested_target: str) -> dict[str, Any]:
    resolved_target = resolve_target_identifier(requested_target)
    record = get_target(config, requested_target)
    alias_used = requested_target if requested_target != resolved_target else None

    if resolved_target == "control":
        control_health = build_cli_health_payload(config, resolve_version_info().version)
        return {
            "ok": True,
            "requested_target": requested_target,
            "resolved_target": resolved_target,
            "alias_used": alias_used,
            "target": record.as_dict(),
            "health": control_health,
        }

    log_service = target_log_service_name(resolved_target)
    primary_log = service_log_file(config, log_service)
    return {
        "ok": True,
        "requested_target": requested_target,
        "resolved_target": resolved_target,
        "alias_used": alias_used,
        "target": record.as_dict(),
        "health": {
            "ok": False,
            "status": "registered",
            "error_message": "",
            "recovery_message": "runtime lifecycle inspection is provided by later capability domains",
        },
        "logs": [build_log_ref(log_service, primary_log).as_dict()],
    }
