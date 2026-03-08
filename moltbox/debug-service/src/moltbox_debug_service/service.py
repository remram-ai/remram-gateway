from __future__ import annotations

import argparse
import contextlib
import json
import shutil
import subprocess
import tarfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from mcp.server.fastmcp import Context, FastMCP
from starlette.routing import Mount
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from .jobs import JobStore
from .redaction import redact_text
from .runtime import RuntimeContext, build_runtime, ensure_runtime_dirs, load_service_config, source_ip_allowed


TEST_GATEWAY_PORT = 18790
TEST_DEBUG_SERVICE_PORT = 18891
TEST_COMPOSE_PROJECT = "moltbox-test"
ALLOWED_PATCH_PREFIXES = ("moltbox/", "schemas/")
MUTATING_OPERATIONS = {
    "start_stack",
    "stop_stack",
    "restart_stack",
    "create_test_runtime",
    "start_test_stack",
    "destroy_test_stack",
    "backup_runtime",
    "patch_repo",
    "run_bootstrap",
}
TRUSTED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "moltbox-prime",
    "moltbox-prime.local",
    "*.local",
]


class MoltboxDebugService:
    def __init__(self) -> None:
        self.runtime = build_runtime("prod")
        ensure_runtime_dirs(self.runtime)
        self.config = load_service_config(self.runtime)
        self.jobs = JobStore(self.runtime.jobs_dir)
        self._busy_lock = threading.Lock()
        self._busy_runtimes: set[str] = set()
        self.mcp = FastMCP(
            "Moltbox Debug Service",
            instructions="Controlled developer API for the Moltbox runtime.",
            stateless_http=True,
            json_response=True,
        )
        self.mcp.settings.streamable_http_path = "/"
        self.mcp.settings.host = "0.0.0.0"
        self.mcp.settings.port = self.config.port
        self._register_tools()

    def _register_tools(self) -> None:
        @self.mcp.tool()
        async def runtime_status(runtime: str = "prod", ctx: Context | None = None) -> dict:
            self._enforce_scope(ctx, "runtime_status")
            return self._runtime_status(runtime)

        @self.mcp.tool()
        async def logs_openclaw(runtime: str = "prod", tail_lines: int = 200, ctx: Context | None = None) -> dict:
            self._enforce_scope(ctx, "logs_openclaw")
            return self._logs(runtime, "openclaw", tail_lines)

        @self.mcp.tool()
        async def logs_ollama(runtime: str = "prod", tail_lines: int = 200, ctx: Context | None = None) -> dict:
            self._enforce_scope(ctx, "logs_ollama")
            return self._logs(runtime, "ollama", tail_lines)

        @self.mcp.tool()
        async def logs_opensearch(runtime: str = "prod", tail_lines: int = 200, ctx: Context | None = None) -> dict:
            self._enforce_scope(ctx, "logs_opensearch")
            return self._logs(runtime, "opensearch", tail_lines)

        @self.mcp.tool()
        async def tail_logs(
            runtime: str = "prod",
            service: str = "openclaw",
            tail_lines: int = 200,
            since_seconds: int | None = None,
            ctx: Context | None = None,
        ) -> dict:
            self._enforce_scope(ctx, "tail_logs")
            return self._logs(runtime, service, tail_lines, since_seconds)

        @self.mcp.tool()
        async def job_status(job_id: str, ctx: Context | None = None) -> dict:
            self._enforce_scope(ctx, "job_status")
            return self.jobs.get(job_id)

        @self.mcp.tool()
        async def job_output(job_id: str, tail_lines: int = 200, ctx: Context | None = None) -> dict:
            self._enforce_scope(ctx, "job_output")
            return self.jobs.tail_output(job_id, max(1, min(tail_lines, self.config.max_log_lines)))

        async def launch(
            operation: str,
            runtime: str,
            handler: Callable[[str], dict],
            ctx: Context | None,
        ) -> dict:
            self._enforce_scope(ctx, operation)
            return self._submit_async(operation, runtime, handler)

        @self.mcp.tool()
        async def openclaw_doctor(runtime: str = "prod", ctx: Context | None = None) -> dict:
            return await launch("openclaw_doctor", runtime, lambda job_id: self._openclaw_doctor(runtime, job_id), ctx)

        @self.mcp.tool()
        async def validate_stack(runtime: str = "prod", ctx: Context | None = None) -> dict:
            return await launch("validate_stack", runtime, lambda job_id: self._run_script(runtime, "30-validate.sh", job_id, "validate_stack"), ctx)

        @self.mcp.tool()
        async def collect_diagnostics(runtime: str = "prod", ctx: Context | None = None) -> dict:
            return await launch("collect_diagnostics", runtime, lambda job_id: self._collect_diagnostics(runtime, job_id), ctx)

        @self.mcp.tool()
        async def start_stack(runtime: str = "prod", ctx: Context | None = None) -> dict:
            return await launch("start_stack", runtime, lambda job_id: self._start_stack(runtime, job_id), ctx)

        @self.mcp.tool()
        async def stop_stack(runtime: str = "prod", ctx: Context | None = None) -> dict:
            return await launch("stop_stack", runtime, lambda job_id: self._stop_stack(runtime, job_id), ctx)

        @self.mcp.tool()
        async def restart_stack(runtime: str = "prod", ctx: Context | None = None) -> dict:
            return await launch("restart_stack", runtime, lambda job_id: self._restart_stack(runtime, job_id), ctx)

        @self.mcp.tool()
        async def create_test_runtime(force_recreate: bool = False, ctx: Context | None = None) -> dict:
            return await launch("create_test_runtime", "test", lambda job_id: self._create_test_runtime(force_recreate, job_id), ctx)

        @self.mcp.tool()
        async def start_test_stack(ctx: Context | None = None) -> dict:
            return await launch("start_test_stack", "test", lambda job_id: self._start_stack("test", job_id), ctx)

        @self.mcp.tool()
        async def destroy_test_stack(force: bool = False, ctx: Context | None = None) -> dict:
            return await launch("destroy_test_stack", "test", lambda job_id: self._destroy_test_runtime(force, job_id), ctx)

        @self.mcp.tool()
        async def backup_runtime(runtime: str = "prod", include_logs: bool = True, ctx: Context | None = None) -> dict:
            return await launch("backup_runtime", runtime, lambda job_id: self._backup_runtime(runtime, include_logs, job_id), ctx)

        @self.mcp.tool()
        async def patch_repo(patch: str, runtime: str = "test", ctx: Context | None = None) -> dict:
            return await launch("patch_repo", runtime, lambda job_id: self._patch_repo(runtime, patch, job_id), ctx)

        @self.mcp.tool()
        async def run_bootstrap(runtime: str = "prod", ctx: Context | None = None) -> dict:
            return await launch("run_bootstrap", runtime, lambda job_id: self._run_script(runtime, "20-bootstrap.sh", job_id, "run_bootstrap"), ctx)

    def create_app(self) -> FastAPI:
        @contextlib.asynccontextmanager
        async def lifespan(_: FastAPI):
            async with self.mcp.session_manager.run():
                yield

        app = FastAPI(title="Moltbox Debug Service", lifespan=lifespan)
        app.add_middleware(ProxyHeadersMiddleware)
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=TRUSTED_HOSTS)

        @app.middleware("http")
        async def auth_middleware(request: Request, call_next: Callable):
            if request.url.path in {"/health", "/healthz", "/readyz"}:
                return await call_next(request)
            auth_result = self._authenticate_request(request)
            if auth_result is not None:
                return auth_result
            return await call_next(request)

        @app.get("/health")
        async def health() -> dict:
            return {"status": "ok"}

        @app.get("/healthz")
        async def healthz() -> dict:
            return {"ok": True}

        @app.get("/readyz")
        async def readyz() -> dict:
            return {"ok": True, "runtime_root": str(self.runtime.runtime_root)}

        @app.get("/artifacts/{artifact_path:path}")
        async def artifact(artifact_path: str) -> FileResponse:
            safe_path = (self.runtime.artifacts_dir / artifact_path).resolve()
            root = self.runtime.artifacts_dir.resolve()
            if not str(safe_path).startswith(str(root)) or not safe_path.is_file():
                raise HTTPException(status_code=404, detail="artifact not found")
            return FileResponse(safe_path)

        app.router.routes.append(Mount("/mcp", app=self.mcp.streamable_http_app()))
        return app

    def _authenticate_request(self, request: Request) -> JSONResponse | None:
        runtime = self.runtime
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content={"error": "missing bearer token"})
        token = auth_header[7:]
        if token != runtime.debug_service_token or not token:
            return JSONResponse(status_code=403, content={"error": "invalid token"})

        client_id = request.headers.get("x-moltbox-client", "").strip()
        if not client_id:
            return JSONResponse(status_code=401, content={"error": "missing client id"})
        rule = self.config.clients.get(client_id)
        if rule is None:
            return JSONResponse(status_code=403, content={"error": "unknown client"})

        host = request.client.host if request.client else ""
        if not source_ip_allowed(host, self.config.lan_cidr):
            return JSONResponse(status_code=403, content={"error": "source ip not allowed"})

        origin = request.headers.get("origin")
        if origin:
            allowed_origins = set(rule.allowed_origins or tuple(runtime.allowed_origins))
            if origin not in allowed_origins:
                return JSONResponse(status_code=403, content={"error": "origin not allowed"})

        request.state.moltbox_client_id = client_id
        return None

    def _enforce_scope(self, ctx: Any, operation: str) -> None:
        request_context = getattr(ctx, "request_context", None)
        if request_context is None:
            return
        request = getattr(request_context, "request", None)
        client_id = getattr(getattr(request, "state", object()), "moltbox_client_id", None)
        if not client_id:
            return
        rule = self.config.clients.get(client_id)
        if rule and "*" not in rule.scopes and operation not in rule.scopes:
            raise PermissionError(f"client '{client_id}' cannot call '{operation}'")

    def _submit_async(self, operation: str, runtime: str, fn: Callable[[str], dict]) -> dict:
        self._load_runtime(runtime)
        if operation in MUTATING_OPERATIONS:
            with self._busy_lock:
                if runtime in self._busy_runtimes:
                    raise RuntimeError(f"Another mutating operation is already running for runtime '{runtime}'")
                self._busy_runtimes.add(runtime)

            def guarded(job_id: str) -> dict:
                try:
                    return fn(job_id)
                finally:
                    with self._busy_lock:
                        self._busy_runtimes.discard(runtime)
        else:
            guarded = fn

        job = self.jobs.submit(operation=operation, runtime=runtime, handler=guarded)
        return {
            "ok": True,
            "operation": operation,
            "runtime": runtime,
            "job_id": job["job_id"],
            "status": job["status"],
            "stdout": "",
            "stderr": "",
            "artifacts": [],
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
        }

    def _load_runtime(self, runtime: str) -> RuntimeContext:
        ctx = build_runtime(runtime)
        ensure_runtime_dirs(ctx)
        return ctx

    def _compose_args(self, ctx: RuntimeContext, *extra: str) -> list[str]:
        return ["docker", "compose", "--env-file", str(ctx.env_file), "-f", str(ctx.compose_file), *extra]

    def _run_command(
        self,
        ctx: RuntimeContext,
        argv: list[str],
        *,
        job_id: str | None = None,
        timeout: int | None = None,
        cwd: Path | None = None,
        stdin: str | None = None,
    ) -> dict:
        completed = subprocess.run(
            argv,
            cwd=str(cwd or ctx.repo_root),
            env=ctx.base_env,
            check=False,
            capture_output=True,
            text=True,
            input=stdin,
            timeout=timeout or self.config.default_timeout_seconds,
        )
        stdout = redact_text(completed.stdout)
        stderr = redact_text(completed.stderr)
        if job_id:
            self.jobs.append_log(job_id, stdout)
            self.jobs.append_log(job_id, stderr)
        return {
            "ok": completed.returncode == 0,
            "operation": "",
            "runtime": ctx.name,
            "status": "succeeded" if completed.returncode == 0 else "failed",
            "stdout": stdout,
            "stderr": stderr,
            "artifacts": [],
            "started_at": None,
            "finished_at": None,
            "exit_code": completed.returncode,
        }

    def _curl_probe(self, port: int, path: str) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                ["curl", "-fsS", f"http://127.0.0.1:{port}{path}"],
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except FileNotFoundError:
            return {"ok": False, "status_code": None, "body": "curl not available"}
        return {
            "ok": completed.returncode == 0,
            "status_code": 200 if completed.returncode == 0 else None,
            "body": redact_text(completed.stdout or completed.stderr),
        }

    def _runtime_status(self, runtime: str) -> dict:
        ctx = self._load_runtime(runtime)
        compose = self._run_command(ctx, self._compose_args(ctx, "ps"))
        health = self._curl_probe(ctx.gateway_port, "/healthz")
        ready = self._curl_probe(ctx.gateway_port, "/readyz")
        return {
            "ok": compose["ok"],
            "operation": "runtime_status",
            "runtime": runtime,
            "job_id": None,
            "status": "succeeded" if compose["ok"] else "failed",
            "stdout": compose["stdout"],
            "stderr": compose["stderr"],
            "artifacts": [],
            "started_at": None,
            "finished_at": None,
            "exit_code": compose["exit_code"],
            "details": {
                "runtime_root": str(ctx.runtime_root),
                "repo_root": str(ctx.repo_root),
                "compose_project_name": ctx.compose_project_name,
                "gateway_port": ctx.gateway_port,
                "debug_service_port": ctx.debug_service_port,
                "openclaw_container_name": ctx.openclaw_container_name,
                "gateway_health": health,
                "gateway_ready": ready,
            },
        }

    def _logs(self, runtime: str, service: str, tail_lines: int, since_seconds: int | None = None) -> dict:
        ctx = self._load_runtime(runtime)
        if service not in {"openclaw", "ollama", "opensearch"}:
            raise ValueError(f"Unsupported service: {service}")
        tail_lines = max(1, min(tail_lines, self.config.max_log_lines))
        argv = self._compose_args(ctx, "logs", "--tail", str(tail_lines))
        if since_seconds:
            argv.extend(["--since", f"{since_seconds}s"])
        argv.append(service)
        result = self._run_command(ctx, argv)
        result.update({"operation": f"logs_{service}" if since_seconds is None else "tail_logs", "runtime": runtime})
        return result

    def _openclaw_doctor(self, runtime: str, job_id: str) -> dict:
        ctx = self._load_runtime(runtime)
        result = self._run_command(
            ctx,
            self._compose_args(ctx, "exec", "-T", "openclaw", "openclaw", "doctor"),
            job_id=job_id,
            timeout=self.config.long_timeout_seconds,
        )
        result["operation"] = "openclaw_doctor"
        return result

    def _run_script(self, runtime: str, script_name: str, job_id: str, operation_name: str | None = None) -> dict:
        ctx = self._load_runtime(runtime)
        if runtime == "test" and script_name == "20-bootstrap.sh":
            self._ensure_test_runtime_idle()
        result = self._run_command(
            ctx,
            ["bash", str(ctx.script_dir / script_name)],
            job_id=job_id,
            timeout=self.config.long_timeout_seconds,
            cwd=ctx.repo_root / "moltbox",
        )
        result["operation"] = operation_name or script_name
        return result

    def _collect_diagnostics(self, runtime: str, job_id: str) -> dict:
        result = self._run_script(runtime, "99-diagnostics.sh", job_id)
        artifact = None
        for line in result["stdout"].splitlines():
            if line.startswith("/tmp/") and line.endswith(".tar.gz"):
                artifact = line.strip()
        if artifact:
            result["artifacts"] = [artifact]
        result["operation"] = "collect_diagnostics"
        return result

    def _start_stack(self, runtime: str, job_id: str) -> dict:
        ctx = self._load_runtime(runtime)
        if runtime == "test":
            self._ensure_test_runtime_idle()
        result = self._run_command(ctx, self._compose_args(ctx, "up", "-d"), job_id=job_id, timeout=self.config.long_timeout_seconds)
        result["operation"] = "start_stack"
        return result

    def _stop_stack(self, runtime: str, job_id: str) -> dict:
        ctx = self._load_runtime(runtime)
        result = self._run_command(ctx, self._compose_args(ctx, "down"), job_id=job_id, timeout=self.config.long_timeout_seconds)
        result["operation"] = "stop_stack"
        return result

    def _restart_stack(self, runtime: str, job_id: str) -> dict:
        ctx = self._load_runtime(runtime)
        if runtime == "test":
            self._ensure_test_runtime_idle()
        result = self._run_command(ctx, self._compose_args(ctx, "restart"), job_id=job_id, timeout=self.config.long_timeout_seconds)
        result["operation"] = "restart_stack"
        return result

    def _create_test_runtime(self, force_recreate: bool, job_id: str) -> dict:
        prod = self._load_runtime("prod")
        test = self._load_runtime("test")
        if test.runtime_root.exists() and any(test.runtime_root.iterdir()) and not force_recreate:
            raise RuntimeError(f"Test runtime already exists at {test.runtime_root}; rerun with force_recreate=true")

        self._safe_remove_path(test.runtime_root)
        self._remove_worktree(test.repo_root)

        added = self._run_command(
            prod,
            ["git", "worktree", "add", "--force", str(test.repo_root), "HEAD"],
            job_id=job_id,
            timeout=self.config.long_timeout_seconds,
            cwd=prod.repo_root,
        )
        if not added["ok"]:
            added["operation"] = "create_test_runtime"
            return added

        test.runtime_root.mkdir(parents=True, exist_ok=True)
        self._copy_runtime_subset(prod.runtime_root, test.runtime_root)
        self._prepare_test_runtime_files(test)
        ensure_runtime_dirs(test)

        stdout = f"Created test runtime at {test.runtime_root}\nCreated test worktree at {test.repo_root}\n"
        self.jobs.append_log(job_id, stdout)
        return {
            "ok": True,
            "operation": "create_test_runtime",
            "runtime": "test",
            "stdout": stdout,
            "stderr": "",
            "artifacts": [str(test.runtime_root), str(test.repo_root)],
            "exit_code": 0,
        }

    def _destroy_test_runtime(self, force: bool, job_id: str) -> dict:
        test = self._load_runtime("test")
        if test.runtime_root.exists():
            stop = self._run_command(
                test,
                self._compose_args(test, "down"),
                job_id=job_id,
                timeout=self.config.long_timeout_seconds,
            )
            if not stop["ok"] and not force:
                stop["operation"] = "destroy_test_stack"
                return stop
        self._safe_remove_path(test.runtime_root)
        self._remove_worktree(test.repo_root)
        stdout = f"Removed test runtime {test.runtime_root}\nRemoved test worktree {test.repo_root}\n"
        self.jobs.append_log(job_id, stdout)
        return {
            "ok": True,
            "operation": "destroy_test_stack",
            "runtime": "test",
            "stdout": stdout,
            "stderr": "",
            "artifacts": [],
            "exit_code": 0,
        }

    def _backup_runtime(self, runtime: str, include_logs: bool, job_id: str) -> dict:
        ctx = self._load_runtime(runtime)
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
        artifact = self.runtime.artifacts_dir / f"{runtime}-runtime-backup-{timestamp}.tar.gz"
        with tarfile.open(artifact, "w:gz") as archive:
            for path in ctx.runtime_root.iterdir():
                if path.name == "debug-service":
                    continue
                if not include_logs and path.name == "logs":
                    continue
                archive.add(path, arcname=path.name)
        stdout = f"Created backup archive: {artifact}\n"
        self.jobs.append_log(job_id, stdout)
        return {
            "ok": True,
            "operation": "backup_runtime",
            "runtime": runtime,
            "stdout": stdout,
            "stderr": "",
            "artifacts": [str(artifact)],
            "exit_code": 0,
        }

    def _patch_repo(self, runtime: str, patch: str, job_id: str) -> dict:
        if runtime != "test":
            raise RuntimeError("patch_repo is limited to the test runtime")
        ctx = self._load_runtime("test")
        if not ctx.repo_root.exists():
            raise RuntimeError("Test worktree does not exist; run create_test_runtime first")
        self._validate_patch_paths(patch)
        check = self._run_command(
            ctx,
            ["git", "apply", "--check", "--verbose", "-"],
            job_id=job_id,
            timeout=self.config.long_timeout_seconds,
            cwd=ctx.repo_root,
            stdin=patch,
        )
        if not check["ok"]:
            check["operation"] = "patch_repo"
            check["runtime"] = runtime
            return check

        applied = self._run_command(
            ctx,
            ["git", "apply", "-"],
            job_id=job_id,
            timeout=self.config.long_timeout_seconds,
            cwd=ctx.repo_root,
            stdin=patch,
        )
        applied["operation"] = "patch_repo"
        applied["runtime"] = runtime
        return applied

    def _validate_patch_paths(self, patch: str) -> None:
        if "GIT binary patch" in patch or "\x00" in patch:
            raise RuntimeError("Binary patches are not allowed")

        touched: set[str] = set()
        for line in patch.splitlines():
            if line.startswith("+++ b/") or line.startswith("--- a/"):
                path = line[6:]
                if path != "/dev/null":
                    touched.add(path)
            elif line.startswith("diff --git "):
                parts = line.split()
                if len(parts) >= 4:
                    touched.add(parts[2][2:])
                    touched.add(parts[3][2:])

        if not touched:
            raise RuntimeError("Patch did not contain any file paths")

        for path in touched:
            if path.startswith("/") or ".." in Path(path).parts:
                raise RuntimeError(f"Invalid patch path: {path}")
            if not path.startswith(ALLOWED_PATCH_PREFIXES):
                raise RuntimeError(f"Patch path outside allowlist: {path}")

    def _copy_runtime_subset(self, source: Path, target: Path) -> None:
        for name in (
            ".env",
            "container.env",
            "openclaw.json",
            "model-runtime.yml",
            "opensearch.yml",
            "agents.yaml",
            "channels.yaml",
            "routing.yaml",
            "tools.yaml",
            "escalation.yaml",
        ):
            src = source / name
            if src.is_file():
                shutil.copy2(src, target / name)

    def _prepare_test_runtime_files(self, ctx: RuntimeContext) -> None:
        env_values = dict(ctx.env_values)
        env_values["COMPOSE_PROJECT_NAME"] = TEST_COMPOSE_PROJECT
        env_values["GATEWAY_PORT"] = str(TEST_GATEWAY_PORT)
        env_values["DEBUG_SERVICE_PORT"] = str(TEST_DEBUG_SERVICE_PORT)
        env_values["OPENCLAW_CONTAINER_NAME"] = "moltbox-test-openclaw"
        env_values["OLLAMA_CONTAINER_NAME"] = "moltbox-test-ollama"
        env_values["OPENSEARCH_CONTAINER_NAME"] = "moltbox-test-opensearch"
        self._write_env_file(ctx.env_file, env_values)

        config = ctx.openclaw_values if isinstance(ctx.openclaw_values, dict) else {}
        gateway = config.setdefault("gateway", {})
        control_ui = gateway.setdefault("controlUi", {})
        existing = control_ui.get("allowedOrigins")
        if not isinstance(existing, list):
            existing = []
        for origin in (f"http://127.0.0.1:{TEST_GATEWAY_PORT}", f"http://localhost:{TEST_GATEWAY_PORT}"):
            if origin not in existing:
                existing.append(origin)
        control_ui["allowedOrigins"] = existing
        ctx.openclaw_config_file.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

        config_path = ctx.runtime_root / "debug-service" / "config.json"
        clients_path = ctx.runtime_root / "debug-service" / "clients.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps(
                {
                    "host": "0.0.0.0",
                    "port": TEST_DEBUG_SERVICE_PORT,
                    "lan_cidr": env_values.get("LAN_CIDR", "192.168.1.0/24"),
                    "job_retention_hours": 24,
                    "max_log_lines": 400,
                    "artifact_ttl_hours": 48,
                    "timeouts": {"default_seconds": 120, "long_seconds": 1800},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if not clients_path.exists():
            clients_path.write_text(
                json.dumps(
                    {
                        "allowed_clients": [
                            {"id": "codex", "allowed_origins": [], "scopes": ["*"]},
                            {"id": "vscode-agent", "allowed_origins": [], "scopes": ["*"]},
                        ]
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

    def _write_env_file(self, path: Path, values: dict[str, str]) -> None:
        path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")

    def _ensure_test_runtime_idle(self) -> None:
        prod = self._load_runtime("prod")
        completed = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        names = set(completed.stdout.splitlines())
        if {prod.openclaw_container_name, prod.ollama_container_name, prod.opensearch_container_name} & names:
            raise RuntimeError("Production stack is running; stop it before mutating the test runtime")

    def _safe_remove_path(self, path: Path) -> None:
        if not path.exists():
            return
        resolved = path.resolve()
        allowed = {Path.home() / ".openclaw-test", Path.home() / "git" / "remram-gateway-test"}
        if resolved not in {entry.resolve() for entry in allowed}:
            raise RuntimeError(f"Refusing to remove unsafe path: {resolved}")
        shutil.rmtree(resolved, ignore_errors=True)

    def _remove_worktree(self, worktree_path: Path) -> None:
        if not worktree_path.exists():
            return
        prod = self._load_runtime("prod")
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree_path)],
            cwd=str(prod.repo_root),
            capture_output=True,
            text=True,
            check=False,
            timeout=self.config.long_timeout_seconds,
        )
        self._safe_remove_path(worktree_path)


def build_service() -> MoltboxDebugService:
    return MoltboxDebugService()


def run_server(args: argparse.Namespace) -> None:
    service = build_service()
    uvicorn.run(
        service.create_app(),
        host=args.host or "0.0.0.0",
        port=args.port or service.config.port,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
