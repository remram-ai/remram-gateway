from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .config import AppConfig
from .layout import build_repo_layout


DEFAULT_POLICY = {
    "mcp": {
        "tools": {"verbs": ["version", "health", "status", "inspect"]},
        "host": {"verbs": ["status", "inspect", "logs"]},
        "runtime": {
            "dev": {"verbs": ["deploy", "rollback", "status", "inspect", "logs", "start", "stop", "restart"]},
            "test": {"verbs": ["deploy", "status", "logs", "inspect"]},
            "prod": {"verbs": ["deploy", "inspect", "logs"]},
        },
    }
}


def _default_policy_path() -> Path:
    return build_repo_layout().config_dir / "control-plane-policy.yaml"


def _read_yaml_mapping(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else None


def load_mcp_policy(config: AppConfig) -> tuple[dict[str, Any], Path]:
    for path in (config.policy_path, _default_policy_path()):
        payload = _read_yaml_mapping(path)
        if payload is not None:
            return payload, path
    return DEFAULT_POLICY, _default_policy_path()


def _verb_list(payload: Any) -> set[str]:
    if not isinstance(payload, list):
        return set()
    return {str(item).strip() for item in payload if str(item).strip()}


def allowed_tools_verbs(policy: dict[str, Any]) -> set[str]:
    return _verb_list(((policy.get("mcp") or {}).get("tools") or {}).get("verbs"))


def allowed_host_verbs(policy: dict[str, Any]) -> set[str]:
    return _verb_list(((policy.get("mcp") or {}).get("host") or {}).get("verbs"))


def allowed_runtime_verbs(policy: dict[str, Any], environment: str) -> set[str]:
    return _verb_list((((policy.get("mcp") or {}).get("runtime") or {}).get(environment) or {}).get("verbs"))


def denial_payload(*, domain: str, verb: str, policy_source: Path, target: str | None = None, environment: str | None = None) -> dict[str, Any]:
    subject = environment or target or domain
    return {
        "ok": False,
        "status": "denied",
        "error_type": "mcp_policy_denied",
        "error_message": f"MCP does not allow `{verb}` for {subject}",
        "recovery_message": "use the trusted local MoltBox CLI on the host for unrestricted operator access",
        "details": {
            "domain": domain,
            "target": target,
            "environment": environment,
            "verb": verb,
            "policy_source": str(policy_source),
        },
    }
