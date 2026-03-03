# Moltbox - Implementation Guide

## Title Page

*   Version: 0.1 (Draft)
*   Last Edited: March 2, 2026
*   Author: Jason (with Codex drafting support)

### Overview

This guide delivers a practical, infrastructure-first deployment manual for a Moltbox MVP appliance using Docker Compose, LAN-only access controls, OpenClaw gateway HTTP, Ollama local routing, Together AI escalation, OpenSearch single-node infrastructure, and UFW-based hardening.

### Revision Notes

*   Initial structured draft with implementation-first chapter layout.
*   Governance and drafting guardrails isolated into Appendix A.

## Table of Contents

1.  Chapter 1 - Scope, Constraints, and Baseline Architecture
2.  Chapter 2 - Host Baseline and Container Stack
3.  Chapter 3 - OpenClaw Docker Onboarding and Gateway Baseline
4.  Chapter 4 - Ollama Local Routing Model Setup
5.  Chapter 5 - OpenSearch Single-Node Deployment (Internal Only)
6.  Chapter 6 - Together AI Escalation Provider Configuration
7.  Chapter 7 - Signal Polling Channel Configuration
8.  Chapter 8 - LAN-Only Exposure and UFW Hardening
9.  Chapter 9 - Validation Checklist and Runbook
10.  Appendix A - Authoring Guardrails and AI Drafting Rules

## Chapter 1 - Scope, Constraints, and Baseline Architecture

This guide defines a production-quality, infrastructure-first Moltbox MVP: an OpenClaw appliance running on local hardware with LAN-only exposure, local routing via Ollama, controlled cloud escalation via Together AI, and supporting container infrastructure for experimentation. Moltbox does not replace OpenClaw Docker deployment; it constrains and hardens the documented Docker onboarding flow for appliance use.

### 1.1 MVP Objective

The MVP must deliver a stable appliance that:

*   Runs OpenClaw in Docker Compose.
*   Uses OpenClaw documented Docker onboarding as the canonical deployment baseline (`docker-setup.sh` + onboarding wizard).
*   Uses Ollama with `qwen3:8b` as the local routing model.
*   Supports escalation to a configurable Together AI model.
*   Runs OpenSearch in single-node container mode for infrastructure readiness.
*   OpenSearch is internal-only readiness in MVP and is not wired to Cortex or indexing pipelines in this phase.
*   Exposes only OpenClaw gateway port and SSH to the LAN.
*   Keeps OpenSearch and Ollama internal to Docker networking.
*   Applies minimal hardening with UFW and no public exposure.

### 1.2 Source-of-Truth Inputs

This guide is grounded in:

*   `docs/overview/remram-project-charter.md`
*   `docs/overview/remram-system-architecture.md`
*   `docs/ref/openclaw-reference.md`
*   `docs/design/edge/remram-edge(moltbox)-design.md`

And your confirmed implementation decisions for this MVP.

### 1.3 Fixed Decisions for This Guide

The following are locked for MVP and are not treated as optional variants:

*   Deployment model: Docker Compose for OpenClaw and supporting services.
*   Deployment philosophy: appliance profile on top of OpenClaw documented Docker flow; no replacement of upstream deployment mechanics.
*   Access posture: LAN-only; no public ingress, no port forwarding, no tailnet/VPN requirement.
*   Signal: included in polling mode, configured in a way compatible with OpenClaw identity/session model.
*   Firewall: UFW with straightforward LAN-allow posture for required services only.
*   Exposed services: OpenClaw gateway port and SSH only.
*   OpenSearch security model: single-node, no additional security hardening beyond network isolation.
*   Escalation provider: Together AI examples are canonical.
*   Local routing model: `qwen3:8b` via Ollama.
*   Escalation tiers: single configurable Together AI reasoning tier only (no deep-thinking tier).
*   Scope exclusion: no remram-cortex integration, no custom orchestration hook wiring.
*   Versioning posture: stable tags with `.env` overrides; avoid strict minor pinning unless docs require it; do not use `:latest` image tags in this guide.
*   Gateway protocol posture: HTTP for MVP, with token authentication and LAN-only exposure.

### 1.4 Non-Goals (Explicitly Out of Scope)

The MVP does not include:

*   Public-facing deployment patterns.
*   Reverse proxy, VPN hosting, or internet-exposed ingress.
*   Custom OpenClaw hook pipelines (`before_model_resolve`, `before_prompt_build`, `tool_result_persist`, `agent_end`).
*   remram-cortex service integration.
*   Enterprise OpenSearch security, clustering, or multi-node design.
*   Deep-thinking model tier policy.

### 1.5 Implementation Shape

