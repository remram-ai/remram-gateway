from __future__ import annotations

import ipaddress
import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROD_RUNTIME_ROOT = Path.home() / ".openclaw"
TEST_RUNTIME_ROOT = Path.home() / ".openclaw-test"
PROD_REPO_ROOT = Path.home() / "git" / "remram-gateway"
TEST_REPO_ROOT = Path.home() / "git" / "remram-gateway-test"
DEFAULT_ALLOWED_CLIENTS = ("codex", "vscode-agent")


@dataclass(frozen=True)
class RuntimeContext:
    name: str
    runtime_root: Path
    repo_root: Path
    script_dir: Path
    config_dir: Path
    remote_script_dir: Path
    env_file: Path
    container_env_file: Path
    openclaw_config_file: Path
    debug_root: Path
    debug_config_file: Path
    debug_clients_file: Path
    jobs_dir: Path
    flows_dir: Path
    artifacts_dir: Path
    compose_file: Path
    env_values: dict[str, str]
    container_env_values: dict[str, str]
    openclaw_values: dict[str, Any]

    @property
    def compose_project_name(self) -> str:
        return self.env_values.get("COMPOSE_PROJECT_NAME", "moltbox" if self.name == "prod" else "moltbox-test")

    @property
    def gateway_port(self) -> int:
        return int(self.env_values.get("GATEWAY_PORT", "18789" if self.name == "prod" else "18790"))

    @property
    def debug_service_token(self) -> str:
        return self.env_values.get("DEBUG_SERVICE_TOKEN", "")

    @property
    def debug_service_port(self) -> int:
        return int(self.env_values.get("DEBUG_SERVICE_PORT", "18890" if self.name == "prod" else "18891"))

    @property
    def openclaw_container_name(self) -> str:
        return self.env_values.get(
            "OPENCLAW_CONTAINER_NAME",
            "moltbox-openclaw" if self.name == "prod" else "moltbox-test-openclaw",
        )

    @property
    def ollama_container_name(self) -> str:
        return self.env_values.get(
            "OLLAMA_CONTAINER_NAME",
            "moltbox-ollama" if self.name == "prod" else "moltbox-test-ollama",
        )

    @property
    def opensearch_container_name(self) -> str:
        return self.env_values.get(
            "OPENSEARCH_CONTAINER_NAME",
            "moltbox-opensearch" if self.name == "prod" else "moltbox-test-opensearch",
        )

    @property
    def lan_cidr(self) -> str:
        return self.env_values.get("LAN_CIDR", "192.168.1.0/24")

    @property
    def allowed_origins(self) -> list[str]:
        raw = self.openclaw_values.get("gateway", {}).get("controlUi", {}).get("allowedOrigins", [])
        return [item for item in raw if isinstance(item, str)]

    @property
    def base_env(self) -> dict[str, str]:
        merged = os.environ.copy()
        merged["MOLTBOX_RUNTIME_ROOT"] = str(self.runtime_root)
        return merged


@dataclass(frozen=True)
class ClientRule:
    client_id: str
    allowed_origins: tuple[str, ...]
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class ServiceConfig:
    host: str
    port: int
    lan_cidr: str
    job_retention_hours: int
    max_log_lines: int
    default_timeout_seconds: int
    long_timeout_seconds: int
    artifact_ttl_hours: int
    clients: dict[str, ClientRule]


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = _strip_env_value(value.strip())
    return values


def _strip_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        try:
            return shlex.split(value)[0]
        except (ValueError, IndexError):
            return value[1:-1]
    return value


def load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def build_runtime(name: str) -> RuntimeContext:
    if name not in {"prod", "test"}:
        raise ValueError(f"Unsupported runtime: {name}")

    runtime_root = PROD_RUNTIME_ROOT if name == "prod" else TEST_RUNTIME_ROOT
    repo_root = PROD_REPO_ROOT if name == "prod" else TEST_REPO_ROOT
    env_file = runtime_root / ".env"
    container_env_file = runtime_root / "container.env"
    openclaw_config_file = runtime_root / "openclaw.json"
    debug_root = runtime_root / "debug-service"
    return RuntimeContext(
        name=name,
        runtime_root=runtime_root,
        repo_root=repo_root,
        script_dir=repo_root / "moltbox" / "scripts",
        config_dir=repo_root / "moltbox" / "config",
        remote_script_dir=repo_root / "moltbox" / "remote" / "exec",
        env_file=env_file,
        container_env_file=container_env_file,
        openclaw_config_file=openclaw_config_file,
        debug_root=debug_root,
        debug_config_file=debug_root / "config.json",
        debug_clients_file=debug_root / "clients.json",
        jobs_dir=debug_root / "jobs",
        flows_dir=debug_root / "flows",
        artifacts_dir=debug_root / "artifacts",
        compose_file=repo_root / "moltbox" / "config" / "docker-compose.yml",
        env_values=parse_env_file(env_file),
        container_env_values=parse_env_file(container_env_file),
        openclaw_values=load_json(openclaw_config_file, {}),
    )


def load_service_config(runtime: RuntimeContext) -> ServiceConfig:
    raw = load_json(runtime.debug_config_file, {})
    host = str(raw.get("host", "0.0.0.0"))
    port = int(raw.get("port", runtime.debug_service_port))
    lan_cidr = str(raw.get("lan_cidr", runtime.lan_cidr))
    job_retention_hours = int(raw.get("job_retention_hours", 24))
    max_log_lines = int(raw.get("max_log_lines", 400))
    timeouts = raw.get("timeouts", {}) if isinstance(raw.get("timeouts"), dict) else {}
    default_timeout_seconds = int(timeouts.get("default_seconds", 120))
    long_timeout_seconds = int(timeouts.get("long_seconds", 1800))
    artifact_ttl_hours = int(raw.get("artifact_ttl_hours", 48))
    client_data = load_json(runtime.debug_clients_file, {})
    rules: dict[str, ClientRule] = {}
    for item in client_data.get("allowed_clients", []) if isinstance(client_data, dict) else []:
        if not isinstance(item, dict):
            continue
        client_id = item.get("id")
        if not isinstance(client_id, str) or not client_id:
            continue
        origins = tuple(origin for origin in item.get("allowed_origins", []) if isinstance(origin, str))
        scopes = tuple(scope for scope in item.get("scopes", ["*"]) if isinstance(scope, str))
        rules[client_id] = ClientRule(client_id=client_id, allowed_origins=origins, scopes=scopes)

    if not rules:
        for client_id in DEFAULT_ALLOWED_CLIENTS:
            rules[client_id] = ClientRule(client_id=client_id, allowed_origins=(), scopes=("*",))

    return ServiceConfig(
        host=host,
        port=port,
        lan_cidr=lan_cidr,
        job_retention_hours=job_retention_hours,
        max_log_lines=max_log_lines,
        default_timeout_seconds=default_timeout_seconds,
        long_timeout_seconds=long_timeout_seconds,
        artifact_ttl_hours=artifact_ttl_hours,
        clients=rules,
    )


def ensure_runtime_dirs(runtime: RuntimeContext) -> None:
    runtime.debug_root.mkdir(parents=True, exist_ok=True)
    runtime.jobs_dir.mkdir(parents=True, exist_ok=True)
    runtime.flows_dir.mkdir(parents=True, exist_ok=True)
    runtime.artifacts_dir.mkdir(parents=True, exist_ok=True)


def source_ip_allowed(host: str, cidr: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
        network = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False
    return address in network or address.is_loopback
