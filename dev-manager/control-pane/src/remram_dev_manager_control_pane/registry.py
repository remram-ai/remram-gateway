from __future__ import annotations

from .config import AppConfig
from .registry_bootstrap import ensure_registry_bootstrap
from .registry_store import load_target_record, target_file_path
from .target_resolution import resolve_target_identifier


def list_targets(config: AppConfig) -> list[dict[str, object]]:
    return [record.as_dict() for record in ensure_registry_bootstrap(config)]


def get_target(config: AppConfig, target_id: str):
    resolved = resolve_target_identifier(target_id)
    path = target_file_path(config.layout, resolved)
    return load_target_record(path)
