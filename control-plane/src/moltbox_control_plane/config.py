from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError
from .layout import HostLayout, build_host_layout


def _env_value(*env_names: str) -> str | None:
    for env_name in env_names:
        raw = os.environ.get(env_name)
        if raw:
            return raw
    return None


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
    policy_path: Path
    state_root: Path
    runtime_artifacts_root: Path
    services_repo_url: str | None
    runtime_repo_url: str | None
    skills_repo_url: str | None
    internal_host: str
    internal_port: int
    cli_command: list[str]
    layout: HostLayout

    def as_dict(self) -> dict[str, object]:
        return {
            "config_path": str(self.config_path),
            "policy_path": str(self.policy_path),
            "state_root": str(self.state_root),
            "runtime_artifacts_root": str(self.runtime_artifacts_root),
            "services_repo_url": self.services_repo_url or "",
            "runtime_repo_url": self.runtime_repo_url or "",
            "skills_repo_url": self.skills_repo_url or "",
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


def _resolve_path(flag_value: str | None, env_names: tuple[str, ...], config_value: Any, default: Path) -> Path:
    raw = flag_value if flag_value else _env_value(*env_names)
    if raw:
        return Path(raw).expanduser().resolve()
    if isinstance(config_value, str) and config_value:
        return Path(config_value).expanduser().resolve()
    return default.expanduser().resolve()


def _resolve_string(flag_value: str | None, env_names: tuple[str, ...], config_value: Any, default: str) -> str:
    raw = flag_value if flag_value else _env_value(*env_names)
    if raw:
        return raw
    if isinstance(config_value, str) and config_value:
        return config_value
    return default


def _resolve_int(flag_value: int | None, env_names: tuple[str, ...], config_value: Any, default: int) -> int:
    if flag_value is not None:
        return flag_value
    env_raw = _env_value(*env_names)
    if env_raw:
        try:
            return int(env_raw)
        except ValueError as exc:
            env_label = " or ".join(env_names)
            raise ConfigError(
                f"environment variable {env_label} must be an integer",
                f"set one of {env_label} to a numeric port value and rerun the command",
                env_names=list(env_names),
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
    config_path_default = state_root_default / "tools" / "config.yaml"
    legacy_config_path_default = state_root_default / "control-plane" / "config.yaml"
    config_path = _resolve_path(
        getattr(args, "config_path", None),
        ("MOLTBOX_CONFIG_PATH", "REMRAM_CONFIG_PATH"),
        None,
        config_path_default,
    )
    if (
        getattr(args, "config_path", None) is None
        and _env_value("MOLTBOX_CONFIG_PATH", "REMRAM_CONFIG_PATH") is None
        and not config_path.exists()
        and legacy_config_path_default.exists()
    ):
        config_path = legacy_config_path_default.resolve()
    config_payload = _load_config_file(config_path)

    state_root = _resolve_path(
        getattr(args, "state_root", None),
        ("MOLTBOX_STATE_ROOT", "REMRAM_STATE_ROOT"),
        _deep_get(config_payload, "paths", "state_root"),
        state_root_default,
    )
    policy_path = _resolve_path(
        getattr(args, "policy_path", None),
        ("MOLTBOX_POLICY_PATH", "REMRAM_POLICY_PATH"),
        _deep_get(config_payload, "policy", "path"),
        state_root / "tools" / "control-plane-policy.yaml",
    )
    runtime_artifacts_root = _resolve_path(
        getattr(args, "runtime_artifacts_root", None),
        ("MOLTBOX_RUNTIME_ROOT", "REMRAM_RUNTIME_ROOT"),
        _deep_get(config_payload, "paths", "runtime_root"),
        Path.home() / "Moltbox",
    )
    internal_host = _resolve_string(
        getattr(args, "internal_host", None),
        ("MOLTBOX_INTERNAL_HOST", "REMRAM_INTERNAL_HOST"),
        _deep_get(config_payload, "service", "host"),
        "127.0.0.1",
    )
    internal_port = _resolve_int(
        getattr(args, "internal_port", None),
        ("MOLTBOX_INTERNAL_PORT", "REMRAM_INTERNAL_PORT"),
        _deep_get(config_payload, "service", "port"),
        7474,
    )
    cli_path = _resolve_string(
        getattr(args, "cli_path", None),
        ("MOLTBOX_CLI_PATH", "REMRAM_CLI_PATH"),
        _deep_get(config_payload, "cli", "path"),
        "moltbox",
    )
    services_repo_url = _resolve_string(
        getattr(args, "services_repo_url", None),
        ("MOLTBOX_SERVICES_REPO_URL", "REMRAM_SERVICES_REPO_URL"),
        _deep_get(config_payload, "repositories", "services", "url"),
        "",
    )
    runtime_repo_url = _resolve_string(
        getattr(args, "runtime_repo_url", None),
        ("MOLTBOX_RUNTIME_REPO_URL", "REMRAM_RUNTIME_REPO_URL"),
        _deep_get(config_payload, "repositories", "runtime", "url"),
        "",
    )
    skills_repo_url = _resolve_string(
        getattr(args, "skills_repo_url", None),
        ("MOLTBOX_SKILLS_REPO_URL", "REMRAM_SKILLS_REPO_URL"),
        _deep_get(config_payload, "repositories", "skills", "url"),
        "",
    )

    layout = build_host_layout(
        root=state_root,
        runtime_artifacts_root=runtime_artifacts_root,
        config_path=config_path,
        policy_path=policy_path,
    )
    return AppConfig(
        config_path=config_path,
        policy_path=policy_path,
        state_root=state_root,
        runtime_artifacts_root=runtime_artifacts_root,
        services_repo_url=services_repo_url or None,
        runtime_repo_url=runtime_repo_url or None,
        skills_repo_url=skills_repo_url or None,
        internal_host=internal_host,
        internal_port=internal_port,
        cli_command=[cli_path],
        layout=layout,
    )
