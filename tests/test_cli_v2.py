from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
from pathlib import Path

from moltbox_cli.cli import execute
from moltbox_commands.core.config import resolve_config
from moltbox_commands.core.layout import find_repo_root
from moltbox_docker import engine as docker_engine
from moltbox_repos import adapters as repo_adapters
from moltbox_services import pipeline as service_pipeline

from .conftest import create_git_repo, run_cli


class Args:
    config_path = None
    state_root = None
    runtime_artifacts_root = None
    services_repo_url = None
    runtime_repo_url = None
    skills_repo_url = None
    internal_host = None
    internal_port = None
    cli_path = None


def test_version_returns_json() -> None:
    completed = run_cli("--version")
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert "version" in payload


def test_help_lists_v2_namespaces() -> None:
    completed = run_cli("--help")
    assert completed.returncode == 0
    assert "usage: moltbox" in completed.stdout
    assert "service deploy <service>" in completed.stdout
    assert "openclaw-dev" in completed.stdout
    assert "skill deploy <skill>" in completed.stdout


def test_failed_command_returns_nonzero_exit_code() -> None:
    completed = run_cli("service", "list")
    assert completed.returncode != 0
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["error_type"] == "config_error"


def test_service_list_reads_external_services_repository(tmp_path: Path) -> None:
    services_repo = create_git_repo(
        tmp_path / "moltbox-services",
        {
            "services/openclaw-dev/compose.yml.template": "services:\n  openclaw-dev:\n    image: example/openclaw-dev:latest\n",
            "services/openclaw-test/compose.yml.template": "services:\n  openclaw-test:\n    image: example/openclaw-test:latest\n",
            "services/caddy/compose.yml.template": "services:\n  caddy:\n    image: caddy:latest\n",
        },
    )
    env = {
        "MOLTBOX_STATE_ROOT": str(tmp_path / ".remram"),
        "MOLTBOX_RUNTIME_ROOT": str(tmp_path / "Moltbox"),
        "MOLTBOX_SERVICES_REPO_URL": str(services_repo),
        "MOLTBOX_REPO_ROOT": str(Path(__file__).resolve().parents[1]),
    }
    completed = run_cli("service", "list", env=env)
    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    service_names = {service["name"] for service in payload["services"]}
    assert {"openclaw-dev", "openclaw-test", "caddy"} == service_names


def test_services_repo_checkout_is_serialized_for_parallel_calls(tmp_path: Path, monkeypatch) -> None:
    services_repo = create_git_repo(
        tmp_path / "moltbox-services",
        {
            "services/caddy/compose.yml.template": "services:\n  caddy:\n    image: caddy:latest\n",
        },
    )
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    monkeypatch.setenv("MOLTBOX_SERVICES_REPO_URL", str(services_repo))
    monkeypatch.setenv("MOLTBOX_REPO_ROOT", str(Path(__file__).resolve().parents[1]))
    config = resolve_config(Args())

    def load_services() -> list[str]:
        return [resource.relative_path for resource in repo_adapters.list_services(config)]

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(load_services)
        second = executor.submit(load_services)

    assert first.result() == ["services/caddy"]
    assert second.result() == ["services/caddy"]


def test_services_repo_checkout_marks_cached_repo_as_safe_directory(tmp_path: Path, monkeypatch) -> None:
    services_repo = create_git_repo(
        tmp_path / "moltbox-services",
        {
            "services/caddy/compose.yml.template": "services:\n  caddy:\n    image: caddy:latest\n",
        },
    )
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    monkeypatch.setenv("MOLTBOX_SERVICES_REPO_URL", str(services_repo))
    config = resolve_config(Args())
    checkout_dir = config.layout.repos_root / "moltbox-services"
    checkout_dir.parent.mkdir(parents=True, exist_ok=True)
    _ = repo_adapters.list_services(config)

    commands: list[tuple[str, ...]] = []
    real_git = repo_adapters._git

    def recording_git(*args: str, cwd: Path | None = None):
        commands.append(tuple(args))
        return real_git(*args, cwd=cwd)

    monkeypatch.setattr(repo_adapters, "_git", recording_git)

    _ = repo_adapters.list_services(config)

    assert ("config", "--global", "--add", "safe.directory", str(checkout_dir)) in commands
    assert ("config", "--global", "--add", "safe.directory", str(services_repo)) in commands


