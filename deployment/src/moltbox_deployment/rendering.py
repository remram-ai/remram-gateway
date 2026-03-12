from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .component_resolution import resolve_component
from .config import AppConfig
from .errors import ValidationError
from .jsonio import write_json_file
from .layout import build_repo_layout
from .operation_ids import utc_now_iso
from .repository_adapters import runtime_resource, service_resource
from .registry import get_target
from .ssl_ingress import build_ssl_render_context
from .target_resolution import canonical_cli_command
from .versioning import resolve_version_info


def deployment_assets_root() -> Path:
    return build_repo_layout().containers_dir


def _component_for_record(target_id: str) -> str:
    return {
        "tools": "gateway",
        "dev": "openclaw-dev",
        "test": "openclaw-test",
        "prod": "openclaw-prod",
        "ssl": "caddy",
        "ollama": "ollama",
        "opensearch": "opensearch",
    }.get(target_id, target_id)


def asset_source_for_target(config: AppConfig, target_id: str) -> tuple[Path, Path, dict[str, str]]:
    if target_id == "tools":
        repo_layout = build_repo_layout()
        path = repo_layout.containers_dir / "tools"
        return (
            path,
            repo_layout.repo_root,
            {
                "repository": "remram-gateway",
                "repository_url": "",
                "relative_path": "moltbox/containers/tools",
                "path": str(path),
            },
        )
    component = resolve_component(_component_for_record(target_id))
    resource = service_resource(config, str(component.service_repo_name))
    return resource.path, resource.repository.checkout_dir, resource.as_dict()


def _runtime_config_mappings(config_dir: Path) -> list[tuple[Path, Path]]:
    mappings = [
        (config_dir / "openclaw.json", Path("openclaw.json")),
        (config_dir / "model-runtime.yml", Path("model-runtime.yml")),
        (config_dir / "opensearch.yml", Path("opensearch.yml")),
    ]
    openclaw_dir = config_dir / "openclaw"
    for source in sorted(path for path in openclaw_dir.rglob("*") if path.is_file()):
        mappings.append((source, source.relative_to(openclaw_dir)))
    return mappings


def config_source_for_target(config: AppConfig, target_id: str, target_class: str) -> tuple[Path | None, dict[str, str] | None]:
    if target_id == "tools":
        config_path = build_repo_layout().config_dir / "control-plane-policy.yaml"
        return (
            config_path,
            {
                "repository": "remram-gateway",
                "repository_url": "",
                "relative_path": "moltbox/config/control-plane-policy.yaml",
                "path": str(config_path),
            },
        )
    component = resolve_component(_component_for_record(target_id))
    if component.runtime_repo_name is None:
        return None, None
    try:
        resource = runtime_resource(config, component.runtime_repo_name)
    except ValidationError:
        if target_class == "runtime":
            raise
        return None, None
    return resource.path, resource.as_dict()


def rendered_output_dir(config: AppConfig, target: str, profile: str | None) -> Path:
    bucket = profile if profile else "shared"
    return config.layout.deploy_dir / "rendered" / bucket / target


def _runtime_render_dir(config: AppConfig, target: str, profile: str | None) -> Path:
    timestamp = utc_now_iso().replace(":", "").replace("-", "").replace(".", "")
    return rendered_output_dir(config, target, profile) / timestamp


def _existing_owner(path: Path) -> tuple[str, str]:
    override_uid = os.environ.get("MOLTBOX_CONTAINER_UID")
    override_gid = os.environ.get("MOLTBOX_CONTAINER_GID")
    if override_uid and override_gid:
        return override_uid, override_gid
    for candidate in (path, *path.parents):
        if not candidate.exists():
            continue
        try:
            stat_result = candidate.stat()
        except OSError:
            continue
        return str(stat_result.st_uid), str(stat_result.st_gid)
    return str(getattr(os, "getuid", lambda: 1000)()), str(getattr(os, "getgid", lambda: 1000)())


def _docker_socket_gid(default_gid: str) -> str:
    override_gid = os.environ.get("MOLTBOX_DOCKER_SOCKET_GID")
    if override_gid:
        return override_gid
    socket_path = Path("/var/run/docker.sock")
    try:
        return str(socket_path.stat().st_gid)
    except OSError:
        return default_gid


def _target_env_value(target: str, base_name: str) -> str:
    for candidate in (f"{base_name}_{target.upper()}", base_name):
        raw = os.environ.get(candidate)
        if raw:
            trimmed = raw.strip()
            if trimmed:
                return trimmed
    return ""