The appliance will be implemented as:

*   One host OS (Ubuntu Server LTS) with NVIDIA-capable runtime.
*   One Docker Compose stack for OpenClaw, OpenSearch, and Ollama.
*   One internal Docker network for non-public service communication.
*   One UFW ruleset limiting inbound exposure to LAN-required surfaces.
*   One script-driven bring-up flow using `./scripts/XX-*.sh`.

### 1.6 Script-First Authoring Rule

All long operational procedures in later chapters are externalized to scripts and referenced by filename.

Script references will state:

*   Where the script runs (`host` or `docker exec` target).
*   Who runs it (normal user with `sudo`, or root when unavoidable).
*   What state it changes (host, container, or both).
*   Whether re-running is safe (idempotency expectation).

Only short validation commands are kept inline in the guide.

### 1.7 Chapter Flow for This Guide

The remainder of the guide will be authored chapter-by-chapter in this order:

1.  Hardware and OS preparation.
2.  Container runtime and Compose baseline.
3.  OpenClaw Docker onboarding and gateway baseline configuration.
4.  Ollama local routing model setup.
5.  OpenSearch single-node deployment (internal only).
6.  Together AI escalation provider configuration.
7.  Signal polling channel configuration.
8.  LAN-only exposure and UFW hardening.
9.  Validation checklist and runbook.

## Chapter 2 - Host Baseline and Container Stack

This chapter defines the reproducible host and container baseline for Moltbox MVP. It prepares the machine for Docker Compose deployment with GPU support and internal service networking.

### 2.0 Upstream Baseline and Moltbox Delta

OpenClaw provides multiple getting-started paths. For Moltbox, the Docker onboarding path is canonical.

For Moltbox MVP, we reuse that operational model and adapt only the deployment substrate:

*   Upstream baseline: `docker-setup.sh -> onboard -> gateway status/probe -> dashboard`.
*   Moltbox delta: Docker Compose appliance deployment instead of host-native install/daemon setup.
*   Host installer flow is non-canonical for Moltbox MVP.
*   Result: same OpenClaw runtime behavior, but with containerized reproducibility and internal service isolation.

### 2.1 Host Baseline (Ubuntu LTS Appliance)

Prepare a clean Ubuntu Server LTS host with:

*   A non-root administrative user with `sudo`.
*   SSH enabled for LAN management.
*   NVIDIA driver installed and validated on the host.
*   Hostname and timezone set for operational logs.

Run:

*   `./scripts/10-host-baseline.sh`

Script contract:

*   Runs on: host
*   Executed by: admin user with `sudo`
*   Modifies: host OS packages and baseline configuration
*   Re-run safety: expected idempotent for package/bootstrap operations

### 2.2 Container Runtime Prerequisites

Install and validate:

*   Docker Engine
*   Docker Compose plugin
*   NVIDIA Container Toolkit for GPU pass-through to model containers

Run:

*   `./scripts/20-install-docker.sh`
*   `./scripts/21-install-nvidia-container-toolkit.sh`

Script contracts:

*   Runs on: host
*   Executed by: admin user with `sudo`
*   Modifies: host container runtime and NVIDIA runtime integration
*   Re-run safety: expected idempotent with version checks and conditional install logic

Short validation commands:

```
docker --version
docker compose version
nvidia-smi
```

### 2.3 Project Runtime Layout

Initialize a predictable appliance directory structure for Compose-managed infrastructure.

Run:

*   `./scripts/30-init-runtime-layout.sh`

Required layout:

*   `./deploy/compose/` for Compose manifests
*   `./deploy/env/` for environment files
*   `./data/` for persistent service state
*   `./logs/` for operational logs
*   `./scripts/` for reproducible bring-up workflows

Script contract:

*   Runs on: host
*   Executed by: normal project user
*   Modifies: repository-local directories and permissions
*   Re-run safety: expected idempotent directory creation and ownership reconciliation

### 2.4 Container Image Strategy

Use stable, overrideable image references through environment variables in `./deploy/env/moltbox.env`.

Baseline images:

*   OpenClaw: official container image (stable tag via environment variable)
*   OpenSearch: official single-node image (stable major tag)
*   Ollama: official runtime image (stable tag)

Initialization:

*   `./scripts/31-init-env.sh`

Script contract:

*   Runs on: host
*   Executed by: normal project user
*   Modifies: environment template files only
*   Re-run safety: safe when implemented as merge/update of known keys

### 2.5 Compose Networking and Exposure Baseline

The Compose stack must enforce the MVP exposure posture:

