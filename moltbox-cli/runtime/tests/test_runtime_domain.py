from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


CLI_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = CLI_ROOT / "tools" / "src"


sys.path.insert(0, str(SRC_DIR))

from moltbox_cli.config import resolve_config
from moltbox_cli.deployment_service import render_assets
from moltbox_cli.deployment_service import runtime_lifecycle
from moltbox_cli.jsonio import read_json_file
from moltbox_cli.jsonio import write_json_file
from moltbox_cli.registry import get_target
from moltbox_cli.runtime_config import seed_runtime_root_config


class Args:
    config_path = None
    state_root = None
    runtime_artifacts_root = None
    internal_host = None
    internal_port = None
    cli_path = None


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


def test_runtime_render_uses_runtime_container_assets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    config = resolve_config(Args())
    payload = render_assets(config, "dev")
    assert payload["ok"] is True
    assert payload["command"] == "moltbox runtime dev deploy"
    rendered = payload["render"]
    asset_path = rendered["asset_path"].replace("/", "\\")
    config_path = rendered["config_path"].replace("/", "\\")
    assert "moltbox\\containers\\runtimes\\openclaw" in asset_path
    assert "moltbox\\config" in config_path
    assert Path(rendered["output_dir"]) == config.layout.deploy_dir / "rendered" / "dev" / "dev"
    assert Path(rendered["rendered_runtime_root_dir"]) == Path(rendered["output_dir"]) / "runtime-root"
    assert rendered["gateway_port"] == "18789"
    manifest = read_json_file(Path(rendered["render_manifest_path"]))
    source_paths = [path.replace("/", "\\") for path in manifest["source_asset_paths"]]
    config_source_paths = [path.replace("/", "\\") for path in manifest["source_config_paths"]]
    assert source_paths
    assert config_source_paths
    assert all("moltbox\\containers\\runtimes\\openclaw" in path for path in source_paths)
    assert any(path.endswith("moltbox\\config\\openclaw.json") for path in config_source_paths)
    assert any(path.endswith("moltbox\\config\\openclaw\\agents.yaml") for path in config_source_paths)
    compose_text = (Path(rendered["output_dir"]) / "compose.yml").read_text(encoding="utf-8")
    assert "./config/openclaw:/app/config/openclaw:ro" not in compose_text
    runtime_root_dir = Path(rendered["rendered_runtime_root_dir"])
    assert (runtime_root_dir / "openclaw.json").exists()
    assert (runtime_root_dir / "agents.yaml").exists()
    assert (runtime_root_dir / "routing.yaml").exists()
    target = get_target(config, "dev")
    assert Path(target.runtime_root) == config.layout.runtime_artifacts_root / "openclaw" / "dev"


