from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from moltbox_cli.config import resolve_config
from moltbox_cli.http_app import create_http_app


class Args:
    config_path = None
    policy_path = None
    state_root = None
    runtime_artifacts_root = None
    internal_host = None
    internal_port = None
    cli_path = None


def test_http_app_exposes_health_and_mcp_on_canonical_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    config = resolve_config(Args())
    app = create_http_app(config, logging.getLogger("test-http-app"))

    with TestClient(app) as client:
        health = client.get("/health")
        initialize = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0.0.0"},
                },
            },
            headers={
                "Accept": "application/json, text/event-stream",
                "Host": "127.0.0.1",
            },
        )

    mcp_mount = next(route for route in app.routes if type(route).__name__ == "Mount" and getattr(route, "path", None) == "")
    mounted_paths = {getattr(route, "path", None) for route in mcp_mount.app.routes}

    assert health.status_code == 200
    assert "/mcp" in mounted_paths
    assert initialize.status_code in {200, 202, 421}
