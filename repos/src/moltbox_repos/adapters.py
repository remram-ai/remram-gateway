from __future__ import annotations

import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from moltbox_commands.core.config import GatewayConfig
from moltbox_commands.core.errors import ConfigError, ValidationError


@dataclass(frozen=True)
class RepoCheckout:
    name: str
    url: str
    checkout_dir: Path
    head: str

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "url": self.url,
            "checkout_dir": str(self.checkout_dir),
            "head": self.head,
        }


@dataclass(frozen=True)
class RepoResource:
    checkout: RepoCheckout
    path: Path
    relative_path: str

    def as_dict(self) -> dict[str, str]:
        return {
            **self.checkout.as_dict(),
            "path": str(self.path),
            "relative_path": self.relative_path,
        }


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=False)


def _mark_safe_directory(path: Path) -> None:
    candidates = [path]
    git_dir = path / ".git"
    if git_dir.exists():
        candidates.append(git_dir)
    for candidate in candidates:
        _git("config", "--global", "--add", "safe.directory", str(candidate))


def _mark_safe_source_url(url: str) -> None:
    if "://" in url:
        return
    candidate = Path(url).expanduser()
    if candidate.exists():
        _mark_safe_directory(candidate.resolve())


def _require_repo_url(name: str, url: str | None) -> str:
    if url:
        return url
    raise ConfigError(
        f"{name} repository URL is not configured",
        f"set the {name} repository URL before running this command",
        repository=name,
    )


@contextmanager
def _checkout_lock(
    checkout_dir: Path,
    *,
    timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 0.1,
    stale_after_seconds: float = 300.0,
):
    lock_path = checkout_dir.with_name(f"{checkout_dir.name}.lock")
    deadline = time.monotonic() + max(timeout_seconds, 0)
    while True:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with lock_path.open("x", encoding="utf-8") as handle:
                handle.write(f"{os.getpid()}\n")
            break
        except FileExistsError:
            try:
                age_seconds = time.time() - lock_path.stat().st_mtime
            except OSError:
                age_seconds = 0.0
            if age_seconds >= stale_after_seconds:
                try:
                    lock_path.unlink()
                    continue
                except OSError:
                    pass
            if time.monotonic() >= deadline:
                raise ConfigError(
                    f"timed out waiting for the {checkout_dir.name} repository cache lock",
                    "wait for the in-flight command to finish or remove the stale lock file and rerun the command",
                    checkout_dir=str(checkout_dir),
                    lock_path=str(lock_path),
                )
            time.sleep(max(poll_interval_seconds, 0))
    try:
        yield
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass


def _ensure_checkout(config: GatewayConfig, name: str, url: str | None) -> RepoCheckout:
    resolved_url = _require_repo_url(name, url)
    _mark_safe_source_url(resolved_url)
    checkout_dir = config.layout.repos_root / name
    with _checkout_lock(checkout_dir):
        if not checkout_dir.exists():
            checkout_dir.parent.mkdir(parents=True, exist_ok=True)
            temp_checkout_dir = checkout_dir.parent / f".{checkout_dir.name}.clone-{os.getpid()}-{time.time_ns()}"
            cloned = _git("clone", resolved_url, str(temp_checkout_dir))
            if cloned.returncode != 0:
                shutil.rmtree(temp_checkout_dir, ignore_errors=True)
                raise ConfigError(
                    f"failed to clone {name} repository",
                    "verify the repository URL and gateway Git access",
                    repository=name,
                    repository_url=resolved_url,
                    git_stderr=cloned.stderr.strip(),
                )
            try:
                temp_checkout_dir.replace(checkout_dir)
            except OSError as exc:
                shutil.rmtree(temp_checkout_dir, ignore_errors=True)
                raise ConfigError(
                    f"failed to move the cloned {name} repository into the cache path",
                    "remove the partially created cache directory and rerun the command",
                    repository=name,
                    checkout_dir=str(checkout_dir),
                ) from exc
        elif not (checkout_dir / ".git").exists():
            raise ConfigError(
                f"repository cache path is not a Git checkout: {checkout_dir}",
                "remove the invalid cache path and rerun the command",
                repository=name,
                checkout_dir=str(checkout_dir),
            )
        else:
            _mark_safe_directory(checkout_dir)
            pulled = _git("-C", str(checkout_dir), "pull", "--ff-only")
            if pulled.returncode != 0:
                raise ConfigError(
                    f"failed to update {name} repository cache",
                    "resolve the Git pull error in the cached checkout and rerun the command",
                    repository=name,
                    checkout_dir=str(checkout_dir),
                    git_stderr=pulled.stderr.strip(),
                )
    _mark_safe_directory(checkout_dir)
    head = _git("-C", str(checkout_dir), "rev-parse", "HEAD")
    if head.returncode != 0:
        raise ConfigError(
            f"failed to resolve {name} repository HEAD",
            "repair the cached Git checkout and rerun the command",
            repository=name,
            checkout_dir=str(checkout_dir),
        )
    return RepoCheckout(name=name, url=resolved_url, checkout_dir=checkout_dir, head=head.stdout.strip())


