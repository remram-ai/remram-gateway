from __future__ import annotations

from typing import Any

from moltbox_commands.core.errors import ValidationError
from moltbox_repos.adapters import load_skill_recipe, skill_package_resource
from moltbox_runtime import skills as runtime_skill_operations

from .component import execute_component, sync_component_config
from .service import deploy_service
from .shared import success_payload


def _as_list(payload: object, *, field_name: str) -> list[str]:
    if payload is None:
        return []
    if isinstance(payload, str):
        return [payload]
    if not isinstance(payload, list):
        raise ValidationError(
            f"skill recipe field '{field_name}' must be a list of strings",
            "rewrite the skill recipe with list entries and rerun the command",
            field=field_name,
        )
    values: list[str] = []
    for item in payload:
        if not isinstance(item, str) or not item.strip():
            raise ValidationError(
                f"skill recipe field '{field_name}' must contain non-empty strings",
                "remove invalid skill recipe entries and rerun the command",
                field=field_name,
            )
        values.append(item.strip())
    return values


def _normalize_plan(recipe: dict[str, Any]) -> dict[str, list[str]]:
    services_section = recipe.get("services")
    runtime_section = recipe.get("runtime")
    return {
        "service_deploy": _as_list(recipe.get("service_deploy"), field_name="service_deploy")
        + _as_list(services_section.get("deploy") if isinstance(services_section, dict) else None, field_name="services.deploy"),
        "runtime_sync": _as_list(recipe.get("runtime_sync"), field_name="runtime_sync")
        + _as_list(runtime_section.get("sync") if isinstance(runtime_section, dict) else None, field_name="runtime.sync"),
        "runtime_reload": _as_list(recipe.get("runtime_reload"), field_name="runtime_reload")
        + _as_list(runtime_section.get("reload") if isinstance(runtime_section, dict) else None, field_name="runtime.reload"),
        "component_restart": _as_list(recipe.get("component_restart"), field_name="component_restart")
        + _as_list(recipe.get("restart"), field_name="restart"),
    }


def _optional_skill_package(config: Any, skill_name: str):
    try:
        return skill_package_resource(config, skill_name)
    except ValidationError:
        return None


def _optional_skill_recipe(config: Any, skill_name: str):
    try:
        return load_skill_recipe(config, skill_name)
    except ValidationError:
        return None


def deploy_skill(config: Any, skill_name: str) -> dict[str, Any]:
    package_resource = _optional_skill_package(config, skill_name)
    recipe_loaded = _optional_skill_recipe(config, skill_name)
    if package_resource is None and recipe_loaded is None:
        raise ValidationError(
            f"skill '{skill_name}' was not found in remram-skills",
            "create the skill package or deployment recipe in remram-skills and rerun the command",
            skill=skill_name,
        )

    operations: list[dict[str, Any]] = []
    install_result: dict[str, Any] | None = None
    recipe_resource = None
    recipe: dict[str, Any] = {}
    plan = {
        "service_deploy": [],
        "runtime_sync": [],
        "runtime_reload": [],
        "component_restart": [],
    }

    if package_resource is not None:
        if (package_resource.path / "openclaw.plugin.json").exists():
            install_result = runtime_skill_operations.deploy_plugin_backed_skill(
                config,
                skill_name=skill_name,
                package_dir=package_resource.path,
            )
        else:
            install_result = runtime_skill_operations.deploy_pure_skill(
                config,
                skill_name=skill_name,
                package_dir=package_resource.path,
            )
        operations.append(
            {
                "operation": "skill_install",
                "target": skill_name,
                "result": install_result,
            }
        )

    if recipe_loaded is not None:
        recipe_resource, recipe = recipe_loaded
        plan = _normalize_plan(recipe)

        for service_name in plan["service_deploy"]:
            operations.append({"operation": "service_deploy", "target": service_name, "result": deploy_service(config, service_name)})

        for component_name in plan["runtime_sync"]:
            operations.append({"operation": "runtime_sync", "target": component_name, "result": sync_component_config(config, component_name)})

        for component_name in plan["runtime_reload"]:
            operations.append({"operation": "runtime_reload", "target": component_name, "result": execute_component(config, component_name, "reload")})

        for component_name in plan["component_restart"]:
            operations.append({"operation": "component_restart", "target": component_name, "result": execute_component(config, component_name, "restart")})

    return success_payload(
        f"moltbox skill deploy {skill_name}",
        skill=skill_name,
        skill_package=package_resource.as_dict() if package_resource is not None else None,
        install_result=install_result,
        skill_recipe=recipe_resource.as_dict() if recipe_resource is not None else None,
        plan=plan,
        operations=operations,
    )
