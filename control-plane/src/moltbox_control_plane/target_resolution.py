ALIASES = {
    "cli": "tools",
    "caddy": "ssl",
    "control": "tools",
    "control-plane": "tools",
    "gateway": "tools",
    "openclaw": "prod",
    "openclaw-dev": "dev",
    "openclaw-test": "test",
    "openclaw-prod": "prod",
}

RUNTIME_TARGETS = {"dev", "test", "prod"}
HOST_TARGETS = {"ollama", "opensearch", "ssl"}
TOOLS_TARGET = "tools"


def resolve_target_identifier(target_id: str) -> str:
    return ALIASES.get(target_id, target_id)


def target_domain(target_id: str) -> str:
    resolved = resolve_target_identifier(target_id)
    if resolved == TOOLS_TARGET:
        return "tools"
    if resolved in RUNTIME_TARGETS:
        return "runtime"
    if resolved in HOST_TARGETS:
        return "host"
    return "host"


def canonical_cli_command(target_id: str, verb: str) -> str:
    resolved = resolve_target_identifier(target_id)
    if resolved == TOOLS_TARGET:
        gateway_verb = "update" if verb == "deploy" else verb
        return f"moltbox gateway {gateway_verb}"
    component_name = {
        "dev": "openclaw-dev",
        "test": "openclaw-test",
        "prod": "openclaw-prod",
        "ssl": "caddy",
    }.get(resolved, resolved)
    if verb in {"deploy", "rollback"}:
        return f"moltbox service {verb} {component_name}"
    return f"moltbox {component_name} {verb}"
