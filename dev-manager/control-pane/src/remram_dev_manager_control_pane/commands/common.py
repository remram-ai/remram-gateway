from __future__ import annotations

from typing import Any


def successful_payload(status: str = "ok", **extra: Any) -> dict[str, Any]:
    payload = {"ok": True, "status": status}
    payload.update(extra)
    return payload
