from __future__ import annotations

import json
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


def test_runtime_chat_uses_openclaw_agent_gateway_path(monkeypatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []
    runtime_root = tmp_path / "runtime-root"
    debug_dir = runtime_root / "semantic-router-debug"
    debug_dir.mkdir(parents=True)

    monkeypatch.setattr(host_tool_cli, "_docker_available", lambda: True)

    def fake_run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        session_id = command[8]
        debug_payload = {
            "packet": {
                "response": {
                    "status": "answer",
                    "answer": "pong",
                    "telemetry": {
                        "answering_model": "qwen3:8b",
                        "answering_provider": "ollama",
                        "stages": [
                            {
                                "stage": "local",
                                "provider": "ollama",
                                "model": "qwen3:8b",
                                "decision": "answer",
                                "duration_ms": 17,
                                "tokens_in": 32,
                                "tokens_out": 4,
                            }
                        ],
                    },
                }
            },
            "telemetry": {
                "answering_model": "qwen3:8b",
                "answering_provider": "ollama",
                "stages": [
                    {
                        "stage": "local",
                        "provider": "ollama",
                        "model": "qwen3:8b",
                        "decision": "answer",
                        "duration_ms": 17,
                        "tokens_in": 32,
                        "tokens_out": 4,
                    }
                ],
            },
            "stages": [],
        }
        (debug_dir / f"{session_id}.json").write_text(json.dumps(debug_payload), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, json.dumps({"reply": "pong"}), "")

    monkeypatch.setattr(host_tool_cli, "_run_command", fake_run_command)

    result = host_tool_cli._runtime_chat(
        {
            "target": "test",
            "container_names": ["openclaw-test"],
            "message": "ping",
            "timeout_seconds": 25,
            "runtime_root": str(runtime_root),
            "session_id": "runtime-chat-smoke",
        }
    )

    assert result["ok"] is True
    assert len(commands) == 1
    assert commands[0][:5] == [
        "docker",
        "exec",
        "openclaw-test",
        "openclaw",
        "agent",
    ]
    assert commands[0][5:11] == ["--agent", "main", "--session-id", "runtime-chat-smoke", "--message", "ping"]
    assert "--local" not in commands[0]
    assert result["details"]["cli_output"]["reply"] == "pong"
    response = result["details"]["semantic_router"]["packet"]["response"]
    assert response["status"] == "answer"
    assert response["answer"] == "pong"
    assert result["details"]["semantic_router"]["telemetry"]["answering_model"] == "qwen3:8b"
    assert result["details"]["semantic_router"]["telemetry"]["answering_provider"] == "ollama"


def test_runtime_chat_reads_debug_artifact_for_escalated_turn(monkeypatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []
    runtime_root = tmp_path / "runtime-root"
    debug_dir = runtime_root / "semantic-router-debug"
    debug_dir.mkdir(parents=True)

    monkeypatch.setattr(host_tool_cli, "_docker_available", lambda: True)

    def fake_run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        session_id = command[8]
        debug_payload = {
            "packet": {
                "ledger": [
                    {"kind": "preflight"},
                    {"kind": "semantic_stage", "stage": "local", "decision": "escalate", "provider": "ollama"},
                    {"kind": "semantic_stage", "stage": "reasoning", "decision": "answer", "provider": "together"},
                ],
                "response": {
                    "status": "answer",
                    "answer": "final answer",
                    "telemetry": {
                        "answering_model": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
                        "answering_provider": "together",
                        "stages": [
                            {
                                "stage": "local",
                                "provider": "ollama",
                                "model": "qwen3:8b",
                                "decision": "escalate",
                                "duration_ms": 25,
                                "tokens_in": 90,
                                "tokens_out": 8,
                            },
                            {
                                "stage": "reasoning",
                                "provider": "together",
                                "model": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
                                "decision": "answer",
                                "duration_ms": 210,
                                "tokens_in": 120,
                                "tokens_out": 14,
                            },
                        ],
                    },
                },
            },
            "telemetry": {
                "answering_model": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
                "answering_provider": "together",
                "stages": [
                    {
                        "stage": "local",
                        "provider": "ollama",
                        "model": "qwen3:8b",
                        "decision": "escalate",
                        "duration_ms": 25,
                        "tokens_in": 90,
                        "tokens_out": 8,
                    },
                    {
                        "stage": "reasoning",
                        "provider": "together",
                        "model": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
                        "decision": "answer",
                        "duration_ms": 210,
                        "tokens_in": 120,
                        "tokens_out": 14,
                    },
                ],
            },
            "stages": [
                {"stage": "local", "rawContent": "{\"decision\":\"escalate\"}"},
                {"stage": "reasoning", "rawContent": "{\"decision\":\"answer\",\"answer\":\"final answer\"}"},
            ],
        }
        (debug_dir / f"{session_id}.json").write_text(json.dumps(debug_payload), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, json.dumps({"reply": "final answer"}), "")

    monkeypatch.setattr(host_tool_cli, "_run_command", fake_run_command)

    result = host_tool_cli._runtime_chat(
        {
            "target": "test",
            "container_names": ["openclaw-test"],
            "message": "plan this carefully",
            "timeout_seconds": 25,
            "runtime_root": str(runtime_root),
            "session_id": "runtime-chat-escalated",
        }
    )

    assert result["ok"] is True
    assert len(commands) == 1
    packet = result["details"]["semantic_router"]["packet"]
    assert packet["response"]["status"] == "answer"
    assert packet["response"]["answer"] == "final answer"
    assert packet["response"]["telemetry"]["answering_model"] == "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8"
    assert packet["response"]["telemetry"]["answering_provider"] == "together"
    semantic_entries = [entry for entry in packet["ledger"] if entry["kind"] == "semantic_stage"]
    assert [entry["decision"] for entry in semantic_entries] == ["escalate", "answer"]
    assert [entry["provider"] for entry in semantic_entries] == ["ollama", "together"]


def test_runtime_chat_falls_back_to_actual_agent_session_id(monkeypatch, tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime-root"
    debug_dir = runtime_root / "semantic-router-debug"
    debug_dir.mkdir(parents=True)

    monkeypatch.setattr(host_tool_cli, "_docker_available", lambda: True)

    def fake_run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
        actual_session_id = "actual-openclaw-session"
        debug_payload = {
            "packet": {
                "response": {
                    "status": "answer",
                    "answer": "shared lifecycle path",
                    "telemetry": {
                        "answering_model": "qwen3:8b",
                        "answering_provider": "ollama",
                        "stages": [
                            {
                                "stage": "local",
                                "provider": "ollama",
                                "model": "qwen3:8b",
                                "decision": "answer",
                                "duration_ms": 41,
                                "tokens_in": 20,
                                "tokens_out": 5,
                            }
                        ],
                    },
                }
            },
            "telemetry": {
                "answering_model": "qwen3:8b",
                "answering_provider": "ollama",
                "stages": [
                    {
                        "stage": "local",
                        "provider": "ollama",
                        "model": "qwen3:8b",
                        "decision": "answer",
                        "duration_ms": 41,
                        "tokens_in": 20,
                        "tokens_out": 5,
                    }
                ],
            },
            "stages": [],
        }
        (debug_dir / f"{actual_session_id}.json").write_text(json.dumps(debug_payload), encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "result": {
                        "meta": {
                            "agentMeta": {
                                "sessionId": actual_session_id,
                            }
                        }
                    }
                }
            ),
            "",
        )

    monkeypatch.setattr(host_tool_cli, "_run_command", fake_run_command)

    result = host_tool_cli._runtime_chat(
        {
            "target": "dev",
            "container_names": ["openclaw-dev"],
            "message": "hello",
            "timeout_seconds": 25,
            "runtime_root": str(runtime_root),
            "session_id": "requested-session-id",
        }
    )

    assert result["ok"] is True
    assert result["details"]["actual_session_id"] == "actual-openclaw-session"
    assert result["details"]["debug_session_id"] == "actual-openclaw-session"
    assert result["details"]["semantic_router"]["packet"]["response"]["answer"] == "shared lifecycle path"


def test_runtime_chat_prefers_matching_turn_artifact_when_latest_session_file_is_stale(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "runtime-root"
    debug_dir = runtime_root / "semantic-router-debug"
    debug_dir.mkdir(parents=True)

    monkeypatch.setattr(host_tool_cli, "_docker_available", lambda: True)

    stale_payload = {
        "prompt": "old prompt",
        "packet": {"response": {"status": "answer", "answer": "stale"}},
        "written_at": "2026-03-11T05:00:00Z",
    }
    turn_payload = {
        "prompt": "Need the real answer for this turn",
        "packet": {"response": {"status": "answer", "answer": "fresh"}},
        "telemetry": {"answering_model": "qwen3:8b", "answering_provider": "ollama"},
        "written_at": "2999-01-01T00:00:00Z",
    }
    (debug_dir / "actual-openclaw-session.json").write_text(json.dumps(stale_payload), encoding="utf-8")
    (debug_dir / "actual-openclaw-session__turn-123.json").write_text(
        json.dumps(turn_payload),
        encoding="utf-8",
    )

    def fake_run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "result": {
                        "meta": {
                            "agentMeta": {
                                "sessionId": "actual-openclaw-session",
                            }
                        }
                    }
                }
            ),
            "",
        )

    monkeypatch.setattr(host_tool_cli, "_run_command", fake_run_command)

    result = host_tool_cli._runtime_chat(
        {
            "target": "dev",
            "container_names": ["openclaw-dev"],
            "message": "Need the real answer for this turn",
            "timeout_seconds": 25,
            "runtime_root": str(runtime_root),
            "session_id": "requested-session-id",
        }
    )

    assert result["ok"] is True
    assert result["details"]["semantic_router"]["packet"]["response"]["answer"] == "fresh"
    assert result["details"]["debug_artifact_path"].endswith("actual-openclaw-session__turn-123.json")