*   Internal Docker network for service-to-service traffic.
*   OpenSearch not published to host ports.
*   Ollama not published to host ports.
*   Only OpenClaw published for LAN HTTP access.
*   SSH remains host-managed, not container-managed.

Compose validation:

*   `./scripts/40-validate-compose.sh`

Script contract:

*   Runs on: host
*   Executed by: normal project user
*   Modifies: none (read/validate only)
*   Re-run safety: fully safe (non-mutating)

### 2.6 Stack Lifecycle Controls

Use script-driven lifecycle controls for repeatable operations.

Run:

*   `./scripts/50-stack-up.sh`
*   `./scripts/51-stack-status.sh`
*   `./scripts/52-stack-down.sh`

Script contracts:

*   Runs on: host
*   Executed by: normal project user (Docker group or `sudo` as configured)
*   Modifies: container runtime state; persistent data volumes remain intact
*   Re-run safety: `up` and `status` should be re-runnable; `down` must not destroy volumes unless explicitly requested

Short validation commands:

```
docker compose -f deploy/compose/moltbox.yml ps
docker network ls
```

## Chapter 3 - OpenClaw Docker Onboarding and Gateway Baseline

This chapter defines the canonical OpenClaw Docker onboarding baseline for Moltbox MVP. It uses the official `docker-setup.sh` and onboarding flow, then applies constrained appliance settings through documented CLI/config methods.

### 3.1 Canonical Baseline (Upstream Docker Flow)

Official Docker quick-start baseline:

*   Run from OpenClaw repo root: `./docker-setup.sh`
*   Let onboarding configure gateway and auth interactively or via documented non-interactive wizard flags
*   Start gateway via Docker Compose
*   Use generated token for Control UI access

Moltbox policy is an overlay on this baseline, not a replacement.

### 3.2 Moltbox Appliance Constraints Applied to Docker Baseline

After baseline bring-up, enforce:

*   `gateway.port`
*   documented bind modes use `lan` or `loopback`; Moltbox enforces `lan`
*   `gateway.bind = lan`
*   `gateway.auth.mode = token`
*   LAN-only exposure and UFW controls from Chapter 8
*   No reverse proxy, no public ingress

Do not edit raw JSON directly unless an upstream doc explicitly requires it.

### 3.3 Scripted Baseline Bring-Up

Run:

*   `./scripts/60-openclaw-docker-setup.sh`
*   `./scripts/61-openclaw-onboard.sh`
*   `./scripts/62-openclaw-policy-enforce.sh`

Script contracts:

*   Runs on: host
*   Executed by: normal project user
*   Modifies: Docker image/container state and OpenClaw documented config paths through CLI commands
*   Re-run safety: expected re-runnable with convergent settings

### 3.4 Documented Configuration Methods Only

Use only documented command paths such as:

*   `docker compose run --rm openclaw-cli onboard ...`
*   `docker compose run --rm openclaw-cli config set gateway.mode local`
*   `docker compose run --rm openclaw-cli config set gateway.bind lan`

Token auth is required. If onboarding did not set token mode, enforce with documented onboarding/config workflow before proceeding.

### 3.5 HTTP Posture for MVP

MVP uses OpenClaw gateway HTTP only in this chapter.

*   Use the official Docker flow as documented.
*   Do not add reverse proxies or sidecars.
*   Do not add native TLS configuration in MVP.
*   Keep token authentication enabled with LAN-only exposure and UFW enforcement.

### 3.6 Baseline Validation Checklist

Short validation commands:

```
docker compose -f deploy/compose/moltbox.yml ps
docker compose run --rm openclaw-cli gateway status
docker compose -f deploy/compose/moltbox.yml run --rm openclaw-cli config get gateway.bind
docker compose -f deploy/compose/moltbox.yml run --rm openclaw-cli config get gateway.auth.mode
docker compose -f deploy/compose/moltbox.yml run --rm openclaw-cli config get gateway.mode
```

OpenClaw endpoint validation:

```
curl http://127.0.0.1:18789/healthz
curl http://127.0.0.1:18789/readyz
```

Pass Criteria:

*   `/healthz` returns HTTP `200` with live status response.
*   `/readyz` returns HTTP `200` with ready status response.
*   `gateway.bind` resolves to `lan`.
*   Token authentication is enabled and required by gateway clients.
*   Control UI is reachable on LAN via the published gateway port.

Fail Means:

*   Any command returns a non-zero exit code.
*   `/healthz` or `/readyz` does not return HTTP `200`.
*   `gateway.bind` is not `lan`.
*   `gateway.auth.mode` is not `token`.
*   `gateway.mode` is not set to the required local mode.

### 3.7 Confirmed Baseline Endpoint (MVP)

