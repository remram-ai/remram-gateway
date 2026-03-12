from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from moltbox_commands.core.config import GatewayConfig
from moltbox_commands.core.errors import ConfigError, ValidationError


OPENCLAW_CONTAINER_CANDIDATES = (
    "openclaw-prod",
    "moltbox-openclaw",
    "openclaw",
)


def _run(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, input=input_text, capture_output=True, text=True, check=False)


def _docker(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return _run(["docker", *args], input_text=input_text)


def _container_exists(container_name: str) -> bool:
    completed = _docker("inspect", container_name)
    return completed.returncode == 0


def resolve_openclaw_container() -> str:
    configured = os.environ.get("MOLTBOX_OPENCLAW_CONTAINER")
    if configured and _container_exists(configured):
        return configured
    for candidate in OPENCLAW_CONTAINER_CANDIDATES:
        if _container_exists(candidate):
            return candidate
    raise ConfigError(
        "no supported OpenClaw runtime container is currently running",
        "deploy the production OpenClaw runtime or set MOLTBOX_OPENCLAW_CONTAINER before deploying skills",
        container_candidates=list(OPENCLAW_CONTAINER_CANDIDATES),
    )


def _docker_exec(container_name: str, shell_command: str, *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return _docker("exec", "-i", container_name, "sh", "-lc", shell_command, input_text=input_text)


def _ensure_success(completed: subprocess.CompletedProcess[str], *, error_message: str, recovery_message: str, **details: Any) -> str:
    if completed.returncode == 0:
        return completed.stdout
    raise ConfigError(
        error_message,
        recovery_message,
        stdout=completed.stdout.strip(),
        stderr=completed.stderr.strip(),
        **details,
    )


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"skill config overlay '{path}' must be valid JSON",
            "fix the JSON syntax and rerun the skill deploy command",
            skill_file=str(path),
        ) from exc
    if not isinstance(loaded, dict):
        raise ValidationError(
            f"skill config overlay '{path}' must contain a JSON object",
            "rewrite the overlay as a JSON object and rerun the skill deploy command",
            skill_file=str(path),
        )
    return loaded


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _read_container_json(container_name: str, path: str) -> dict[str, Any]:
    completed = _docker_exec(container_name, f"cat {path}")
    raw = _ensure_success(
        completed,
        error_message=f"failed to read '{path}' from the OpenClaw container",
        recovery_message="inspect the runtime container filesystem and rerun the skill deployment",
        container=container_name,
        path=path,
    )
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"runtime config file '{path}' is not valid JSON",
            "repair the OpenClaw runtime config and rerun the skill deployment",
            container=container_name,
            path=path,
            raw=raw,
        ) from exc
    if not isinstance(loaded, dict):
        raise ConfigError(
            f"runtime config file '{path}' must contain a JSON object",
            "repair the OpenClaw runtime config and rerun the skill deployment",
            container=container_name,
            path=path,
        )
    return loaded


def _write_container_json(container_name: str, path: str, payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload, indent=2) + "\n"
    completed = _docker_exec(container_name, f"cat > {path}", input_text=serialized)
    _ensure_success(
        completed,
        error_message=f"failed to write '{path}' in the OpenClaw container",
        recovery_message="inspect the runtime container filesystem permissions and rerun the skill deployment",
        container=container_name,
        path=path,
    )


def _resolve_gateway_port(runtime_config: dict[str, Any]) -> int:
    allowed_origins = (((runtime_config.get("gateway") or {}).get("controlUi") or {}).get("allowedOrigins") or [])
    if isinstance(allowed_origins, list):
        for item in allowed_origins:
            if not isinstance(item, str):
                continue
            parsed = urlparse(item)
            if parsed.hostname in {"127.0.0.1", "localhost"} and parsed.port:
                return int(parsed.port)
    return 18789


