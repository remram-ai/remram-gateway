from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .config import AppConfig
from .diagnostics import build_log_ref
from .jsonio import display_path
from .log_paths import service_log_file
from .registry_bootstrap import ensure_registry_bootstrap
from .runtime_state import pid_is_running, read_pid, read_runtime_state


def _paths(config: AppConfig) -> dict[str, str]:
    return {
        "config_path": display_path(config.config_path),
        "state_root": display_path(config.layout.root),
        "tools_state_dir": display_path(config.layout.control_plane_dir),
        "runtime_state_file": display_path(config.layout.runtime_state_file),
        "pid_file": display_path(config.layout.pid_file),
        "target_registry_dir": display_path(config.layout.target_registry_dir),
        "runtime_artifacts_root": display_path(config.layout.runtime_artifacts_root),
        "logs_root": display_path(config.layout.logs_dir),
        "serve_log": display_path(service_log_file(config, "tools")),
    }


def _base_health(config: AppConfig) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "degraded",
        "version": "",
        "serve_state": "down",
        "config_status": "ok",
        "paths": _paths(config),
        "started_at": None,
        "uptime": None,
        "logs": [build_log_ref("serve", service_log_file(config, "tools")).as_dict()],
        "error_message": "",
        "recovery_message": "",
    }


def build_local_health_payload(config: AppConfig, version: str) -> dict[str, Any]:
    ensure_registry_bootstrap(config)
    payload = _base_health(config)
    payload["version"] = version
    runtime_state = read_runtime_state(config.layout.runtime_state_file) or {}
    pid = read_pid(config.layout.pid_file)
    if pid is not None and pid_is_running(pid):
        serve_state = str(runtime_state.get("serve_state") or "ready")
        payload["serve_state"] = serve_state
        payload["started_at"] = runtime_state.get("started_at")
        payload["uptime"] = runtime_state.get("uptime")
        if serve_state == "ready":
            payload["ok"] = True
            payload["status"] = "ready"
        else:
            payload["status"] = serve_state
            payload["error_message"] = f"serve process state is '{serve_state}'"
            payload["recovery_message"] = "inspect the tools logs or restart with `moltbox tools serve`"
        return payload

    payload["error_message"] = "serve process not running"
    payload["recovery_message"] = (
        f"start with `moltbox tools serve` or inspect logs at {display_path(service_log_file(config, 'tools'))}"
    )
    payload["serve_state"] = "down"
    return payload


def _fetch_live_health(config: AppConfig) -> dict[str, Any] | None:
    url = f"http://{config.internal_host}:{config.internal_port}/health"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=2) as response:
            if response.status != 200:
                return None
            body = response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def build_cli_health_payload(config: AppConfig, version: str) -> dict[str, Any]:
    live = _fetch_live_health(config)
    if live is not None:
        return live
    return build_local_health_payload(config, version)