When this chapter is complete, the confirmed baseline endpoint is:

*   `http://<MOLTBOX_LAN_HOST>:18789`

This endpoint is LAN-only and token-authenticated under the official Docker onboarding model.

## Chapter 4 - Ollama Local Routing Model Setup

This chapter configures Ollama as the local routing-model runtime for Moltbox MVP using the official `ollama/ollama` container image. It follows the OpenClaw Docker baseline and applies appliance constraints: internal-only networking, GPU acceleration, and deterministic model pre-pull.

### 4.1 Scope and Boundary

This chapter covers:

*   Ollama container runtime setup in the existing Docker appliance stack
*   GPU enablement for Ollama via NVIDIA runtime
*   Internal-only service exposure (no host-published Ollama API port)
*   Pre-pull and verification of `qwen3:8b`
*   Basic runtime validation for OpenClaw-to-Ollama connectivity

This chapter does not cover:

*   Escalation providers
*   Routing policy or tier logic
*   Cloud model configuration

### 4.2 Required Runtime Decisions (Locked)

The following are mandatory for MVP:

*   Image: official `ollama/ollama`
*   GPU: enabled through Docker NVIDIA runtime/toolkit
*   Network exposure: internal Docker network only
*   Host ports: do not publish `11434`
*   Local routing model: `qwen3:8b`
*   Model provisioning: pre-pull during bring-up

### 4.3 Compose Service Posture for Ollama

Ollama is added as an internal service in the appliance compose stack.

Service constraints:

*   Joins the same internal network used by OpenClaw
*   Uses persistent volume for model storage
*   Uses GPU runtime configuration supported by Docker + NVIDIA toolkit
*   Does not publish `11434` to host

Run:

*   `./scripts/70-ollama-service-ensure.sh`

Script contract:

*   Runs on: host
*   Executed by: normal project user
*   Modifies: compose service definition and persistent volume mapping
*   Re-run safety: idempotent merge/update of Ollama service block

### 4.4 GPU Enablement Prerequisites

Before starting Ollama with GPU:

*   Host NVIDIA driver must be installed and healthy
*   Docker NVIDIA runtime integration must be present

Run:

*   `./scripts/71-ollama-gpu-preflight.sh`

Script contract:

*   Runs on: host
*   Executed by: normal project user (with `sudo` only when host diagnostics require it)
*   Modifies: none (preflight validation)
*   Re-run safety: fully safe (non-mutating)

Short validation commands:

```
nvidia-smi
docker info
```

### 4.5 Deterministic Model Provisioning (Pre-Pull)

MVP requires `qwen3:8b` to be pulled during provisioning, not first-use.

Run:

*   `./scripts/72-ollama-model-prepull.sh`

Script contract:

*   Runs on: host (invokes `docker compose exec`/`run` into Ollama service)
*   Executed by: normal project user
*   Modifies: Ollama model store inside mounted persistent volume
*   Re-run safety: safe; existing model is reused and pull is convergent

Required model target:

*   `qwen3:8b`

### 4.6 Bring-Up Sequence

Run:

*   `./scripts/73-ollama-up.sh`
*   `./scripts/74-ollama-status.sh`

Script contracts:

*   Runs on: host
*   Executed by: normal project user
*   Modifies: container runtime state
*   Re-run safety: safe for repeated bring-up/status checks

### 4.7 Internal-Only Networking Validation

Validate that Ollama is reachable internally and not published externally.

Run:

```
docker compose -f deploy/compose/moltbox.yml ps
docker compose -f deploy/compose/moltbox.yml exec openclaw-gateway sh -lc 'node -e "require(\"http\").get(\"http://ollama:11434/api/tags\", r => { r.pipe(process.stdout) }).on(\"error\", e => { console.error(e); process.exit(1); })"'
```

If `openclaw-gateway` is not present in your compose service names, run the same command with `openclaw-cli` instead.

Pass Criteria:

*   Ollama container is running
*   No host port mapping for `11434` appears in `docker compose ps`
*   Internal call to `http://ollama:11434/api/tags` succeeds

Fail Means:

*   Any command returns a non-zero exit code.
*   Ollama is not running.
*   Port `11434` is published to host.
*   Internal call to `http://ollama:11434/api/tags` fails.

### 4.8 Model Readiness Validation

Validate that `qwen3:8b` is present before OpenClaw routing tests.

Run:

```
docker compose -f deploy/compose/moltbox.yml exec ollama ollama list
```

Pass Criteria:

*   `qwen3:8b` is listed in local model inventory
*   No on-demand pull is required at first request time

Fail Means:

