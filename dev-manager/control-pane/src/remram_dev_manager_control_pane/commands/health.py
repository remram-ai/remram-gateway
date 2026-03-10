from __future__ import annotations

from ..config import AppConfig
from ..health import build_cli_health_payload
from ..versioning import resolve_version_info


def handle_health(config: AppConfig) -> dict[str, object]:
    return build_cli_health_payload(config, resolve_version_info().version)
