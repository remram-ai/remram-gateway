from __future__ import annotations

from dataclasses import dataclass

from .errors import ValidationError


@dataclass(frozen=True)
class ComponentSpec:
    requested_name: str
    component_name: str
    backend_target: str
    component_type: str
    service_repo_name: str | None = None
    runtime_repo_name: str | None = None
    supports_config_sync: bool = False
    supports_reload: bool = False
    supports_chat: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "requested_name": self.requested_name,
            "component_name": self.component_name,
            "backend_target": self.backend_target,
            "component_type": self.component_type,
            "service_repo_name": self.service_repo_name,
            "runtime_repo_name": self.runtime_repo_name,
            "supports_config_sync": self.supports_config_sync,
            "supports_reload": self.supports_reload,
            "supports_chat": self.supports_chat,
        }


_COMPONENT_INDEX = {
    "gateway": {
        "component_name": "gateway",
        "backend_target": "tools",
        "component_type": "gateway",
    },
    "openclaw": {
        "component_name": "openclaw-prod",
        "backend_target": "prod",
        "component_type": "runtime",
        "service_repo_name": "openclaw-prod",
        "runtime_repo_name": "openclaw-prod",
        "supports_config_sync": True,
        "supports_reload": True,
        "supports_chat": True,
    },
    "openclaw-prod": {
        "component_name": "openclaw-prod",
        "backend_target": "prod",
        "component_type": "runtime",
        "service_repo_name": "openclaw-prod",
        "runtime_repo_name": "openclaw-prod",
        "supports_config_sync": True,
        "supports_reload": True,
        "supports_chat": True,
    },
    "openclaw-dev": {
        "component_name": "openclaw-dev",
        "backend_target": "dev",
        "component_type": "runtime",
        "service_repo_name": "openclaw-dev",
        "runtime_repo_name": "openclaw-dev",
        "supports_config_sync": True,
        "supports_reload": True,
        "supports_chat": True,
    },
    "openclaw-test": {
        "component_name": "openclaw-test",
        "backend_target": "test",
        "component_type": "runtime",
        "service_repo_name": "openclaw-test",
        "runtime_repo_name": "openclaw-test",
        "supports_config_sync": True,
        "supports_reload": True,
        "supports_chat": True,
    },
    "opensearch": {
        "component_name": "opensearch",
        "backend_target": "opensearch",
        "component_type": "service",
        "service_repo_name": "opensearch",
        "runtime_repo_name": "opensearch",
    },
    "caddy": {
        "component_name": "caddy",
        "backend_target": "ssl",
        "component_type": "service",
        "service_repo_name": "caddy",
        "runtime_repo_name": "caddy",
    },
    "ollama": {
        "component_name": "ollama",
        "backend_target": "ollama",
        "component_type": "service",
        "service_repo_name": "ollama",
        "runtime_repo_name": "ollama",
    },
    "tools": {
        "component_name": "gateway",
        "backend_target": "tools",
        "component_type": "gateway",
    },
}


COMPONENT_COMMANDS = {
    "gateway": {"health", "status", "inspect", "logs", "update", "rollback"},
    "runtime": {"status", "inspect", "logs", "start", "stop", "restart", "reload", "doctor", "monitor", "chat"},
    "service": {"status", "inspect", "logs", "start", "stop", "restart", "doctor"},
}


def try_resolve_component(name: str) -> ComponentSpec | None:
    raw = name.strip().lower()
    definition = _COMPONENT_INDEX.get(raw)
    if definition is None:
        return None
    return ComponentSpec(requested_name=name, **definition)


def resolve_component(name: str) -> ComponentSpec:
    resolved = try_resolve_component(name)
    if resolved is not None:
        return resolved
    raise ValidationError(
        f"component '{name}' is not supported by the gateway refactor mapping",
        "use `moltbox service list` to inspect known services or add a component mapping before retrying",
        component=name,
    )


def ensure_component_command(component: ComponentSpec, command: str) -> None:
    allowed = COMPONENT_COMMANDS.get(component.component_type, set())
    if command in allowed:
        return
    raise ValidationError(
        f"component '{component.requested_name}' does not support command '{command}'",
        "run `moltbox --help` and choose a command that matches the component type",
        component=component.as_dict(),
        command=command,
        allowed_commands=sorted(allowed),
    )
