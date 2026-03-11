from __future__ import annotations

from pathlib import Path
import sys
import subprocess


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from moltbox_cli import host_tool_cli
from moltbox_cli.primitive_runner import PRIMITIVES


def test_validate_target_waits_for_starting_health(monkeypatch) -> None:
    states = iter(
        [
            [
                {
                    "name": "moltbox-ollama",
                    "present": True,
                    "container_id": "abc",
                    "state": "running",
                    "health": "starting",
                    "image": "ollama/ollama:0.6.8",
                    "mounts": [],
                }
            ],
            [
                {
                    "name": "moltbox-ollama",
                    "present": True,
                    "container_id": "abc",
                    "state": "running",
                    "health": "healthy",
                    "image": "ollama/ollama:0.6.8",
                    "mounts": [],
                }
            ],
        ]
    )

    monkeypatch.setattr(host_tool_cli, "_docker_available", lambda: True)
    monkeypatch.setattr(host_tool_cli, "_container_details", lambda names: next(states))
    monkeypatch.setattr(host_tool_cli.time, "sleep", lambda seconds: None)

    result = host_tool_cli._validate_target(
        {
            "target": "ollama",
            "validator_key": "container_baseline",
            "container_names": ["moltbox-ollama"],
            "validation_timeout_seconds": 1,
            "validation_poll_interval_seconds": 0,
        }
    )

    assert result["ok"] is True
    assert result["details"]["result"] == "pass"


def test_deploy_target_uses_build_when_requested(monkeypatch, tmp_path: Path) -> None:
    render_dir = tmp_path / "rendered"
    render_dir.mkdir()
    commands: list[list[str]] = []
    network_inspected = False

    monkeypatch.setattr(host_tool_cli, "_docker_available", lambda: True)
    monkeypatch.setattr(host_tool_cli, "_container_inspect", lambda name: {"Id": "abc"} if name == "moltbox-ollama" else None)

    def fake_run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
        nonlocal network_inspected
        commands.append(command)
        if command[:4] == ["docker", "network", "inspect", "moltbox_moltbox_internal"]:
            if network_inspected:
                return subprocess.CompletedProcess(command, 0, "[]", "")
            network_inspected = True
            return subprocess.CompletedProcess(command, 1, "", "not found")
        if command[:4] == ["docker", "network", "create", "moltbox_moltbox_internal"]:
            return subprocess.CompletedProcess(command, 0, "created", "")
        if command[:3] == ["docker", "rm", "-f"]:
            return subprocess.CompletedProcess(command, 0, "removed", "")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(host_tool_cli, "_run_command", fake_run_command)
    monkeypatch.setattr(host_tool_cli, "_run_compose_command", fake_run_command)
    monkeypatch.setattr(
        host_tool_cli,
        "_container_details",
        lambda names: [
            {
                "name": "moltbox-ollama",
                "present": True,
                "container_id": "abc",
                "state": "running",
                "health": "healthy",
                "image": "moltbox-ollama:local",
                "mounts": [],
            }
        ],
    )

    result = host_tool_cli._deploy_target(
        {
            "target": "ollama",
            "render_dir": str(render_dir),
            "compose_project": "moltbox",
            "container_names": ["moltbox-ollama"],
            "build_images": True,
            "remove_orphans": False,
            "replace_existing_containers": True,
            "force_recreate": True,
            "internal_network_name": "moltbox_moltbox_internal",
        }
    )

    assert result["ok"] is True
    assert commands
    assert commands[0][:4] == ["docker", "network", "inspect", "moltbox_moltbox_internal"]
    assert commands[1][:4] == ["docker", "network", "create", "moltbox_moltbox_internal"]
    assert commands[2][:3] == ["docker", "rm", "-f"]
    assert commands[3][-4:] == ["up", "-d", "--build", "--force-recreate"]


def test_deploy_target_primitive_accepts_build_images() -> None:
    assert "build_images" in PRIMITIVES["deploy_target"].allowed_payload_keys
    assert "replace_existing_containers" in PRIMITIVES["deploy_target"].allowed_payload_keys
    assert "force_recreate" in PRIMITIVES["deploy_target"].allowed_payload_keys


def test_compose_environment_strips_transient_deploy_overrides(monkeypatch) -> None:
    monkeypatch.setenv("OPENCLAW_IMAGE", "bad-image")
    monkeypatch.setenv("MOLTBOX_TOOLS_IMAGE", "bad-tools-image")
    monkeypatch.setenv("PATH", "test-path")

    env = host_tool_cli._compose_environment()

    assert env["PATH"] == "test-path"
    assert "OPENCLAW_IMAGE" not in env
    assert "MOLTBOX_TOOLS_IMAGE" not in env
