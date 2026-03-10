from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .config import AppConfig
from .jsonio import display_path
from .layout import ensure_host_layout
from .log_paths import service_log_dir, service_log_file
from .models import TargetRecord
from .registry_store import load_target_record, target_file_path, write_target_record


def _iso_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _runtime_target(config: AppConfig, target_id: str, display_name: str, hostname: str) -> TargetRecord:
    runtime_root = config.layout.control_plane_dir if target_id == "control" else Path.home() / ".openclaw" / target_id
    service_name = "control-plane" if target_id == "control" else f"openclaw-{target_id}"
    log_dir = service_log_dir(config, service_name)
    now = _iso_now()
    return TargetRecord(
        id=target_id,
        target_class="runtime",
        display_name=display_name,
        runtime_root=str(runtime_root.expanduser()),
        container_name=None if target_id == "control" else service_name,
        created_at=now,
        updated_at=now,
        metadata={
            "hostname": hostname,
            "log_dir": display_path(log_dir),
            "primary_log": display_path(service_log_file(config, service_name)),
            "mount_path": "/var/log/remram",
            "aliases": ["cli"] if target_id == "control" else (["prime"] if target_id == "prod" else []),
        },
    )


def _shared_service_target(config: AppConfig, target_id: str, display_name: str) -> TargetRecord:
    now = _iso_now()
    log_dir = service_log_dir(config, target_id)
    return TargetRecord(
        id=target_id,
        target_class="shared_service",
        display_name=display_name,
        service_name=target_id,
        container_name=target_id,
        created_at=now,
        updated_at=now,
        metadata={
            "log_dir": display_path(log_dir),
            "primary_log": display_path(service_log_file(config, target_id)),
            "mount_path": "/var/log/remram",
        },
    )


def canonical_target_records(config: AppConfig) -> list[TargetRecord]:
    return [
        _runtime_target(config, "control", "Control Plane", "moltbox-cli"),
        _runtime_target(config, "dev", "Development Runtime", "moltbox-dev"),
        _runtime_target(config, "test", "Test Runtime", "moltbox-test"),
        _runtime_target(config, "prod", "Production Runtime", "moltbox-prod"),
        _shared_service_target(config, "ollama", "Ollama"),
        _shared_service_target(config, "opensearch", "OpenSearch"),
        _shared_service_target(config, "caddy", "Caddy"),
    ]


def ensure_registry_bootstrap(config: AppConfig) -> list[TargetRecord]:
    ensure_host_layout(config.layout)
    records: list[TargetRecord] = []
    for canonical in canonical_target_records(config):
        path = target_file_path(config.layout, canonical.id)
        if path.exists():
            records.append(load_target_record(path))
            continue
        write_target_record(path, canonical)
        records.append(canonical)
    return records
