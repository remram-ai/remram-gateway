from __future__ import annotations

import os
import socket

from .config import AppConfig


def _public_hostname() -> str | None:
    configured = os.environ.get("MOLTBOX_PUBLIC_HOSTNAME", "").strip()
    if configured:
        return configured
    detected = socket.gethostname().strip()
    if not detected:
        return None
    return detected.split(".", 1)[0]


def _route_block(hosts: list[str], upstream: str) -> str:
    host_list = ", ".join(hosts)
    return "\n".join(
        [
            f"{host_list} {{",
            "  tls internal",
            f"  reverse_proxy {upstream}",
            "}",
        ]
    )


def build_ssl_render_context(config: AppConfig) -> dict[str, str]:
    hostname = _public_hostname()
    route_specs = [
        (["moltbox-cli"], f"http://host.docker.internal:{config.internal_port}"),
        (["moltbox-dev"], "http://host.docker.internal:18790"),
        (["moltbox-test"], "http://host.docker.internal:28789"),
        (["moltbox-prod"], "http://host.docker.internal:38789"),
    ]
    if hostname:
        route_specs[1][0].append(f"dev.{hostname}")
        route_specs[2][0].append(f"test.{hostname}")
        route_specs[3][0].append(f"prod.{hostname}")
    managed_routes = "\n\n".join(_route_block(hosts, upstream) for hosts, upstream in route_specs) + "\n"
    return {"managed_routes": managed_routes}
