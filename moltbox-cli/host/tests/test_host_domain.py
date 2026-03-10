from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "src"))

from moltbox_cli.config import resolve_config
from moltbox_cli.deployment_service import host_lifecycle
from moltbox_cli.deployment_service import render_assets
from moltbox_cli.jsonio import read_json_file
from moltbox_cli.registry import get_target


class Args:
    config_path = None
    state_root = None
    runtime_artifacts_root = None
    internal_host = None
    internal_port = None
    cli_path = None


def test_host_render_uses_moltbox_container_assets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    config = resolve_config(Args())
    payload = render_assets(config, "ollama")
    assert payload["ok"] is True
    assert payload["command"] == "moltbox host ollama deploy"
    rendered = payload["render"]
    asset_path = rendered["asset_path"].replace("/", "\\")
    assert "moltbox\\containers\\shared-services\\ollama" in asset_path
    assert Path(rendered["output_dir"]) == config.layout.deploy_dir / "rendered" / "shared" / "ollama"
    manifest = read_json_file(Path(rendered["render_manifest_path"]))
    source_paths = [path.replace("/", "\\") for path in manifest["source_asset_paths"]]
    assert source_paths
    assert all("moltbox\\containers\\shared-services\\ollama" in path for path in source_paths)
    compose_text = (Path(rendered["output_dir"]) / "compose.yml").read_text(encoding="utf-8")
    dockerfile_text = (Path(rendered["output_dir"]) / "Dockerfile").read_text(encoding="utf-8")
    assert "container_name: \"moltbox-ollama\"" in compose_text
    assert "image: \"${OLLAMA_IMAGE:-moltbox-ollama:local}\"" in compose_text
    assert "build:" in compose_text
    assert "OLLAMA_BASE_IMAGE" in compose_text
    assert "gpus: all" in compose_text
    assert "ports:" not in compose_text
    assert "external: true" in compose_text
    assert "name: \"moltbox_moltbox_internal\"" in compose_text
    assert "FROM ${OLLAMA_BASE_IMAGE}" in dockerfile_text
    target = get_target(config, "ollama")
    assert target.compose_project == "moltbox"
    assert target.container_names == ["moltbox-ollama"]
    assert Path(target.runtime_root) == Path.home() / ".openclaw"


def test_opensearch_render_uses_repo_config_and_legacy_service_identity(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    config = resolve_config(Args())
    payload = render_assets(config, "opensearch")
    assert payload["ok"] is True
    rendered = payload["render"]
    compose_text = (Path(rendered["output_dir"]) / "compose.yml").read_text(encoding="utf-8")
    dockerfile_text = (Path(rendered["output_dir"]) / "Dockerfile").read_text(encoding="utf-8")
    assert "container_name: \"moltbox-opensearch\"" in compose_text
    assert "image: \"${OPENSEARCH_IMAGE:-moltbox-opensearch:local}\"" in compose_text
    assert "build:" in compose_text
    assert "OPENSEARCH_BASE_IMAGE" in compose_text
    assert "./config/opensearch.yml:/usr/share/opensearch/config/opensearch.yml:ro" in compose_text
    assert "ports:" not in compose_text
    assert "external: true" in compose_text
    assert "name: \"moltbox_moltbox_internal\"" in compose_text
    assert "FROM ${OPENSEARCH_BASE_IMAGE}" in dockerfile_text
    assert rendered["config_path"].replace("/", "\\").endswith("moltbox\\config\\opensearch.yml")
    assert Path(rendered["rendered_config_path"]).name == "opensearch.yml"
    rendered_config = Path(rendered["rendered_config_path"]).read_text(encoding="utf-8")
    assert "cluster.name: remram-moltbox" in rendered_config
    target = get_target(config, "opensearch")
    assert target.compose_project == "moltbox"
    assert target.container_names == ["moltbox-opensearch"]


def test_host_lifecycle_reports_canonical_cli_command(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    config = resolve_config(Args())

    def fake_run_primitive(config_arg, name: str, payload: dict[str, object]) -> dict[str, object]:
        if name == "restart_target":
            return {"ok": True, "stdout": "", "stderr": "", "details": {}}
        if name == "tail_target_logs":
            return {"ok": True, "details": {"log_tail": "running"}}
        raise AssertionError(name)

    monkeypatch.setattr("moltbox_cli.deployment_service.run_primitive", fake_run_primitive)
    payload = host_lifecycle(config, "ollama", "restart")
    assert payload["command"] == "moltbox host ollama restart"
    assert payload["ok"] is True