*   `ollama list` returns a non-zero exit code.
*   `qwen3:8b` is not listed.

### 4.9 OpenClaw Integration Readiness Check

After Ollama is running and pre-pulled:

*   Confirm OpenClaw remains healthy under Docker baseline
*   Confirm OpenClaw can reach the internal Ollama endpoint

Run:

```
docker compose -f deploy/compose/moltbox.yml run --rm openclaw-cli gateway status
curl http://127.0.0.1:18789/readyz
```

Pass Criteria:

*   Gateway healthy
*   Local model runtime available on internal network
*   Appliance ready for Chapter 6 provider escalation configuration (kept separate)

Fail Means:

*   Any command returns a non-zero exit code.
*   Gateway status is not healthy.
*   `/readyz` does not return HTTP `200`.

## Chapter 5 - OpenSearch Single-Node Deployment (Internal Only)

This chapter deploys OpenSearch as internal-only infrastructure for MVP readiness. It does not wire OpenSearch into Cortex or long-term indexing pipelines in this phase.

### 5.1 Locked Runtime Posture

OpenSearch is configured with the following locked settings:

*   Official OpenSearch Docker image
*   Single-node mode only
*   `discovery.type=single-node`
*   `plugins.security.disabled=true`
*   Explicit heap via `OPENSEARCH_JAVA_OPTS`
*   No host-published `9200`
*   Internal Docker network access only

### 5.2 Host Kernel Prerequisite (`vm.max_map_count`)

OpenSearch requires host virtual memory map limits to be set before stable startup.  
Required value: `262144`.

Run:

*   `./scripts/80-opensearch-host-preflight.sh`

Script contract:

*   Runs on: host
*   Executed by: admin user with `sudo`
*   Modifies: host kernel sysctl setting (`vm.max_map_count`)
*   Re-run safety: idempotent (sets expected value if missing/incorrect)

Short validation command:

```
sysctl vm.max_map_count
```

Pass Criteria:

*   Command exits `0`.
*   Returned value is `>= 262144`.

Fail Means:

*   Command exits non-zero.
*   Returned value is `< 262144`.

### 5.3 Compose Service Definition and Environment

Ensure OpenSearch service is present in the compose stack with required environment keys.

Run:

*   `./scripts/81-opensearch-service-ensure.sh`

Script contract:

*   Runs on: host
*   Executed by: normal project user
*   Modifies: compose service block and environment file values
*   Re-run safety: idempotent merge/update for known keys

Required environment keys:

*   `discovery.type=single-node`
*   `plugins.security.disabled=true`
*   `OPENSEARCH_JAVA_OPTS=-Xms2g -Xmx2g` (or host-appropriate locked value)

Service constraints:

*   Persistent data volume mounted
*   No `ports:` mapping for `9200`
*   Same internal network as OpenClaw and Ollama

### 5.4 Bring-Up and Status

Run:

*   `./scripts/82-opensearch-up.sh`
*   `./scripts/83-opensearch-status.sh`

Script contracts:

*   Runs on: host
*   Executed by: normal project user
*   Modifies: container runtime state
*   Re-run safety: safe to re-run

### 5.5 Internal-Only Validation

Run:

```
docker compose -f deploy/compose/moltbox.yml ps
docker compose -f deploy/compose/moltbox.yml exec openclaw-cli sh -lc "node -e \"require('http').get('http://opensearch:9200/_cluster/health', r => { r.pipe(process.stdout) }).on('error', e => { console.error(e); process.exit(1); })\""
```

Pass Criteria:

*   OpenSearch container is running
*   No host port mapping for `9200` appears in compose status
*   Internal request to `http://opensearch:9200/_cluster/health` succeeds

Fail Means:

*   Any command returns a non-zero exit code.
*   OpenSearch is not running.
*   Port `9200` is published to host.
*   Internal request to `http://opensearch:9200/_cluster/health` fails.

### 5.6 Upstream Reference Points

*   OpenSearch Docker docs: Run OpenSearch in Docker, single-node, and Docker prerequisites
*   OpenSearch Docker environment docs: `discovery.type`, `plugins.security.disabled`, `OPENSEARCH_JAVA_OPTS`

## Chapter 6 - Together AI Escalation Provider Authentication

This chapter configures Together AI authentication only. It does not set or change the global default model in MVP.

### 6.1 Locked Runtime Posture

Chapter 6 enforces:

*   Provider auth only
*   `TOGETHER_API_KEY` stored in `.env`
*   Documented OpenClaw CLI/config flows only
*   One deterministic provider probe call
*   No default model changes

### 6.2 Secret Registration in Environment

Store the Together key in the environment file used by compose.

