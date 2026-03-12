from __future__ import annotations

import socket
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from moltbox_commands.core.components import ComponentSpec
from moltbox_commands.core.config import GatewayConfig
from moltbox_commands.core.errors import ValidationError
from moltbox_commands.core.jsonio import write_json
from moltbox_repos.adapters import RepoResource, runtime_resource
from moltbox_runtime.template_context import (
    component_gateway_port,
    component_profile,
    runtime_template_context,
)


@dataclass(frozen=True)
class RenderedService:
    service_name: str
    compose_project: str
    container_names: list[str]
    output_dir: Path
    compose_file: Path
    render_manifest_path: Path
    service_source: dict[str, str]
    runtime_source: dict[str, str] | None
    artifact: dict[str, Any]
    metadata: dict[str, Any]


def _read_service_metadata(service_dir: Path) -> dict[str, Any]:
    for name in ("service.yaml", "service.yml"):
        path = service_dir / name
        if not path.exists():
            continue
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if loaded is None:
            return {}
        if not isinstance(loaded, dict):
            raise ValidationError(
                f"service metadata '{path}' must contain a mapping",
                "rewrite the service metadata as a YAML object and rerun the command",
                service_metadata_path=str(path),
            )
        return loaded
    return {}


def _find_compose_source(service_dir: Path, metadata: dict[str, Any]) -> Path:
    configured = metadata.get("compose_file")
    if isinstance(configured, str) and configured.strip():
        path = service_dir / configured.strip()
        if path.exists():
            return path
        raise ValidationError(
            f"configured compose file '{configured}' was not found",
            "fix the compose_file setting or add the compose file to the service definition",
            compose_file=str(path),
        )
    for name in ("compose.yml.template", "compose.yaml.template", "compose.yml", "compose.yaml"):
        path = service_dir / name
        if path.exists():
            return path
    raise ValidationError(
        f"service definition '{service_dir.name}' does not contain a compose file",
        "add compose.yml.template, compose.yaml.template, compose.yml, or compose.yaml to the service directory",
        service_dir=str(service_dir),
    )


