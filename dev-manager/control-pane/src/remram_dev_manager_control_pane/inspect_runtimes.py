from __future__ import annotations

from .config import AppConfig
from .status import build_target_status


def inspect_runtime_target(config: AppConfig, target_id: str) -> dict[str, object]:
    return build_target_status(config, target_id)