def test_runtime_root_config_seeds_allowed_origins(tmp_path: Path, monkeypatch) -> None:
    runtime_root = tmp_path / "runtime-root"
    runtime_root.mkdir(parents=True)
    (runtime_root / "openclaw.json").write_text(
        json.dumps({"gateway": {"controlUi": {"allowedOrigins": ["http://existing.local"]}}}, indent=2) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MOLTBOX_PUBLIC_HOST_IP", "192.168.1.189")
    monkeypatch.setenv("MOLTBOX_PUBLIC_HOSTNAME", "moltbox-prime")
    monkeypatch.setenv("MOLTBOX_OPENCLAW_ALLOWED_ORIGINS_EXTRA", "https://console.example")

    seed_runtime_root_config(runtime_root, "28789")

    payload = json.loads((runtime_root / "openclaw.json").read_text(encoding="utf-8"))
    allowed_origins = payload["gateway"]["controlUi"]["allowedOrigins"]
    assert payload["gateway"]["mode"] == "local"
    assert "http://existing.local" in allowed_origins
    assert "http://127.0.0.1:28789" in allowed_origins
    assert "http://192.168.1.189:28789" in allowed_origins
    assert "http://moltbox-prime:28789" in allowed_origins
    assert "https://console.example" in allowed_origins


def test_runtime_root_config_preserves_live_gateway_auth_and_origins(tmp_path: Path, monkeypatch) -> None:
    rendered_root = tmp_path / "rendered-root"
    live_root = tmp_path / "live-root"
    rendered_root.mkdir(parents=True)
    live_root.mkdir(parents=True)
    (rendered_root / "openclaw.json").write_text(
        json.dumps({"gateway": {"controlUi": {"allowedOrigins": []}}, "tools": {"deny": ["group:web"]}}, indent=2) + "\n",
        encoding="utf-8",
    )
    (live_root / "openclaw.json").write_text(
        json.dumps(
            {
                "gateway": {
                    "auth": {"token": "existing-token"},
                    "controlUi": {"allowedOrigins": ["https://already.example"]},
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MOLTBOX_PUBLIC_HOST_IP", "192.168.1.189")
    monkeypatch.setenv("MOLTBOX_PUBLIC_HOSTNAME", "moltbox-prime")

    seed_runtime_root_config(rendered_root, "28789", existing_runtime_root_dir=live_root)

    payload = json.loads((rendered_root / "openclaw.json").read_text(encoding="utf-8"))
    assert payload["gateway"]["auth"]["token"] == "existing-token"
    assert "https://already.example" in payload["gateway"]["controlUi"]["allowedOrigins"]


def test_runtime_lifecycle_reports_new_cli_command(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    config = resolve_config(Args())

    def fake_run_primitive(config_arg, name: str, payload: dict[str, object]) -> dict[str, object]:
        if name == "render_assets":
            return {
                "ok": True,
                "details": {"output_dir": str(config.layout.deploy_dir / "rendered" / "dev" / "dev")},
            }
        if name == "restart_runtime":
            return {"ok": True, "stdout": "", "stderr": "", "details": {}}
        if name == "tail_target_logs":
            return {"ok": True, "details": {"log_tail": "ready"}}
        raise AssertionError(name)

    monkeypatch.setattr("moltbox_cli.deployment_service.run_primitive", fake_run_primitive)
    payload = runtime_lifecycle(config, "dev", "restart")
    assert payload["command"] == "moltbox runtime dev restart"
    assert payload["ok"] is True


def test_runtime_cli_repairs_unrelated_corrupt_host_registry(tmp_path: Path) -> None:
    state_root = tmp_path / ".remram"
    runtime_root = tmp_path / "Moltbox"
    targets_dir = state_root / "state" / "targets"
    targets_dir.mkdir(parents=True, exist_ok=True)
    write_json_file(
        targets_dir / "ollama.json",
        {
            "id": "ollama",
            "target_class": "shared_service",
            "display_name": "Ollama",
            "created_at": "2026-03-10T00:00:00+00:00",
            "updated_at": "2026-03-10T00:00:00+00:00",
            "metadata": {},
            "service_name": "ollama",
            "container_name": "ollama",
        },
    )

    completed = run_cli(
        "runtime",
        "dev",
        "status",
        env={
            "MOLTBOX_STATE_ROOT": str(state_root),
            "MOLTBOX_RUNTIME_ROOT": str(runtime_root),
        },
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["requested_target"] == "dev"
    repaired = read_json_file(targets_dir / "ollama.json")
    assert repaired["asset_path"] == "shared-services/ollama"


def test_runtime_cli_rejects_reversed_runtime_grammar(tmp_path: Path) -> None:
    completed = run_cli(
        "runtime",
        "start",
        "dev",
        env={
            "MOLTBOX_STATE_ROOT": str(tmp_path / ".remram"),
            "MOLTBOX_RUNTIME_ROOT": str(tmp_path / "Moltbox"),
        },
    )

    assert completed.returncode != 0
    assert "invalid choice" in completed.stderr
