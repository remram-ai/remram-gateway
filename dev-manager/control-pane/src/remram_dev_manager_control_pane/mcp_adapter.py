from __future__ import annotations

import json
import shutil
import subprocess
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from .config import AppConfig
from .errors import RemramError


def _cli_base_command(config: AppConfig) -> list[str]:
    if len(config.cli_command) > 1:
        return list(config.cli_command)
    cli_path = config.cli_command[0]
    resolved = shutil.which(cli_path)
    if resolved:
        return [resolved]
    return [sys.executable, "-m", "remram_dev_manager_control_pane"]


def invoke_cli_json(config: AppConfig, args: list[str]) -> dict[str, Any]:
    command = _cli_base_command(config) + args
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    stdout = completed.stdout.strip()
    payload = json.loads(stdout) if stdout else {}
    if completed.returncode != 0:
        raise RemramError(
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
        raise RemramError(
            error_type="invalid_cli_output",
            error_message="cli returned a non-object JSON payload",
            recovery_message="inspect the remram CLI implementation and rerun the command",
            details={"command": command},
        )
    return payload


def create_mcp_server(config: AppConfig) -> FastMCP:
    mcp = FastMCP("Remram Control Plane", stateless_http=True, json_response=True)

    @mcp.tool(description="Return the control-plane version information.")
    async def version() -> dict:
        return invoke_cli_json(config, ["version"])

    @mcp.tool(description="Return the control-plane health model.")
    async def health() -> dict:
        return invoke_cli_json(config, ["health"])

    @mcp.tool(description="List registered control-plane targets.")
    async def list_targets() -> dict:
        return invoke_cli_json(config, ["list-targets"])

    @mcp.tool(description="Read target status for a canonical target identifier.")
    async def status(target: str) -> dict:
        return invoke_cli_json(config, ["status", "--target", target])

    return mcp
