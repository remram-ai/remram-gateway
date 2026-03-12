from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time
from typing import Any

from moltbox_commands.core.errors import ConfigError
from moltbox_docker import engine as docker_engine
from moltbox_services import pipeline as service_pipeline

from .service import deploy_service
from .shared import success_payload


def health_gateway(config: Any) -> dict[str, Any]:
    return success_payload(
        "moltbox gateway health",
        gateway={
            "docker_available": docker_engine.docker_available(),
            "services_repo_configured": bool(config.services_repo_url),
            "runtime_repo_configured": bool(config.runtime_repo_url),
            "skills_repo_configured": bool(config.skills_repo_url),
        },
    )


def status_gateway(config: Any) -> dict[str, Any]:
    payload = service_pipeline.status_service(config, service_pipeline.gateway_spec())
    return success_payload("moltbox gateway status", **payload)


def inspect_gateway(config: Any) -> dict[str, Any]:
    payload = service_pipeline.inspect_service(config, service_pipeline.gateway_spec())
    payload["configured_repositories"] = {
        "moltbox-services": config.services_repo_url,
        "moltbox-runtime": config.runtime_repo_url,
        "remram-skills": config.skills_repo_url,
    }
    return success_payload("moltbox gateway inspect", **payload)


def logs_gateway(config: Any) -> dict[str, Any]:
    payload = service_pipeline.logs_service(config, service_pipeline.gateway_spec())
    return success_payload("moltbox gateway logs", **payload)


def _inside_container() -> bool:
    return Path("/.dockerenv").exists() or os.environ.get("MOLTBOX_RUNNING_IN_CONTAINER") == "1"


def _metadata_flag(metadata: dict[str, Any], field_name: str, *, default: bool = False) -> bool:
    raw = metadata.get(field_name)
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
    return default


def _schedule_gateway_self_update(config: Any, *, version: str | None, commit: str | None) -> dict[str, Any]:
    if not docker_engine.docker_available():
        raise ConfigError(
            "docker is not available inside the gateway container",
            "mount /var/run/docker.sock into the gateway container and rerun the update",
        )
    prepared = service_pipeline.prepare_service_deployment(
        config,
        service_pipeline.gateway_spec(),
        version=version,
        commit=commit,
    )
    network_bootstrap = docker_engine.ensure_external_networks(prepared.rendered.output_dir)
    if not network_bootstrap.get("ok"):
        raise ConfigError(
            "failed to create required gateway Docker networks",
            "repair Docker networking on the host and rerun `moltbox gateway update`",
            network_bootstrap=network_bootstrap.get("details"),
        )
    current_container = os.environ.get("HOSTNAME", "").strip() or "gateway"
    inspected = subprocess.run(
        ["docker", "inspect", current_container, "--format", "{{.Config.Image}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    current_image = inspected.stdout.strip()
    if inspected.returncode != 0 or not current_image:
        raise ConfigError(
            "failed to resolve the running gateway image for self-update",
            "inspect the gateway container on the host and rerun `moltbox gateway update`",
            docker_stdout=inspected.stdout.strip(),
            docker_stderr=inspected.stderr.strip(),
            current_container=current_container,
        )
    helper_name = f"gateway-update-{int(time.time())}"
    command = [
        "docker",
        "run",
        "--detach",
        "--rm",
        "--name",
        helper_name,
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
        "-v",
        f"{config.state_root}:{config.state_root}",
        current_image,
        "compose",
        "-f",
        str(prepared.rendered.compose_file),
        "-p",
        prepared.rendered.compose_project,
        "up",
        "-d",
    ]
    if _metadata_flag(prepared.rendered.metadata, "build_on_deploy", default=False):
        command.append("--build")
    command.extend(["--force-recreate", "--remove-orphans"])
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise ConfigError(
            "failed to launch the detached gateway self-update helper",
            "inspect Docker on the host and rerun `moltbox gateway update`",
            docker_command=command,
            docker_stdout=completed.stdout.strip(),
            docker_stderr=completed.stderr.strip(),
        )
    return success_payload(
        "moltbox gateway update",
        update_mode="detached_self_update",
        helper_container=helper_name,
        helper_command=command,
        artifact=prepared.artifact,
        render={
            "output_dir": str(prepared.rendered.output_dir),
            "compose_file": str(prepared.rendered.compose_file),
            "render_manifest_path": str(prepared.rendered.render_manifest_path),
        },
        network_bootstrap=network_bootstrap.get("details"),
        scheduled=True,
    )


def update_gateway(config: Any, *, version: str | None = None, commit: str | None = None) -> dict[str, Any]:
    if _inside_container():
        return _schedule_gateway_self_update(config, version=version, commit=commit)
    payload = deploy_service(config, "gateway", version=version, commit=commit)
    payload["command"] = "moltbox gateway update"
    return payload