def services_checkout(config: GatewayConfig) -> RepoCheckout:
    return _ensure_checkout(config, "moltbox-services", config.services_repo_url)


def runtime_checkout(config: GatewayConfig) -> RepoCheckout:
    return _ensure_checkout(config, "moltbox-runtime", config.runtime_repo_url)


def skills_checkout(config: GatewayConfig) -> RepoCheckout:
    return _ensure_checkout(config, "remram-skills", config.skills_repo_url)


def service_resource(config: GatewayConfig, service_name: str) -> RepoResource:
    checkout = services_checkout(config)
    relative = Path("services") / service_name
    path = checkout.checkout_dir / relative
    if not path.exists() or not path.is_dir():
        raise ValidationError(
            f"service '{service_name}' was not found in the services repository",
            "create the service definition under moltbox-services/services/ or choose a different service",
            service=service_name,
            expected_path=str(path),
        )
    return RepoResource(checkout=checkout, path=path, relative_path=str(relative).replace("\\", "/"))


def list_services(config: GatewayConfig) -> list[RepoResource]:
    checkout = services_checkout(config)
    services_root = checkout.checkout_dir / "services"
    if not services_root.exists():
        raise ValidationError(
            "services repository does not contain a services/ directory",
            "create moltbox-services/services/ and rerun the command",
            expected_path=str(services_root),
        )
    resources: list[RepoResource] = []
    for path in sorted(candidate for candidate in services_root.iterdir() if candidate.is_dir()):
        relative = Path("services") / path.name
        resources.append(RepoResource(checkout=checkout, path=path, relative_path=str(relative).replace("\\", "/")))
    return resources


def runtime_resource(config: GatewayConfig, component_name: str) -> RepoResource:
    checkout = runtime_checkout(config)
    relative = Path(component_name)
    path = checkout.checkout_dir / relative
    if not path.exists():
        raise ValidationError(
            f"runtime configuration for '{component_name}' was not found",
            "create the runtime directory in moltbox-runtime or choose a different component",
            component=component_name,
            expected_path=str(path),
        )
    return RepoResource(checkout=checkout, path=path, relative_path=str(relative).replace("\\", "/"))


def load_skill_recipe(config: GatewayConfig, skill_name: str) -> tuple[RepoResource, dict[str, Any]]:
    checkout = skills_checkout(config)
    candidates = [
        checkout.checkout_dir / "skills" / skill_name / "deployment.yaml",
        checkout.checkout_dir / "skills" / skill_name / "deployment.yml",
        checkout.checkout_dir / skill_name / "deployment.yaml",
        checkout.checkout_dir / skill_name / "deployment.yml",
    ]
    for path in candidates:
        if not path.exists():
            continue
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if loaded is None:
            loaded = {}
        if not isinstance(loaded, dict):
            raise ValidationError(
                f"skill recipe '{path}' must contain a mapping",
                "rewrite the skill recipe as a YAML object and rerun the command",
                skill=skill_name,
                recipe_path=str(path),
            )
        return (
            RepoResource(
                checkout=checkout,
                path=path,
                relative_path=str(path.relative_to(checkout.checkout_dir)).replace("\\", "/"),
            ),
            loaded,
        )
    raise ValidationError(
        f"skill '{skill_name}' was not found in remram-skills",
        "create a deployment recipe in remram-skills and rerun the command",
        skill=skill_name,
    )


def skill_package_resource(config: GatewayConfig, skill_name: str) -> RepoResource:
    checkout = skills_checkout(config)
    candidates = [
        checkout.checkout_dir / "skills" / skill_name,
        checkout.checkout_dir / skill_name,
    ]
    markers = ("SKILL.md", "openclaw.plugin.json", "package.json")
    for path in candidates:
        if path.exists() and path.is_dir() and any((path / marker).exists() for marker in markers):
            return RepoResource(
                checkout=checkout,
                path=path,
                relative_path=str(path.relative_to(checkout.checkout_dir)).replace("\\", "/"),
            )
    raise ValidationError(
        f"skill package '{skill_name}' was not found in remram-skills",
        "create the skill package under remram-skills/skills/ or choose a different skill",
        skill=skill_name,
    )