Run:

*   `./scripts/90-together-env-ensure.sh`

Script contract:

*   Runs on: host
*   Executed by: normal project user
*   Modifies: `./deploy/env/moltbox.env` secret entry (`TOGETHER_API_KEY`)
*   Re-run safety: idempotent key replacement

### 6.3 Provider Auth Configuration (CLI-Only Flow)

Configure provider auth through documented OpenClaw onboarding/auth CLI surfaces.

Run:

*   `./scripts/91-together-auth-configure.sh`

Script contract:

*   Runs on: host
*   Executed by: normal project user
*   Modifies: OpenClaw auth profile/config state via CLI commands only
*   Re-run safety: convergent update of Together auth profile

Implementation rule for script:

*   Must not persist a global default-model change.
*   If onboarding/auth flow attempts to apply provider default model, script must restore prior `agents.defaults.model.primary` value before exit.

### 6.4 Deterministic Provider Probe

Run a deterministic provider probe using built-in model status probing.

Run:

```
docker compose -f deploy/compose/moltbox.yml run --rm openclaw-cli models status --probe --probe-provider together --probe-concurrency 1 --probe-timeout 10000 --probe-max-tokens 8
```

Pass Criteria:

*   Command exit code is `0`.
*   Probe returns Together provider probe output.

Fail Means:

*   Command exit code is non-zero.
*   Non-zero exit indicates invalid `TOGETHER_API_KEY` or outbound network failure.
*   Treat non-zero exit as a hard failure gate for this chapter.

### 6.5 Policy Guard Validation (No Default Model Mutation)

Run:

```
docker compose -f deploy/compose/moltbox.yml run --rm openclaw-cli config get agents.defaults.model.primary
```

Pass Criteria:

*   Value remains unchanged from pre-Chapter-6 state

Fail Means:

*   Command exits non-zero.
*   Value changed from pre-Chapter-6 baseline.

### 6.6 Upstream Reference Points

*   OpenClaw onboarding docs: non-interactive mode and provider auth flags
*   OpenClaw models CLI docs: `models status --probe` and auth profile flows

## Chapter 7 - Signal Polling Channel Configuration (QR-Link Existing Account)

This chapter enables Signal using the QR-link existing-account path only. Dedicated bot-number registration is out of scope for MVP.

### 7.1 Locked Runtime Posture

Chapter 7 enforces:

*   Existing Signal account linkage via QR flow
*   `channels.signal.enabled=true`
*   Pairing required for DM access control
*   No dedicated bot-number flow

### 7.2 Channel Enablement and Baseline Channel Config

Enable Signal channel through documented CLI/config flows.

Run:

*   `./scripts/100-signal-enable.sh`

Script contract:

*   Runs on: host
*   Executed by: normal project user
*   Modifies: OpenClaw channel config via CLI
*   Re-run safety: idempotent enablement

Required setting target:

*   `channels.signal.enabled=true`

### 7.3 QR Link Existing Account

Link Signal account using channel login flow.

Run:

*   `./scripts/101-signal-login-qr.sh`

Script contract:

*   Runs on: host
*   Executed by: normal project user with access to QR completion flow
*   Modifies: Signal channel credential/session state
*   Re-run safety: safe; re-opens login flow when account is not already linked

### 7.4 Pairing Discipline

MVP retains pairing workflow for first-contact senders.

Policy:

*   First DM from an unpaired sender requires pairing approval
*   No open-allowlist mode is configured in this chapter

### 7.5 Deterministic Validation

Run:

```
docker compose -f deploy/compose/moltbox.yml run --rm openclaw-cli channels status --probe
docker compose -f deploy/compose/moltbox.yml run --rm openclaw-cli config get channels.signal.enabled
```

Pass Criteria:

*   Signal channel appears in probe output without fatal setup errors
*   `channels.signal.enabled` resolves to `true`
*   Manual DM test completes pairing flow and returns a post-pairing response.

Fail Means:

*   Any command exits non-zero.
*   Probe reports blocking setup errors.
*   `channels.signal.enabled` is not `true`.
*   Manual DM test does not complete pairing or does not return a post-pairing response.

### 7.6 Upstream Reference Points

*   OpenClaw Signal channel docs: quick setup and account linking
*   OpenClaw channels CLI docs: `channels login`, `channels status --probe`

## Chapter 8 - LAN-Only Exposure and UFW Hardening

This chapter applies minimal firewall hardening for MVP with LAN-only access.

### 8.1 Locked Firewall Posture

Firewall policy for MVP:

