from __future__ import annotations

import logging
import os
import socket
from datetime import UTC, datetime

import uvicorn

from .config import AppConfig
from .errors import ControlPlaneUnavailableError
from .http_app import create_http_app
from .log_paths import service_log_file
from .logging_setup import configure_logger, log_event
from .registry_bootstrap import ensure_registry_bootstrap
from .runtime_state import clear_pid, write_pid, write_runtime_state
from .versioning import resolve_version_info


def _check_bind_available(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            raise ControlPlaneUnavailableError(
                f"unable to bind internal control-plane service to {host}:{port}",
                "choose a different internal port or stop the conflicting process before running `remram serve` again",
                host=host,
                port=port,
            ) from exc


def run_control_plane_service(config: AppConfig) -> None:
    ensure_registry_bootstrap(config)
    logger = configure_logger(service_log_file(config, "control-plane"))
    version = resolve_version_info().version
    _check_bind_available(config.internal_host, config.internal_port)
    write_pid(config.layout.pid_file, os.getpid())
    write_runtime_state(
        config.layout.runtime_state_file,
        {
            "serve_state": "starting",
            "status": "starting",
            "started_at": datetime.now(tz=UTC).isoformat(),
            "host": config.internal_host,
            "port": config.internal_port,
            "pid": os.getpid(),
            "version": version,
        },
    )
    log_event(logger, logging.INFO, "startup", "control-plane", "starting control-plane service")
    app = create_http_app(config, logger)
    try:
        uvicorn.run(app, host=config.internal_host, port=config.internal_port, access_log=False, log_config=None)
    except Exception:  # noqa: BLE001
        write_runtime_state(
            config.layout.runtime_state_file,
            {
                "serve_state": "failed",
                "status": "failed",
                "started_at": datetime.now(tz=UTC).isoformat(),
                "host": config.internal_host,
                "port": config.internal_port,
                "pid": os.getpid(),
                "version": version,
            },
        )
        log_event(logger, logging.ERROR, "fatal_error", "control-plane", "control-plane service terminated unexpectedly")
        clear_pid(config.layout.pid_file)
        raise
