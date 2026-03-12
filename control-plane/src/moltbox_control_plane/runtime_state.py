from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .jsonio import read_json_file, write_json_file


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_runtime_state(path: Path) -> dict[str, Any] | None:
    payload = read_json_file(path, default=None)
    return payload if isinstance(payload, dict) else None


def write_runtime_state(path: Path, payload: dict[str, Any]) -> None:
    write_json_file(path, payload)


def write_pid(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n", encoding="utf-8")


def read_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def clear_pid(path: Path) -> None:
    if path.exists():
        path.unlink()