def test_runtime_config_sync_reads_external_runtime_repository(tmp_path: Path, monkeypatch) -> None:
    runtime_repo = create_git_repo(
        tmp_path / "moltbox-runtime",
        {
            "openclaw-dev/openclaw.json.template": (
                json.dumps({"gateway": {"controlUi": {"allowedOrigins": ["http://127.0.0.1:{{ gateway_port }}"]}}}, indent=2) + "\n"
            ),
            "openclaw-dev/channels.yaml.template": "channels:\n  discord:\n    enabled: {{ discord_enabled }}\n",
        },
    )
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_REPO_URL", str(runtime_repo))
    monkeypatch.setenv("MOLTBOX_DISCORD_ENABLED_DEV", "true")
    monkeypatch.setenv("MOLTBOX_REPO_ROOT", str(Path(__file__).resolve().parents[1]))
    resolve_config(Args())

    payload = execute(["openclaw-dev", "config", "sync"])

    assert payload["ok"] is True
    staging_dir = Path(str(payload["staging_dir"]))
    runtime_root = Path(str(payload["runtime_root"]))
    synced = json.loads((staging_dir / "openclaw.json").read_text(encoding="utf-8"))
    allowed_origins = synced["gateway"]["controlUi"]["allowedOrigins"]
    assert "http://127.0.0.1:18790" in allowed_origins
    assert (staging_dir / "channels.yaml").read_text(encoding="utf-8").strip().endswith("enabled: true")
    assert not (runtime_root / "openclaw.json").exists()


