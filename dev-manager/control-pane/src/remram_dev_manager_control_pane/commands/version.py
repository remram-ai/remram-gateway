from __future__ import annotations

from .common import successful_payload
from ..versioning import resolve_version_info


def handle_version() -> dict[str, object]:
    version = resolve_version_info()
    return successful_payload(status="ok", version=version.version, version_info=version.as_dict())
