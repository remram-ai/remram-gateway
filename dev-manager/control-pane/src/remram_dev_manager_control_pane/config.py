from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError
from .layout import HostLayout, build_host_layout


def _deep_get(payload: dict[str, Any], *keys: str) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


@dataclass(frozen=True)
class AppConfig:
    config_path: Path
    state_root: Path
    runtime_artifacts_root: Path
    internal_host: str
    internal_port: int
    cli_command: list[str]
    layout: HostLayout

    def as_dict(self) -> dict[str, object]:
        return {
            "config_path": str(self.config_path),
            "state_root": str(self.state_root),
            "runtime_artifacts_root": str(self.runtime_artifacts_root),
            "internal_host": self.internal_host,
            "internal_port": self.internal_port,
            "cli_command": self.cli_command,
            "layout": self.layout.as_dict(),
        }


def _load_config_file(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(
            f"failed to parse config file '{config_path}'",
            "fix the YAML syntax or remove the file and rerun the command",
            config_path=str(config_path),
        ) from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError(
            f"config file '{config_path}' must contain a mapping",
            "rewrite the config file as a YAML object or remove it and rerun the command",
            config_path=str(config_path),
        )
    return loaded


def _resolve_path(flag_value: str | None, env_name: str, config_value: Any, default: Path) -> Path:
    raw = flag_value if flag_value else os.environ.get(env_name)
    if raw:
        return Path(raw).expanduser().resolve()
    if isinstance(config_value, str) and config_value:
        return Path(config_value).expanduser().resolve()
    return default.expanduser().resolve()


def _resolve_string(flag_value: str | None, env_name: str, config_value: Any, default: str) -> str:
    raw = flag_value if flag_value else os.environ.get(env_name)
    if raw:
        return raw
    if isinstance(config_value, str) and config_value:
        return config_value
    return default


def _resolve_int(flag_value: int | None, env_name: str, config_value: Any, default: int) -> int:
    if flag_value is not None:
        return flag_value
    env_raw = os.environ.get(env_name)
    if env_raw:
        try:
            return int(env_raw)
        except ValueError as exc:
            raise ConfigError(
                f"environment variable {env_name} must be an integer",
                f"set {env_name} to a numeric port value and rerun the command",
                env_name=env_name,
                env_value=env_raw,
            ) from exc
    if config_value is None:
        return default
    try:
        return int(config_value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            "configured internal port must be an integer",
            "set service.port to a numeric value in config.yaml and rerun the command",
            configured_value=config_value,
        ) from exc


def resolve_config(args: Any | None = None) -> AppConfig:
    state_root_default = Path.home() / ".remram"
    config_path_default = state_root_default / "control-plane" / "config.yaml"
    config_path = _resolve_path(getattr(args, "config_path", None), "REMRAM_CONFIG_PATH", None, config_path_default)
    config_payload = _load_config_file(config_path)

    state_root = _resolve_path(
        getattr(args, "state_root", None),
        "REMRAM_STATE_ROOT",
        _deep_get(config_payload, "paths", "state_root"),
        state_root_default,
    )
    runtime_artifacts_root = _resolve_path(
        getattr(args, "runtime_artifacts_root", None),
        "REMRAM_RUNTIME_ROOT",
        _deep_get(config_payload, "paths", "runtime_root"),
        Path.home() / "Moltbox",
    )
    internal_host = _resolve_string(
        getattr(args, "internal_host", None),
        "REMRAM_INTERNAL_HOST",
        _deep_get(config_payload, "service", "host"),
        "127.0.0.1",
    )
    internal_port = _resolve_int(
        getattr(args, "internal_port", None),
        "REMRAM_INTERNAL_PORT",
        _deep_get(config_payload, "service", "port"),
        7474,
    )
    cli_path = _resolve_string(
        getattr(args, "cli_path", None),
        "REMRAM_CLI_PATH",
        _deep_get(config_payload, "cli", "path"),
        "remram",
    )

    layout = build_host_layout(root=state_root, runtime_artifacts_root=runtime_artifacts_root, config_path=config_path)
    return AppConfig(
        config_path=config_path,
        state_root=state_root,
        runtime_artifacts_root=runtime_artifacts_root,
        internal_host=internal_host,
        internal_port=internal_port,
        cli_command=[cli_path],
        layout=layout,
    )
