# Moltbox Diagnostics Bundle

`scripts/99-diagnostics.sh` is the official Moltbox debug bundle generator. Run it before troubleshooting so a bug report includes the current host, container, runtime, and model state.

It is not the rollback snapshot mechanism.

For rollback points before mutating host or runtime state, use the host snapshot tool instead:

```bash
sudo /usr/local/bin/moltbox-snapshot create
sudo /usr/local/bin/moltbox-snapshot list
```

## What It Collects

- `system/`: `uname -a`, `/etc/os-release`, `uptime`, `df -h`, `free -h`, `ip addr`, `ss -tulpn`, and `ps aux`
- `docker/`: `docker ps -a`, `docker images`, `docker volume ls`, `docker network ls`, `docker info`, plus `docker inspect` and `docker top` for the runtime-configured OpenClaw, Ollama, and OpenSearch container names
- `logs/`: `docker logs` for the runtime-configured OpenClaw, Ollama, and OpenSearch containers
- `runtime/`: a redacted copy of `~/.openclaw`
- `config/`: a redacted copy of `~/git/remram-gateway/moltbox/config` when present, otherwise the current repository `moltbox/config`
- `models/`: `openclaw doctor`, `openclaw models list`, and the Ollama tags API response
- `network/`: container `docker inspect` output for the three Moltbox services

Text output is redacted before being written into the bundle. Values assigned to `*_KEY`, `*_TOKEN`, `*_SECRET`, and `PASSWORD` are replaced with `REDACTED`.

## How To Run It

From the Moltbox repository on the host:

```bash
cd ~/git/remram-gateway/moltbox
bash ./scripts/99-diagnostics.sh
```

The script creates a timestamped archive at:

```text
/tmp/moltbox-debug-YYYYMMDD-HHMMSS.tar.gz
```

It is safe to run multiple times. Missing containers are recorded in the bundle instead of aborting the collection.

## How To Download The Archive

At the end of the run, the script prints the archive path and an `scp` command you can run from your local machine, for example:

```bash
scp jpekovitch@moltbox-prime:/tmp/moltbox-debug-20260307-160100.tar.gz .
```

If host name resolution does not work from your workstation, replace the host name with the Moltbox IP address.

## How To Attach It To Bug Reports

Attach the generated `.tar.gz` file to the issue, ticket, or troubleshooting thread. Include:

- what you were trying to do
- when the problem happened
- the exact command or UI action that failed
- any relevant error text shown to the operator

The bundle is intended to be the standard first artifact for Moltbox troubleshooting.
