from __future__ import annotations

import os
import subprocess

from . import __version__
from .layout import build_repo_layout
from .models import VersionInfo


def resolve_version_info() -> VersionInfo:
    build_version = os.environ.get("MOLTBOX_BUILD_VERSION") or os.environ.get("REMRAM_BUILD_VERSION")
    if build_version:
        return VersionInfo(version=build_version, source="build")

    try:
        repo_root = build_repo_layout().repo_root
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        git_commit = completed.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        git_commit = ""

    if git_commit:
        return VersionInfo(version=git_commit, source="git", git_commit=git_commit)
    return VersionInfo(version=__version__ if __version__ else "dev", source="fallback")
