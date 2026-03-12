from __future__ import annotations

from pathlib import Path

from .errors import ValidationError
from .jsonio import display_path, read_json_file, write_json_file
from .models import TargetRecord


REQUIRED_FIELDS = {
    "id",
    "target_class",
    "display_name",
    "asset_path",
    "compose_project",
    "container_names",
    "snapshot_scope",
    "validator_key",
    "log_source",
    "created_at",
    "updated_at",
    "metadata",
}


def target_file_path(layout, target_id: str) -> Path:
    return layout.target_registry_dir / f"{target_id}.json"


def load_target_record(path: Path) -> TargetRecord:
    try:
        payload = read_json_file(path)
    except ValueError as exc:
        raise ValidationError(
            "target registry corrupted",
            f"delete the target registry at {display_path(path.parent)} and rerun `moltbox tools health`",
            path=str(path),
        ) from exc
    if not isinstance(payload, dict) or not REQUIRED_FIELDS.issubset(payload):
        raise ValidationError(
            "target registry corrupted",
            f"delete the target registry at {display_path(path.parent)} and rerun `moltbox tools health`",
            path=str(path),
        )
    return TargetRecord(
        id=str(payload["id"]),
        target_class=str(payload["target_class"]),
        display_name=str(payload["display_name"]),
        asset_path=str(payload["asset_path"]),
        compose_project=str(payload["compose_project"]),
        container_names=[str(item) for item in payload.get("container_names", [])],
        snapshot_scope=str(payload["snapshot_scope"]),
        validator_key=str(payload["validator_key"]),
        log_source=str(payload["log_source"]),
        profile=payload.get("profile"),
        runtime_root=payload.get("runtime_root"),
        service_name=payload.get("service_name"),
        container_name=payload.get("container_name"),
        created_at=str(payload["created_at"]),
        updated_at=str(payload["updated_at"]),
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    )


def write_target_record(path: Path, record: TargetRecord) -> None:
    write_json_file(path, record.as_dict())
