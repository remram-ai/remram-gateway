from __future__ import annotations

import argparse
import base64
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


DEFAULT_STATE_ROOT = "/srv/moltbox-state"
DEFAULT_LOGS_ROOT = "/srv/moltbox-logs"
DEFAULT_GATEWAY_PORT = 7474
DEFAULT_GATEWAY_HTTP_PORT = 17474
DEFAULT_GITHUB_OWNER = "remram-ai"
DEFAULT_GITHUB_APP_ID = "3071584"
DEFAULT_GITHUB_APP_KEY_PATH = "~/.ssh/moltbox-prime-github-app.pem"
APP_AUTH_REPOSITORIES = {"moltbox-services", "moltbox-runtime"}


@dataclass(frozen=True)
class RemoteHostInfo:
    ssh_target: str
    hostname: str
    system: str
    kernel_release: str
    distro_id: str
    version_id: str
    pretty_name: str

    def as_dict(self) -> dict[str, str]:
        return {
            "ssh_target": self.ssh_target,
            "hostname": self.hostname,
            "system": self.system,
            "kernel_release": self.kernel_release,
            "distro_id": self.distro_id,
            "version_id": self.version_id,
            "pretty_name": self.pretty_name,
        }


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _ssh(ssh_target: str, remote_command: str) -> subprocess.CompletedProcess[str]:
    return _run(["ssh", ssh_target, f"bash -lc {shlex.quote(remote_command)}"])


