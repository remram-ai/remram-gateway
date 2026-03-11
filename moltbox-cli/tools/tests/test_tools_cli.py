from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


CLI_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = CLI_ROOT / "tools" / "src"

sys.path.insert(0, str(SRC_DIR))

from moltbox_cli.layout import find_repo_root


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    merged_env["PYTHONPATH"] = str(SRC_DIR) + os.pathsep + merged_env.get("PYTHONPATH", "")
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "moltbox_cli", *args],
        cwd=str(CLI_ROOT),
        capture_output=True,
        text=True,
        check=False,
        env=merged_env,
    )


def test_tools_version_returns_json() -> None:
    completed = run_cli("tools", "version")
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert "version" in payload


def test_help_lists_only_canonical_domains() -> None:
    completed = run_cli("--help")
    assert completed.returncode == 0
    assert "usage: moltbox" in completed.stdout
    assert "{tools,host,runtime}" in completed.stdout
    assert "==SUPPRESS==" not in completed.stdout
    assert "render-assets" not in completed.stdout
    assert "list-targets" not in completed.stdout


def test_tools_inspect_lists_targets(tmp_path: Path) -> None:
    env = {
        "MOLTBOX_STATE_ROOT": str(tmp_path / ".remram"),
        "MOLTBOX_RUNTIME_ROOT": str(tmp_path / "Moltbox"),
    }
    completed = run_cli("tools", "inspect", env=env)
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    target_ids = {target["id"] for target in payload["targets"]}
    assert {"tools", "ollama", "dev", "ssl"}.issubset(target_ids)
    assert "caddy" not in target_ids
    assert "control-plane" not in target_ids


def test_tools_inspect_reconciles_legacy_registry(tmp_path: Path) -> None:
    state_root = tmp_path / ".remram"
    runtime_root = tmp_path / "Moltbox"
    target_dir = state_root / "state" / "targets"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "control-plane.json").write_text(
        json.dumps(
            {
                "id": "control-plane",
                "display_name": "MoltBox Tools",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "metadata": {"aliases": ["cli", "control"]},
            }
        ),
        encoding="utf-8",
    )
    (target_dir / "ollama.json").write_text(
        json.dumps(
            {
                "id": "ollama",
                "target_class": "shared_service",
                "display_name": "Ollama",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "metadata": {},
                "service_name": "ollama",
                "container_name": "ollama",
            }
        ),
        encoding="utf-8",
    )
    (target_dir / "caddy.json").write_text(
        json.dumps(
            {
                "id": "caddy",
                "display_name": "Caddy",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )

    completed = run_cli(
        "tools",
        "inspect",
        env={
            "MOLTBOX_STATE_ROOT": str(state_root),
            "MOLTBOX_RUNTIME_ROOT": str(runtime_root),
        },
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    target_ids = {target["id"] for target in payload["targets"]}
    assert "tools" in target_ids
    assert "ssl" in target_ids
    assert "control-plane" not in target_ids

    stored_tools = json.loads((target_dir / "tools.json").read_text(encoding="utf-8"))
    stored_ollama = json.loads((target_dir / "ollama.json").read_text(encoding="utf-8"))
    stored_ssl = json.loads((target_dir / "ssl.json").read_text(encoding="utf-8"))
    assert stored_tools["id"] == "tools"
    assert stored_tools["asset_path"] == "tools"
    assert "control-plane" in stored_tools["metadata"]["aliases"]
    assert stored_ollama["compose_project"] == "moltbox"
    assert stored_ollama["container_names"] == ["moltbox-ollama"]
    assert stored_ssl["id"] == "ssl"
    assert stored_ssl["container_names"] == ["moltbox-caddy"]
    assert "caddy" in stored_ssl["metadata"]["aliases"]
    assert not (target_dir / "control-plane.json").exists()
    assert not (target_dir / "caddy.json").exists()


def test_runtime_rejects_reversed_legacy_order(tmp_path: Path) -> None:
    completed = run_cli(
        "runtime",
        "start",
        "dev",
        env={
            "MOLTBOX_STATE_ROOT": str(tmp_path / ".remram"),
            "MOLTBOX_RUNTIME_ROOT": str(tmp_path / "Moltbox"),
        },
    )

    assert completed.returncode == 2
    assert "usage: moltbox runtime" in completed.stderr
    assert "invalid choice" in completed.stderr


def test_tools_deploy_is_not_exposed(tmp_path: Path) -> None:
    completed = run_cli(
        "tools",
        "deploy",
        env={
            "MOLTBOX_STATE_ROOT": str(tmp_path / ".remram"),
            "MOLTBOX_RUNTIME_ROOT": str(tmp_path / "Moltbox"),
        },
    )

    assert completed.returncode == 2
    assert "usage: moltbox tools" in completed.stderr
    assert "invalid choice" in completed.stderr


def test_find_repo_root_accepts_baked_container_checkout(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "remram-gateway"
    (repo_root / "moltbox").mkdir(parents=True)
    (repo_root / "moltbox-cli").mkdir(parents=True)
    monkeypatch.setenv("MOLTBOX_REPO_ROOT", str(repo_root))
    assert find_repo_root() == repo_root.resolve()
