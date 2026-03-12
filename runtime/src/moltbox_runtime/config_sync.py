from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from moltbox_commands.core.components import ComponentSpec
from moltbox_commands.core.config import GatewayConfig
from moltbox_repos.adapters import runtime_resource

from .template_context import runtime_template_context


def _replace_tokens(text: str, context: dict[str, str]) -> str:
    rendered = text
    for key in sorted(context):
        rendered = rendered.replace(f"{{{{ {key} }}}}", context[key])
        rendered = rendered.replace(f"{{{{{key}}}}}", context[key])
    return rendered


def _copy_or_render(source: Path, destination: Path, context: dict[str, str]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix == ".template":
        destination.with_suffix("").write_text(
            _replace_tokens(source.read_text(encoding="utf-8"), context),
            encoding="utf-8",
        )
        return
    destination.write_bytes(source.read_bytes())


def _runtime_context(config: GatewayConfig, spec: ComponentSpec) -> dict[str, str]:
    return runtime_template_context(config, spec)


def _render_runtime_tree(source_root: Path, staging_dir: Path, context: dict[str, str]) -> list[str]:
    rendered_paths: list[str] = []
    for source in sorted(path for path in source_root.rglob("*") if path.is_file()):
        relative = source.relative_to(source_root)
        destination = staging_dir / relative
        _copy_or_render(source, destination, context)
        rendered_relative = destination.relative_to(staging_dir)
        if source.suffix == ".template":
            rendered_relative = rendered_relative.with_suffix("")
        rendered_paths.append(str(rendered_relative).replace("\\", "/"))
    return rendered_paths


def sync_component_config(config: GatewayConfig, spec: ComponentSpec) -> dict[str, Any]:
    source = runtime_resource(config, spec.runtime_name or spec.service_name)
    staging_dir = config.layout.deploy_root / "runtime-sync" / spec.canonical_name
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    rendered_files = _render_runtime_tree(source.path, staging_dir, _runtime_context(config, spec))
    return {
        "runtime_source": source.as_dict(),
        "staging_dir": str(staging_dir),
        "runtime_root": str(config.layout.runtime_component_dir(spec.canonical_name)),
        "rendered_files": sorted(set(rendered_files)),
    }