def _ssh_python(ssh_target: str, script: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    exports = ""
    if env:
        exports = "".join(f"export {key}={shlex.quote(value)}\n" for key, value in env.items())
    remote_command = f"{exports}python3 - <<'PY'\n{script}\nPY"
    return _ssh(ssh_target, remote_command)


def _require(completed: subprocess.CompletedProcess[str], *, error_message: str, recovery_message: str) -> str:
    if completed.returncode == 0:
        return completed.stdout.strip()
    raise SystemExit(
        json.dumps(
            {
                "ok": False,
                "error": error_message,
                "recovery": recovery_message,
                "stdout": completed.stdout.strip(),
                "stderr": completed.stderr.strip(),
            },
            indent=2,
        )
    )


def detect_remote_host(ssh_target: str) -> RemoteHostInfo:
    probe = """
python3 - <<'PY'
import json
import pathlib
import platform
import socket

payload = {
    "hostname": socket.gethostname(),
    "system": platform.system(),
    "kernel_release": platform.release(),
    "distro_id": "",
    "version_id": "",
    "pretty_name": "",
}
os_release = pathlib.Path("/etc/os-release")
if os_release.exists():
    for line in os_release.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        payload[key.lower()] = value.strip().strip('"')
payload["distro_id"] = payload.get("id", "")
payload["version_id"] = payload.get("version_id", "")
payload["pretty_name"] = payload.get("pretty_name", "")
print(json.dumps(payload))
PY
""".strip()
    completed = _ssh(ssh_target, probe)
    raw = _require(
        completed,
        error_message="failed to detect the remote host runtime",
        recovery_message="verify SSH access and that python3 is installed on the remote host",
    )
    payload = json.loads(raw)
    info = RemoteHostInfo(
        ssh_target=ssh_target,
        hostname=str(payload.get("hostname") or ""),
        system=str(payload.get("system") or ""),
        kernel_release=str(payload.get("kernel_release") or ""),
        distro_id=str(payload.get("distro_id") or ""),
        version_id=str(payload.get("version_id") or ""),
        pretty_name=str(payload.get("pretty_name") or ""),
    )
    if info.system.lower() != "linux":
        raise SystemExit(
            json.dumps(
                {
                    "ok": False,
                    "error": "remote host is not Linux",
                    "recovery": "bootstrap is only supported against a Linux Moltbox host",
                    "host": info.as_dict(),
                },
                indent=2,
            )
        )
    return info


def require_remote_sudo(ssh_target: str) -> None:
    completed = _ssh(ssh_target, "sudo -n true")
    _require(
        completed,
        error_message="passwordless sudo is required to prepare machine-scoped storage roots",
        recovery_message="grant the operator account passwordless sudo for the bootstrap commands or pre-create the appliance directories",
    )


def probe_remote_sudo(ssh_target: str) -> dict[str, str] | None:
    completed = _ssh(ssh_target, "sudo -n true")
    if completed.returncode == 0:
        return None
    return {
        "type": "sudo",
        "message": "passwordless sudo is required to prepare machine-scoped storage roots",
        "recovery": "grant the operator account passwordless sudo for the bootstrap commands or pre-create the appliance directories",
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def _uses_app_auth(repo_name: str) -> bool:
    return repo_name in APP_AUTH_REPOSITORIES


def _github_repo_url(owner: str, repo_name: str) -> str:
    return f"https://github.com/{owner}/{repo_name}.git"


def _github_app_token(
    ssh_target: str,
    *,
    app_id: str,
    private_key_path: str,
    owner: str,
    repo_name: str,
) -> str:
    script = """
import base64
import json
import os
import subprocess
import sys
import time
import urllib.request

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

app_id = os.environ["MOLTBOX_GITHUB_APP_ID"]
private_key_path = os.path.expanduser(os.environ["MOLTBOX_GITHUB_APP_KEY_PATH"])
owner = os.environ["MOLTBOX_GITHUB_OWNER"]
repo_name = os.environ["MOLTBOX_GITHUB_REPO"]

if not os.path.exists(private_key_path):
    print(json.dumps({
        "ok": False,
        "message": "GitHub App private key was not found on the host",
        "private_key_path": private_key_path,
    }))
    sys.exit(1)

header = {"alg": "RS256", "typ": "JWT"}
now = int(time.time())
payload = {"iat": now - 60, "exp": now + 540, "iss": app_id}
signing_input = f"{b64url(json.dumps(header, separators=(',', ':')).encode())}.{b64url(json.dumps(payload, separators=(',', ':')).encode())}"
signed = subprocess.run(
    ["openssl", "dgst", "-binary", "-sha256", "-sign", private_key_path],
    input=signing_input.encode("utf-8"),
    capture_output=True,
    check=False,
)
if signed.returncode != 0:
    print(json.dumps({
        "ok": False,
        "message": "failed to sign GitHub App JWT with openssl",
        "stderr": signed.stderr.decode("utf-8", errors="replace").strip(),
    }))
    sys.exit(1)
jwt_token = f"{signing_input}.{b64url(signed.stdout)}"
headers = {
    "Authorization": f"Bearer {jwt_token}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "moltbox-bootstrap",
}
installation_request = urllib.request.Request(
    f"https://api.github.com/repos/{owner}/{repo_name}/installation",
    headers=headers,
)
with urllib.request.urlopen(installation_request) as response:
    installation = json.loads(response.read().decode("utf-8"))
token_request = urllib.request.Request(
    f"https://api.github.com/app/installations/{installation['id']}/access_tokens",
    headers=headers,
    method="POST",
)
with urllib.request.urlopen(token_request, data=b"{}") as response:
    payload = json.loads(response.read().decode("utf-8"))
print(json.dumps({"ok": True, "token": payload["token"], "installation_id": installation["id"]}))
""".strip()
    completed = _ssh_python(
        ssh_target,
        script,
        env={
            "MOLTBOX_GITHUB_APP_ID": app_id,
            "MOLTBOX_GITHUB_APP_KEY_PATH": private_key_path,
            "MOLTBOX_GITHUB_OWNER": owner,
            "MOLTBOX_GITHUB_REPO": repo_name,
        },
    )
    if completed.returncode != 0:
        raise SystemExit(
            json.dumps(
                {
                    "ok": False,
                    "error": f"failed to acquire a GitHub App installation token for '{repo_name}'",
                    "recovery": "verify the app id, private key path, and repository installation on the host",
                    "stdout": completed.stdout.strip(),
                    "stderr": completed.stderr.strip(),
                },
                indent=2,
            )
        )
    payload = json.loads(completed.stdout.strip())
    if not payload.get("ok"):
        raise SystemExit(json.dumps(payload, indent=2))
    return str(payload["token"])


def _git_with_token(
    ssh_target: str,
    *,
    token: str,
    repo_url: str,
    checkout_dir: str,
    ref: str | None,
    mode: str,
) -> subprocess.CompletedProcess[str]:
    script = """
import json
import os
import pathlib
import stat
import subprocess
import tempfile

repo_url = os.environ["MOLTBOX_REPO_URL"]
checkout_dir = os.environ["MOLTBOX_CHECKOUT_DIR"]
ref = os.environ.get("MOLTBOX_REPO_REF") or None
mode = os.environ["MOLTBOX_GIT_MODE"]
token = os.environ["MOLTBOX_GITHUB_INSTALLATION_TOKEN"]
env = os.environ.copy()
env["GIT_TERMINAL_PROMPT"] = "0"

with tempfile.TemporaryDirectory() as temp_dir:
    askpass_path = pathlib.Path(temp_dir) / "askpass.sh"
    askpass_path.write_text(
        "#!/bin/sh\\n"
        "case \\\"$1\\\" in\\n"
        "  *Username*) echo x-access-token ;;\\n"
        "  *Password*) echo \\\"$MOLTBOX_GITHUB_INSTALLATION_TOKEN\\\" ;;\\n"
        "  *) echo ;;\\n"
        "esac\\n",
        encoding="utf-8",
    )
    askpass_path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    env["GIT_ASKPASS"] = str(askpass_path)
    env["MOLTBOX_GITHUB_INSTALLATION_TOKEN"] = token

    if mode == "probe":
        completed = subprocess.run(
            ["git", "ls-remote", repo_url, "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    else:
        checkout = pathlib.Path(checkout_dir)
        checkout.parent.mkdir(parents=True, exist_ok=True)
        if (checkout / ".git").exists():
            if ref:
                completed = subprocess.run(
                    ["git", "-C", str(checkout), "fetch", "--all", "--tags", "--prune"],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=env,
                )
                if completed.returncode == 0:
                    completed = subprocess.run(
                        ["git", "-C", str(checkout), "checkout", ref],
                        capture_output=True,
                        text=True,
                        check=False,
                        env=env,
                    )
            else:
                completed = subprocess.run(
                    ["git", "-C", str(checkout), "pull", "--ff-only"],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=env,
                )
        else:
            completed = subprocess.run(
                ["git", "clone", repo_url, str(checkout)],
                capture_output=True,
                text=True,
                check=False,
                env=env,
            )
            if completed.returncode == 0 and ref:
                completed = subprocess.run(
                    ["git", "-C", str(checkout), "checkout", ref],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=env,
                )
    print(json.dumps({
        "ok": completed.returncode == 0,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }))
""".strip()
    return _ssh_python(
        ssh_target,
        script,
        env={
            "MOLTBOX_GITHUB_INSTALLATION_TOKEN": token,
            "MOLTBOX_REPO_URL": repo_url,
            "MOLTBOX_CHECKOUT_DIR": checkout_dir,
            "MOLTBOX_REPO_REF": ref or "",
            "MOLTBOX_GIT_MODE": mode,
        },
    )


def probe_remote_git_access(ssh_target: str, *, repo_name: str, repo_url: str) -> dict[str, str] | None:
    completed = _ssh(ssh_target, f"git ls-remote {shlex.quote(repo_url)} HEAD")
    if completed.returncode == 0:
        return None
    return {
        "type": "git_access",
        "repository": repo_name,
        "repository_url": repo_url,
        "message": f"remote host cannot read the required Git repository '{repo_name}'",
        "recovery": "configure host-side Git credentials for the private repositories before retrying bootstrap",
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def probe_remote_git_access_with_app(
    ssh_target: str,
    *,
    repo_name: str,
    owner: str,
    app_id: str,
    private_key_path: str,
) -> dict[str, str] | None:
    repo_url = _github_repo_url(owner, repo_name)
    try:
        token = _github_app_token(
            ssh_target,
            app_id=app_id,
            private_key_path=private_key_path,
            owner=owner,
            repo_name=repo_name,
        )
    except SystemExit as exc:
        return {
            "type": "github_app",
            "repository": repo_name,
            "message": f"remote host could not mint a GitHub App token for '{repo_name}'",
            "recovery": "verify the GitHub App private key path and repository installation on the host",
            "stdout": "",
            "stderr": str(exc),
        }
    completed = _git_with_token(
        ssh_target,
        token=token,
        repo_url=repo_url,
        checkout_dir="/tmp/moltbox-git-probe",
        ref=None,
        mode="probe",
    )
    if completed.returncode != 0:
        return {
            "type": "git_access",
            "repository": repo_name,
            "repository_url": repo_url,
            "message": f"remote host cannot read the required Git repository '{repo_name}' with the GitHub App token",
            "recovery": "verify that the GitHub App is installed on the repository with read access",
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
    payload = json.loads(completed.stdout.strip())
    if payload.get("ok"):
        return None
    return {
        "type": "git_access",
        "repository": repo_name,
        "repository_url": repo_url,
        "message": f"remote host cannot read the required Git repository '{repo_name}' with the GitHub App token",
        "recovery": "verify that the GitHub App is installed on the repository with read access",
        "stdout": str(payload.get("stdout") or ""),
        "stderr": str(payload.get("stderr") or ""),
    }


def prepare_remote_storage(ssh_target: str, *, state_root: str, logs_root: str) -> None:
    command = " && ".join(
        [
            f"sudo install -d -o $USER -g $USER {shlex.quote(state_root)}",
            f"sudo install -d -o $USER -g $USER {shlex.quote(logs_root)}",
            f"sudo install -d -o $USER -g $USER {shlex.quote(state_root + '/upstream')}",
            f"sudo install -d -o $USER -g $USER {shlex.quote(state_root + '/repos')}",
            f"sudo install -d -o $USER -g $USER {shlex.quote(state_root + '/runtime')}",
        ]
    )
    completed = _ssh(ssh_target, command)
    _require(
        completed,
        error_message="failed to prepare machine-scoped Moltbox storage roots",
        recovery_message="verify sudo access and filesystem permissions on the remote host",
    )


def sync_remote_checkout(
    ssh_target: str,
    *,
    repo_name: str,
    repo_url: str,
    checkout_dir: str,
    ref: str | None = None,
    github_owner: str,
    github_app_id: str,
    github_app_private_key_path: str,
) -> None:
    if _uses_app_auth(repo_name):
        token = _github_app_token(
            ssh_target,
            app_id=github_app_id,
            private_key_path=github_app_private_key_path,
            owner=github_owner,
            repo_name=repo_name,
        )
        completed = _git_with_token(
            ssh_target,
            token=token,
            repo_url=_github_repo_url(github_owner, repo_name),
            checkout_dir=checkout_dir,
            ref=ref,
            mode="sync",
        )
        if completed.returncode != 0:
            raise SystemExit(
                json.dumps(
                    {
                        "ok": False,
                        "error": f"failed to sync the remote checkout for {repo_name}",
                        "recovery": "verify the GitHub App installation and rerun bootstrap",
                        "stdout": completed.stdout.strip(),
                        "stderr": completed.stderr.strip(),
                    },
                    indent=2,
                )
            )
        payload = json.loads(completed.stdout.strip())
        if not payload.get("ok"):
            raise SystemExit(
                json.dumps(
                    {
                        "ok": False,
                        "error": f"failed to sync the remote checkout for {repo_name}",
                        "recovery": "verify the GitHub App installation and rerun bootstrap",
                        "stdout": payload.get("stdout", ""),
                        "stderr": payload.get("stderr", ""),
                    },
                    indent=2,
                )
            )
        return

    parent_dir = str(Path(checkout_dir).parent).replace("\\", "/")
    if ref:
        command = (
            f"mkdir -p {shlex.quote(parent_dir)}"
            f" && if [ -d {shlex.quote(checkout_dir + '/.git')} ]; then "
            f"git -C {shlex.quote(checkout_dir)} fetch --all --tags --prune"
            f" && git -C {shlex.quote(checkout_dir)} checkout {shlex.quote(ref)}; "
            f"else git clone {shlex.quote(repo_url)} {shlex.quote(checkout_dir)}"
            f" && git -C {shlex.quote(checkout_dir)} checkout {shlex.quote(ref)}; fi"
        )
    else:
        command = (
            f"mkdir -p {shlex.quote(parent_dir)}"
            f" && if [ -d {shlex.quote(checkout_dir + '/.git')} ]; then "
            f"git -C {shlex.quote(checkout_dir)} pull --ff-only; "
            f"else git clone {shlex.quote(repo_url)} {shlex.quote(checkout_dir)}; fi"
        )
    completed = _ssh(ssh_target, command)
    _require(
        completed,
        error_message=f"failed to sync the remote checkout for {repo_url}",
        recovery_message="verify remote Git credentials and rerun bootstrap",
    )


def deploy_gateway(
    ssh_target: str,
    *,
    gateway_checkout: str,
    state_root: str,
    logs_root: str,
    services_repo_url: str,
    runtime_repo_url: str,
    skills_repo_url: str,
    gateway_ref: str,
) -> dict[str, object]:
    runtime_root = f"{state_root}/runtime"
    pythonpath = ":".join(
        [
            f"{gateway_checkout}/cli/src",
            f"{gateway_checkout}/commands/src",
            f"{gateway_checkout}/services/src",
            f"{gateway_checkout}/runtime/src",
            f"{gateway_checkout}/docker/src",
            f"{gateway_checkout}/repos/src",
        ]
    )
    command = " && ".join(
        [
            f"export PYTHONPATH={shlex.quote(pythonpath)}",
            f"export MOLTBOX_REPO_ROOT={shlex.quote(gateway_checkout)}",
            f"export MOLTBOX_STATE_ROOT={shlex.quote(state_root)}",
            f"export MOLTBOX_LOGS_ROOT={shlex.quote(logs_root)}",
            f"export MOLTBOX_RUNTIME_ROOT={shlex.quote(runtime_root)}",
            f"export MOLTBOX_SERVICES_REPO_URL={shlex.quote(services_repo_url)}",
            f"export MOLTBOX_RUNTIME_REPO_URL={shlex.quote(runtime_repo_url)}",
            f"export MOLTBOX_SKILLS_REPO_URL={shlex.quote(skills_repo_url)}",
            "export MOLTBOX_INTERNAL_HOST=0.0.0.0",
            f"export MOLTBOX_INTERNAL_PORT={DEFAULT_GATEWAY_PORT}",
            "python3 -m moltbox_cli service deploy gateway --commit " + shlex.quote(gateway_ref),
        ]
    )
    completed = _ssh(ssh_target, command)
    raw = _require(
        completed,
        error_message="gateway deploy command failed on the remote host",
        recovery_message="inspect the remote deployment logs and rerun bootstrap after correcting the failure",
    )
    return json.loads(raw)


def probe_gateway(ssh_target: str, *, gateway_http_port: int) -> dict[str, object]:
    completed = _ssh(ssh_target, f"curl -fsS http://127.0.0.1:{gateway_http_port}/health")
    raw = _require(
        completed,
        error_message="gateway health probe failed after deployment",
        recovery_message="inspect the gateway container and logs on the remote host",
    )
    return json.loads(raw)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bootstrap the Moltbox gateway onto a remote Linux host.")
    parser.add_argument("--host", default="moltbox", help="SSH target for the Moltbox host")
    parser.add_argument("--state-root", default=DEFAULT_STATE_ROOT)
    parser.add_argument("--logs-root", default=DEFAULT_LOGS_ROOT)
    parser.add_argument("--gateway-http-port", type=int, default=DEFAULT_GATEWAY_HTTP_PORT)
    parser.add_argument("--gateway-repo-url", default="https://github.com/remram-ai/remram-gateway.git")
    parser.add_argument("--gateway-ref", default="main")
    parser.add_argument("--services-repo-url", default="https://github.com/remram-ai/moltbox-services.git")
    parser.add_argument("--runtime-repo-url", default="https://github.com/remram-ai/moltbox-runtime.git")
    parser.add_argument("--skills-repo-url", default="https://github.com/remram-ai/remram-skills.git")
    parser.add_argument("--github-owner", default=DEFAULT_GITHUB_OWNER)
    parser.add_argument("--github-app-id", default=DEFAULT_GITHUB_APP_ID)
    parser.add_argument("--github-app-private-key-path", default=DEFAULT_GITHUB_APP_KEY_PATH)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    host = detect_remote_host(args.host)
    blockers: list[dict[str, str]] = []
    for probe in (
        probe_remote_git_access(args.host, repo_name="remram-gateway", repo_url=args.gateway_repo_url),
        probe_remote_git_access_with_app(
            args.host,
            repo_name="moltbox-services",
            owner=args.github_owner,
            app_id=args.github_app_id,
            private_key_path=args.github_app_private_key_path,
        ),
        probe_remote_git_access_with_app(
            args.host,
            repo_name="moltbox-runtime",
            owner=args.github_owner,
            app_id=args.github_app_id,
            private_key_path=args.github_app_private_key_path,
        ),
        probe_remote_git_access(args.host, repo_name="remram-skills", repo_url=args.skills_repo_url),
        probe_remote_sudo(args.host),
    ):
        if probe is not None:
            blockers.append(probe)
    if blockers:
        raise SystemExit(
            json.dumps(
                {
                    "ok": False,
                    "host": host.as_dict(),
                    "state_root": args.state_root,
                    "logs_root": args.logs_root,
                    "blockers": blockers,
                },
                indent=2,
            )
        )

    prepare_remote_storage(args.host, state_root=args.state_root, logs_root=args.logs_root)

    gateway_checkout = f"{args.state_root}/upstream/remram-gateway"
    services_checkout = f"{args.state_root}/upstream/moltbox-services"
    runtime_checkout = f"{args.state_root}/upstream/moltbox-runtime"
    skills_checkout = f"{args.state_root}/upstream/remram-skills"

    sync_remote_checkout(
        args.host,
        repo_name="remram-gateway",
        repo_url=args.gateway_repo_url,
        checkout_dir=gateway_checkout,
        ref=args.gateway_ref,
        github_owner=args.github_owner,
        github_app_id=args.github_app_id,
        github_app_private_key_path=args.github_app_private_key_path,
    )
    sync_remote_checkout(
        args.host,
        repo_name="moltbox-services",
        repo_url=args.services_repo_url,
        checkout_dir=services_checkout,
        ref=None,
        github_owner=args.github_owner,
        github_app_id=args.github_app_id,
        github_app_private_key_path=args.github_app_private_key_path,
    )
    sync_remote_checkout(
        args.host,
        repo_name="moltbox-runtime",
        repo_url=args.runtime_repo_url,
        checkout_dir=runtime_checkout,
        ref=None,
        github_owner=args.github_owner,
        github_app_id=args.github_app_id,
        github_app_private_key_path=args.github_app_private_key_path,
    )
    sync_remote_checkout(
        args.host,
        repo_name="remram-skills",
        repo_url=args.skills_repo_url,
        checkout_dir=skills_checkout,
        ref=None,
        github_owner=args.github_owner,
        github_app_id=args.github_app_id,
        github_app_private_key_path=args.github_app_private_key_path,
    )

    deploy_payload = deploy_gateway(
        args.host,
        gateway_checkout=gateway_checkout,
        state_root=args.state_root,
        logs_root=args.logs_root,
        services_repo_url=services_checkout,
        runtime_repo_url=runtime_checkout,
        skills_repo_url=skills_checkout,
        gateway_ref=args.gateway_ref,
    )
    health_payload = probe_gateway(args.host, gateway_http_port=args.gateway_http_port)
    print(
        json.dumps(
            {
                "ok": True,
                "host": host.as_dict(),
                "state_root": args.state_root,
                "logs_root": args.logs_root,
                "deploy": deploy_payload,
                "health": health_payload,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