*   LAN CIDR: `192.168.1.0/24`
*   Allow inbound TCP: `22`, `18789`
*   Default deny incoming
*   IPv6 disabled unless explicitly required
*   Docker traffic control reinforced via `DOCKER-USER` chain guidance

### 8.2 Safe Application Order (Avoid SSH Lockout)

Apply in this order:

1.  Add allow rule for SSH from LAN CIDR
2.  Add allow rule for gateway port from LAN CIDR
3.  Set defaults (`deny incoming`, `allow outgoing`)
4.  Disable IPv6 in UFW configuration (if not required)
5.  Enable UFW

Rollback note:

*   Keep the current SSH session open while applying rules.
*   If remote access is disrupted, use local console access to disable UFW (`sudo ufw disable`) and re-apply Chapter 8 steps in order.

Run:

*   `./scripts/110-ufw-apply-baseline.sh`

Script contract:

*   Runs on: host
*   Executed by: admin user with `sudo`
*   Modifies: host UFW policy and defaults
*   Re-run safety: idempotent rule reconciliation

### 8.3 Docker + UFW Interaction Guard (`DOCKER-USER`)

Docker can bypass simple UFW expectations for published ports. Enforce LAN restriction at `DOCKER-USER`.

Run:

*   `./scripts/111-docker-user-lan-guard.sh`

Script contract:

*   Runs on: host
*   Executed by: admin user with `sudo`
*   Modifies: host iptables `DOCKER-USER` chain policy for published gateway traffic
*   Re-run safety: idempotent rule insertion/update with duplicate protection

Required guard intent:

*   Allow established/related traffic
*   Allow LAN CIDR `192.168.1.0/24` to published gateway port `18789`
*   Drop non-LAN traffic to published gateway port
*   Insert rules at the top of `DOCKER-USER` chain.
*   Use insertion (`-I`), not append (`-A`).
*   Rule order is mandatory: LAN allow rule must appear before non-LAN drop rule.

### 8.4 Firewall State Validation

Run:

```
sudo ufw status verbose
sudo iptables -S DOCKER-USER
docker compose -f deploy/compose/moltbox.yml ps
```

Pass Criteria:

*   UFW enabled with default deny incoming
*   Allow rules present for `22/tcp` and `18789/tcp` from `192.168.1.0/24`
*   `DOCKER-USER` chain includes LAN guard rules for gateway port
*   Only intended published port appears in compose status

Fail Means:

*   Any command exits non-zero.
*   UFW is disabled or default incoming policy is not deny.
*   Required allow rules are missing or incorrect.
*   `DOCKER-USER` rules are missing, appended in wrong order, or place drop before LAN allow.
*   Unexpected host-published ports are present.

### 8.5 Upstream Reference Points

*   Docker docs: Packet filtering and firewalls (`Docker and ufw`, `DOCKER-USER`)
*   OpenClaw gateway docs: bind/auth and LAN operation expectations

## Chapter 9 - Validation Checklist and Runbook

This chapter validates the appliance in strict dependency order and defines pass/fail gates for MVP acceptance.

### 9.1 Validation Order (Required)

Run validations in this order only:

1.  Chapter 3: OpenClaw Docker onboarding baseline
2.  Chapter 4: Ollama local routing model
3.  Chapter 5: OpenSearch internal single-node
4.  Chapter 6: Together provider auth
5.  Chapter 7: Signal channel linking
6.  Chapter 8: UFW hardening and Docker-user guard

### 9.2 Baseline Health and Container State

Run:

```
docker compose -f deploy/compose/moltbox.yml ps
curl http://127.0.0.1:18789/healthz
curl http://127.0.0.1:18789/readyz
docker compose -f deploy/compose/moltbox.yml run --rm openclaw-cli gateway status
```

Pass Criteria:

*   Required containers are `Up`
*   `/healthz` and `/readyz` return HTTP `200`
*   Gateway status reports healthy reachable runtime

Fail Means:

*   Any command exits non-zero.
*   Any required container is not `Up`.
*   `/healthz` or `/readyz` does not return HTTP `200`.
*   Gateway status is not healthy.

### 9.3 Local Model Runtime Validation

Run:

```
docker compose -f deploy/compose/moltbox.yml exec ollama ollama list
docker compose -f deploy/compose/moltbox.yml exec openclaw-cli sh -lc "node -e \"require('http').get('http://ollama:11434/api/tags', r => { r.pipe(process.stdout) }).on('error', e => { console.error(e); process.exit(1); })\""
```

Pass Criteria:

*   `qwen3:8b` exists in Ollama model list
*   Internal network call to Ollama API succeeds
*   No host-published port `11434`

Fail Means:

