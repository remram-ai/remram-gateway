from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _stringify(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_stringify(item) for item in value]
    if isinstance(value, dict):
        return {key: _stringify(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class LogRef:
    name: str
    path: str
    tail: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "path": self.path, "tail": self.tail}


@dataclass(frozen=True)
class TargetRecord:
    id: str
    target_class: str
    display_name: str
    runtime_root: str | None = None
    service_name: str | None = None
    container_name: str | None = None
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "target_class": self.target_class,
            "display_name": self.display_name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": _stringify(self.metadata),
        }
        if self.runtime_root is not None:
            payload["runtime_root"] = self.runtime_root
        if self.service_name is not None:
            payload["service_name"] = self.service_name
        if self.container_name is not None:
            payload["container_name"] = self.container_name
        return payload


@dataclass(frozen=True)
class VersionInfo:
    version: str
    source: str
    git_commit: str | None = None

    def as_dict(self) -> dict[str, str]:
        payload = {"version": self.version, "source": self.source}
        if self.git_commit:
            payload["git_commit"] = self.git_commit
        return payload


@dataclass(frozen=True)
class ErrorPayload:
    error_type: str
    error_message: str
    recovery_message: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "ok": False,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "recovery_message": self.recovery_message,
        }
        payload.update(_stringify(self.details))
        return payload
