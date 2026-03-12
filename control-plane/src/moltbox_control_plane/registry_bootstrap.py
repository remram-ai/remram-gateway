from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .config import AppConfig
from .jsonio import display_path, read_json_file, write_json_file
from .layout import ensure_host_layout
from .log_paths import service_log_dir, service_log_file
from .models import TargetRecord
from .registry_store import load_target_record, target_file_path, write_target_record


def _iso_now() -> str:
    return datetime.now(tz=UTC).isoformat()


def _base_metadata(config: AppConfig, service_name: str, aliases: list[str] | None = None) -> dict[str, object]:
    log_dir = service_log_dir(config, service_name)
    return {
        "aliases": aliases or [],
        "log_dir": display_path(log_dir),
        "primary_log": display_path(service_log_file(config, service_name)),
        "mount_path": "/var/log/moltbox",
    }


def _tools_target(config: AppConfig) -> TargetRecord:
    now = _iso_now()
    return TargetRecord(
        id="tools",
        target_class="tools",
        display_name="MoltBox Tools",
        asset_path="tools",
        compose_project="moltbox-tools",
        container_names=["moltbox-tools"],
        snapshot_scope="target",
        validator_key="container_baseline",
        log_source="docker_logs",
        runtime_root=str(config.layout.control_plane_dir),
        service_name="tools",
        container_name="moltbox-tools",
        created_at=now,
        updated_at=now,
        metadata={
            **_base_metadata(config, "tools", aliases=["cli", "control", "control-plane"]),
            "hostname": "moltbox-cli",
        },
    )


def _runtime_target(config: AppConfig, target_id: str, display_name: str, hostname: str) -> TargetRecord:
    now = _iso_now()
    runtime_root = config.layout.runtime_artifacts_root / "openclaw" / target_id
    service_name = f"openclaw-{target_id}"
    return TargetRecord(
        id=target_id,
        target_class="runtime",
        display_name=display_name,
        asset_path="runtimes/openclaw",
        compose_project=f"remram-{target_id}",
        container_names=[service_name],
        snapshot_scope="target",
        validator_key="container_baseline",
        log_source="docker_logs",
        profile=target_id,
        runtime_root=str(runtime_root.expanduser()),
        service_name=service_name,
        container_name=service_name,
        created_at=now,
        updated_at=now,
        metadata={
            **_base_metadata(config, service_name),
            "hostname": hostname,
        },
    )


def _shared_service_target(config: AppConfig, target_id: str, display_name: str) -> TargetRecord:
    now = _iso_now()
    shared_root = config.layout.shared_dir / target_id
    container_name = "moltbox-caddy" if target_id == "ssl" else f"moltbox-{target_id}"
    return TargetRecord(
        id=target_id,
        target_class="shared_service",
        display_name=display_name,
        asset_path=f"shared-services/{target_id}",
        compose_project="moltbox",
        container_names=[container_name],
        snapshot_scope="target",
        validator_key="container_baseline",
        log_source="docker_logs",
        runtime_root=str((Path.home() / ".openclaw").expanduser()),
        service_name=target_id,
        container_name=container_name,
        created_at=now,
        updated_at=now,
        metadata={
            **_base_metadata(config, target_id, aliases=["caddy"] if target_id == "ssl" else None),
            "shared_root": display_path(shared_root),
        },
    )


def canonical_target_records(config: AppConfig) -> list[TargetRecord]:
    return [
        _tools_target(config),
        _shared_service_target(config, "ollama", "Ollama"),
        _shared_service_target(config, "opensearch", "OpenSearch"),
        _shared_service_target(config, "ssl", "SSL Ingress"),
        _runtime_target(config, "dev", "Development Runtime", "moltbox-dev"),
        _runtime_target(config, "test", "Test Runtime", "moltbox-test"),
        _runtime_target(config, "prod", "Production Runtime", "moltbox-prod"),
    ]


def _read_target_payload(path: Path) -> dict[str, object] | None:
    try:
        payload = read_json_file(path, default=None)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _migrate_legacy_tools_record(config: AppConfig) -> None:
    tools_path = target_file_path(config.layout, "tools")
    legacy_paths = [
        target_file_path(config.layout, "control"),
        target_file_path(config.layout, "control-plane"),
    ]
    if not tools_path.exists():
        for legacy_path in legacy_paths:
            if not legacy_path.exists():
                continue
            legacy_payload = _read_target_payload(legacy_path)
            if legacy_payload is not None:
                write_json_file(tools_path, legacy_payload)
                break
    for legacy_path in legacy_paths:
        legacy_path.unlink(missing_ok=True)


def _migrate_legacy_ssl_record(config: AppConfig) -> None:
    ssl_path = target_file_path(config.layout, "ssl")
    legacy_path = target_file_path(config.layout, "caddy")
    if not ssl_path.exists() and legacy_path.exists():
        legacy_payload = _read_target_payload(legacy_path)
        if legacy_payload is not None:
            write_json_file(ssl_path, legacy_payload)
    legacy_path.unlink(missing_ok=True)


def _merge_aliases(canonical_id: str, canonical_aliases: list[str]) -> list[str]:
    merged: list[str] = []
    for alias in canonical_aliases:
        alias_text = str(alias).strip()
        if not alias_text or alias_text == canonical_id or alias_text in merged:
            continue
        merged.append(alias_text)
    return merged


def _reconcile_target_record(canonical: TargetRecord, existing_payload: dict[str, object] | None) -> TargetRecord:
    existing_payload = existing_payload or {}
    existing_metadata = existing_payload.get("metadata") if isinstance(existing_payload.get("metadata"), dict) else {}
    metadata = dict(existing_metadata)
    metadata.update({key: value for key, value in canonical.metadata.items() if key != "aliases"})
    metadata["aliases"] = _merge_aliases(
        canonical.id,
        [str(item) for item in canonical.metadata.get("aliases", [])],
    )

    created_at = str(existing_payload.get("created_at") or canonical.created_at)
    updated_at = str(existing_payload.get("updated_at") or canonical.updated_at)
    if existing_payload:
        desired_payload = canonical.as_dict()
        desired_payload["created_at"] = created_at
        desired_payload["updated_at"] = updated_at
        desired_payload["metadata"] = metadata
        if desired_payload != existing_payload:
            updated_at = _iso_now()

    return TargetRecord(
        id=canonical.id,
        target_class=canonical.target_class,
        display_name=canonical.display_name,
        asset_path=canonical.asset_path,
        compose_project=canonical.compose_project,
        container_names=canonical.container_names,
        snapshot_scope=canonical.snapshot_scope,
        validator_key=canonical.validator_key,
        log_source=canonical.log_source,
        profile=canonical.profile,
        runtime_root=canonical.runtime_root,
        service_name=canonical.service_name,
        container_name=canonical.container_name,
        created_at=created_at,
        updated_at=updated_at,
        metadata=metadata,
    )


def ensure_registry_bootstrap(config: AppConfig) -> list[TargetRecord]:
    ensure_host_layout(config.layout)
    _migrate_legacy_tools_record(config)
    _migrate_legacy_ssl_record(config)
    records: list[TargetRecord] = []
    for canonical in canonical_target_records(config):
        path = target_file_path(config.layout, canonical.id)
        existing_payload = _read_target_payload(path) if path.exists() else None
        reconciled = _reconcile_target_record(canonical, existing_payload)
        if existing_payload != reconciled.as_dict():
            write_target_record(path, reconciled)
        records.append(load_target_record(path))
    return records