def _copy_package_to_container(container_name: str, package_dir: Path, destination_root: str) -> str:
    package_root = f"{destination_root}/{package_dir.name}"
    _ensure_success(
        _docker_exec(container_name, f"rm -rf {package_root} && mkdir -p {destination_root}"),
        error_message="failed to prepare the OpenClaw skill staging directory",
        recovery_message="inspect the runtime container filesystem permissions and rerun the skill deployment",
        container=container_name,
        staging_root=destination_root,
    )
    completed = _docker("cp", str(package_dir), f"{container_name}:{destination_root}/")
    _ensure_success(
        completed,
        error_message="failed to copy the skill package into the OpenClaw container",
        recovery_message="inspect Docker on the host and rerun the skill deployment",
        container=container_name,
        package_dir=str(package_dir),
        staging_root=destination_root,
    )
    return package_root


def _read_manifest(package_dir: Path) -> dict[str, Any]:
    manifest_path = package_dir / "openclaw.plugin.json"
    if not manifest_path.exists():
        raise ValidationError(
            f"plugin-backed skill package '{package_dir.name}' is missing openclaw.plugin.json",
            "add the OpenClaw plugin manifest and rerun the skill deployment",
            package_dir=str(package_dir),
        )
    return _read_json_file(manifest_path)


def _plugin_id(manifest: dict[str, Any], package_dir: Path) -> str:
    plugin_id = manifest.get("id")
    if not isinstance(plugin_id, str) or not plugin_id.strip():
        raise ValidationError(
            f"plugin manifest for '{package_dir.name}' must define a non-empty id",
            "fix openclaw.plugin.json and rerun the skill deployment",
            package_dir=str(package_dir),
        )
    return plugin_id.strip()


def _load_overlay(package_dir: Path, gateway_port: int) -> dict[str, Any]:
    overlay_path = package_dir / "example-config.json"
    if not overlay_path.exists():
        return {}
    raw = overlay_path.read_text(encoding="utf-8").replace("<gateway-port>", str(gateway_port))
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"skill config overlay '{overlay_path}' must be valid JSON",
            "fix the JSON syntax and rerun the skill deploy command",
            skill_file=str(overlay_path),
        ) from exc
    if not isinstance(loaded, dict):
        raise ValidationError(
            f"skill config overlay '{overlay_path}' must contain a JSON object",
            "rewrite the overlay as a JSON object and rerun the skill deploy command",
            skill_file=str(overlay_path),
        )
    return loaded


def _apply_plugin_config(runtime_config: dict[str, Any], *, plugin_id: str, overlay: dict[str, Any]) -> dict[str, Any]:
    merged = _deep_merge(runtime_config, overlay)
    plugins = merged.setdefault("plugins", {})
    if not isinstance(plugins, dict):
        raise ConfigError(
            "OpenClaw runtime config must keep plugins as an object",
            "repair openclaw.json and rerun the skill deployment",
        )
    allow = plugins.setdefault("allow", [])
    if not isinstance(allow, list):
        raise ConfigError(
            "OpenClaw runtime config must keep plugins.allow as an array",
            "repair openclaw.json and rerun the skill deployment",
        )
    if plugin_id not in allow:
        allow.append(plugin_id)
    entries = plugins.setdefault("entries", {})
    if not isinstance(entries, dict):
        raise ConfigError(
            "OpenClaw runtime config must keep plugins.entries as an object",
            "repair openclaw.json and rerun the skill deployment",
        )
    entry = entries.setdefault(plugin_id, {})
    if not isinstance(entry, dict):
        raise ConfigError(
            f"OpenClaw runtime config must keep plugins.entries.{plugin_id} as an object",
            "repair openclaw.json and rerun the skill deployment",
        )
    entry["enabled"] = True
    return merged


def _restart_container(container_name: str) -> dict[str, Any]:
    completed = _docker("restart", container_name)
    _ensure_success(
        completed,
        error_message="failed to restart the OpenClaw runtime after skill deployment",
        recovery_message="inspect the runtime container logs and rerun the skill deployment",
        container=container_name,
    )
    return {"stdout": completed.stdout.strip(), "stderr": completed.stderr.strip()}


def _parse_json_output(command_name: str, completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    if completed.returncode != 0:
        raise ConfigError(
            f"{command_name} failed during skill validation",
            "inspect the OpenClaw runtime logs and rerun the skill deployment",
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
        )
    try:
        payload = json.loads(completed.stdout.strip() or "{}")
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{command_name} did not return valid JSON",
            "inspect the OpenClaw runtime command output and rerun the skill deployment",
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
        ) from exc
    if not isinstance(payload, dict):
        raise ConfigError(
            f"{command_name} did not return a JSON object",
            "inspect the OpenClaw runtime command output and rerun the skill deployment",
            stdout=completed.stdout.strip(),
            stderr=completed.stderr.strip(),
        )
    return payload


