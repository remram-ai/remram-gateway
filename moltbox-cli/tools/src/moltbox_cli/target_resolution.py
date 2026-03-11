ALIASES = {
    "cli": "tools",
    "caddy": "ssl",
    "control": "tools",
    "control-plane": "tools",
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
    domain = target_domain(resolved)
    if domain == "tools":
        return f"moltbox tools {'update' if verb == 'deploy' else verb}"
    return f"moltbox {domain} {resolved} {verb}"
