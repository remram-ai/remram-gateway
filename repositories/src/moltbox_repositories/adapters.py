from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .config import AppConfig
from .errors import ConfigError, ValidationError
from .layout import ensure_host_layout


@dataclass(frozen=True)
class RepositoryCheckout:
    name: str
    url: str
    checkout_dir: Path
    git_head: str

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "url": self.url,
            "checkout_dir": str(self.checkout_dir),
            "git_head": self.git_head,
        }


@dataclass(frozen=True)
class RepositoryResource:
    repository: RepositoryCheckout
    relative_path: str
    path: Path

    def as_dict(self) -> dict[str, str]:
        return {
            **self.repository.as_dict(),
            "relative_path": self.relative_path,
            "path": str(self.path),
        }


def _git_command(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = ["git", *args]
    return subprocess.run(command, cwd=str(cwd) if cwd is not None else None, capture_output=True, text=True, check=False)


def _require_url(name: str, url: str | None) -> str:
    if url:
        return url
    raise ConfigError(
        f"{name} repository URL is not configured",
        f"set MOLTBOX_{name.upper()}_REPO_URL or repositories.{name}.url before running this command",
        repository=name,
    )


def _repository_checkout(config: AppConfig, name: str, url: str | None) -> RepositoryCheckout:
    ensure_host_layout(config.layout)
    resolved_url = _require_url(name, url)
    checkout_dir = config.layout.repositories_dir / name
    if not checkout_dir.exists():
        checkout_dir.parent.mkdir(parents=True, exist_ok=True)
        cloned = _git_command("clone", resolved_url, str(checkout_dir))
        if cloned.returncode != 0:
            raise ConfigError(
                f"failed to clone {name} repository",
                "verify the repository URL and gateway Git access, then rerun the command",
                repository=name,
                repository_url=resolved_url,
                git_stderr=cloned.stderr.strip(),
            )
    elif not (checkout_dir / ".git").exists():
        raise ConfigError(
            f"repository cache path is not a Git checkout: {checkout_dir}",
            "remove the invalid cache directory or choose a different state root before rerunning the command",
            repository=name,
            checkout_dir=str(checkout_dir),
        )
    else:
        pulled = _git_command("-C", str(checkout_dir), "pull", "--ff-only")
        if pulled.returncode != 0:
            raise ConfigError(
                f"failed to update {name} repository cache",
                "resolve the Git pull error in the cached checkout and rerun the command",
                repository=name,
                checkout_dir=str(checkout_dir),
                git_stderr=pulled.stderr.strip(),
            )
    head = _git_command("-C", str(checkout_dir), "rev-parse", "HEAD")
    if head.returncode != 0:
        raise ConfigError(
            f"failed to resolve {name} repository HEAD",
            "repair the cached Git checkout and rerun the command",
            repository=name,
            checkout_dir=str(checkout_dir),
            git_stderr=head.stderr.strip(),
        )
    return RepositoryCheckout(name=name, url=resolved_url, checkout_dir=checkout_dir, git_head=head.stdout.strip())


def ensure_services_repository(config: AppConfig) -> RepositoryCheckout:
    return _repository_checkout(config, "services", config.services_repo_url)


def ensure_runtime_repository(config: AppConfig) -> RepositoryCheckout:
    return _repository_checkout(config, "runtime", config.runtime_repo_url)


def ensure_skills_repository(config: AppConfig) -> RepositoryCheckout:
    return _repository_checkout(config, "skills", config.skills_repo_url)


def service_resource(config: AppConfig, service_name: str) -> RepositoryResource:
    checkout = ensure_services_repository(config)
    relative = Path("services") / service_name
    path = checkout.checkout_dir / relative
    if not path.exists() or not path.is_dir():
        raise ValidationError(
            f"service '{service_name}' was not found in the services repository",
            "create the service definition under moltbox-services/services/ or deploy a different service",
            service=service_name,
            repository=checkout.as_dict(),
            expected_path=str(path),
        )
    return RepositoryResource(repository=checkout, relative_path=str(relative).replace("\\", "/"), path=path)


def list_service_resources(config: AppConfig) -> list[RepositoryResource]:
    checkout = ensure_services_repository(config)
    services_root = checkout.checkout_dir / "services"
    if not services_root.exists():
        raise ValidationError(
            "services repository does not contain a services/ directory",
            "create moltbox-services/services/ and rerun the command",
            repository=checkout.as_dict(),
            expected_path=str(services_root),
        )
    resources: list[RepositoryResource] = []
    for path in sorted(candidate for candidate in services_root.iterdir() if candidate.is_dir()):
        relative = Path("services") / path.name
        resources.append(RepositoryResource(repository=checkout, relative_path=str(relative).replace("\\", "/"), path=path))
    return resources


def runtime_resource(config: AppConfig, component_name: str) -> RepositoryResource:
    checkout = ensure_runtime_repository(config)
    relative = Path(component_name)
    path = checkout.checkout_dir / relative
    if not path.exists():
        raise ValidationError(
            f"runtime configuration for '{component_name}' was not found in the runtime repository",
            "create the runtime directory in moltbox-runtime or choose a different component",
            component=component_name,
            repository=checkout.as_dict(),
            expected_path=str(path),
        )
    return RepositoryResource(repository=checkout, relative_path=str(relative).replace("\\", "/"), path=path)


def _skill_dir_candidates(root: Path, skill_name: str) -> list[Path]:
    return [root / "skills" / skill_name, root / skill_name]


def _skill_manifest_candidates(skill_dir: Path) -> list[Path]:
    names = (
        "moltbox-skill.yaml",
        "moltbox-skill.yml",
        "deployment.yaml",
        "deployment.yml",
        "skill.yaml",
        "skill.yml",
    )
    return [skill_dir / name for name in names]


def load_skill_manifest(config: AppConfig, skill_name: str) -> tuple[RepositoryResource, dict[str, Any]]:
    checkout = ensure_skills_repository(config)
    for skill_dir in _skill_dir_candidates(checkout.checkout_dir, skill_name):
        if not skill_dir.exists() or not skill_dir.is_dir():
            continue
        for manifest_path in _skill_manifest_candidates(skill_dir):
            if not manifest_path.exists():
                continue
            loaded = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            if loaded is None:
                loaded = {}
            if not isinstance(loaded, dict):
                raise ValidationError(
                    f"skill manifest '{manifest_path}' must contain a mapping",
                    "rewrite the skill manifest as YAML object data and rerun the command",
                    skill=skill_name,
                    manifest_path=str(manifest_path),
                )
            resource = RepositoryResource(
                repository=checkout,
                relative_path=str(manifest_path.relative_to(checkout.checkout_dir)).replace("\\", "/"),
                path=manifest_path,
            )
            return resource, loaded
    raise ValidationError(
        f"skill '{skill_name}' was not found in the skills repository",
        "create the skill manifest in remram-skills and rerun the command",
        skill=skill_name,
        repository=checkout.as_dict(),
    )
