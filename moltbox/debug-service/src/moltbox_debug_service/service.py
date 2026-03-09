from __future__ import annotations

import argparse
import contextlib
import json
import shlex
import shutil
import subprocess
import tarfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit, urlunsplit

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.routing import Mount
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from .flows import FlowStore
from .jobs import JobStore
from .redaction import redact_text
from .runtime import RuntimeContext, build_runtime, ensure_runtime_dirs, load_json, load_service_config, parse_env_file, source_ip_allowed


TEST_GATEWAY_PORT = 18790
TEST_DEBUG_SERVICE_PORT = 18891
TEST_COMPOSE_PROJECT = "moltbox-test"
TEST_CONTROL_UI_HOSTS = ("moltbox-test",)
ALLOWED_PATCH_PREFIXES = ("moltbox/", "schemas/")
SNAPSHOT_TOOL = "/usr/local/bin/moltbox-snapshot"
SNAPSHOT_ROOT = "/mnt/moltbox-backup/snapshots"
SAFE_SCRIPT_MAP = {
    "runtime_reset": "12-runtime-reset.sh",
    "bootstrap": "20-bootstrap.sh",
    "validate": "30-validate.sh",
    "diagnostics": "99-diagnostics.sh",
}
MUTATING_OPERATIONS = {
    "publish_branch",
    "finalize_test_publish",
    "start_stack",
    "stop_stack",
    "restart_stack",
    "create_test_runtime",
    "start_test_stack",
    "destroy_test_stack",
    "backup_runtime",
    "snapshot_runtime",
    "restore_runtime_snapshot",
    "patch_repo",
    "run_bootstrap",
    "run_script",
    "run_script_sync",
    "run_remote_script",
    "run_remote_script_sync",
    "repo_pull",
    "repo_checkout_ref",
}
TRUSTED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "moltbox-prime",
    "moltbox-prime.local",
    "*.local",
]
MCP_ALLOWED_HOSTS = [
    "localhost",
    "localhost:*",
    "127.0.0.1",
    "127.0.0.1:*",
    "moltbox-prime",
    "moltbox-prime:*",
    "moltbox-prime.local",
    "moltbox-prime.local:*",
    "192.168.1.189",
    "192.168.1.189:*",
]


