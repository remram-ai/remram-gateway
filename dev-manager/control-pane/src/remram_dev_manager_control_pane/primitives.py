from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .layout import HostLayout, RepoLayout, build_host_layout, build_repo_layout


@dataclass(frozen=True)
class PrimitiveDefinition:
    name: str
    category: str
    summary: str
    relative_source_path: str
    runtime_scoped: bool
    mutates_state: bool
    requires_snapshot: bool

    def source_path(self, repo_layout: RepoLayout) -> Path:
        return repo_layout.host_tools_source_dir / Path(self.relative_source_path)

    def installed_path(self, host_layout: HostLayout) -> Path:
        return host_layout.host_tools_dir / Path(self.relative_source_path)

    def as_dict(self, repo_layout: RepoLayout, host_layout: HostLayout) -> dict[str, str | bool]:
        source_path = self.source_path(repo_layout)
        return {
            "name": self.name,
            "category": self.category,
            "summary": self.summary,
            "source_path": str(source_path),
            "installed_path": str(self.installed_path(host_layout)),
            "runtime_scoped": self.runtime_scoped,
            "mutates_state": self.mutates_state,
            "requires_snapshot": self.requires_snapshot,
            "source_exists": source_path.exists(),
        }


PRIMITIVES: tuple[PrimitiveDefinition, ...] = (
    PrimitiveDefinition(
        name="create_runtime",
        category="runtime",
        summary="Create or initialize a runtime root for an environment.",
        relative_source_path="runtime/create-runtime.sh",
        runtime_scoped=True,
        mutates_state=True,
        requires_snapshot=False,
    ),
    PrimitiveDefinition(
        name="destroy_runtime",
        category="runtime",
        summary="Destroy a runtime root and its local execution artifacts.",
        relative_source_path="runtime/destroy-runtime.sh",
        runtime_scoped=True,
        mutates_state=True,
        requires_snapshot=False,
    ),
    PrimitiveDefinition(
        name="start_runtime",
        category="stack",
        summary="Start the runtime containers for an environment.",
        relative_source_path="stack/start-runtime.sh",
        runtime_scoped=True,
        mutates_state=True,
        requires_snapshot=False,
    ),
    PrimitiveDefinition(
        name="stop_runtime",
        category="stack",
        summary="Stop the runtime containers for an environment.",
        relative_source_path="stack/stop-runtime.sh",
        runtime_scoped=True,
        mutates_state=True,
        requires_snapshot=False,
    ),
    PrimitiveDefinition(
        name="restart_runtime",
        category="stack",
        summary="Restart the runtime containers for an environment.",
        relative_source_path="stack/restart-runtime.sh",
        runtime_scoped=True,
        mutates_state=True,
        requires_snapshot=False,
    ),
    PrimitiveDefinition(
        name="deploy_commit",
        category="deploy",
        summary="Deploy a specific git commit into a target runtime.",
        relative_source_path="deploy/deploy-commit.sh",
        runtime_scoped=True,
        mutates_state=True,
        requires_snapshot=False,
    ),
    PrimitiveDefinition(
        name="validate_runtime",
        category="validate",
        summary="Run structured validation for a runtime.",
        relative_source_path="validate/validate-runtime.sh",
        runtime_scoped=True,
        mutates_state=False,
        requires_snapshot=False,
    ),
    PrimitiveDefinition(
        name="collect_diagnostics",
        category="diagnostics",
        summary="Collect non-mutating diagnostics for a runtime.",
        relative_source_path="diagnostics/collect-diagnostics.sh",
        runtime_scoped=True,
        mutates_state=False,
        requires_snapshot=False,
    ),
    PrimitiveDefinition(
        name="snapshot_runtime",
        category="snapshot",
        summary="Create a rollback snapshot for a runtime.",
        relative_source_path="snapshot/snapshot-runtime.sh",
        runtime_scoped=True,
        mutates_state=True,
        requires_snapshot=False,
    ),
    PrimitiveDefinition(
        name="restore_snapshot",
        category="snapshot",
        summary="Restore a runtime to a prior snapshot.",
        relative_source_path="snapshot/restore-snapshot.sh",
        runtime_scoped=True,
        mutates_state=True,
        requires_snapshot=False,
    ),
)


def list_primitives(
    repo_layout: RepoLayout | None = None,
    host_layout: HostLayout | None = None,
) -> list[dict[str, str | bool]]:
    repo = repo_layout or build_repo_layout()
    host = host_layout or build_host_layout()
    return [primitive.as_dict(repo, host) for primitive in PRIMITIVES]
