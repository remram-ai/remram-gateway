from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path
from typing import Callable


CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _env_value(environ: dict[str, str], *names: str) -> str | None:
    for name in names:
        value = environ.get(name)
        if value:
            stripped = value.strip()
            if stripped:
                return stripped
    return None


def _inside_container() -> bool:
    return Path("/.dockerenv").exists()


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _detect_local_host_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("1.1.1.1", 80))
            host_ip = sock.getsockname()[0]
    except OSError:
        return None
    if not host_ip or host_ip.startswith("127."):
        return None
    return host_ip


def _detect_local_hostname() -> str | None:
    host_name = socket.gethostname().strip()
    if not host_name:
        return None
    return host_name.split(".", 1)[0]


def _helper_image(environ: dict[str, str]) -> str:
    return _env_value(environ, "MOLTBOX_RUNTIME_HELPER_IMAGE") or "alpine"


def _detect_host_identity_via_helper(
    command_runner: CommandRunner,
    environ: dict[str, str],
) -> tuple[str | None, str | None]:
    helper_script = """
host_name="$(hostname -s 2>/dev/null || hostname 2>/dev/null || true)"
host_ip=""
set -- $(ip route get 1.1.1.1 2>/dev/null)
while [ "$#" -gt 0 ]; do
  if [ "$1" = "src" ] && [ "$#" -ge 2 ]; then
    host_ip="$2"
    break
  fi
  shift
done
printf 'hostname=%s\\nip=%s\\n' "$host_name" "$host_ip"
""".strip()
    completed = command_runner(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "host",
            "--uts",
            "host",
            _helper_image(environ),
            "sh",
            "-lc",
            helper_script,
        ]
    )
    if completed.returncode != 0:
        return None, None
    host_ip: str | None = None
    host_name: str | None = None
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if line.startswith("hostname="):
            value = line.partition("=")[2].strip()
            if value:
                host_name = value.split(".", 1)[0]
        if line.startswith("ip="):
            value = line.partition("=")[2].strip()
            if value and not value.startswith("127."):
                host_ip = value
    return host_ip, host_name


def _resolve_host_identity(
    command_runner: CommandRunner,
    environ: dict[str, str],
) -> tuple[str | None, str | None]:
    host_ip = _env_value(environ, "MOLTBOX_PUBLIC_HOST_IP")
    host_name = _env_value(environ, "MOLTBOX_PUBLIC_HOSTNAME")
    if host_ip and host_name:
        return host_ip, host_name

    if _inside_container():
        detected_ip, detected_name = _detect_host_identity_via_helper(command_runner, environ)
    else:
        detected_ip = _detect_local_host_ip()
        detected_name = _detect_local_hostname()

    return host_ip or detected_ip, host_name or detected_name


def _allowed_origins(host_ip: str, host_name: str | None, gateway_port: str, environ: dict[str, str]) -> list[str]:
    values = [
        "http://127.0.0.1",
        "https://127.0.0.1",
        f"http://127.0.0.1:{gateway_port}",
        f"https://127.0.0.1:{gateway_port}",
        "http://localhost",
        "https://localhost",
        f"http://localhost:{gateway_port}",
        f"https://localhost:{gateway_port}",
        f"http://{host_ip}",
        f"https://{host_ip}",
        f"http://{host_ip}:{gateway_port}",
        f"https://{host_ip}:{gateway_port}",
    ]
    if host_name:
        values.extend(
            [
                f"http://{host_name}",
                f"https://{host_name}",
                f"http://{host_name}:{gateway_port}",
                f"https://{host_name}:{gateway_port}",
            ]
        )
    extra_origins = _env_value(environ, "MOLTBOX_OPENCLAW_ALLOWED_ORIGINS_EXTRA")
    if extra_origins:
        values.extend(item.strip() for item in extra_origins.split(",") if item.strip())

    merged: list[str] = []
    for value in values:
        if value and value not in merged:
            merged.append(value)
    return merged


def _merge_existing_payload(existing: object, rendered: object) -> object:
    if not isinstance(existing, dict) or not isinstance(rendered, dict):
        return rendered
    merged = dict(existing)
    for key, value in rendered.items():
        existing_value = merged.get(key)
        if isinstance(existing_value, dict) and isinstance(value, dict):
            merged[key] = _merge_existing_payload(existing_value, value)
            continue
        merged[key] = value
    return merged


def seed_runtime_root_config(
    runtime_root_dir: Path,
    gateway_port: str,
    *,
    command_runner: CommandRunner | None = None,
    environ: dict[str, str] | None = None,
    existing_runtime_root_dir: Path | None = None,
) -> None:
    openclaw_config_path = runtime_root_dir / "openclaw.json"
    if not openclaw_config_path.exists():
        return

    env = dict(os.environ if environ is None else environ)
    runner = command_runner or _run_command
    host_ip, host_name = _resolve_host_identity(runner, env)
    if not host_ip:
        raise RuntimeError(
            "unable to determine the MoltBox host LAN IP for runtime control UI allowedOrigins; "
            "set MOLTBOX_PUBLIC_HOST_IP and rerun the command"
        )
    if not gateway_port.strip():
        raise RuntimeError("runtime gateway port is required to seed OpenClaw control UI origins")

    payload = json.loads(openclaw_config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        payload = {}
    existing_allowed_origins: list[str] = []
    if existing_runtime_root_dir is not None:
        existing_config_path = existing_runtime_root_dir / "openclaw.json"
        if existing_config_path.exists():
            existing_payload = json.loads(existing_config_path.read_text(encoding="utf-8"))
            if isinstance(existing_payload, dict):
                payload = _merge_existing_payload(existing_payload, payload)
                existing_allowed_origins_raw = (
                    ((existing_payload.get("gateway") or {}).get("controlUi") or {}).get("allowedOrigins")
                )
                if isinstance(existing_allowed_origins_raw, list):
                    existing_allowed_origins = [value for value in existing_allowed_origins_raw if isinstance(value, str)]
    gateway = payload.setdefault("gateway", {})
    if not isinstance(gateway, dict):
        gateway = {}
        payload["gateway"] = gateway
    gateway["mode"] = "local"
    control_ui = gateway.setdefault("controlUi", {})
    if not isinstance(control_ui, dict):
        control_ui = {}
        gateway["controlUi"] = control_ui

    existing = control_ui.get("allowedOrigins")
    merged = []
    if isinstance(existing, list):
        for value in existing:
            if isinstance(value, str) and value and value not in merged:
                merged.append(value)
    for value in existing_allowed_origins:
        if value and value not in merged:
            merged.append(value)
    for value in _allowed_origins(host_ip, host_name, gateway_port, env):
        if value not in merged:
            merged.append(value)
    control_ui["allowedOrigins"] = merged

    openclaw_config_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
