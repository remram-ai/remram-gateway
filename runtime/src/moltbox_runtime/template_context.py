from __future__ import annotations

import os

from moltbox_commands.core.components import ComponentSpec
from moltbox_commands.core.config import GatewayConfig


def component_gateway_port(component_name: str, default_port: int) -> int:
    return {
        "openclaw-dev": 18790,
        "openclaw-test": 28789,
        "openclaw-prod": 38789,
    }.get(component_name, default_port)


def component_profile(component_name: str) -> str:
    if component_name.endswith("-dev"):
        return "dev"
    if component_name.endswith("-test"):
        return "test"
    if component_name.endswith("-prod"):
        return "prod"
    return component_name


def _target_env_value(component_name: str, base_name: str) -> str:
    profile = component_profile(component_name).upper()
    component = component_name.upper().replace("-", "_")
    for candidate in (f"{base_name}_{profile}", f"{base_name}_{component}", base_name):
        raw = os.environ.get(candidate)
        if raw and raw.strip():
            return raw.strip()
    return ""


def _env_bool(component_name: str, base_name: str, *, default: bool = False) -> str:
    raw = _target_env_value(component_name, base_name)
    if not raw:
        return "true" if default else "false"
    return "true" if raw.lower() in {"1", "true", "yes", "on"} else "false"


def _comma_split(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _discord_guilds_block(component_name: str) -> str:
    guild_id = _target_env_value(component_name, "MOLTBOX_DISCORD_GUILD_ID")
    if not guild_id:
        return ""

    require_mention = _env_bool(component_name, "MOLTBOX_DISCORD_REQUIRE_MENTION", default=True)
    user_ids = _comma_split(_target_env_value(component_name, "MOLTBOX_DISCORD_USER_IDS"))
    channel_ids = _comma_split(_target_env_value(component_name, "MOLTBOX_DISCORD_CHANNEL_IDS"))
    single_channel_id = _target_env_value(component_name, "MOLTBOX_DISCORD_CHANNEL_ID")
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


def resolve_public_hostname() -> str:
    configured = os.environ.get("MOLTBOX_PUBLIC_HOSTNAME") or os.environ.get("REMRAM_PUBLIC_HOSTNAME")
    if configured and configured.strip():
        return configured.strip()
    return ""


def runtime_template_context(config: GatewayConfig, spec: ComponentSpec) -> dict[str, str]:
    return {
        "component_name": spec.canonical_name,
        "service_name": spec.service_name,
        "runtime_name": spec.runtime_name or spec.service_name,
        "profile": component_profile(spec.canonical_name),
        "gateway_port": str(component_gateway_port(spec.canonical_name, config.internal_port)),
        "internal_host": config.internal_host,
        "internal_port": str(config.internal_port),
        "state_root": str(config.state_root),
        "logs_root": str(config.logs_root),
        "runtime_root": str(config.runtime_artifacts_root),
        "runtime_component_dir": str(config.layout.runtime_component_dir(spec.canonical_name)),
        "public_hostname": resolve_public_hostname(),
        "discord_enabled": _env_bool(spec.canonical_name, "MOLTBOX_DISCORD_ENABLED", default=False),
        "discord_guilds_block": _discord_guilds_block(spec.canonical_name),
    }
