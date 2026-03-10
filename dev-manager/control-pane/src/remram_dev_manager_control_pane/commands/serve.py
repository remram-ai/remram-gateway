from __future__ import annotations

from ..config import AppConfig


def handle_serve(config: AppConfig) -> None:
    from ..service import run_control_plane_service

    run_control_plane_service(config)
