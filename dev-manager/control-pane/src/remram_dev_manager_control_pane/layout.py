from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


CANONICAL_LOG_DIRS = (
    "control-plane",
    "openclaw-dev",
    "openclaw-test",
    "openclaw-prod",
    "ollama",
    "opensearch",
    "caddy",
)


@dataclass(frozen=True)
class RepoLayout:
    repo_root: Path
    dev_manager_dir: Path
    control_pane_dir: Path
    host_tools_source_dir: Path
    docs_dir: Path

    def as_dict(self) -> dict[str, str]:
        return {
            "repo_root": str(self.repo_root),
            "dev_manager_dir": str(self.dev_manager_dir),
            "control_pane_dir": str(self.control_pane_dir),
            "host_tools_source_dir": str(self.host_tools_source_dir),
            "docs_dir": str(self.docs_dir),
        }


@dataclass(frozen=True)
class HostLayout:
    root: Path
    control_plane_dir: Path
    config_path: Path
    state_dir: Path
    target_registry_dir: Path
    runtime_state_file: Path
    pid_file: Path
    tools_dir: Path
    host_tools_dir: Path
    runtimes_dir: Path
    shared_dir: Path
    snapshots_dir: Path
    deploy_dir: Path
    runtime_artifacts_root: Path
    logs_dir: Path

    def as_dict(self) -> dict[str, str]:
        return {
            "root": str(self.root),
            "control_plane_dir": str(self.control_plane_dir),
            "config_path": str(self.config_path),
            "state_dir": str(self.state_dir),
            "target_registry_dir": str(self.target_registry_dir),
            "runtime_state_file": str(self.runtime_state_file),
            "pid_file": str(self.pid_file),
            "tools_dir": str(self.tools_dir),
            "host_tools_dir": str(self.host_tools_dir),
            "runtimes_dir": str(self.runtimes_dir),
            "shared_dir": str(self.shared_dir),
            "snapshots_dir": str(self.snapshots_dir),
            "deploy_dir": str(self.deploy_dir),
            "runtime_artifacts_root": str(self.runtime_artifacts_root),
            "logs_dir": str(self.logs_dir),
        }


def find_repo_root(start: Path | None = None) -> Path:
    current = (start or Path(__file__)).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    raise RuntimeError("Unable to locate repository root from control-plane package path")


def build_repo_layout(repo_root: Path | None = None) -> RepoLayout:
    root = (repo_root or find_repo_root()).resolve()
    dev_manager_dir = root / "dev-manager"
    return RepoLayout(
        repo_root=root,
        dev_manager_dir=dev_manager_dir,
        control_pane_dir=dev_manager_dir / "control-pane",
        host_tools_source_dir=dev_manager_dir / "host-tools",
        docs_dir=dev_manager_dir / "docs",
    )


def build_host_layout(
    root: Path | None = None,
    runtime_artifacts_root: Path | None = None,
    config_path: Path | None = None,
) -> HostLayout:
    state_root = (root or (Path.home() / ".remram")).expanduser().resolve()
    control_plane_dir = state_root / "control-plane"
    runtime_root = (runtime_artifacts_root or (Path.home() / "Moltbox")).expanduser().resolve()
    resolved_config_path = (config_path or (control_plane_dir / "config.yaml")).expanduser().resolve()
    return HostLayout(
        root=state_root,
        control_plane_dir=control_plane_dir,
        config_path=resolved_config_path,
        state_dir=state_root / "state",
        target_registry_dir=state_root / "state" / "targets",
        runtime_state_file=control_plane_dir / "runtime.json",
        pid_file=control_plane_dir / "pid",
        tools_dir=state_root / "tools",
        host_tools_dir=state_root / "host-tools",
        runtimes_dir=state_root / "runtimes",
        shared_dir=state_root / "shared",
        snapshots_dir=state_root / "snapshots",
        deploy_dir=state_root / "deploy",
        runtime_artifacts_root=runtime_root,
        logs_dir=runtime_root / "logs",
    )


def ensure_host_layout(layout: HostLayout) -> HostLayout:
    for path in (
        layout.root,
        layout.control_plane_dir,
        layout.state_dir,
        layout.target_registry_dir,
        layout.tools_dir,
        layout.host_tools_dir,
        layout.runtimes_dir,
        layout.shared_dir,
        layout.snapshots_dir,
        layout.deploy_dir,
        layout.runtime_artifacts_root,
        layout.logs_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
    for name in CANONICAL_LOG_DIRS:
        (layout.logs_dir / name).mkdir(parents=True, exist_ok=True)
    return layout