class MoltboxDebugService:
    def __init__(self) -> None:
        self.runtime = build_runtime("prod")
        ensure_runtime_dirs(self.runtime)
        self.config = load_service_config(self.runtime)
        self.jobs = JobStore(self.runtime.jobs_dir)
        self.flows = FlowStore(self.runtime.flows_dir)
        self._busy_lock = threading.Lock()
        self._busy_runtimes: set[str] = set()
        self.mcp = FastMCP(
            "Moltbox Debug Service",
            instructions="Controlled developer API for the Moltbox runtime.",
            stateless_http=True,
            json_response=True,
            transport_security=TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=MCP_ALLOWED_HOSTS,
            ),
        )
        self.mcp.settings.streamable_http_path = "/"
        self.mcp.settings.host = "0.0.0.0"
        self.mcp.settings.port = self.config.port
        self._register_tools()

    def _register_tools(self) -> None:
        @self.mcp.tool(description="Inspect the selected Moltbox runtime, container health, and gateway readiness.")
        async def runtime_status(runtime: str = "prod", ctx: Context | None = None) -> dict:
            self._enforce_scope(ctx, "runtime_status")
            return self._runtime_status(runtime)

        @self.mcp.tool(description="Read recent OpenClaw container logs for the selected runtime.")
        async def logs_openclaw(runtime: str = "prod", tail_lines: int = 200, ctx: Context | None = None) -> dict:
            self._enforce_scope(ctx, "logs_openclaw")
            return self._logs(runtime, "openclaw", tail_lines)

        @self.mcp.tool(description="Read recent Ollama container logs for the selected runtime.")
        async def logs_ollama(runtime: str = "prod", tail_lines: int = 200, ctx: Context | None = None) -> dict:
            self._enforce_scope(ctx, "logs_ollama")
            return self._logs(runtime, "ollama", tail_lines)

        @self.mcp.tool(description="Read recent OpenSearch container logs for the selected runtime.")
        async def logs_opensearch(runtime: str = "prod", tail_lines: int = 200, ctx: Context | None = None) -> dict:
            self._enforce_scope(ctx, "logs_opensearch")
            return self._logs(runtime, "opensearch", tail_lines)

        @self.mcp.tool(description="Read bounded logs for a specific service in the selected runtime.")
        async def tail_logs(
            runtime: str = "prod",
            service: str = "openclaw",
            tail_lines: int = 200,
            since_seconds: int | None = None,
            ctx: Context | None = None,
        ) -> dict:
            self._enforce_scope(ctx, "tail_logs")
            return self._logs(runtime, service, tail_lines, since_seconds)

        @self.mcp.tool(description="Inspect the status and result envelope for an async debug-service job.")
        async def job_status(job_id: str, ctx: Context | None = None) -> dict:
            self._enforce_scope(ctx, "job_status")
            return self.jobs.get(job_id)

        @self.mcp.tool(description="Inspect the git branch, commit, and working tree status for the selected runtime repo.")
        async def repo_status(runtime: str = "prod", ctx: Context | None = None) -> dict:
            self._enforce_scope(ctx, "repo_status")
            return self._repo_status(runtime)

        @self.mcp.tool(description="Summarize persisted runtime configuration, selected env values, client allowlists, and key OpenClaw settings.")
        async def runtime_config_summary(runtime: str = "prod", ctx: Context | None = None) -> dict:
            self._enforce_scope(ctx, "runtime_config_summary")
            return self._runtime_config_summary(runtime)

        @self.mcp.tool(description="Inspect persisted gateway auth and browser access settings without exposing raw secrets.")
        async def gateway_auth_state(runtime: str = "prod", ctx: Context | None = None) -> dict:
            self._enforce_scope(ctx, "gateway_auth_state")
            return self._gateway_auth_state(runtime)

        @self.mcp.tool(description="Inspect currently paired and pending OpenClaw devices for the selected runtime.")
        async def paired_devices(runtime: str = "prod", ctx: Context | None = None) -> dict:
            self._enforce_scope(ctx, "paired_devices")
            return self._paired_devices(runtime)

        @self.mcp.tool(description="List available host snapshots under /mnt/moltbox-backup/snapshots.")
        async def list_runtime_snapshots(ctx: Context | None = None) -> dict:
            self._enforce_scope(ctx, "list_runtime_snapshots")
            return self._list_runtime_snapshots()

        @self.mcp.tool(description="Inspect snapshot inventory and the latest rollback point under /mnt/moltbox-backup/snapshots.")
        async def snapshot_state(ctx: Context | None = None) -> dict:
            self._enforce_scope(ctx, "snapshot_state")
            return self._snapshot_state()

        @self.mcp.tool(description="List persisted deployment and publish workflow runs recorded under the Moltbox runtime.")
        async def list_publish_runs(ctx: Context | None = None) -> dict:
            self._enforce_scope(ctx, "list_publish_runs")
            return self._list_publish_runs()

        @self.mcp.tool(description="Read the structured report for a persisted publish workflow run.")
        async def publish_report(flow_id: str, ctx: Context | None = None) -> dict:
            self._enforce_scope(ctx, "publish_report")
            return self._publish_report(flow_id)

        @self.mcp.tool(description="List allowlisted remote scripts available under moltbox/remote in the selected runtime repo.")
        async def list_remote_scripts(runtime: str = "prod", ctx: Context | None = None) -> dict:
            self._enforce_scope(ctx, "list_remote_scripts")
            return self._list_remote_scripts(runtime)

        @self.mcp.tool(description="Read the captured stdout and stderr tail for an async debug-service job.")
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

        @self.mcp.tool(description="Run OpenClaw doctor checks against the selected runtime as an async job.")
        async def openclaw_doctor(runtime: str = "prod", ctx: Context | None = None) -> dict:
            return await launch("openclaw_doctor", runtime, lambda job_id: self._openclaw_doctor(runtime, job_id), ctx)

        @self.mcp.tool(description="Run the Moltbox stack validation script against the selected runtime as an async job.")
        async def validate_stack(runtime: str = "prod", ctx: Context | None = None) -> dict:
            return await launch("validate_stack", runtime, lambda job_id: self._run_script(runtime, "30-validate.sh", job_id, "validate_stack"), ctx)

        @self.mcp.tool(description="Collect a diagnostics bundle for the selected runtime as an async job.")
        async def collect_diagnostics(runtime: str = "prod", ctx: Context | None = None) -> dict:
            return await launch("collect_diagnostics", runtime, lambda job_id: self._collect_diagnostics(runtime, job_id), ctx)

        @self.mcp.tool(description="Start the selected Moltbox runtime stack as an async job.")
        async def start_stack(runtime: str = "prod", ctx: Context | None = None) -> dict:
            return await launch("start_stack", runtime, lambda job_id: self._start_stack(runtime, job_id), ctx)

        @self.mcp.tool(description="Stop the selected Moltbox runtime stack as an async job.")
        async def stop_stack(runtime: str = "prod", ctx: Context | None = None) -> dict:
            return await launch("stop_stack", runtime, lambda job_id: self._stop_stack(runtime, job_id), ctx)

        @self.mcp.tool(description="Restart the selected Moltbox runtime stack as an async job.")
        async def restart_stack(runtime: str = "prod", ctx: Context | None = None) -> dict:
            return await launch("restart_stack", runtime, lambda job_id: self._restart_stack(runtime, job_id), ctx)

        @self.mcp.tool(description="Create or reset the disposable Moltbox test runtime and worktree as an async job.")
        async def create_test_runtime(
            force_recreate: bool = False,
            ref: str | None = None,
            ctx: Context | None = None,
        ) -> dict:
            return await launch("create_test_runtime", "test", lambda job_id: self._create_test_runtime(force_recreate, ref, job_id), ctx)

        @self.mcp.tool(description="Start the Moltbox test runtime stack as an async job.")
        async def start_test_stack(ctx: Context | None = None) -> dict:
            return await launch("start_test_stack", "test", lambda job_id: self._start_stack("test", job_id), ctx)

        @self.mcp.tool(description="Stop and remove the Moltbox test runtime, worktree, and test artifacts as an async job.")
        async def destroy_test_stack(force: bool = False, ctx: Context | None = None) -> dict:
            return await launch("destroy_test_stack", "test", lambda job_id: self._destroy_test_runtime(force, job_id), ctx)

        @self.mcp.tool(description="Create a timestamped backup archive of the selected runtime as an async job.")
        async def backup_runtime(runtime: str = "prod", include_logs: bool = True, ctx: Context | None = None) -> dict:
            return await launch("backup_runtime", runtime, lambda job_id: self._backup_runtime(runtime, include_logs, job_id), ctx)

        @self.mcp.tool(description="Create a host snapshot with sudo /usr/local/bin/moltbox-snapshot create before mutating Moltbox state.")
        async def snapshot_runtime(ctx: Context | None = None) -> dict:
            return await launch("snapshot_runtime", "prod", lambda job_id: self._snapshot_runtime(job_id), ctx)

        @self.mcp.tool(description="Publish a git branch into the test or live Moltbox workflow. Test mode snapshots prod, pauses prod, deploys into the disposable test runtime, and prepares a report. Live mode deploys the latest approved test run for that branch to production.")
        async def publish_branch(
            mode: str,
            branch: str,
            validate: bool = True,
            exercise_script: str | None = None,
            exercise_args: list[str] | None = None,
            collect_diagnostics: bool = False,
            ctx: Context | None = None,
        ) -> dict:
            target_runtime = "test" if mode == "test" else "prod"
            return await launch(
                "publish_branch",
                target_runtime,
                lambda job_id: self._publish_branch(mode, branch, validate, exercise_script, exercise_args or [], collect_diagnostics, job_id),
                ctx,
            )

        @self.mcp.tool(description="Stop or destroy the test runtime, optionally collect diagnostics, resume production, and finalize the structured publish report.")
        async def finalize_test_publish(
            flow_id: str,
            resume_production: bool = True,
            destroy_test_runtime: bool = True,
            collect_diagnostics: bool = True,
            ctx: Context | None = None,
        ) -> dict:
            return await launch(
                "finalize_test_publish",
                "test",
                lambda job_id: self._finalize_test_publish(flow_id, resume_production, destroy_test_runtime, collect_diagnostics, job_id),
                ctx,
            )

        @self.mcp.tool(description="Record explicit operator approval or rejection for a completed test publish run before live deployment.")
        async def approve_test_publish(flow_id: str, approved: bool, note: str = "", ctx: Context | None = None) -> dict:
            self._enforce_scope(ctx, "approve_test_publish")
            return self._approve_test_publish(flow_id, approved, note)

        @self.mcp.tool(description="Apply a restricted unified diff patch to the disposable test worktree as an async job.")
        async def patch_repo(patch: str, runtime: str = "test", ctx: Context | None = None) -> dict:
            return await launch("patch_repo", runtime, lambda job_id: self._patch_repo(runtime, patch, job_id), ctx)

        @self.mcp.tool(description="Run the Moltbox bootstrap script against the selected runtime as an async job.")
        async def run_bootstrap(runtime: str = "prod", ctx: Context | None = None) -> dict:
            return await launch("run_bootstrap", runtime, lambda job_id: self._run_script(runtime, "20-bootstrap.sh", job_id, "run_bootstrap"), ctx)

        @self.mcp.tool(description="Run an allowlisted operational Moltbox script by script id as an async job.")
        async def run_script(script_id: str, runtime: str = "prod", ctx: Context | None = None) -> dict:
            return await launch("run_script", runtime, lambda job_id: self._run_named_script(runtime, script_id, job_id), ctx)

        @self.mcp.tool(description="Run an allowlisted operational Moltbox script and return its stdout/stderr directly.")
        async def run_script_sync(
            script_id: str,
            runtime: str = "prod",
            timeout_seconds: int | None = None,
            ctx: Context | None = None,
        ) -> dict:
            self._enforce_scope(ctx, "run_script_sync")
            return self._run_sync_operation("run_script_sync", runtime, lambda: self._run_named_script_sync(runtime, script_id, timeout_seconds))

        @self.mcp.tool(description="Run a synced script from moltbox/remote in the selected runtime repo as an async job.")
        async def run_remote_script(
            script_name: str,
            runtime: str = "prod",
            args: list[str] | None = None,
            ctx: Context | None = None,
        ) -> dict:
            return await launch("run_remote_script", runtime, lambda job_id: self._run_remote_script(runtime, script_name, args or [], job_id), ctx)

        @self.mcp.tool(description="Run a synced script from moltbox/remote/exec and return its stdout/stderr directly.")
        async def run_remote_script_sync(
            script_name: str,
            runtime: str = "prod",
            args: list[str] | None = None,
            timeout_seconds: int | None = None,
            ctx: Context | None = None,
        ) -> dict:
            self._enforce_scope(ctx, "run_remote_script_sync")
            return self._run_sync_operation(
                "run_remote_script_sync",
                runtime,
                lambda: self._run_remote_script_sync(runtime, script_name, args or [], timeout_seconds),
            )

        @self.mcp.tool(description="Fetch and fast-forward pull the selected runtime repo from git as an async job.")
        async def repo_pull(runtime: str = "prod", branch: str | None = None, ctx: Context | None = None) -> dict:
            return await launch("repo_pull", runtime, lambda job_id: self._repo_pull(runtime, branch, job_id), ctx)

        @self.mcp.tool(description="Check out a specific git ref in the selected runtime repo as an async job. Test runtime only.")
        async def repo_checkout_ref(ref: str, runtime: str = "test", ctx: Context | None = None) -> dict:
            return await launch("repo_checkout_ref", runtime, lambda job_id: self._repo_checkout_ref(runtime, ref, job_id), ctx)

        @self.mcp.tool(description="Restore a host snapshot folder with sudo /usr/local/bin/moltbox-snapshot restore as an async job.")
        async def restore_runtime_snapshot(snapshot_folder: str, ctx: Context | None = None) -> dict:
            return await launch("restore_runtime_snapshot", "prod", lambda job_id: self._restore_runtime_snapshot(snapshot_folder, job_id), ctx)

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

    def _run_sync_operation(self, operation: str, runtime: str, handler: Callable[[], dict]) -> dict:
        self._load_runtime(runtime)
        if operation not in MUTATING_OPERATIONS:
            return handler()
        with self._busy_lock:
            if runtime in self._busy_runtimes:
                raise RuntimeError(f"Another mutating operation is already running for runtime '{runtime}'")
            self._busy_runtimes.add(runtime)
        try:
            return handler()
        finally:
            with self._busy_lock:
                self._busy_runtimes.discard(runtime)

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

    def _repo_status(self, runtime: str) -> dict:
        ctx = self._load_runtime(runtime)
        branch = self._run_command(ctx, ["git", "branch", "--show-current"], cwd=ctx.repo_root)
        head = self._run_command(ctx, ["git", "rev-parse", "HEAD"], cwd=ctx.repo_root)
        status = self._run_command(ctx, ["git", "status", "--short", "--branch"], cwd=ctx.repo_root)
        return {
            "ok": branch["ok"] and head["ok"] and status["ok"],
            "operation": "repo_status",
            "runtime": runtime,
            "job_id": None,
            "status": "succeeded" if branch["ok"] and head["ok"] and status["ok"] else "failed",
            "stdout": status["stdout"],
            "stderr": "\n".join(filter(None, [branch["stderr"], head["stderr"], status["stderr"]])),
            "artifacts": [],
            "started_at": None,
            "finished_at": None,
            "exit_code": 0 if branch["ok"] and head["ok"] and status["ok"] else 1,
            "details": {
                "repo_root": str(ctx.repo_root),
                "branch": branch["stdout"].strip(),
                "head": head["stdout"].strip(),
            },
        }

    def _list_runtime_snapshots(self) -> dict:
        ctx = self._load_runtime("prod")
        result = self._run_command(
            ctx,
            ["sudo", SNAPSHOT_TOOL, "list"],
            timeout=self.config.default_timeout_seconds,
            cwd=ctx.repo_root,
        )
        snapshots = [line.strip() for line in result["stdout"].splitlines() if line.strip()]
        result.update(
            {
                "operation": "list_runtime_snapshots",
                "runtime": "prod",
                "details": {"snapshot_root": SNAPSHOT_ROOT, "snapshots": snapshots},
            }
        )
        return result

    def _snapshot_state(self) -> dict:
        snapshots = self._snapshot_names()
        latest = snapshots[-1] if snapshots else None
        latest_path = f"{SNAPSHOT_ROOT}/{latest}" if latest else None
        return {
            "ok": True,
            "operation": "snapshot_state",
            "runtime": "prod",
            "job_id": None,
            "status": "succeeded",
            "stdout": "\n".join(snapshots) + ("\n" if snapshots else ""),
            "stderr": "",
            "artifacts": [latest_path] if latest_path else [],
            "started_at": None,
            "finished_at": None,
            "exit_code": 0,
            "details": {
                "snapshot_root": SNAPSHOT_ROOT,
                "snapshot_count": len(snapshots),
                "latest_snapshot": latest,
                "latest_snapshot_path": latest_path,
                "snapshots": snapshots,
            },
        }

    def _list_publish_runs(self) -> dict:
        runs = []
        for flow in self.flows.list():
            if flow.get("kind") != "publish":
                continue
            runs.append(
                {
                    "flow_id": flow["flow_id"],
                    "status": flow.get("status"),
                    "target_branch": flow.get("target_branch"),
                    "tested_head": flow.get("refs", {}).get("test_head"),
                    "deployed_head": flow.get("refs", {}).get("deployed_head"),
                    "approved": flow.get("approval", {}).get("approved"),
                    "updated_at": flow.get("updated_at"),
                }
            )
        return {
            "ok": True,
            "operation": "list_publish_runs",
            "runtime": "prod",
            "job_id": None,
            "status": "succeeded",
            "stdout": "\n".join(f"{item['flow_id']} {item['status']} {item['target_branch']}" for item in runs) + ("\n" if runs else ""),
            "stderr": "",
            "artifacts": [],
            "started_at": None,
            "finished_at": None,
            "exit_code": 0,
            "details": {"runs": runs},
        }

    def _publish_report(self, flow_id: str) -> dict:
        flow = self.flows.get(flow_id)
        details = {
            "flow_id": flow["flow_id"],
            "status": flow.get("status"),
            "target_branch": flow.get("target_branch"),
            "approval": flow.get("approval", {}),
            "refs": flow.get("refs", {}),
            "snapshot": flow.get("snapshot", {}),
            "production": flow.get("production", {}),
            "test_runtime": flow.get("test_runtime", {}),
            "report": flow.get("report", {}),
            "steps": flow.get("steps", []),
            "report_path": str(self.runtime.flows_dir / f"{flow_id}.json"),
        }
        return {
            "ok": True,
            "operation": "publish_report",
            "runtime": "prod",
            "job_id": None,
            "status": "succeeded",
            "stdout": json.dumps(details, indent=2) + "\n",
            "stderr": "",
            "artifacts": list(flow.get("report", {}).get("artifacts", [])),
            "started_at": flow.get("created_at"),
            "finished_at": flow.get("updated_at"),
            "exit_code": 0,
            "details": details,
        }

    def _publish_branch(
        self,
        mode: str,
        branch: str,
        validate: bool,
        exercise_script: str | None,
        exercise_args: list[str],
        collect_diagnostics: bool,
        job_id: str,
    ) -> dict:
        if mode not in {"test", "live"}:
            raise RuntimeError(f"Unsupported publish mode: {mode}")
        self._validate_git_ref(branch)
        if mode == "test":
            return self._publish_branch_to_test(branch, validate, exercise_script, exercise_args, collect_diagnostics, job_id)
        return self._publish_branch_to_live(branch, validate, collect_diagnostics, job_id)

    def _publish_branch_to_test(
        self,
        branch: str,
        validate: bool,
        exercise_script: str | None,
        exercise_args: list[str],
        collect_diagnostics: bool,
        job_id: str,
    ) -> dict:
        prod = self._load_runtime("prod")
        test = self._load_runtime("test")
        prod_running = self._stack_running(prod)
        prod_refs = self._git_refs(prod)
        flow = self.flows.create(
            {
                "kind": "publish",
                "status": "preparing_test",
                "target_branch": branch,
                "approval": {"required": True, "approved": None, "approved_at": None, "note": ""},
                "snapshot": {"before_test": None, "before_live": None},
                "refs": {
                    "prod_head_before_test": prod_refs["head"],
                    "prod_branch_before_test": prod_refs["branch"],
                    "test_head": None,
                    "test_branch": None,
                    "deployed_head": None,
                },
                "production": {
                    "was_running_before_test": prod_running,
                    "stopped_for_test": False,
                    "resumed_after_test": False,
                    "rollback_head": prod_refs["head"],
                    "rollback_branch": prod_refs["branch"],
                },
                "test_runtime": {
                    "runtime_root": str(test.runtime_root),
                    "repo_root": str(test.repo_root),
                    "status": "not_created",
                },
                "report": {
                    "summary": "",
                    "artifacts": [],
                    "manual_testing_required": True,
                    "manual_testing_complete": False,
                    "live_deploy": {"status": "not_started"},
                },
                "steps": [],
            }
        )
        self.jobs.append_log(job_id, f"publish_flow_id={flow['flow_id']}\n")

        snapshot = self._snapshot_runtime(job_id)
        flow["snapshot"]["before_test"] = snapshot.get("details", {}).get("snapshot_path")
        self._record_publish_step(flow, "snapshot_before_test", snapshot)
        if not snapshot["ok"]:
            return self._fail_publish_flow(flow, "Test publish snapshot failed", snapshot)

        prod_running_after_snapshot = self._stack_running(prod)
        flow["production"]["was_running_before_test"] = prod_running or prod_running_after_snapshot
        if prod_running_after_snapshot:
            stopped = self._stop_stack("prod", job_id)
            self._record_publish_step(flow, "stop_prod_for_test", stopped)
            if not stopped["ok"]:
                return self._fail_publish_flow(flow, "Stopping production before test publish failed", stopped)
            flow["production"]["stopped_for_test"] = True

        created = self._create_test_runtime(True, prod_refs["head"], job_id)
        self._record_publish_step(flow, "create_test_runtime", created)
        if not created["ok"]:
            return self._fail_publish_flow(flow, "Creating the disposable test runtime failed", created)
        flow["test_runtime"]["status"] = "created"

        fetched = self._run_command(
            test,
            ["git", "fetch", "--prune", "origin"],
            job_id=job_id,
            timeout=self.config.long_timeout_seconds,
            cwd=test.repo_root,
        )
        fetched["operation"] = "publish_branch_fetch_test"
        self._record_publish_step(flow, "fetch_test_branch", fetched)
        if not fetched["ok"]:
            return self._fail_publish_flow(flow, "Fetching the target test branch failed", fetched)

        checked_out = self._checkout_test_branch(branch, job_id)
        self._record_publish_step(flow, "checkout_test_branch", checked_out)
        if not checked_out["ok"]:
            return self._fail_publish_flow(flow, "Checking out the target test branch failed", checked_out)

        test_refs = self._git_refs(test)
        flow["refs"]["test_head"] = test_refs["head"]
        flow["refs"]["test_branch"] = test_refs["branch"]
        self.flows.write(flow["flow_id"], flow)

        bootstrapped = self._run_script(
            "test",
            "20-bootstrap.sh",
            job_id,
            "publish_branch_bootstrap_test",
            allow_prod_running_for_test=True,
        )
        self._record_publish_step(flow, "bootstrap_test_runtime", bootstrapped)
        if not bootstrapped["ok"]:
            return self._fail_publish_flow(flow, "Bootstrapping the test runtime failed", bootstrapped)

        validated = None
        if validate:
            validated = self._run_script("test", "30-validate.sh", job_id, "publish_branch_validate_test")
            self._record_publish_step(flow, "validate_test_runtime", validated)
            if not validated["ok"]:
                return self._fail_publish_flow(flow, "Validation failed for the test runtime", validated)

        exercised = None
        if exercise_script:
            exercised = self._run_remote_script("test", exercise_script, exercise_args, job_id)
            self._record_publish_step(flow, "exercise_test_runtime", exercised)
            if not exercised["ok"]:
                return self._fail_publish_flow(flow, "The configured exercise script failed in the test runtime", exercised)

        diagnostics = None
        if collect_diagnostics:
            diagnostics = self._collect_diagnostics("test", job_id)
            self._record_publish_step(flow, "collect_test_diagnostics", diagnostics)

        test_status = self._runtime_status("test")
        self._record_publish_step(flow, "test_runtime_status", test_status)
        if not test_status["ok"]:
            return self._fail_publish_flow(flow, "The test runtime did not reach a healthy state", test_status)

        flow["status"] = "test_active"
        flow["test_runtime"]["status"] = "active"
        flow["report"]["summary"] = (
            f"Branch '{branch}' published into the disposable test runtime. "
            f"Test head {flow['refs']['test_head']} is active and ready for manual or scripted exercise."
        )
        flow["report"]["manual_testing_complete"] = False
        self.flows.write(flow["flow_id"], flow)
        artifacts = list(flow["report"]["artifacts"])
        stdout_lines = [
            f"flow_id={flow['flow_id']}",
            f"target_branch={branch}",
            f"test_head={flow['refs']['test_head']}",
            f"report_path={self.runtime.flows_dir / (flow['flow_id'] + '.json')}",
        ]
        return {
            "ok": True,
            "operation": "publish_branch",
            "runtime": "test",
            "stdout": "\n".join(stdout_lines) + "\n",
            "stderr": "",
            "artifacts": artifacts,
            "exit_code": 0,
            "details": {
                "mode": "test",
                "flow_id": flow["flow_id"],
                "target_branch": branch,
                "report_path": str(self.runtime.flows_dir / f"{flow['flow_id']}.json"),
                "test_head": flow["refs"]["test_head"],
                "test_branch": flow["refs"]["test_branch"],
                "snapshot_path": flow["snapshot"]["before_test"],
            },
        }

    def _publish_branch_to_live(self, branch: str, validate: bool, collect_diagnostics: bool, job_id: str) -> dict:
        flow = self._latest_approved_publish_flow(branch)
        if flow is None:
            raise RuntimeError(f"No approved test publish exists for branch '{branch}'")

        prod = self._load_runtime("prod")
        prod_refs = self._git_refs(prod)
        dirty = self._git_dirty(prod)
        if dirty:
            raise RuntimeError("Production repo has uncommitted changes; refuse live publish until it is clean")

        flow["status"] = "deploying_live"
        flow["refs"]["prod_head_before_live"] = prod_refs["head"]
        flow["refs"]["prod_branch_before_live"] = prod_refs["branch"]
        flow["production"]["rollback_head"] = prod_refs["head"]
        flow["production"]["rollback_branch"] = prod_refs["branch"]
        self.flows.write(flow["flow_id"], flow)
        self.jobs.append_log(job_id, f"publish_flow_id={flow['flow_id']}\n")

        snapshot = self._snapshot_runtime(job_id)
        flow["snapshot"]["before_live"] = snapshot.get("details", {}).get("snapshot_path")
        self._record_publish_step(flow, "snapshot_before_live", snapshot)
        if not snapshot["ok"]:
            return self._fail_publish_flow(flow, "Live publish snapshot failed", snapshot)

        if self._stack_running(prod):
            stopped = self._stop_stack("prod", job_id)
            self._record_publish_step(flow, "stop_prod_for_live", stopped)
            if not stopped["ok"]:
                return self._fail_publish_flow(flow, "Stopping production before live publish failed", stopped)

        fetched = self._run_command(
            prod,
            ["git", "fetch", "--prune", "origin"],
            job_id=job_id,
            timeout=self.config.long_timeout_seconds,
            cwd=prod.repo_root,
        )
        fetched["operation"] = "publish_branch_fetch_prod"
        self._record_publish_step(flow, "fetch_prod_repo", fetched)
        if not fetched["ok"]:
            return self._fail_publish_flow(flow, "Fetching production git refs failed", fetched)

        tested_head = flow.get("refs", {}).get("test_head")
        deployed = self._checkout_repo_detached(prod, tested_head, job_id)
        self._record_publish_step(flow, "checkout_live_head", deployed)
        if not deployed["ok"]:
            return self._fail_publish_flow(flow, "Checking out the approved tested commit for live publish failed", deployed)

        bootstrapped = self._run_script("prod", "20-bootstrap.sh", job_id, "publish_branch_bootstrap_live")
        self._record_publish_step(flow, "bootstrap_live_runtime", bootstrapped)
        if not bootstrapped["ok"]:
            return self._rollback_live_publish(flow, job_id, "Bootstrapping production failed after switching to the approved test commit", bootstrapped)

        validated = None
        if validate:
            validated = self._run_script("prod", "30-validate.sh", job_id, "publish_branch_validate_live")
            self._record_publish_step(flow, "validate_live_runtime", validated)
            if not validated["ok"]:
                return self._rollback_live_publish(flow, job_id, "Validation failed after live publish", validated)

        diagnostics = None
        if collect_diagnostics:
            diagnostics = self._collect_diagnostics("prod", job_id)
            self._record_publish_step(flow, "collect_live_diagnostics", diagnostics)

        prod_status = self._runtime_status("prod")
        self._record_publish_step(flow, "live_runtime_status", prod_status)
        if not prod_status["ok"]:
            return self._rollback_live_publish(flow, job_id, "Production runtime health check failed after live publish", prod_status)

        flow["status"] = "live_deployed"
        flow["refs"]["deployed_head"] = tested_head
        flow["report"]["live_deploy"] = {
            "status": "deployed",
            "deployed_head": tested_head,
            "deployed_at": datetime.now(tz=UTC).isoformat(),
            "snapshot_path": flow["snapshot"]["before_live"],
        }
        flow["report"]["summary"] = (
            f"Branch '{branch}' live publish succeeded. "
            f"Production is now running tested commit {tested_head}."
        )
        self.flows.write(flow["flow_id"], flow)
        return {
            "ok": True,
            "operation": "publish_branch",
            "runtime": "prod",
            "stdout": (
                f"flow_id={flow['flow_id']}\n"
                f"target_branch={branch}\n"
                f"deployed_head={tested_head}\n"
                f"report_path={self.runtime.flows_dir / (flow['flow_id'] + '.json')}\n"
            ),
            "stderr": "",
            "artifacts": list(flow["report"]["artifacts"]),
            "exit_code": 0,
            "details": {
                "mode": "live",
                "flow_id": flow["flow_id"],
                "target_branch": branch,
                "deployed_head": tested_head,
                "report_path": str(self.runtime.flows_dir / f"{flow['flow_id']}.json"),
                "snapshot_path": flow["snapshot"]["before_live"],
            },
        }

    def _finalize_test_publish(
        self,
        flow_id: str,
        resume_production: bool,
        destroy_test_runtime: bool,
        collect_diagnostics: bool,
        job_id: str,
    ) -> dict:
        flow = self.flows.get(flow_id)
        if flow.get("kind") != "publish":
            raise RuntimeError(f"Flow '{flow_id}' is not a publish workflow")
        if flow.get("status") not in {"test_active", "awaiting_approval", "approved", "rejected"}:
            raise RuntimeError(f"Flow '{flow_id}' is not in a finalizable state: {flow.get('status')}")

        test_status = self._runtime_status("test")
        self._record_publish_step(flow, "final_test_runtime_status", test_status)

        if collect_diagnostics:
            diagnostics = self._collect_diagnostics("test", job_id)
            self._record_publish_step(flow, "final_test_diagnostics", diagnostics)

        if destroy_test_runtime:
            destroyed = self._destroy_test_runtime(True, job_id)
            self._record_publish_step(flow, "destroy_test_runtime", destroyed)
            if not destroyed["ok"]:
                return self._fail_publish_flow(flow, "Destroying the test runtime failed during finalization", destroyed)
            flow["test_runtime"]["status"] = "destroyed"
        else:
            stopped = self._stop_stack("test", job_id)
            self._record_publish_step(flow, "stop_test_runtime", stopped)
            if not stopped["ok"]:
                return self._fail_publish_flow(flow, "Stopping the test runtime failed during finalization", stopped)
            flow["test_runtime"]["status"] = "stopped"

        if resume_production and flow.get("production", {}).get("stopped_for_test"):
            started = self._start_stack("prod", job_id)
            self._record_publish_step(flow, "resume_prod_after_test", started)
            if not started["ok"]:
                return self._fail_publish_flow(flow, "Resuming production after test finalization failed", started)
            flow["production"]["resumed_after_test"] = True

        flow["status"] = "awaiting_approval"
        flow["report"]["manual_testing_complete"] = True
        flow["report"]["summary"] = (
            f"Test publish for branch '{flow.get('target_branch')}' is complete. "
            "Review the report, then record approval before publishing live."
        )
        self.flows.write(flow["flow_id"], flow)
        return {
            "ok": True,
            "operation": "finalize_test_publish",
            "runtime": "test",
            "stdout": (
                f"flow_id={flow['flow_id']}\n"
                f"status={flow['status']}\n"
                f"report_path={self.runtime.flows_dir / (flow['flow_id'] + '.json')}\n"
            ),
            "stderr": "",
            "artifacts": list(flow["report"]["artifacts"]),
            "exit_code": 0,
            "details": {
                "flow_id": flow["flow_id"],
                "status": flow["status"],
                "report_path": str(self.runtime.flows_dir / f"{flow['flow_id']}.json"),
                "approval_required": True,
            },
        }

    def _approve_test_publish(self, flow_id: str, approved: bool, note: str) -> dict:
        flow = self.flows.get(flow_id)
        if flow.get("kind") != "publish":
            raise RuntimeError(f"Flow '{flow_id}' is not a publish workflow")
        if flow.get("status") not in {"awaiting_approval", "approved", "rejected"}:
            raise RuntimeError(f"Flow '{flow_id}' is not ready for approval: {flow.get('status')}")
        flow["approval"] = {
            **flow.get("approval", {}),
            "approved": approved,
            "approved_at": datetime.now(tz=UTC).isoformat(),
            "note": note,
        }
        flow["status"] = "approved" if approved else "rejected"
        flow["report"]["summary"] = (
            f"Test publish for branch '{flow.get('target_branch')}' was "
            f"{'approved' if approved else 'rejected'} for live deployment."
        )
        self.flows.write(flow["flow_id"], flow)
        return {
            "ok": True,
            "operation": "approve_test_publish",
            "runtime": "prod",
            "job_id": None,
            "status": "succeeded",
            "stdout": (
                f"flow_id={flow['flow_id']}\n"
                f"approved={'true' if approved else 'false'}\n"
            ),
            "stderr": "",
            "artifacts": list(flow["report"]["artifacts"]),
            "started_at": None,
            "finished_at": None,
            "exit_code": 0,
            "details": {
                "flow_id": flow["flow_id"],
                "approved": approved,
                "note": note,
                "status": flow["status"],
                "report_path": str(self.runtime.flows_dir / f"{flow['flow_id']}.json"),
            },
        }

    def _runtime_config_summary(self, runtime: str) -> dict:
        ctx = self._load_runtime(runtime)
        cfg = load_service_config(ctx)
        snapshots = self._snapshot_names()
        client_summary = [
            {
                "id": rule.client_id,
                "allowed_origins": list(rule.allowed_origins),
                "scopes": list(rule.scopes),
            }
            for rule in cfg.clients.values()
        ]
        env_summary = {
            "compose_project_name": ctx.compose_project_name,
            "gateway_port": ctx.gateway_port,
            "debug_service_port": ctx.debug_service_port,
            "openclaw_gateway_bind": ctx.env_values.get("OPENCLAW_GATEWAY_BIND", ""),
            "openclaw_trusted_proxies": ctx.env_values.get("OPENCLAW_TRUSTED_PROXIES", ""),
            "openclaw_trusted_proxies_auto": ctx.env_values.get("OPENCLAW_TRUSTED_PROXIES_AUTO", ""),
            "openclaw_allowed_origins_extra": ctx.env_values.get("OPENCLAW_ALLOWED_ORIGINS_EXTRA", ""),
            "lan_cidr": ctx.lan_cidr,
            "openclaw_container_name": ctx.openclaw_container_name,
            "ollama_container_name": ctx.ollama_container_name,
            "opensearch_container_name": ctx.opensearch_container_name,
            "ollama_image": ctx.env_values.get("OLLAMA_IMAGE", ""),
            "opensearch_image": ctx.env_values.get("OPENSEARCH_IMAGE", ""),
            "openclaw_image": ctx.env_values.get("OPENCLAW_IMAGE", ""),
        }
        gateway = ctx.openclaw_values.get("gateway", {}) if isinstance(ctx.openclaw_values, dict) else {}
        control_ui = gateway.get("controlUi", {}) if isinstance(gateway, dict) else {}
        trusted_proxies = gateway.get("trustedProxies", []) if isinstance(gateway, dict) else []
        token_details = {
            "gateway_token_present": bool(ctx.env_values.get("OPENCLAW_GATEWAY_TOKEN")),
            "gateway_token_fingerprint": self._token_fingerprint(ctx.env_values.get("OPENCLAW_GATEWAY_TOKEN", "")),
            "debug_service_token_present": bool(ctx.env_values.get("DEBUG_SERVICE_TOKEN")),
            "debug_service_token_fingerprint": self._token_fingerprint(ctx.env_values.get("DEBUG_SERVICE_TOKEN", "")),
        }
        details = {
            "runtime_root": str(ctx.runtime_root),
            "repo_root": str(ctx.repo_root),
            "env": env_summary,
            "gateway": {
                "mode": gateway.get("mode") if isinstance(gateway, dict) else None,
                "allow_insecure_auth": bool(control_ui.get("allowInsecureAuth")) if isinstance(control_ui, dict) else False,
                "allowed_origins": [value for value in control_ui.get("allowedOrigins", []) if isinstance(value, str)] if isinstance(control_ui, dict) else [],
                "trusted_proxies": [value for value in trusted_proxies if isinstance(value, str)] if isinstance(trusted_proxies, list) else [],
            },
            "debug_service": {
                "host": cfg.host,
                "port": cfg.port,
                "lan_cidr": cfg.lan_cidr,
                "allowed_clients": client_summary,
            },
            "tokens": token_details,
            "snapshot": {
                "latest_snapshot": (snapshots[-1] if snapshots else None),
            },
        }
        return {
            "ok": True,
            "operation": "runtime_config_summary",
            "runtime": runtime,
            "job_id": None,
            "status": "succeeded",
            "stdout": json.dumps(details, indent=2) + "\n",
            "stderr": "",
            "artifacts": [],
            "started_at": None,
            "finished_at": None,
            "exit_code": 0,
            "details": details,
        }

    def _gateway_auth_state(self, runtime: str) -> dict:
        ctx = self._load_runtime(runtime)
        gateway = ctx.openclaw_values.get("gateway", {}) if isinstance(ctx.openclaw_values, dict) else {}
        control_ui = gateway.get("controlUi", {}) if isinstance(gateway, dict) else {}
        trusted_proxies = gateway.get("trustedProxies", []) if isinstance(gateway, dict) else []
        details = {
            "gateway_mode": gateway.get("mode") if isinstance(gateway, dict) else None,
            "allow_insecure_auth": bool(control_ui.get("allowInsecureAuth")) if isinstance(control_ui, dict) else False,
            "allowed_origins": [value for value in control_ui.get("allowedOrigins", []) if isinstance(value, str)] if isinstance(control_ui, dict) else [],
            "trusted_proxies": [value for value in trusted_proxies if isinstance(value, str)] if isinstance(trusted_proxies, list) else [],
            "gateway_token_present": bool(ctx.env_values.get("OPENCLAW_GATEWAY_TOKEN")),
            "gateway_token_fingerprint": self._token_fingerprint(ctx.env_values.get("OPENCLAW_GATEWAY_TOKEN", "")),
            "debug_service_token_present": bool(ctx.env_values.get("DEBUG_SERVICE_TOKEN")),
            "debug_service_token_fingerprint": self._token_fingerprint(ctx.env_values.get("DEBUG_SERVICE_TOKEN", "")),
        }
        return {
            "ok": True,
            "operation": "gateway_auth_state",
            "runtime": runtime,
            "job_id": None,
            "status": "succeeded",
            "stdout": json.dumps(details, indent=2) + "\n",
            "stderr": "",
            "artifacts": [],
            "started_at": None,
            "finished_at": None,
            "exit_code": 0,
            "details": details,
        }

    def _paired_devices(self, runtime: str) -> dict:
        ctx = self._load_runtime(runtime)
        result = self._run_command(
            ctx,
            self._compose_args(ctx, "exec", "-T", "openclaw", "openclaw", "devices", "list"),
            timeout=self.config.default_timeout_seconds,
            cwd=ctx.repo_root,
        )
        sections = self._parse_devices_list(result["stdout"])
        result.update(
            {
                "operation": "paired_devices",
                "runtime": runtime,
                "details": sections,
            }
        )
        return result

    def _list_remote_scripts(self, runtime: str) -> dict:
        ctx = self._load_runtime(runtime)
        scripts: list[dict[str, Any]] = []
        if ctx.remote_script_dir.is_dir():
            for path in sorted(ctx.remote_script_dir.glob("*.sh")):
                if path.is_file():
                    scripts.append(
                        {
                            "name": path.name,
                            "path": str(path.relative_to(ctx.repo_root)),
                            "size_bytes": path.stat().st_size,
                        }
                    )
        return {
            "ok": True,
            "operation": "list_remote_scripts",
            "runtime": runtime,
            "job_id": None,
            "status": "succeeded",
            "stdout": "\n".join(script["name"] for script in scripts) + ("\n" if scripts else ""),
            "stderr": "",
            "artifacts": [],
            "started_at": None,
            "finished_at": None,
            "exit_code": 0,
            "details": {"repo_root": str(ctx.repo_root), "remote_script_dir": str(ctx.remote_script_dir), "scripts": scripts},
        }

    def _record_publish_step(self, flow: dict, name: str, result: dict) -> None:
        step = {
            "name": name,
            "ok": result.get("ok"),
            "status": result.get("status"),
            "exit_code": result.get("exit_code"),
            "stdout_tail": "\n".join(result.get("stdout", "").splitlines()[-20:]),
            "stderr_tail": "\n".join(result.get("stderr", "").splitlines()[-20:]),
            "artifacts": list(result.get("artifacts", [])),
        }
        if "details" in result:
            step["details"] = result["details"]
        flow.setdefault("steps", []).append(step)
        artifacts = flow.setdefault("report", {}).setdefault("artifacts", [])
        for item in result.get("artifacts", []):
            if item not in artifacts:
                artifacts.append(item)
        self.flows.write(flow["flow_id"], flow)

    def _fail_publish_flow(self, flow: dict, message: str, result: dict) -> dict:
        flow["status"] = "failed"
        flow.setdefault("report", {})["summary"] = message
        self.flows.write(flow["flow_id"], flow)
        return {
            **result,
            "operation": result.get("operation") or "publish_branch",
            "details": {
                **result.get("details", {}),
                "flow_id": flow["flow_id"],
                "report_path": str(self.runtime.flows_dir / f"{flow['flow_id']}.json"),
                "summary": message,
            },
        }

    def _rollback_live_publish(self, flow: dict, job_id: str, message: str, failed_result: dict) -> dict:
        prod = self._load_runtime("prod")
        rollback = self._checkout_repo_detached(prod, flow.get("production", {}).get("rollback_head"), job_id)
        self._record_publish_step(flow, "rollback_prod_head", rollback)
        if rollback["ok"]:
            rebootstrap = self._run_script("prod", "20-bootstrap.sh", job_id, "publish_branch_bootstrap_rollback")
            self._record_publish_step(flow, "rollback_bootstrap_prod", rebootstrap)
            if rebootstrap["ok"]:
                revalidate = self._run_script("prod", "30-validate.sh", job_id, "publish_branch_validate_rollback")
                self._record_publish_step(flow, "rollback_validate_prod", revalidate)
                if revalidate["ok"]:
                    flow["status"] = "live_failed_rolled_back"
                    flow.setdefault("report", {})["summary"] = f"{message}. Production was rolled back to the previous deployed commit."
                    self.flows.write(flow["flow_id"], flow)
                    return {
                        **failed_result,
                        "details": {
                            **failed_result.get("details", {}),
                            "flow_id": flow["flow_id"],
                            "report_path": str(self.runtime.flows_dir / f"{flow['flow_id']}.json"),
                            "summary": flow["report"]["summary"],
                            "rollback_head": flow.get("production", {}).get("rollback_head"),
                        },
                    }
        flow["status"] = "live_failed"
        flow.setdefault("report", {})["summary"] = f"{message}. Automatic rollback did not complete cleanly."
        self.flows.write(flow["flow_id"], flow)
        return {
            **failed_result,
            "details": {
                **failed_result.get("details", {}),
                "flow_id": flow["flow_id"],
                "report_path": str(self.runtime.flows_dir / f"{flow['flow_id']}.json"),
                "summary": flow["report"]["summary"],
                "rollback_head": flow.get("production", {}).get("rollback_head"),
            },
        }

    def _latest_approved_publish_flow(self, branch: str) -> dict | None:
        for flow in self.flows.list():
            if flow.get("kind") != "publish":
                continue
            if flow.get("target_branch") != branch:
                continue
            if flow.get("approval", {}).get("approved") is True:
                return flow
        return None

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

    def _git_refs(self, ctx: RuntimeContext) -> dict[str, str]:
        branch = self._run_command(ctx, ["git", "branch", "--show-current"], cwd=ctx.repo_root)
        head = self._run_command(ctx, ["git", "rev-parse", "HEAD"], cwd=ctx.repo_root)
        if not branch["ok"] or not head["ok"]:
            raise RuntimeError(f"Failed to inspect git refs for runtime '{ctx.name}'")
        return {"branch": branch["stdout"].strip(), "head": head["stdout"].strip()}

    def _git_dirty(self, ctx: RuntimeContext) -> bool:
        status = self._run_command(ctx, ["git", "status", "--porcelain", "--untracked-files=all"], cwd=ctx.repo_root)
        if not status["ok"]:
            return True
        ignored_prefixes = (
            "moltbox/debug-service/.venv/",
            "moltbox/debug-service/src/moltbox_debug_service.egg-info/",
            "moltbox/debug-service/src/moltbox_debug_service/__pycache__/",
        )
        for line in status["stdout"].splitlines():
            path = line[3:].strip()
            if not path:
                continue
            if path.startswith(ignored_prefixes):
                continue
            return True
        return False

    def _stack_running(self, ctx: RuntimeContext) -> bool:
        ps = self._run_command(
            ctx,
            self._compose_args(ctx, "ps", "--status", "running", "-q", "openclaw"),
            timeout=15,
            cwd=ctx.repo_root,
        )
        return bool(ps["ok"] and ps["stdout"].strip())

    def _checkout_test_branch(self, branch: str, job_id: str) -> dict:
        test = self._load_runtime("test")
        direct = self._run_command(
            test,
            ["git", "checkout", branch],
            job_id=job_id,
            timeout=self.config.long_timeout_seconds,
            cwd=test.repo_root,
        )
        direct["operation"] = "publish_branch_checkout_test"
        if direct["ok"]:
            return direct
        fallback = self._run_command(
            test,
            ["git", "checkout", "-B", branch, f"origin/{branch}"],
            job_id=job_id,
            timeout=self.config.long_timeout_seconds,
            cwd=test.repo_root,
        )
        fallback["operation"] = "publish_branch_checkout_test"
        return fallback

    def _checkout_repo_detached(self, ctx: RuntimeContext, ref: str | None, job_id: str) -> dict:
        if not ref:
            raise RuntimeError("Missing git ref for detached checkout")
        result = self._run_command(
            ctx,
            ["git", "checkout", "--detach", ref],
            job_id=job_id,
            timeout=self.config.long_timeout_seconds,
            cwd=ctx.repo_root,
        )
        result["operation"] = "publish_branch_checkout_detached"
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

    def _run_script(
        self,
        runtime: str,
        script_name: str,
        job_id: str,
        operation_name: str | None = None,
        *,
        allow_prod_running_for_test: bool = False,
    ) -> dict:
        ctx = self._load_runtime(runtime)
        if runtime == "test" and script_name == "20-bootstrap.sh" and not allow_prod_running_for_test:
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

    def _run_named_script(self, runtime: str, script_id: str, job_id: str) -> dict:
        script_name = SAFE_SCRIPT_MAP.get(script_id)
        if script_name is None:
            raise RuntimeError(f"Unsupported script_id: {script_id}")
        return self._run_script(runtime, script_name, job_id, "run_script")

    def _run_named_script_sync(self, runtime: str, script_id: str, timeout_seconds: int | None) -> dict:
        script_name = SAFE_SCRIPT_MAP.get(script_id)
        if script_name is None:
            raise RuntimeError(f"Unsupported script_id: {script_id}")
        ctx = self._load_runtime(runtime)
        if runtime == "test" and script_name == "20-bootstrap.sh":
            self._ensure_test_runtime_idle()
        result = self._run_command(
            ctx,
            ["bash", str(ctx.script_dir / script_name)],
            timeout=self._clamp_timeout(timeout_seconds),
            cwd=ctx.repo_root / "moltbox",
        )
        result["operation"] = "run_script_sync"
        result["runtime"] = runtime
        result["details"] = {"script_id": script_id, "script_name": script_name}
        return result

    def _run_remote_script(self, runtime: str, script_name: str, args: list[str], job_id: str) -> dict:
        ctx = self._load_runtime(runtime)
        path = self._resolve_remote_script(ctx, script_name)
        argv = ["bash", str(path), *self._sanitize_script_args(args)]
        result = self._run_command(
            ctx,
            argv,
            job_id=job_id,
            timeout=self.config.long_timeout_seconds,
            cwd=ctx.repo_root,
        )
        result["operation"] = "run_remote_script"
        result["details"] = {"script_name": path.name, "script_path": str(path.relative_to(ctx.repo_root)), "args": args}
        return result

    def _run_remote_script_sync(self, runtime: str, script_name: str, args: list[str], timeout_seconds: int | None) -> dict:
        ctx = self._load_runtime(runtime)
        path = self._resolve_remote_script(ctx, script_name)
        argv = ["bash", str(path), *self._sanitize_script_args(args)]
        result = self._run_command(
            ctx,
            argv,
            timeout=self._clamp_timeout(timeout_seconds),
            cwd=ctx.repo_root,
        )
        result["operation"] = "run_remote_script_sync"
        result["runtime"] = runtime
        result["details"] = {"script_name": path.name, "script_path": str(path.relative_to(ctx.repo_root)), "args": args}
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

    def _create_test_runtime(self, force_recreate: bool, ref: str | None, job_id: str) -> dict:
        prod = self._load_runtime("prod")
        test = self._load_runtime("test")
        if test.runtime_root.exists() and any(test.runtime_root.iterdir()) and not force_recreate:
            raise RuntimeError(f"Test runtime already exists at {test.runtime_root}; rerun with force_recreate=true")

        self._safe_remove_path(test.runtime_root)
        self._remove_worktree(test.repo_root)
        checkout_ref = ref or "HEAD"
        if ref is not None:
            self._validate_git_ref(ref)

        added = self._run_command(
            prod,
            ["git", "worktree", "add", "--force", str(test.repo_root), checkout_ref],
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

        stdout = (
            f"Created test runtime at {test.runtime_root}\n"
            f"Created test worktree at {test.repo_root}\n"
            f"Checked out test ref: {checkout_ref}\n"
        )
        self.jobs.append_log(job_id, stdout)
        return {
            "ok": True,
            "operation": "create_test_runtime",
            "runtime": "test",
            "stdout": stdout,
            "stderr": "",
            "artifacts": [str(test.runtime_root), str(test.repo_root)],
            "exit_code": 0,
            "details": {"ref": checkout_ref},
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

    def _snapshot_runtime(self, job_id: str) -> dict:
        ctx = self._load_runtime("prod")
        result = self._run_command(
            ctx,
            ["sudo", SNAPSHOT_TOOL, "create"],
            job_id=job_id,
            timeout=self.config.long_timeout_seconds,
            cwd=ctx.repo_root,
        )
        snapshot_path = self._extract_snapshot_path(result["stdout"])
        if snapshot_path:
            result["artifacts"] = [snapshot_path]
        result["operation"] = "snapshot_runtime"
        result["runtime"] = "prod"
        result["details"] = {"snapshot_root": SNAPSHOT_ROOT, "snapshot_path": snapshot_path}
        return result

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

    def _repo_pull(self, runtime: str, branch: str | None, job_id: str) -> dict:
        ctx = self._load_runtime(runtime)
        fetch = self._run_command(
            ctx,
            ["git", "fetch", "--prune", "origin"],
            job_id=job_id,
            timeout=self.config.long_timeout_seconds,
            cwd=ctx.repo_root,
        )
        if not fetch["ok"]:
            fetch["operation"] = "repo_pull"
            fetch["runtime"] = runtime
            return fetch

        argv = ["git", "pull", "--ff-only"]
        if branch:
            self._validate_git_ref(branch)
            argv.extend(["origin", branch])

        pulled = self._run_command(
            ctx,
            argv,
            job_id=job_id,
            timeout=self.config.long_timeout_seconds,
            cwd=ctx.repo_root,
        )
        pulled["operation"] = "repo_pull"
        pulled["runtime"] = runtime
        return pulled

    def _repo_checkout_ref(self, runtime: str, ref: str, job_id: str) -> dict:
        if runtime != "test":
            raise RuntimeError("repo_checkout_ref is limited to the test runtime")
        self._validate_git_ref(ref)
        ctx = self._load_runtime(runtime)
        if not ctx.repo_root.exists():
            raise RuntimeError("Test worktree does not exist; run create_test_runtime first")

        result = self._run_command(
            ctx,
            ["git", "checkout", ref],
            job_id=job_id,
            timeout=self.config.long_timeout_seconds,
            cwd=ctx.repo_root,
        )
        result["operation"] = "repo_checkout_ref"
        result["runtime"] = runtime
        result["details"] = {"ref": ref, "repo_root": str(ctx.repo_root)}
        return result

    def _restore_runtime_snapshot(self, snapshot_folder: str, job_id: str) -> dict:
        ctx = self._load_runtime("prod")
        if not snapshot_folder or Path(snapshot_folder).name != snapshot_folder:
            raise RuntimeError(f"Invalid snapshot folder: {snapshot_folder}")
        result = self._run_command(
            ctx,
            ["sudo", SNAPSHOT_TOOL, "restore", snapshot_folder],
            job_id=job_id,
            timeout=self.config.long_timeout_seconds,
            cwd=ctx.repo_root,
        )
        result["operation"] = "restore_runtime_snapshot"
        result["runtime"] = "prod"
        result["details"] = {"snapshot_root": SNAPSHOT_ROOT, "snapshot_folder": snapshot_folder}
        return result

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
        prod = self._load_runtime("prod")
        env_values = parse_env_file(ctx.env_file)
        env_values["COMPOSE_PROJECT_NAME"] = TEST_COMPOSE_PROJECT
        env_values["GATEWAY_PORT"] = str(TEST_GATEWAY_PORT)
        env_values["DEBUG_SERVICE_PORT"] = str(TEST_DEBUG_SERVICE_PORT)
        env_values["OPENCLAW_CONTAINER_NAME"] = "moltbox-test-openclaw"
        env_values["OLLAMA_CONTAINER_NAME"] = "moltbox-test-ollama"
        env_values["OPENSEARCH_CONTAINER_NAME"] = "moltbox-test-opensearch"
        self._write_env_file(ctx.env_file, env_values)

        config = load_json(ctx.openclaw_config_file, {})
        if not isinstance(config, dict):
            config = {}
        gateway = config.setdefault("gateway", {})
        if not isinstance(gateway, dict):
            gateway = {}
            config["gateway"] = gateway
        gateway["mode"] = "local"
        control_ui = gateway.setdefault("controlUi", {})
        if not isinstance(control_ui, dict):
            control_ui = {}
            gateway["controlUi"] = control_ui
        existing = control_ui.get("allowedOrigins")
        if not isinstance(existing, list):
            existing = []
        # Test publishes use plain HTTP on the disposable gateway port, so allow
        # token-only auth there instead of requiring a browser secure context.
        control_ui["allowInsecureAuth"] = True
        control_ui["allowedOrigins"] = self._build_test_allowed_origins(existing, prod)
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
        lines: list[str] = []
        for key, value in values.items():
            rendered = value
            if any(char.isspace() for char in value) or any(char in value for char in "\"'#$`\\"):
                rendered = shlex.quote(value)
            lines.append(f"{key}={rendered}\n")
        path.write_text("".join(lines), encoding="utf-8")

    def _resolve_remote_script(self, ctx: RuntimeContext, script_name: str) -> Path:
        if not script_name or Path(script_name).name != script_name or not script_name.endswith(".sh"):
            raise RuntimeError(f"Invalid remote script name: {script_name}")
        path = (ctx.remote_script_dir / script_name).resolve()
        root = ctx.remote_script_dir.resolve()
        if not str(path).startswith(str(root)) or not path.is_file():
            raise RuntimeError(f"Remote script not found: {script_name}")
        return path

    def _sanitize_script_args(self, args: list[str]) -> list[str]:
        sanitized: list[str] = []
        for value in args:
            if not isinstance(value, str):
                raise RuntimeError("Script args must be strings")
            if "\n" in value or "\r" in value or "\x00" in value:
                raise RuntimeError("Script args must not contain control characters")
            sanitized.append(value)
        return sanitized

    def _clamp_timeout(self, timeout_seconds: int | None) -> int:
        if timeout_seconds is None:
            return self.config.default_timeout_seconds
        return max(1, min(timeout_seconds, self.config.long_timeout_seconds))

    def _validate_git_ref(self, ref: str) -> None:
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-")
        if not ref or any(char not in allowed for char in ref) or ".." in ref or ref.startswith("/") or ref.endswith("/"):
            raise RuntimeError(f"Invalid git ref: {ref}")

    def _extract_snapshot_path(self, stdout: str) -> str | None:
        for line in stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{SNAPSHOT_ROOT}/"):
                return stripped
        return None

    def _build_test_allowed_origins(self, existing: list[str], prod: RuntimeContext) -> list[str]:
        merged: list[str] = []
        for origin in existing:
            if isinstance(origin, str) and origin not in merged:
                merged.append(origin)

        hosts: list[str] = []
        for origin in prod.allowed_origins:
            try:
                parsed = urlsplit(origin)
            except ValueError:
                continue
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
            if parsed.hostname not in hosts:
                hosts.append(parsed.hostname)

        for host in ("127.0.0.1", "localhost"):
            if host not in hosts:
                hosts.append(host)

        for host in TEST_CONTROL_UI_HOSTS:
            if host not in hosts:
                hosts.append(host)

        for host in hosts:
            for scheme in ("http", "https"):
                for include_port in (False, True):
                    origin = self._origin_for_host(host, scheme, TEST_GATEWAY_PORT if include_port else None)
                    if origin not in merged:
                        merged.append(origin)

        return merged

    def _origin_for_host(self, host: str, scheme: str, port: int | None) -> str:
        netloc = host if port is None else f"{host}:{port}"
        return urlunsplit((scheme, netloc, "", "", ""))

    def _snapshot_names(self) -> list[str]:
        root = Path(SNAPSHOT_ROOT)
        if not root.exists():
            return []
        return sorted(path.name for path in root.iterdir() if path.is_dir())

    def _token_fingerprint(self, value: str) -> str | None:
        if not value:
            return None
        if len(value) <= 8:
            return value
        return f"{value[:4]}...{value[-4:]}"

    def _parse_devices_list(self, stdout: str) -> dict[str, Any]:
        pending_count = 0
        paired_count = 0
        for line in stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("Pending (") and stripped.endswith(")"):
                pending_count = self._extract_section_count(stripped)
            elif stripped.startswith("Paired (") and stripped.endswith(")"):
                paired_count = self._extract_section_count(stripped)
        return {
            "pending_count": pending_count,
            "paired_count": paired_count,
            "raw": stdout,
        }

    def _extract_section_count(self, value: str) -> int:
        try:
            start = value.index("(") + 1
            end = value.index(")", start)
            return int(value[start:end])
        except (ValueError, TypeError):
            return 0

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
