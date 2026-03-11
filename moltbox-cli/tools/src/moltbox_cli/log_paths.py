from __future__ import annotations

from pathlib import Path

from .config import AppConfig


PRIMARY_LOG_FILES = {
    "tools": "serve.log",
    "openclaw-dev": "openclaw.log",
    "openclaw-test": "openclaw.log",
    "openclaw-prod": "openclaw.log",
    "ollama": "service.log",
    "opensearch": "service.log",
    "ssl": "service.log",
    "caddy": "service.log",
}


def service_log_dir(config: AppConfig, service_name: str) -> Path:
    return config.layout.logs_dir / service_name


def service_log_file(config: AppConfig, service_name: str) -> Path:
    return service_log_dir(config, service_name) / PRIMARY_LOG_FILES[service_name]


def target_log_service_name(target_id: str) -> str:
    if target_id == "tools":
        return "tools"
    if target_id in {"dev", "test", "prod"}:
        return f"openclaw-{target_id}"
    return target_id
