from __future__ import annotations

from pathlib import Path

from .jsonio import display_path
from .models import LogRef
from .tail import read_tail


def build_log_ref(name: str, path: Path, max_lines: int = 50) -> LogRef:
    return LogRef(name=name, path=display_path(path), tail=read_tail(path, max_lines=max_lines))