def deploy_plugin_backed_skill(config: GatewayConfig, *, skill_name: str, package_dir: Path) -> dict[str, Any]:
    container_name = resolve_openclaw_container()
    manifest = _read_manifest(package_dir)
    plugin_id = _plugin_id(manifest, package_dir)
    runtime_config_path = "/home/node/.openclaw/openclaw.json"
    runtime_config = _read_container_json(container_name, runtime_config_path)
    gateway_port = _resolve_gateway_port(runtime_config)
    staged_package_dir = _copy_package_to_container(container_name, package_dir, "/tmp/moltbox-skills")

    install_completed = _docker_exec(container_name, f"openclaw plugins install -l {staged_package_dir}")
    _ensure_success(
        install_completed,
        error_message="failed to install the plugin-backed skill into OpenClaw",
        recovery_message="inspect the OpenClaw plugin install output and rerun the skill deployment",
        container=container_name,
        plugin_id=plugin_id,
        package_dir=str(package_dir),
    )

    overlay = _load_overlay(package_dir, gateway_port)
    merged_config = _apply_plugin_config(runtime_config, plugin_id=plugin_id, overlay=overlay)
    backup_completed = _docker_exec(container_name, f"cp {runtime_config_path} {runtime_config_path}.moltbox.bak")
    _ensure_success(
        backup_completed,
        error_message="failed to back up the OpenClaw runtime config before updating it",
        recovery_message="inspect the OpenClaw container filesystem and rerun the skill deployment",
        container=container_name,
        path=runtime_config_path,
    )
    _write_container_json(container_name, runtime_config_path, merged_config)
    enable_completed = _docker_exec(container_name, f"openclaw plugins enable {plugin_id}")
    _ensure_success(
        enable_completed,
        error_message="failed to enable the installed OpenClaw plugin",
        recovery_message="inspect the OpenClaw plugin config and rerun the skill deployment",
        container=container_name,
        plugin_id=plugin_id,
    )
    restart_result = _restart_container(container_name)

    doctor = _parse_json_output(
        "openclaw plugins doctor --json",
        _docker_exec(container_name, "openclaw plugins doctor --json"),
    )
    plugin_info = _parse_json_output(
        f"openclaw plugins info {plugin_id} --json",
        _docker_exec(container_name, f"openclaw plugins info {plugin_id} --json"),
    )
    skill_info = _parse_json_output(
        f"openclaw skills info {skill_name} --json",
        _docker_exec(container_name, f"openclaw skills info {skill_name} --json"),
    )

    return {
        "install_mode": "plugin-backed",
        "skill_name": skill_name,
        "plugin_id": plugin_id,
        "target_container": container_name,
        "gateway_port": gateway_port,
        "skill_package_dir": str(package_dir),
        "staged_package_dir": staged_package_dir,
        "manifest": manifest,
        "config_overlay": overlay,
        "runtime_config_path": runtime_config_path,
        "install": {
            "stdout": install_completed.stdout.strip(),
            "stderr": install_completed.stderr.strip(),
        },
        "restart": restart_result,
        "validation": {
            "doctor": doctor,
            "plugin_info": plugin_info,
            "skill_info": skill_info,
        },
    }


def deploy_pure_skill(config: GatewayConfig, *, skill_name: str, package_dir: Path) -> dict[str, Any]:
    container_name = resolve_openclaw_container()
    staged_skill_dir = _copy_package_to_container(container_name, package_dir, "/home/node/.openclaw/skills")
    restart_result = _restart_container(container_name)
    skill_info = _parse_json_output(
        f"openclaw skills info {skill_name} --json",
        _docker_exec(container_name, f"openclaw skills info {skill_name} --json"),
    )
    return {
        "install_mode": "pure-skill",
        "skill_name": skill_name,
        "target_container": container_name,
        "skill_package_dir": str(package_dir),
        "staged_skill_dir": staged_skill_dir,
        "restart": restart_result,
        "validation": {
            "skill_info": skill_info,
        },
    }