def test_runtime_config_sync_renders_templates_without_touching_runtime_state(tmp_path: Path, monkeypatch) -> None:
    runtime_repo = create_git_repo(
        tmp_path / "moltbox-runtime",
        {
            "openclaw-dev/openclaw.json.template": (
                json.dumps(
                    {
                        "gateway": {
                            "controlUi": {
                                "allowedOrigins": ["http://127.0.0.1:{{ gateway_port }}"],
                                "dangerouslyAllowHostHeaderOriginFallback": True,
                            }
                        },
                        "plugins": {"enabled": True},
                    },
                    indent=2,
                )
                + "\n"
            ),
            "openclaw-dev/channels.yaml.template": (
                "channels:\n"
                "  discord:\n"
                "    enabled: {{ discord_enabled }}\n"
                "{{ discord_guilds_block }}\n"
            ),
        },
    )
    runtime_root = tmp_path / "Moltbox" / "openclaw-dev"
    (runtime_root / "workspace").mkdir(parents=True, exist_ok=True)
    (runtime_root / "credentials").mkdir(parents=True, exist_ok=True)
    (runtime_root / "workspace" / "notes.txt").write_text("keep\n", encoding="utf-8")
    (runtime_root / "credentials" / "token.txt").write_text("keep\n", encoding="utf-8")
    (runtime_root / "openclaw.json").write_text(
        json.dumps(
            {
                "gateway": {
                    "auth": {"token": "existing-token"},
                    "controlUi": {"allowedOrigins": ["https://already.example"]},
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_REPO_URL", str(runtime_repo))
    monkeypatch.setenv("MOLTBOX_PUBLIC_HOST_IP", "192.168.1.50")
    monkeypatch.setenv("MOLTBOX_PUBLIC_HOSTNAME", "moltbox-lab")
    monkeypatch.setenv("MOLTBOX_DISCORD_ENABLED_DEV", "true")
    monkeypatch.setenv("MOLTBOX_DISCORD_GUILD_ID_DEV", "1481179628323340393")
    monkeypatch.setenv("MOLTBOX_DISCORD_CHANNEL_ID_DEV", "1481180067580219402")
    monkeypatch.setenv("MOLTBOX_REPO_ROOT", str(Path(__file__).resolve().parents[1]))
    resolve_config(Args())

    payload = execute(["openclaw-dev", "config", "sync"])

    assert payload["ok"] is True
    staging_dir = Path(str(payload["staging_dir"]))
    synced = json.loads((staging_dir / "openclaw.json").read_text(encoding="utf-8"))
    allowed_origins = synced["gateway"]["controlUi"]["allowedOrigins"]
    assert "http://127.0.0.1:18790" in allowed_origins
    channels_text = (staging_dir / "channels.yaml").read_text(encoding="utf-8")
    assert "enabled: true" in channels_text
    assert '"1481179628323340393"' in channels_text
    assert '"1481180067580219402"' in channels_text
    preserved = json.loads((runtime_root / "openclaw.json").read_text(encoding="utf-8"))
    assert preserved["gateway"]["auth"]["token"] == "existing-token"
    assert "https://already.example" in preserved["gateway"]["controlUi"]["allowedOrigins"]
    assert (runtime_root / "workspace" / "notes.txt").read_text(encoding="utf-8") == "keep\n"
    assert (runtime_root / "credentials" / "token.txt").read_text(encoding="utf-8") == "keep\n"


def test_skill_deploy_uses_skill_recipe_plan(tmp_path: Path, monkeypatch) -> None:
    skills_repo = create_git_repo(
        tmp_path / "remram-skills",
        {
            "skills/discord/deployment.yaml": (
                "service_deploy:\n"
                "  - caddy\n"
                "runtime_sync:\n"
                "  - openclaw-dev\n"
                "runtime_reload:\n"
                "  - openclaw-dev\n"
            ),
        },
    )
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    monkeypatch.setenv("MOLTBOX_SKILLS_REPO_URL", str(skills_repo))
    monkeypatch.setenv("MOLTBOX_REPO_ROOT", str(Path(__file__).resolve().parents[1]))

    from moltbox_commands import skill as skill_commands

    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(skill_commands, "deploy_service", lambda config, name, version=None, commit=None: calls.append(("service_deploy", name)) or {"ok": True})
    monkeypatch.setattr(skill_commands, "sync_component_config", lambda config, name: calls.append(("runtime_sync", name)) or {"ok": True})
    monkeypatch.setattr(skill_commands, "execute_component", lambda config, name, verb: calls.append((verb, name)) or {"ok": True})

    payload = execute(["skill", "deploy", "discord"])

    assert payload["ok"] is True
    assert calls == [
        ("service_deploy", "caddy"),
        ("runtime_sync", "openclaw-dev"),
        ("reload", "openclaw-dev"),
    ]


def test_skill_deploy_plugin_backed_package_uses_runtime_installer(tmp_path: Path, monkeypatch) -> None:
    skills_repo = create_git_repo(
        tmp_path / "remram-skills",
        {
            "skills/semantic-router/SKILL.md": "# Semantic Router\n",
            "skills/semantic-router/openclaw.plugin.json": json.dumps({"id": "semantic-router", "skills": ["./"]}, indent=2) + "\n",
            "skills/semantic-router/example-config.json": json.dumps({"plugins": {"allow": ["semantic-router"]}}, indent=2) + "\n",
        },
    )
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    monkeypatch.setenv("MOLTBOX_SKILLS_REPO_URL", str(skills_repo))
    monkeypatch.setenv("MOLTBOX_REPO_ROOT", str(Path(__file__).resolve().parents[1]))

    from moltbox_commands import skill as skill_commands

    captured: dict[str, object] = {}

    def fake_plugin_install(config, *, skill_name: str, package_dir: Path):
        captured["skill_name"] = skill_name
        captured["package_dir"] = package_dir
        return {"install_mode": "plugin-backed", "plugin_id": "semantic-router"}

    monkeypatch.setattr(skill_commands.runtime_skill_operations, "deploy_plugin_backed_skill", fake_plugin_install)
    monkeypatch.setattr(skill_commands.runtime_skill_operations, "deploy_pure_skill", lambda *args, **kwargs: {"install_mode": "pure-skill"})

    payload = execute(["skill", "deploy", "semantic-router"])

    assert payload["ok"] is True
    assert payload["skill_package"]["relative_path"] == "skills/semantic-router"
    assert payload["install_result"]["install_mode"] == "plugin-backed"
    assert captured["skill_name"] == "semantic-router"
    assert Path(str(captured["package_dir"])).name == "semantic-router"


def test_service_deploy_gateway_uses_clean_service_pipeline(tmp_path: Path, monkeypatch) -> None:
    services_repo = create_git_repo(
        tmp_path / "moltbox-services",
        {
            "services/gateway/compose.yml.template": (
                "services:\n"
                "  gateway:\n"
                "    image: moltbox-gateway:{{ selected_artifact }}\n"
                "    build:\n"
                "      context: .\n"
                "      dockerfile: Dockerfile\n"
                "      args:\n"
                "        GATEWAY_GIT_REF: {{ selected_artifact }}\n"
                "    container_name: {{ container_name }}\n"
            ),
            "services/gateway/service.yaml": (
                "compose_project: gateway\n"
                "container_names:\n"
                "  - gateway\n"
                "runtime_required: true\n"
                "build_on_deploy: true\n"
                "skip_pull: true\n"
            ),
            "services/gateway/Dockerfile": "FROM docker:29-cli\n",
        },
    )
    runtime_repo = create_git_repo(
        tmp_path / "moltbox-runtime",
        {
            "gateway/config.yaml": (
                "paths:\n"
                "  state_root: /srv/remram/state\n"
                "  runtime_root: /srv/remram/runtime\n"
                "gateway:\n"
                "  host: 0.0.0.0\n"
                "  port: 7474\n"
            ),
        },
    )
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    monkeypatch.setenv("MOLTBOX_SERVICES_REPO_URL", str(services_repo))
    monkeypatch.setenv("MOLTBOX_RUNTIME_REPO_URL", str(runtime_repo))
    monkeypatch.setenv("MOLTBOX_REPO_ROOT", str(Path(__file__).resolve().parents[1]))
    resolve_config(Args())

    calls: list[str] = []
    monkeypatch.setattr(service_pipeline.docker_engine, "docker_available", lambda: True)
    monkeypatch.setattr(service_pipeline.docker_engine, "inspect_containers", lambda names: {"ok": True, "details": {"container_state": {"containers": []}, "container_ids": []}})
    monkeypatch.setattr(service_pipeline.docker_engine, "pull_stack", lambda render_dir, compose_project: calls.append("pull") or {"ok": True})
    monkeypatch.setattr(
        service_pipeline.docker_engine,
        "deploy_stack",
        lambda **kwargs: calls.append(f"deploy:{kwargs['build_images']}") or {"ok": True, "details": {"compose_command": ["docker", "compose"]}},
    )
    monkeypatch.setattr(service_pipeline.docker_engine, "validate_containers", lambda names, timeout_seconds=30, poll_interval_seconds=2: calls.append("validate") or {"ok": True, "details": {"result": "pass"}})

    payload = execute(["service", "deploy", "gateway"])

    assert payload["ok"] is True
    assert payload["resolved_service"] == "gateway"
    assert payload["service_source"]["relative_path"] == "services/gateway"
    assert payload["runtime_source"]["relative_path"] == "gateway"
    assert calls == ["deploy:True", "validate"]


def test_service_deploy_runtime_mounts_rendered_config_and_prepares_state_root(tmp_path: Path, monkeypatch) -> None:
    services_repo = create_git_repo(
        tmp_path / "moltbox-services",
        {
            "services/openclaw-dev/compose.yml.template": (
                "services:\n"
                "  openclaw-dev:\n"
                "    image: ghcr.io/openclaw/openclaw:latest\n"
                "    container_name: \"{{ container_name }}\"\n"
                "    environment:\n"
                "      OPENCLAW_CONFIG_DIR: /app/config/openclaw\n"
                "    ports:\n"
                "      - \"{{ gateway_port }}:18789\"\n"
                "    volumes:\n"
                "      - \"{{ runtime_component_dir }}:/home/node/.openclaw\"\n"
                "      - \"./config/{{ service_name }}:/app/config/openclaw:ro\"\n"
                "    command:\n"
                "      - sh\n"
                "      - -lc\n"
                "      - >\n"
                "        mkdir -p /home/node/.openclaw &&\n"
                "        if [ ! -f /home/node/.openclaw/openclaw.json ]; then cp /app/config/openclaw/openclaw.json /home/node/.openclaw/openclaw.json; fi &&\n"
                "        exec node dist/index.js gateway --bind lan --port 18789\n"
            ),
            "services/openclaw-dev/service.yaml": (
                "compose_project: openclaw-dev\n"
                "container_names:\n"
                "  - openclaw-dev\n"
                "runtime_required: true\n"
            ),
        },
    )
    runtime_repo = create_git_repo(
        tmp_path / "moltbox-runtime",
        {
            "openclaw-dev/openclaw.json.template": json.dumps({"gateway": {"controlUi": {"allowedOrigins": []}}}, indent=2) + "\n",
            "openclaw-dev/channels.yaml.template": (
                "channels:\n"
                "  discord:\n"
                "    enabled: {{ discord_enabled }}\n"
                "{{ discord_guilds_block }}\n"
            ),
        },
    )
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    monkeypatch.setenv("MOLTBOX_SERVICES_REPO_URL", str(services_repo))
    monkeypatch.setenv("MOLTBOX_RUNTIME_REPO_URL", str(runtime_repo))
    monkeypatch.setenv("MOLTBOX_PUBLIC_HOST_IP", "192.168.1.50")
    monkeypatch.setenv("MOLTBOX_DISCORD_ENABLED_DEV", "true")
    monkeypatch.setenv("MOLTBOX_DISCORD_GUILD_ID_DEV", "1481179628323340393")
    monkeypatch.setenv("MOLTBOX_DISCORD_CHANNEL_ID_DEV", "1481180067580219402")
    monkeypatch.setenv("MOLTBOX_REPO_ROOT", str(Path(__file__).resolve().parents[1]))
    config = resolve_config(Args())
    runtime_root = config.layout.runtime_component_dir("openclaw-dev")

    monkeypatch.setattr(service_pipeline.docker_engine, "docker_available", lambda: True)
    monkeypatch.setattr(
        service_pipeline.docker_engine,
        "inspect_containers",
        lambda names: {"ok": True, "details": {"container_state": {"containers": []}, "container_ids": []}},
    )

    def fake_pull(render_dir: Path, compose_project: str) -> dict[str, object]:
        compose_text = (render_dir / "compose.yml").read_text(encoding="utf-8")
        assert compose_project == "openclaw-dev"
        assert '"18790:18789"' in compose_text
        assert str(runtime_root).replace("\\", "/") in compose_text.replace("\\", "/")
        assert './config/openclaw-dev:/app/config/openclaw:ro' in compose_text
        assert "OPENCLAW_CONFIG_DIR: /app/config/openclaw" in compose_text
        assert "cp /app/config/openclaw/openclaw.json /home/node/.openclaw/openclaw.json" in compose_text
        assert (render_dir / "config" / "openclaw-dev" / "openclaw.json").exists()
        channels_text = (render_dir / "config" / "openclaw-dev" / "channels.yaml").read_text(encoding="utf-8")
        assert "enabled: true" in channels_text
        assert '"1481179628323340393"' in channels_text
        assert '"1481180067580219402"' in channels_text
        assert "{{ discord_enabled }}" not in channels_text
        assert "{{ discord_guilds_block }}" not in channels_text
        return {"ok": True}

    monkeypatch.setattr(service_pipeline.docker_engine, "pull_stack", fake_pull)
    monkeypatch.setattr(
        service_pipeline.docker_engine,
        "deploy_stack",
        lambda **kwargs: {"ok": True, "details": {"compose_command": ["docker", "compose"]}},
    )
    monkeypatch.setattr(
        service_pipeline.docker_engine,
        "validate_containers",
        lambda names, timeout_seconds=30, poll_interval_seconds=2: {"ok": True, "details": {"result": "pass"}},
    )

    payload = execute(["service", "deploy", "openclaw-dev"])

    assert payload["ok"] is True
    assert payload["runtime_source"]["relative_path"] == "openclaw-dev"


def test_validate_containers_fails_when_docker_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(docker_engine, "docker_available", lambda: False)

    payload = docker_engine.validate_containers(["opensearch"])

    assert payload["ok"] is False
    assert payload["details"]["result"] == "fail"
    assert payload["details"]["reason"] == "docker_not_available"


def test_validate_containers_fails_when_health_never_becomes_ready(monkeypatch) -> None:
    inspections = iter(
        [
            {
                "ok": True,
                "details": {
                    "container_state": {
                        "containers": [
                            {
                                "name": "openclaw-dev",
                                "present": True,
                                "state": "running",
                                "health": "starting",
                                "container_id": "abc",
                                "image": "ghcr.io/openclaw/openclaw:latest",
                            }
                        ]
                    }
                },
            },
            {
                "ok": True,
                "details": {
                    "container_state": {
                        "containers": [
                            {
                                "name": "openclaw-dev",
                                "present": True,
                                "state": "restarting",
                                "health": None,
                                "container_id": "abc",
                                "image": "ghcr.io/openclaw/openclaw:latest",
                            }
                        ]
                    }
                },
            },
        ]
    )
    monotonic_values = iter([0.0, 0.5, 1.5])

    monkeypatch.setattr(docker_engine, "inspect_containers", lambda names: next(inspections))
    monkeypatch.setattr(docker_engine.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(docker_engine.time, "sleep", lambda seconds: None)

    payload = docker_engine.validate_containers(["openclaw-dev"], timeout_seconds=1, poll_interval_seconds=0)

    assert payload["ok"] is False
    assert payload["details"]["result"] == "fail"
    assert payload["details"]["reason"] == "containers_not_ready"
    assert payload["errors"] == ["openclaw-dev"]


def test_resolve_openclaw_container_prefers_environment_runtime_over_legacy(monkeypatch) -> None:
    from moltbox_runtime import skills as runtime_skills

    existing = {"openclaw-dev", "moltbox-openclaw"}
    monkeypatch.setattr(runtime_skills, "_container_exists", lambda container_name: container_name in existing)

    assert runtime_skills.resolve_openclaw_container() == "openclaw-dev"


def test_resolve_gateway_port_prefers_container_command_port(monkeypatch) -> None:
    from moltbox_runtime import skills as runtime_skills

    inspected = [
        {
            "Config": {
                "Cmd": [
                    "sh",
                    "-lc",
                    "exec node dist/index.js gateway --bind lan --port 18789",
                    "--port",
                    "18789",
                ]
            }
        }
    ]

    class Completed:
        returncode = 0
        stdout = json.dumps(inspected)
        stderr = ""

    monkeypatch.setattr(runtime_skills, "_docker", lambda *args, input_text=None: Completed())

    assert runtime_skills._resolve_gateway_port("openclaw-dev") == 18789


def test_service_deploy_opensearch_uses_external_runtime_config(tmp_path: Path, monkeypatch) -> None:
    services_repo = create_git_repo(
        tmp_path / "moltbox-services",
        {
            "services/opensearch/compose.yml.template": (
                "services:\n"
                "  opensearch:\n"
                "    image: \"${OPENSEARCH_IMAGE:-moltbox-opensearch:local}\"\n"
                "    build:\n"
                "      context: .\n"
                "      dockerfile: Dockerfile\n"
                "      args:\n"
                "        OPENSEARCH_BASE_IMAGE: \"${OPENSEARCH_BASE_IMAGE:-opensearchproject/opensearch:2.18.0}\"\n"
                "    container_name: \"{{ container_name }}\"\n"
                "    env_file:\n"
                "      - \"./config/opensearch/.env\"\n"
                "      - \"./config/opensearch/container.env\"\n"
                "    volumes:\n"
                "      - \"./config/opensearch/opensearch.yml:/usr/share/opensearch/config/opensearch.yml:ro\"\n"
                "    networks:\n"
                "      - moltbox_internal\n\n"
                "networks:\n"
                "  moltbox_internal:\n"
                "    external: true\n"
                "    name: \"{{ internal_network_name }}\"\n"
            ),
            "services/opensearch/Dockerfile": (
                "ARG OPENSEARCH_BASE_IMAGE=opensearchproject/opensearch:2.18.0\n"
                "FROM ${OPENSEARCH_BASE_IMAGE}\n"
            ),
            "services/opensearch/service.yaml": (
                "compose_project: opensearch\n"
                "container_names:\n"
                "  - opensearch\n"
                "runtime_required: true\n"
            ),
        },
    )
    runtime_repo = create_git_repo(
        tmp_path / "moltbox-runtime",
        {
            "opensearch/.env": "OPENSEARCH_ENV=1\n",
            "opensearch/container.env": "DISCOVERY_TYPE=single-node\n",
            "opensearch/opensearch.yml": "cluster.name: remram-moltbox\n",
        },
    )
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    monkeypatch.setenv("MOLTBOX_SERVICES_REPO_URL", str(services_repo))
    monkeypatch.setenv("MOLTBOX_RUNTIME_REPO_URL", str(runtime_repo))
    monkeypatch.setenv("MOLTBOX_REPO_ROOT", str(Path(__file__).resolve().parents[1]))
    resolve_config(Args())

    monkeypatch.setattr(service_pipeline.docker_engine, "docker_available", lambda: True)
    monkeypatch.setattr(
        service_pipeline.docker_engine,
        "inspect_containers",
        lambda names: {"ok": True, "details": {"container_state": {"containers": []}, "container_ids": []}},
    )

    def fake_pull(render_dir: Path, compose_project: str) -> dict[str, object]:
        assert compose_project == "opensearch"
        compose_text = (render_dir / "compose.yml").read_text(encoding="utf-8")
        assert 'name: "moltbox_moltbox_internal"' in compose_text
        assert (render_dir / "config" / "opensearch" / ".env").exists()
        assert (render_dir / "config" / "opensearch" / "container.env").exists()
        assert (render_dir / "config" / "opensearch" / "opensearch.yml").exists()
        return {"ok": True}

    monkeypatch.setattr(service_pipeline.docker_engine, "pull_stack", fake_pull)
    monkeypatch.setattr(
        service_pipeline.docker_engine,
        "deploy_stack",
        lambda **kwargs: {"ok": True, "details": {"compose_command": ["docker", "compose"]}},
    )
    monkeypatch.setattr(
        service_pipeline.docker_engine,
        "validate_containers",
        lambda names, timeout_seconds=30, poll_interval_seconds=2: {"ok": True, "details": {"result": "pass"}},
    )

    payload = execute(["service", "deploy", "opensearch"])

    assert payload["ok"] is True
    assert payload["runtime_source"]["relative_path"] == "opensearch"
    manifest = json.loads(Path(payload["render"]["render_manifest_path"]).read_text(encoding="utf-8"))
    runtime_source_paths = {Path(path).name for path in manifest["runtime_source_paths"]}
    assert runtime_source_paths == {".env", "container.env", "opensearch.yml"}


def test_service_deploy_caddy_uses_external_runtime_config(tmp_path: Path, monkeypatch) -> None:
    services_repo = create_git_repo(
        tmp_path / "moltbox-services",
        {
            "services/caddy/compose.yml.template": (
                "services:\n"
                "  caddy:\n"
                "    image: \"${CADDY_IMAGE:-caddy:2.8.4}\"\n"
                "    container_name: \"{{ container_name }}\"\n"
                "    extra_hosts:\n"
                "      - \"host.docker.internal:host-gateway\"\n"
                "    volumes:\n"
                "      - \"./config/caddy/Caddyfile:/etc/caddy/Caddyfile:ro\"\n"
                "      - \"{{ shared_root }}/data:/data\"\n"
                "      - \"{{ shared_root }}/config:/config\"\n"
                "    networks:\n"
                "      - moltbox_internal\n"
                "networks:\n"
                "  moltbox_internal:\n"
                "    external: true\n"
                "    name: \"{{ internal_network_name }}\"\n"
            ),
            "services/caddy/service.yaml": (
                "compose_project: caddy\n"
                "container_names:\n"
                "  - caddy\n"
                "runtime_required: true\n"
            ),
        },
    )
    runtime_repo = create_git_repo(
        tmp_path / "moltbox-runtime",
        {
            "caddy/Caddyfile.template": (
                ":80 {\n"
                "  respond /healthz 200\n\n"
                "  @cli host moltbox-cli\n"
                "  handle @cli {\n"
                "    reverse_proxy {{ gateway_container_name }}:{{ gateway_container_port }}\n"
                "  }\n\n"
                "  @dev host moltbox-dev{{ dev_public_host }}\n"
                "  handle @dev {\n"
                "    reverse_proxy host.docker.internal:18790\n"
                "  }\n\n"
                "  @test host moltbox-test{{ test_public_host }}\n"
                "  handle @test {\n"
                "    reverse_proxy host.docker.internal:28789\n"
                "  }\n\n"
                "  @prod host moltbox-prod{{ prod_public_host }}\n"
                "  handle @prod {\n"
                "    reverse_proxy host.docker.internal:38789\n"
                "  }\n"
                "}\n"
            ),
        },
    )
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    monkeypatch.setenv("MOLTBOX_SERVICES_REPO_URL", str(services_repo))
    monkeypatch.setenv("MOLTBOX_RUNTIME_REPO_URL", str(runtime_repo))
    monkeypatch.setenv("MOLTBOX_PUBLIC_HOSTNAME", "moltbox-lab")
    monkeypatch.setenv("MOLTBOX_REPO_ROOT", str(Path(__file__).resolve().parents[1]))
    resolve_config(Args())

    monkeypatch.setattr(service_pipeline.docker_engine, "docker_available", lambda: True)
    monkeypatch.setattr(
        service_pipeline.docker_engine,
        "inspect_containers",
        lambda names: {"ok": True, "details": {"container_state": {"containers": []}, "container_ids": []}},
    )

    def fake_pull(render_dir: Path, compose_project: str) -> dict[str, object]:
        assert compose_project == "caddy"
        caddyfile = (render_dir / "config" / "caddy" / "Caddyfile").read_text(encoding="utf-8")
        compose_text = (render_dir / "compose.yml").read_text(encoding="utf-8")
        assert 'name: "moltbox_moltbox_internal"' in compose_text
        assert "host.docker.internal:host-gateway" in compose_text
        assert "reverse_proxy gateway:7474" in caddyfile
        assert "reverse_proxy host.docker.internal:18790" in caddyfile
        assert "dev.moltbox-lab" in caddyfile
        assert "test.moltbox-lab" in caddyfile
        assert "prod.moltbox-lab" in caddyfile
        return {"ok": True}

    monkeypatch.setattr(service_pipeline.docker_engine, "pull_stack", fake_pull)
    monkeypatch.setattr(
        service_pipeline.docker_engine,
        "deploy_stack",
        lambda **kwargs: {"ok": True, "details": {"compose_command": ["docker", "compose"]}},
    )
    monkeypatch.setattr(
        service_pipeline.docker_engine,
        "validate_containers",
        lambda names, timeout_seconds=30, poll_interval_seconds=2: {"ok": True, "details": {"result": "pass"}},
    )

    payload = execute(["service", "deploy", "caddy"])

    assert payload["ok"] is True
    assert payload["runtime_source"]["relative_path"] == "caddy"


def test_service_inspect_uses_first_class_shared_service_runtime_mapping(tmp_path: Path, monkeypatch) -> None:
    services_repo = create_git_repo(
        tmp_path / "moltbox-services",
        {
            "services/caddy/compose.yml.template": "services:\n  caddy:\n    image: caddy:latest\n",
        },
    )
    runtime_repo = create_git_repo(
        tmp_path / "moltbox-runtime",
        {
            "caddy/Caddyfile": ":80 { respond /healthz 200 }\n",
        },
    )
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    monkeypatch.setenv("MOLTBOX_SERVICES_REPO_URL", str(services_repo))
    monkeypatch.setenv("MOLTBOX_RUNTIME_REPO_URL", str(runtime_repo))
    monkeypatch.setenv("MOLTBOX_REPO_ROOT", str(Path(__file__).resolve().parents[1]))

    monkeypatch.setattr(
        service_pipeline.docker_engine,
        "inspect_containers",
        lambda names: {"ok": True, "details": {"container_state": {"containers": []}, "container_ids": []}},
    )

    payload = execute(["service", "inspect", "caddy"])

    assert payload["ok"] is True
    assert payload["component"]["runtime_name"] == "caddy"
    assert payload["runtime_source"]["relative_path"] == "caddy"


def test_openclaw_alias_reload_targets_prod(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    monkeypatch.setenv("MOLTBOX_REPO_ROOT", str(Path(__file__).resolve().parents[1]))

    from moltbox_runtime import operations as runtime_operations

    monkeypatch.setattr(runtime_operations, "reload_component", lambda config, spec: {"runtime": spec.canonical_name, "reload": {"ok": True}})

    payload = execute(["openclaw", "reload"])

    assert payload["ok"] is True
    assert payload["resolved_component"] == "openclaw-prod"


def test_gateway_update_delegates_to_gateway_service_pipeline(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    monkeypatch.setenv("MOLTBOX_REPO_ROOT", str(Path(__file__).resolve().parents[1]))

    from moltbox_commands import gateway as gateway_commands

    monkeypatch.setattr(gateway_commands, "deploy_service", lambda config, service_name, version=None, commit=None: {"ok": True, "resolved_service": service_name, "command": "moltbox service deploy gateway"})

    payload = execute(["gateway", "update"])

    assert payload["ok"] is True
    assert payload["command"] == "moltbox gateway update"


def test_gateway_update_inside_container_uses_detached_helper(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    monkeypatch.setenv("MOLTBOX_REPO_ROOT", str(Path(__file__).resolve().parents[1]))
    monkeypatch.setenv("MOLTBOX_RUNNING_IN_CONTAINER", "1")

    from moltbox_commands import gateway as gateway_commands

    @dataclass(frozen=True)
    class FakeRendered:
        compose_project: str
        compose_file: Path
        output_dir: Path
        render_manifest_path: Path
        metadata: dict[str, object]

    @dataclass(frozen=True)
    class FakePrepared:
        artifact: dict[str, object]
        rendered: FakeRendered

    render_dir = tmp_path / ".remram" / "deploy" / "rendered" / "gateway"
    render_dir.mkdir(parents=True, exist_ok=True)
    (render_dir / "compose.yml").write_text("services:\n  gateway:\n    image: moltbox-gateway:main\n", encoding="utf-8")
    (render_dir / "render-manifest.json").write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(gateway_commands.docker_engine, "docker_available", lambda: True)
    monkeypatch.setattr(gateway_commands.docker_engine, "ensure_external_networks", lambda render_dir: {"ok": True, "details": {}})
    monkeypatch.setattr(
        gateway_commands.service_pipeline,
        "prepare_service_deployment",
        lambda config, spec, version=None, commit=None: FakePrepared(
            artifact={"selected_artifact": commit or "main"},
            rendered=FakeRendered(
                compose_project="gateway",
                compose_file=render_dir / "compose.yml",
                output_dir=render_dir,
                render_manifest_path=render_dir / "render-manifest.json",
                metadata={"build_on_deploy": True},
            ),
        ),
    )

    class Completed:
        def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    captured: dict[str, object] = {"commands": []}

    def fake_subprocess_run(command, capture_output, text, check):
        commands = captured["commands"]
        assert isinstance(commands, list)
        commands.append(command)
        if command[:3] == ["docker", "inspect", "gateway"]:
            return Completed(stdout="moltbox-gateway:main\n")
        return Completed(stdout="helper-123\n")

    monkeypatch.setattr(gateway_commands.subprocess, "run", fake_subprocess_run)

    payload = execute(["gateway", "update", "--commit", "abc123"])

    assert payload["ok"] is True
    assert payload["update_mode"] == "detached_self_update"
    commands = captured["commands"]
    assert isinstance(commands, list)
    assert commands[0][:3] == ["docker", "inspect", "gateway"]
    assert commands[1][0] == "docker"
    assert "docker" in commands[1]
    assert "compose" in commands[1]
    assert "moltbox-gateway:main" in commands[1]
    assert "--build" in commands[1]
    assert payload["artifact"]["selected_artifact"] == "abc123"


def test_gateway_serve_routes_to_gateway_server(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MOLTBOX_STATE_ROOT", str(tmp_path / ".remram"))
    monkeypatch.setenv("MOLTBOX_RUNTIME_ROOT", str(tmp_path / "Moltbox"))
    monkeypatch.setenv("MOLTBOX_REPO_ROOT", str(Path(__file__).resolve().parents[1]))

    from moltbox_cli import cli as cli_module

    called: list[str] = []
    monkeypatch.setattr(cli_module.gateway_server, "serve", lambda config: called.append("serve") or 0)

    assert cli_module.run(["gateway", "serve"]) == 0
    assert called == ["serve"]


def test_find_repo_root_falls_back_to_working_tree_when_package_path_is_outside_checkout(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "remram-gateway"
    nested = repo_root / "nested" / "workdir"
    nested.mkdir(parents=True, exist_ok=True)
    (repo_root / ".git").mkdir()
    monkeypatch.delenv("MOLTBOX_REPO_ROOT", raising=False)
    monkeypatch.chdir(nested)

    resolved = find_repo_root(start=tmp_path / "site-packages" / "moltbox_commands" / "core" / "layout.py")

    assert resolved == repo_root


def test_deploy_stack_bootstraps_external_networks(monkeypatch, tmp_path: Path) -> None:
    compose_dir = tmp_path / "render"
    compose_dir.mkdir(parents=True, exist_ok=True)
    (compose_dir / "compose.yml").write_text(
        "services:\n"
        "  caddy:\n"
        "    image: caddy:latest\n"
        "    networks:\n"
        "      - moltbox_internal\n"
        "networks:\n"
        "  moltbox_internal:\n"
        "    external: true\n"
        "    name: moltbox_moltbox_internal\n",
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def fake_run(command: list[str], cwd: Path | None = None):
        commands.append(command)

        class Completed:
            def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        if command[:4] == ["docker", "network", "inspect", "moltbox_moltbox_internal"]:
            return Completed(1, stderr="not found")
        if command[:4] == ["docker", "network", "create", "moltbox_moltbox_internal"]:
            return Completed(0, stdout="moltbox_moltbox_internal\n")
        if command[:4] == ["docker", "compose", "-f", str(compose_dir / "compose.yml")]:
            return Completed(0)
        if command[:2] == ["docker", "inspect"]:
            return Completed(0, stdout='[{"Id":"abc","State":{"Status":"running"},"Config":{"Image":"caddy:latest"}}]')
        return Completed(0)

    monkeypatch.setattr(docker_engine, "docker_available", lambda: True)
    monkeypatch.setattr(docker_engine, "_run", fake_run)

    payload = docker_engine.deploy_stack(
        render_dir=compose_dir,
        compose_project="caddy",
        container_names=["caddy"],
    )

    assert payload["ok"] is True
    assert payload["details"]["network_bootstrap"]["created"] == ["moltbox_moltbox_internal"]