def _env_bool(target: str, base_name: str, default: bool = False) -> str:
    raw = _target_env_value(target, base_name)
    if not raw:
        return "true" if default else "false"
    return "true" if raw.lower() in {"1", "true", "yes", "on"} else "false"


def _comma_split(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _discord_guilds_block(target: str) -> str:
    guild_id = _target_env_value(target, "MOLTBOX_DISCORD_GUILD_ID")
    if not guild_id:
        return ""

    require_mention = _env_bool(target, "MOLTBOX_DISCORD_REQUIRE_MENTION", default=True)
    user_ids = _comma_split(_target_env_value(target, "MOLTBOX_DISCORD_USER_IDS"))
    channel_ids = _comma_split(_target_env_value(target, "MOLTBOX_DISCORD_CHANNEL_IDS"))
    single_channel_id = _target_env_value(target, "MOLTBOX_DISCORD_CHANNEL_ID")
    if single_channel_id and single_channel_id not in channel_ids:
        channel_ids.append(single_channel_id)

    lines = [
        "    guilds:",
        f'      "{guild_id}":',
        f"        requireMention: {require_mention}",
    ]
    if user_ids:
        lines.append("        users:")
        for user_id in user_ids:
            lines.append(f'          - "{user_id}"')
    if channel_ids:
        lines.append("        channels:")
        for channel_id in channel_ids:
            lines.append(f'          "{channel_id}":')
            lines.append(f"            requireMention: {require_mention}")
    return "\n".join(lines)


def render_context(config: AppConfig, target: str, repo_root: Path) -> dict[str, str]:
    record = get_target(config, target)
    runtime_root = record.runtime_root or ""
    shared_root = str(config.layout.shared_dir / target) if record.target_class == "shared_service" else ""
    container_uid, container_gid = _existing_owner(config.state_root)
    data_volume_name = {
        "ollama": "moltbox_ollama_data",
        "opensearch": "moltbox_opensearch_data",
    }.get(record.id, "")
    gateway_port = {
        "tools": "7474",
        "dev": "18790",
        "test": "28789",
        "prod": "38789",
    }.get(record.id, "")
    return {
        "target": record.id,
        "profile": record.profile or "",
        "compose_project": record.compose_project,
        "container_name": record.container_names[0] if record.container_names else record.id,
        "runtime_root": runtime_root,
        "repo_root": str(repo_root),
        "shared_root": shared_root,
        "data_volume_name": data_volume_name,
        "internal_network_name": "moltbox_moltbox_internal"
        if record.target_class in {"shared_service", "runtime"}
        else "",
        "state_root": str(config.state_root),
        "runtime_artifacts_root": str(config.runtime_artifacts_root),
        "gateway_port": gateway_port,
        "container_uid": container_uid,
        "container_gid": container_gid,
        "docker_socket_gid": _docker_socket_gid(container_gid),
        "discord_enabled": _env_bool(record.id, "MOLTBOX_DISCORD_ENABLED", default=False),
        "discord_guilds_block": _discord_guilds_block(record.id),
    }


def _replace_tokens(text: str, context: dict[str, str]) -> str:
    rendered = text
    for key in sorted(context):
        rendered = rendered.replace(f"{{{{ {key} }}}}", context[key])
        rendered = rendered.replace(f"{{{{{key}}}}}", context[key])
    return rendered


def _render_file(source: Path, destination: Path, context: dict[str, str]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.name.endswith(".template"):
        output_name = source.name[: -len(".template")]
        target_path = destination.parent / output_name
        text = source.read_text(encoding="utf-8")
        target_path.write_text(_replace_tokens(text, context), encoding="utf-8")
        return
    destination.write_bytes(source.read_bytes())


def _render_tree(source_root: Path, output_root: Path, context: dict[str, str]) -> list[str]:
    source_paths: list[str] = []
    for source in sorted(path for path in source_root.rglob("*") if path.is_file()):
        relative = source.relative_to(source_root)
        _render_file(source, output_root / relative, context)
        source_paths.append(str(source))
    return source_paths


def _render_mapped_files(
    mappings: list[tuple[Path, Path]],
    output_root: Path,
    context: dict[str, str],
) -> tuple[list[str], Path]:
    source_paths: list[str] = []
    for source, relative_path in mappings:
        _render_file(source, output_root / relative_path, context)
        source_paths.append(str(source))
    if len(mappings) == 1:
        return source_paths, output_root / mappings[0][1]
    return source_paths, output_root


def _render_config_source(source: Path, output_root: Path, context: dict[str, str]) -> tuple[list[str], Path]:
    if source.is_dir():
        rendered_root = output_root / source.name
        return _render_tree(source, rendered_root, context), rendered_root
    return _render_mapped_files([(source, Path(source.name))], output_root, context)


def render_target(config: AppConfig, target: str, profile: str | None = None) -> dict[str, Any]:
    record = get_target(config, target)
    render_profile = profile or record.profile
    if record.profile and render_profile != record.profile:
        raise ValidationError(
            f"target '{record.id}' requires profile '{record.profile}'",
            f"rerun `{canonical_cli_command(record.id, 'deploy')}` using the required profile",
            target=record.id,
            profile=render_profile,
        )
    asset_dir, asset_repo_root, asset_source = asset_source_for_target(config, record.id)
    if not asset_dir.exists():
        raise ValidationError(
            f"deployment assets for target '{record.id}' were not found",
            "create the canonical deployment asset directory and rerun the command",
            target=record.id,
            asset_path=str(asset_dir),
        )
    config_source, config_source_details = config_source_for_target(config, record.id, record.target_class)
    if record.target_class == "runtime" and config_source is not None:
        runtime_config_required = all(source.exists() for source, _ in _runtime_config_mappings(config_source))
    else:
        runtime_config_required = config_source is not None and config_source.exists()
    if record.target_class == "runtime" and not runtime_config_required:
        raise ValidationError(
            f"deployment config for target '{record.id}' was not found",
            "create the canonical runtime config directory under `moltbox/config/` and rerun the command",
            target=record.id,
            config_path=str(config_source) if config_source is not None else "",
        )
    output_dir = (
        _runtime_render_dir(config, record.id, render_profile)
        if record.target_class == "runtime"
        else rendered_output_dir(config, record.id, render_profile)
    )
    if output_dir.exists():
        for child in sorted(output_dir.rglob("*"), reverse=True):
            if child.is_file():
                child.unlink()
            elif child.is_dir():
                child.rmdir()
    output_dir.mkdir(parents=True, exist_ok=True)

    context = render_context(config, record.id, asset_repo_root)
    if record.id == "ssl":
        context.update(build_ssl_render_context(config))
    source_paths = _render_tree(asset_dir, output_dir, context)
    config_source_paths: list[str] = []
    rendered_config_path: Path | None = None
    if config_source is not None:
        if record.target_class == "runtime":
            config_source_paths, rendered_config_path = _render_mapped_files(
                _runtime_config_mappings(config_source),
                output_dir / "runtime-root",
                context,
            )
            openclaw_config_dir = config_source / "openclaw"
            if openclaw_config_dir.exists():
                config_source_paths.extend(
                    _render_tree(openclaw_config_dir, output_dir / "config" / "openclaw", context)
                )
        else:
            config_source_paths, rendered_config_path = _render_config_source(
                config_source,
                output_dir / "config",
                context,
            )

    manifest = {
        "target": record.id,
        "profile": render_profile,
        "render_timestamp": utc_now_iso(),
        "render_version": resolve_version_info().version,
        "render_outcome": "success",
        "source_asset_paths": source_paths,
        "source_config_paths": config_source_paths,
    }
    write_json_file(output_dir / "render-manifest.json", manifest)
    payload = {
        "target": record.id,
        "profile": render_profile,
        "output_dir": str(output_dir),
        "render_manifest_path": str(output_dir / "render-manifest.json"),
        "asset_path": str(asset_dir),
        "asset_source": asset_source,
    }
    if context.get("gateway_port"):
        payload["gateway_port"] = context["gateway_port"]
    if context.get("internal_network_name"):
        payload["internal_network_name"] = context["internal_network_name"]
    if config_source is not None and rendered_config_path is not None:
        payload["config_path"] = str(config_source)
        if config_source_details is not None:
            payload["config_source"] = config_source_details
        if record.target_class == "runtime":
            payload["rendered_runtime_root_dir"] = str(rendered_config_path)
        elif config_source.is_dir():
            payload["rendered_config_dir"] = str(rendered_config_path)
        else:
            payload["rendered_config_path"] = str(rendered_config_path)
    return payload
