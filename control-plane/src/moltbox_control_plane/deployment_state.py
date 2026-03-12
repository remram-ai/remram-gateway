from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import AppConfig
from .jsonio import read_json_file, write_json_file


def deployment_logs_dir(config: AppConfig, target: str) -> Path:
    return config.layout.deploy_dir / "logs" / target


def deployment_rendered_dir(config: AppConfig, target: str, profile: str | None) -> Path:
    bucket = profile if profile else "shared"
    return config.layout.deploy_dir / "rendered" / bucket / target


def target_snapshots_dir(config: AppConfig, target: str) -> Path:
    return config.layout.snapshots_dir / target


def write_deployment_record(config: AppConfig, target: str, record_id: str, payload: dict[str, Any]) -> Path:
    path = deployment_logs_dir(config, target) / f"{record_id}.json"
    write_json_file(path, payload)
    return path


def latest_deployment_record(config: AppConfig, target: str, record_type: str | None = None) -> dict[str, Any] | None:
    root = deployment_logs_dir(config, target)
    if not root.exists():
        return None
    for path in reversed(sorted(root.glob("*.json"))):
        payload = read_json_file(path, default=None)
        if not isinstance(payload, dict):
            continue
        if record_type and payload.get("record_type") != record_type:
            continue
        return payload
    return None


def latest_snapshot_metadata(config: AppConfig, target: str) -> dict[str, Any] | None:
    root = target_snapshots_dir(config, target)
    if not root.exists():
        return None
    for directory in reversed(sorted([path for path in root.iterdir() if path.is_dir()])):
        payload = read_json_file(directory / "metadata.json", default=None)
        if isinstance(payload, dict):
            return payload
    return None


def snapshot_metadata(config: AppConfig, target: str, snapshot_id: str) -> dict[str, Any] | None:
    path = target_snapshots_dir(config, target) / snapshot_id / "metadata.json"
    payload = read_json_file(path, default=None)
    return payload if isinstance(payload, dict) else None
