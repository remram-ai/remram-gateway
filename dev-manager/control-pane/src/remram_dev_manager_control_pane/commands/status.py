from __future__ import annotations

from ..config import AppConfig
from ..status import build_target_status


def handle_status(config: AppConfig, target: str) -> dict[str, object]:
    return build_target_status(config, target)
