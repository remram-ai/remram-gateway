from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


CANONICAL_LOG_DIRS = (
    "tools",
    "openclaw-dev",
    "openclaw-test",
    "openclaw-prod",
    "ollama",
    "opensearch",
    "ssl",
    "caddy",
)


@dataclass(frozen=True)
class RepoLayout:
    repo_root: Path
    archive_dir: Path
    docs_dir: Path
    moltbox_dir: Path
    containers_dir: Path
    hardware_dir: Path
    config_dir: Path
    moltbox_cli_dir: Path
    runtime_commands_dir: Path
    host_commands_dir: Path
    tools_source_dir: Path

    def as_dict(self) -> dict[str, str]:
        return {
            "repo_root": str(self.repo_root),
            "archive_dir": str(self.archive_dir),
            "docs_dir": str(self.docs_dir),
            "moltbox_dir": str(self.moltbox_dir),
            "containers_dir": str(self.containers_dir),
            "hardware_dir": str(self.hardware_dir),
            "config_dir": str(self.config_dir),
            "moltbox_cli_dir": str(self.moltbox_cli_dir),
            "runtime_commands_dir": str(self.runtime_commands_dir),
            "host_commands_dir": str(self.host_commands_dir),
            "tools_source_dir": str(self.tools_source_dir),
        }


@dataclass(frozen=True)
class HostLayout:
    root: Path
    control_plane_dir: Path
    config_path: Path
    policy_path: Path
    state_dir: Path
    ssl_state_dir: Path
    ssl_routes_path: Path
    target_registry_dir: Path
    runtime_state_file: Path
    pid_file: Path
    tools_dir: Path
    host_tools_dir: Path
    runtimes_dir: Path
    shared_dir: Path
    snapshots_dir: Path
    deploy_dir: Path
    repositories_dir: Path
    runtime_artifacts_root: Path
    logs_dir: Path

    def as_dict(self) -> dict[str, str]:
        return {
            "root": str(self.root),
            "control_plane_dir": str(self.control_plane_dir),
            "config_path": str(self.config_path),
            "policy_path": str(self.policy_path),
            "state_dir": str(self.state_dir),
            "ssl_state_dir": str(self.ssl_state_dir),
            "ssl_routes_path": str(self.ssl_routes_path),
            "target_registry_dir": str(self.target_registry_dir),
            "runtime_state_file": str(self.runtime_state_file),
            "pid_file": str(self.pid_file),
            "tools_dir": str(self.tools_dir),
            "host_tools_dir": str(self.host_tools_dir),
            "runtimes_dir": str(self.runtimes_dir),
            "shared_dir": str(self.shared_dir),
            "snapshots_dir": str(self.snapshots_dir),
            "deploy_dir": str(self.deploy_dir),
            "repositories_dir": str(self.repositories_dir),
            "runtime_artifacts_root": str(self.runtime_artifacts_root),
            "logs_dir": str(self.logs_dir),
        }


def find_repo_root(start: Path | None = None) -> Path:
    configured = os.environ.get("MOLTBOX_REPO_ROOT")
    if configured:
        candidate = Path(configured).expanduser().resolve()
        if (candidate / "moltbox").exists() and (candidate / "moltbox-cli").exists():
            return candidate
        raise RuntimeError(f"MOLTBOX_REPO_ROOT does not point to a valid remram-gateway checkout: {candidate}")
    current = (start or Path(__file__)).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
        if (candidate / "moltbox").exists() and (candidate / "moltbox-cli").exists():
            return candidate
    raise RuntimeError("Unable to locate the repository root from the MoltBox CLI package path")


def build_repo_layout(repo_root: Path | None = None) -> RepoLayout:
    root = (repo_root or find_repo_root()).resolve()
    moltbox_dir = root / "moltbox"
    moltbox_cli_dir = root / "moltbox-cli"
    return RepoLayout(
        repo_root=root,
        archive_dir=root / "archive",
        docs_dir=root / "docs",
        moltbox_dir=moltbox_dir,
        containers_dir=moltbox_dir / "containers",
        hardware_dir=moltbox_dir / "hardware",
        config_dir=moltbox_dir / "config",
        moltbox_cli_dir=moltbox_cli_dir,
        runtime_commands_dir=moltbox_cli_dir / "runtime" / "commands",
        host_commands_dir=moltbox_cli_dir / "host" / "commands",
        tools_source_dir=moltbox_cli_dir / "tools" / "src",
    )


def build_host_layout(
    root: Path | None = None,
    runtime_artifacts_root: Path | None = None,
    config_path: Path | None = None,
    policy_path: Path | None = None,
) -> HostLayout:
    state_root = (root or (Path.home() / ".remram")).expanduser().resolve()
    control_plane_dir = state_root / "tools"
    runtime_root = (runtime_artifacts_root or (Path.home() / "Moltbox")).expanduser().resolve()
    resolved_config_path = (config_path or (control_plane_dir / "config.yaml")).expanduser().resolve()
    resolved_policy_path = (policy_path or (control_plane_dir / "control-plane-policy.yaml")).expanduser().resolve()
    return HostLayout(
        root=state_root,
        control_plane_dir=control_plane_dir,
        config_path=resolved_config_path,
        policy_path=resolved_policy_path,
        state_dir=state_root / "state",
        ssl_state_dir=state_root / "state" / "ssl",
        ssl_routes_path=state_root / "state" / "ssl" / "routes.json",
        target_registry_dir=state_root / "state" / "targets",
        runtime_state_file=control_plane_dir / "runtime.json",
        pid_file=control_plane_dir / "pid",
        tools_dir=state_root / "tools",
        host_tools_dir=state_root / "host-tools",
        runtimes_dir=state_root / "runtimes",
        shared_dir=state_root / "shared",
        snapshots_dir=state_root / "snapshots",
        deploy_dir=state_root / "deploy",
        repositories_dir=state_root / "repositories",
        runtime_artifacts_root=runtime_root,
        logs_dir=runtime_root / "logs",
    )


def ensure_host_layout(layout: HostLayout) -> HostLayout:
    for path in (
        layout.root,
        layout.control_plane_dir,
        layout.state_dir,
        layout.ssl_state_dir,
        layout.target_registry_dir,
        layout.tools_dir,
        layout.host_tools_dir,
        layout.runtimes_dir,
        layout.shared_dir,
        layout.snapshots_dir,
        layout.deploy_dir,
        layout.repositories_dir,
        layout.runtime_artifacts_root,
        layout.logs_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
    for name in CANONICAL_LOG_DIRS:
        (layout.logs_dir / name).mkdir(parents=True, exist_ok=True)
    return layout
