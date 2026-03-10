ALIASES = {
    "cli": "control",
    "prime": "prod",
}


def resolve_target_identifier(target_id: str) -> str:
    return ALIASES.get(target_id, target_id)