*   Any command exits non-zero.
*   `qwen3:8b` is missing from `ollama list`.
*   Internal Ollama API call fails.
*   Port `11434` is published to host.

### 9.4 OpenSearch Runtime Validation

Run:

```
sysctl vm.max_map_count
docker compose -f deploy/compose/moltbox.yml exec openclaw-cli sh -lc "node -e \"require('http').get('http://opensearch:9200/_cluster/health', r => { r.pipe(process.stdout) }).on('error', e => { console.error(e); process.exit(1); })\""
```

Pass Criteria:

*   `vm.max_map_count` value is `>= 262144`
*   OpenSearch cluster health endpoint responds internally
*   No host-published port `9200`

Fail Means:

*   Any command exits non-zero.
*   `vm.max_map_count` value is `< 262144`.
*   Internal OpenSearch health request fails.
*   Port `9200` is published to host.

### 9.5 Together Provider Validation

Run:

```
docker compose -f deploy/compose/moltbox.yml run --rm openclaw-cli models status --probe --probe-provider together --probe-concurrency 1 --probe-timeout 10000 --probe-max-tokens 8
```

Pass Criteria:

*   Command exit code is `0`.
*   Together provider probe executes successfully.
*   No default-model drift introduced by Chapter 6 policy

Fail Means:

*   Command exit code is non-zero.
*   Non-zero exit indicates invalid `TOGETHER_API_KEY` or outbound network failure.
*   Treat non-zero exit as a hard failure gate.
*   Default model value drift is detected.

### 9.6 Signal Channel Validation

Run:

```
docker compose -f deploy/compose/moltbox.yml run --rm openclaw-cli channels status --probe
docker compose -f deploy/compose/moltbox.yml run --rm openclaw-cli config get channels.signal.enabled
```

Pass Criteria:

*   Signal channel is enabled
*   Probe does not report blocking channel setup errors
*   Linked account can complete DM + pairing flow

Fail Means:

*   Any command exits non-zero.
*   Channel is not enabled.
*   Probe reports blocking setup errors.
*   DM + pairing flow fails.

### 9.7 Firewall and LAN Reachability Validation

Run on Moltbox host:

```
sudo ufw status verbose
sudo iptables -S DOCKER-USER
```

Run from second LAN client (`192.168.1.0/24`):

```
curl http://<MOLTBOX_LAN_HOST>:18789/healthz
```

Pass Criteria:

*   UFW rules match Chapter 8 policy
*   DOCKER-USER LAN guard rules present
*   Second LAN client can reach gateway health endpoint
*   Non-LAN access is blocked by firewall policy

Fail Means:

*   Any command exits non-zero.
*   UFW or `DOCKER-USER` rules do not match Chapter 8 requirements.
*   Second LAN client cannot reach `http://<MOLTBOX_LAN_HOST>:18789/healthz`.
*   Non-LAN access to `18789/tcp` is not blocked.

### 9.8 Post-Hardening Re-Test

After Chapter 8 hardening, re-run:

*   Gateway health endpoints
*   Provider probe (Together)
*   Signal probe
*   Internal OpenSearch and Ollama checks

Run:

*   `./scripts/120-post-hardening-retest.sh`

Script contract:

*   Runs on: host
*   Executed by: normal project user (with `sudo` for firewall state checks)
*   Modifies: none (read/verify only)
*   Re-run safety: fully safe (non-mutating)

Pass Criteria:

*   Script exits `0`.
*   Gateway health checks pass after hardening.
*   Together provider probe exits `0`.
*   Signal probe passes.
*   Internal OpenSearch and Ollama checks pass.

Fail Means:

*   Script exits non-zero.
*   Any gated check in the script fails.

### 9.9 Runbook Failure Handling

If any gate fails:

1.  Stop progression to the next chapter.
2.  Capture command output and relevant container logs.
3.  Fix only the failing layer.
4.  Re-run current layer checks before continuing.

This enforces deterministic layered recovery and prevents compounding failures.

## Appendix A - Authoring Guardrails and AI Drafting Rules

This appendix defines non-implementation drafting governance used to keep the deployment manual accurate and consistent.

*   Confirm configuration keys against official OpenClaw documentation before documenting or using them.
*   Do not invent undocumented configuration keys, flags, ports, or behaviors.
*   Prefer built-in OpenClaw capabilities over custom logic or speculative infrastructure.
*   If documentation is unclear, pause and request clarification before drafting affected sections.
*   Keep AI reasoning commentary separate from implementation instructions.
*   Keep verification reminders and documentation-check reminders in authoring governance sections, not in implementation chapters.
*   Treat project source documents and confirmed user decisions as authoritative for scope and constraints.