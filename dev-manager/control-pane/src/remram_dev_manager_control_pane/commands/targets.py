from __future__ import annotations

from ..config import AppConfig
from ..registry import list_targets


def handle_list_targets(config: AppConfig) -> dict[str, object]:
    return {"ok": True, "status": "ok", "targets": list_targets(config)}
