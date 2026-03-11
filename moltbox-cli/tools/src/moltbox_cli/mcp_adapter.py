from __future__ import annotations

import json
import shutil
import subprocess
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .config import AppConfig
from .errors import MoltboxCliError
from .mcp_policy import allowed_host_verbs, allowed_runtime_verbs, allowed_tools_verbs, denial_payload, load_mcp_policy
from .target_resolution import resolve_target_identifier, target_domain


def _cli_base_command(config: AppConfig) -> list[str]:
    if len(config.cli_command) > 1:
        return list(config.cli_command)
    cli_path = config.cli_command[0]
    resolved = shutil.which(cli_path)
    if resolved:
        return [resolved]
    return [sys.executable, "-m", "moltbox_cli"]


def _config_flags(config: AppConfig) -> list[str]:
    return [
        "--config-path",
        str(config.config_path),
        "--policy-path",
        str(config.policy_path),
        "--state-root",
        str(config.state_root),
        "--runtime-artifacts-root",
        str(config.runtime_artifacts_root),
        "--internal-host",
        config.internal_host,
        "--internal-port",
        str(config.internal_port),
    ]


def invoke_cli_json(config: AppConfig, args: list[str]) -> dict[str, Any]:
    command = _cli_base_command(config) + _config_flags(config) + args
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    stdout = completed.stdout.strip()
    payload = json.loads(stdout) if stdout else {}
    if completed.returncode != 0:
        raise MoltboxCliError(
            error_type=str(payload.get("error_type") or "cli_error"),
            error_message=str(payload.get("error_message") or "cli invocation failed"),
            recovery_message=str(payload.get("recovery_message") or "inspect stderr and rerun the command"),
            details={
                "exit_code": completed.returncode,
                "stderr": completed.stderr.strip(),
                "command": command,
            },
        )
    if not isinstance(payload, dict):
        raise MoltboxCliError(
            error_type="invalid_cli_output",
            error_message="cli returned a non-object JSON payload",
            recovery_message="inspect the MoltBox CLI implementation and rerun the command",
            details={"command": command},
        )
    return payload


def _status_args(target: str) -> list[str]:
    resolved = resolve_target_identifier(target)
    domain = target_domain(resolved)
    if domain == "tools":
        return ["tools", "status"]
    return [domain, resolved, "status"]


def dispatch_tools_action(config: AppConfig, verb: str) -> dict[str, Any]:
    policy, source = load_mcp_policy(config)
    if verb not in allowed_tools_verbs(policy):
        return denial_payload(domain="tools", verb=verb, policy_source=source, target="tools")
    return invoke_cli_json(config, ["tools", verb])


def dispatch_host_action(config: AppConfig, service: str, verb: str) -> dict[str, Any]:
    policy, source = load_mcp_policy(config)
    if verb not in allowed_host_verbs(policy):
        return denial_payload(domain="host", verb=verb, policy_source=source, target=resolve_target_identifier(service))
    return invoke_cli_json(config, ["host", service, verb])


def dispatch_runtime_action(config: AppConfig, environment: str, verb: str) -> dict[str, Any]:
    resolved = resolve_target_identifier(environment)
    policy, source = load_mcp_policy(config)
    if verb not in allowed_runtime_verbs(policy, resolved):
        return denial_payload(domain="runtime", verb=verb, policy_source=source, environment=resolved)
    return invoke_cli_json(config, ["runtime", resolved, verb])


def create_mcp_server(config: AppConfig) -> FastMCP:
    policy, _ = load_mcp_policy(config)
    mcp = FastMCP(
        "MoltBox CLI",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    if "version" in allowed_tools_verbs(policy):
        @mcp.tool(description="Return the MoltBox CLI version information.")
        async def tools_version() -> dict:
            return dispatch_tools_action(config, "version")

    if "health" in allowed_tools_verbs(policy):
        @mcp.tool(description="Return the MoltBox CLI health model.")
        async def tools_health() -> dict:
            return dispatch_tools_action(config, "health")

    if "status" in allowed_tools_verbs(policy):
        @mcp.tool(description="Return the MoltBox tools status model.")
        async def tools_status() -> dict:
            return dispatch_tools_action(config, "status")

    if "inspect" in allowed_tools_verbs(policy):
        @mcp.tool(description="List the registered MoltBox targets.")
        async def tools_inspect() -> dict:
            return dispatch_tools_action(config, "inspect")

    if allowed_host_verbs(policy):
        @mcp.tool(description="Execute an allowed host action for a canonical shared-service target.")
        async def host_action(service: str, verb: str) -> dict:
            return dispatch_host_action(config, service, verb)

    if any(allowed_runtime_verbs(policy, env) for env in ("dev", "test", "prod")):
        @mcp.tool(description="Execute an allowed runtime action for a canonical environment target.")
        async def runtime_action(environment: str, verb: str) -> dict:
            return dispatch_runtime_action(config, environment, verb)

    return mcp
