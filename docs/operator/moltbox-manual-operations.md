# Moltbox Manual Operations

This document is a practical operator note for the current Moltbox host. It is based on the checked-in `main` branch plus live host verification on `moltbox-prime` on March 10, 2026.

For command syntax and verb coverage, see [moltbox-cli-reference.md](/D:/Development/RemRam/remram-gateway/docs/operator/moltbox-cli-reference.md).

## Host Identity

- SSH alias: `moltbox`
- Hostname: `moltbox-prime`
- Primary LAN IP: `192.168.1.189`

Connect with:

```powershell
ssh moltbox
```

## Current Running Containers

The host is currently running these containers:

- `moltbox-caddy`
  - HTTPS ingress and reverse proxy
  - Public ports: `80`, `443`
  - Managed by the `moltbox host ssl ...` CLI target

- `moltbox-tools`
  - Control-plane tools service
  - Direct host port: `7474`
  - Serves `/health` and `/mcp`

- `moltbox-ollama`
  - Shared model service
  - Internal port: `11434`

- `moltbox-opensearch`
  - Shared index/search service
  - Internal port: `9200`

- `moltbox-openclaw`
  - Legacy live runtime
  - Direct host port: `18789`

- `openclaw-dev`
  - Managed dev runtime
  - Direct host port: `18790`

- `openclaw-test`
  - Managed test runtime
  - Direct host port: `28789`

There is no running managed `openclaw-prod` container at this time.

## Public Endpoints

The current ingress routing is:

- `https://moltbox-cli` -> `host.docker.internal:7474`
- `https://moltbox-dev` -> `host.docker.internal:18790`
- `https://dev.moltbox-prime` -> `host.docker.internal:18790`
- `https://moltbox-test` -> `host.docker.internal:28789`
- `https://test.moltbox-prime` -> `host.docker.internal:28789`
- `https://moltbox-prod` -> `host.docker.internal:38789`
- `https://prod.moltbox-prime` -> `host.docker.internal:38789`

Useful URLs:

- MCP health: `https://moltbox-cli/health`
- MCP endpoint: `https://moltbox-cli/mcp`
- Dev runtime UI: `https://dev.moltbox-prime/`
- Test runtime UI: `https://test.moltbox-prime/`

Operational note:

- `prod.moltbox-prime` is configured in ingress, but no managed prod runtime is deployed yet.
- The older `moltbox-openclaw` container on `18789` still exists separately from the managed environments.

## Port Map

Host-published ports:

- `80` -> `moltbox-caddy`
- `443` -> `moltbox-caddy`
- `7474` -> `moltbox-tools`
- `18789` -> `moltbox-openclaw`
- `18790` -> `openclaw-dev`
- `28789` -> `openclaw-test`

Internal-only ports:

- `11434` -> `moltbox-ollama`
- `9200` -> `moltbox-opensearch`

Reserved by current routing:

- `38789` -> managed prod runtime if deployed later

## Docker Topology

Primary shared network:

- `moltbox_moltbox_internal`

Current containers attached to that network:

- `moltbox-ollama` -> `172.18.0.2`
- `moltbox-opensearch` -> `172.18.0.3`
- `openclaw-test` -> `172.18.0.4`
- `moltbox-openclaw` -> `172.18.0.5`
- `openclaw-dev` -> `172.18.0.6`

The tools container is on its own compose network:

- `moltbox-tools_default`

Important DNS names on the shared network:

- `ollama`
- `opensearch`

Important caution:

- More than one runtime currently advertises the alias `openclaw`.
- Use ingress hostnames or the explicit container names instead of relying on `openclaw` as a stable DNS target.

Other networks currently visible on the host:

- `bridge`
- `config_moltbox_internal`
- `host`
- `moltbox_default`
- `none`
- `remram-test_default`

Those appear to be default or leftover networks rather than the intended steady-state topology.

## On-Disk Layout

Main runtime root:

```text
/home/jpekovitch/Moltbox
```

Current managed runtime directories:

```text
/home/jpekovitch/Moltbox/openclaw/dev
/home/jpekovitch/Moltbox/openclaw/test
```

Control-plane state root:

```text
/home/jpekovitch/.remram
```

Important state/config paths:

```text
/home/jpekovitch/.remram/tools/config.yaml
/home/jpekovitch/.remram/tools/control-plane-policy.yaml
/home/jpekovitch/.remram/deploy/rendered
/home/jpekovitch/.remram/shared
/home/jpekovitch/.remram/snapshots
```

## Important Container Mounts

`moltbox-tools` mounts:

- `/home/jpekovitch/.remram`
- `/home/jpekovitch/Moltbox`
- `/var/run/docker.sock`

`openclaw-dev` mounts:

- `/home/jpekovitch/Moltbox/openclaw/dev` -> `/home/node/.openclaw`
- rendered config under `.remram/deploy/rendered/.../config/openclaw` -> `/app/config/openclaw`

`openclaw-test` mounts:

- `/home/jpekovitch/Moltbox/openclaw/test` -> `/home/node/.openclaw`
- rendered config under `.remram/deploy/rendered/.../config/openclaw` -> `/app/config/openclaw`

`moltbox-caddy` mounts:

- rendered `Caddyfile` from `.remram/deploy/rendered/shared/ssl/config/ssl/Caddyfile`
- persistent data under `.remram/shared/ssl/data`
- persistent config under `.remram/shared/ssl/config`

## Manual Operation

Preferred interface is the local CLI on the host:

```bash
moltbox tools health
moltbox tools status
moltbox tools inspect

moltbox host ssl status
moltbox host ssl inspect
moltbox host ssl logs

moltbox host ollama status
moltbox host opensearch status

moltbox runtime dev status
moltbox runtime dev logs
moltbox runtime dev deploy
moltbox runtime dev rollback

moltbox runtime test status
moltbox runtime test logs
moltbox runtime test deploy
```

If the CLI is unavailable, use Docker directly:

```bash
docker ps
docker logs -f moltbox-tools
docker logs -f moltbox-caddy
docker logs -f openclaw-dev
docker logs -f openclaw-test
docker inspect openclaw-dev
docker inspect moltbox-tools
docker network inspect moltbox_moltbox_internal
```

## MCP Access Policy

Policy is enforced in MCP, not in the local host CLI.

Policy file:

```text
/home/jpekovitch/.remram/tools/control-plane-policy.yaml
```

Current policy:

- `dev`
  - `deploy`, `rollback`, `status`, `inspect`, `logs`, `start`, `stop`, `restart`
- `test`
  - `deploy`, `status`, `logs`, `inspect`
- `prod`
  - `deploy`, `inspect`, `logs`

Host CLI remains unrestricted for operators with shell access.

## Known Operational Oddities

- The canonical CLI target is `ssl`, but the running ingress container is still named `moltbox-caddy`.
- The legacy runtime `moltbox-openclaw` is still running on `18789`.
- Managed `dev` and `test` runtimes are live.
- Managed `prod` routing exists in ingress, but managed prod is not running.
- Shared services are expected to be reached by Docker DNS:
  - `http://ollama:11434`
  - `http://opensearch:9200`
