from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def new_operation_id(target: str) -> str:
    stamp = utc_now().strftime("%Y%m%dT%H%M%S%fZ")
    return f"{stamp}-{target}"
