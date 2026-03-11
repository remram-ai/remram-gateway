from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "src"))

from moltbox_cli.config import resolve_config
from moltbox_cli.deployment_service import render_assets
from moltbox_cli.jsonio import read_json_file, write_json_file
from moltbox_cli.registry import get_target
from moltbox_cli.registry_store import target_file_path


class Args:
    config_path = None
    state_root = None
    runtime_artifacts_root = None
    internal_host = None
    internal_port = None
    cli_path = None


def test_tools_render_uses_tools_container_assets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    config = resolve_config(Args())
    payload = render_assets(config, "tools")
    assert payload["ok"] is True
    assert payload["target"] == "tools"
    assert payload["command"] == "moltbox tools update"
    rendered = payload["render"]
    asset_path = rendered["asset_path"].replace("/", "\\")
    assert "moltbox\\containers\\tools" in asset_path
    assert Path(rendered["output_dir"]) == config.layout.deploy_dir / "rendered" / "shared" / "tools"
    compose_text = (Path(rendered["output_dir"]) / "compose.yml").read_text(encoding="utf-8")
    assert "image: \"${MOLTBOX_TOOLS_IMAGE:-moltbox-tools:local}\"" in compose_text
    assert "build:" in compose_text
    assert "dockerfile:" in compose_text
    assert "container_name: \"moltbox-tools\"" in compose_text
    assert "user: \"" in compose_text
    assert "group_add:" in compose_text
    assert "MOLTBOX_INTERNAL_HOST: \"0.0.0.0\"" in compose_text
    assert "MOLTBOX_CONFIG_PATH: " in compose_text
    assert "MOLTBOX_POLICY_PATH: " in compose_text
    assert "MOLTBOX_TOOLS_PORT: " in compose_text
    assert "/var/run/docker.sock:/var/run/docker.sock" in compose_text
    assert rendered["config_path"].replace("/", "\\").endswith("moltbox\\config\\control-plane-policy.yaml")
    assert Path(rendered["rendered_config_path"]).name == "control-plane-policy.yaml"
    manifest = read_json_file(Path(rendered["render_manifest_path"]))
    source_paths = [path.replace("/", "\\") for path in manifest["source_asset_paths"]]
    assert source_paths
    assert all("moltbox\\containers\\tools" in path for path in source_paths)


def test_registry_bootstrap_reconciles_stale_tools_asset_paths(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    config = resolve_config(Args())
    config.layout.target_registry_dir.mkdir(parents=True, exist_ok=True)
    stale_path = target_file_path(config.layout, "tools")
    write_json_file(
        stale_path,
        {
            "id": "tools",
            "target_class": "tools",
            "display_name": "MoltBox Tools",
            "asset_path": "control-plane",
            "compose_project": "moltbox-control-plane",
            "container_names": ["control-plane"],
            "snapshot_scope": "target",
            "validator_key": "container_baseline",
            "log_source": "docker_logs",
            "runtime_root": str(config.layout.control_plane_dir),
            "service_name": "tools",
            "container_name": "control-plane",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "metadata": {"aliases": ["cli"], "hostname": "moltbox-cli"},
        },
    )

    target = get_target(config, "tools")
    stored = read_json_file(stale_path)

    assert target.asset_path == "tools"
    assert target.compose_project == "moltbox-tools"
    assert target.container_name == "moltbox-tools"
    assert target.container_names == ["moltbox-tools"]
    assert stored["asset_path"] == "tools"
    assert stored["compose_project"] == "moltbox-tools"
    assert stored["container_name"] == "moltbox-tools"
    assert stored["container_names"] == ["moltbox-tools"]
    assert stored["metadata"]["aliases"] == ["cli", "control", "control-plane"]


def test_registry_bootstrap_migrates_legacy_caddy_target(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    config = resolve_config(Args())
    config.layout.target_registry_dir.mkdir(parents=True, exist_ok=True)
    legacy_path = target_file_path(config.layout, "caddy")
    write_json_file(
        legacy_path,
        {
            "id": "caddy",
            "target_class": "shared_service",
            "display_name": "Caddy",
            "asset_path": "shared-services/caddy",
            "compose_project": "moltbox",
            "container_names": ["moltbox-caddy"],
            "snapshot_scope": "target",
            "validator_key": "container_baseline",
            "log_source": "docker_logs",
            "runtime_root": str(config.layout.shared_dir),
            "service_name": "caddy",
            "container_name": "moltbox-caddy",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "metadata": {},
        },
    )

    target = get_target(config, "caddy")
    stored = read_json_file(target_file_path(config.layout, "ssl"))

    assert target.id == "ssl"
    assert stored["id"] == "ssl"
    assert stored["asset_path"] == "shared-services/ssl"
    assert stored["container_names"] == ["moltbox-caddy"]
    assert "caddy" in stored["metadata"]["aliases"]
    assert not legacy_path.exists()
