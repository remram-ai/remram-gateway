"""Remram control plane package."""

from __future__ import annotations

import os

__all__ = ["__version__"]

__version__ = os.environ.get("REMRAM_BUILD_VERSION", "0.2.0")