def _string_dict(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValidationError(
            "template_context must be a mapping of strings",
            "rewrite template_context in service metadata and rerun the command",
            configured_value=raw,
        )
    values: dict[str, str] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            raise ValidationError(
                "template_context keys must be strings",
                "rewrite template_context with string keys and rerun the command",
                configured_value=raw,
            )
        values[str(key)] = "" if value is None else str(value)
    return values


def _bool_value(raw: Any, *, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValidationError(
        "boolean service metadata fields must be booleans or boolean strings",
        "rewrite the service metadata value and rerun the command",
        configured_value=raw,
    )


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


def _copy_tree(service_dir: Path, output_dir: Path, context: dict[str, str], *, skipped_names: set[str] | None = None) -> list[str]:
    source_paths: list[str] = []
    ignored = skipped_names or set()
    for source in sorted(path for path in service_dir.rglob("*") if path.is_file()):
        if source.name in ignored:
            continue
        destination = output_dir / source.relative_to(service_dir)
        _copy_or_render(source, destination, context)
        source_paths.append(str(source))
    return source_paths


def _public_host_suffix(subdomain: str, hostname: str) -> str:
    if not hostname:
        return ""
    return f", {subdomain}.{hostname}"


def _service_runtime_source(config: GatewayConfig, spec: ComponentSpec, *, required: bool) -> RepoResource | None:
    runtime_name = spec.runtime_name or spec.service_name
    if not runtime_name:
        return None
    if not config.runtime_repo_url:
        if required:
            return runtime_resource(config, runtime_name)
        return None
    try:
        return runtime_resource(config, runtime_name)
    except ValidationError:
        if required:
            raise
        return None


def _shared_service_root(config: GatewayConfig, spec: ComponentSpec) -> Path:
    return config.layout.runtime_component_dir(spec.canonical_name)


def render_service(
    config: GatewayConfig,
    spec: ComponentSpec,
    source: RepoResource,
    artifact: dict[str, Any],
) -> RenderedService:
    metadata = _read_service_metadata(source.path)
    runtime_required = _bool_value(metadata.get("runtime_required"), default=False)
    runtime_source = _service_runtime_source(config, spec, required=runtime_required)
    compose_source = _find_compose_source(source.path, metadata)
    compose_project = str(metadata.get("compose_project") or spec.compose_project)
    raw_container_names = metadata.get("container_names")
    if raw_container_names is None:
        container_names = [spec.container_name]
    elif isinstance(raw_container_names, list) and raw_container_names and all(isinstance(item, str) and item.strip() for item in raw_container_names):
        container_names = [item.strip() for item in raw_container_names]
    else:
        raise ValidationError(
            "container_names must be a non-empty list of strings",
            "rewrite container_names in service metadata and rerun the command",
            configured_value=raw_container_names,
        )

    output_dir = config.layout.rendered_service_dir(spec.canonical_name)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    runtime_context = runtime_template_context(config, spec)
    public_hostname = runtime_context.get("public_hostname") or socket.gethostname().strip().split(".", 1)[0]
    context = {
        "service_name": spec.canonical_name,
        "component_name": spec.canonical_name,
        "profile": component_profile(spec.canonical_name),
        "container_name": container_names[0],
        "compose_project": compose_project,
        "state_root": str(config.state_root),
        "logs_root": str(config.logs_root),
        "service_state_dir": str(config.layout.service_state_dir(spec.canonical_name)),
        "runtime_root": str(config.runtime_artifacts_root),
        "runtime_component_dir": str(config.layout.runtime_component_dir(spec.canonical_name)),
        "shared_root": str(_shared_service_root(config, spec)),
        "internal_network_name": "moltbox_moltbox_internal",
        "internal_host": config.internal_host,
        "internal_port": str(config.internal_port),
        "gateway_port": str(component_gateway_port(spec.canonical_name, config.internal_port)),
        "gateway_container_name": "gateway",
        "gateway_container_port": str(config.internal_port),
        "public_hostname": public_hostname,
        "dev_public_host": _public_host_suffix("dev", public_hostname),
        "test_public_host": _public_host_suffix("test", public_hostname),
        "prod_public_host": _public_host_suffix("prod", public_hostname),
        "selected_artifact": str(artifact.get("selected_artifact") or ""),
        "version": str(artifact.get("version") or ""),
        "commit": str(artifact.get("commit") or ""),
        "artifact_channel": str(artifact.get("channel") or ""),
        "artifact_strategy": str(artifact.get("strategy") or ""),
        "service_source_path": str(source.path),
        **runtime_context,
        **_string_dict(metadata.get("template_context")),
    }
    source_paths = _copy_tree(source.path, output_dir, context, skipped_names={"service.yaml", "service.yml"})
    runtime_source_paths: list[str] = []
    if runtime_source is not None:
        runtime_output_dir = output_dir / "config" / runtime_source.path.name
        runtime_source_paths = _copy_tree(runtime_source.path, runtime_output_dir, context)
    compose_relative = compose_source.relative_to(source.path)
    compose_file = output_dir / compose_relative
    if compose_file.suffix == ".template":
        compose_file = compose_file.with_suffix("")
    render_manifest_path = output_dir / "render-manifest.json"
    write_json(
        render_manifest_path,
        {
            "service": spec.canonical_name,
            "compose_project": compose_project,
            "container_names": container_names,
            "artifact": artifact,
            "source_paths": source_paths,
            "runtime_source": runtime_source.as_dict() if runtime_source is not None else None,
            "runtime_source_paths": runtime_source_paths,
        },
    )
    return RenderedService(
        service_name=spec.canonical_name,
        compose_project=compose_project,
        container_names=container_names,
        output_dir=output_dir,
        compose_file=compose_file,
        render_manifest_path=render_manifest_path,
        service_source=source.as_dict(),
        runtime_source=runtime_source.as_dict() if runtime_source is not None else None,
        artifact=artifact,
        metadata=metadata,
    )
