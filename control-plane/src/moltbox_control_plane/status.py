from __future__ import annotations

from typing import Any

from .config import AppConfig
from .deployment_service import build_target_status as build_deployment_target_status


def build_target_status(config: AppConfig, requested_target: str) -> dict[str, Any]:
    return build_deployment_target_status(config, requested_target)
