from __future__ import annotations

from pathlib import Path


def read_tail(path: Path, max_lines: int = 50) -> str:
    if not path.exists() or not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if max_lines <= 0:
        return ""
    return "\n".join(lines[-max_lines:])
