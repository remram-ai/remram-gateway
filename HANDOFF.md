# Moltbox Gateway Legacy Implementation Handoff

This document is informational only.

The next Moltbox gateway implementation is expected to be a clean greenfield build. Do not reuse legacy implementation code from this repository as the structural base. Use this document to preserve operational knowledge, command shape, and failure lessons from the retiring Python implementation.

## Language Recommendation

Recommendation: move the new gateway + CLI to Go.

Why:
- Container orchestration is a good fit for Go: strong Docker/API libraries, clear concurrency, easy JSON/YAML handling.
- The appliance deployment model favors a single static binary on a minimal Linux host over a Python runtime plus packaging environment.
- Binary distribution and upgrades are materially simpler with Go: host PATH installation, versioned artifacts, rollback, and bootstrap are easier.
- Control-plane reliability is better with fewer runtime dependencies and a faster-starting binary.
- The gateway is now primarily an orchestrator, not an application platform. That shifts the balance toward Go.

Tradeoffs:
- Rewrite cost is real.
- Python is faster for prototyping and scripting.
- Templating, Git integration, and config merge behavior will need deliberate library choices in Go.

Translation assessment:
- The high-level patterns translate cleanly: typed command parsing, central dispatch, explicit repo adapters, explicit deployment pipelines.
- The code should not be ported literally. Several legacy implementation choices should be retired rather than translated.

## CLI Command Surface

Legacy implemented command surface:

```text
moltbox <component> <command>
```

### Global flags

- `--help`, `-h`
- `--version`
- `--config-path`
- `--state-root`
- `--logs-root`
- `--runtime-artifacts-root`
- `--services-repo-url`
- `--runtime-repo-url`
- `--skills-repo-url`
- `--internal-host`
- `--internal-port`
- `--cli-path`

### gateway

```text
gateway
  health
    flags: none
    purpose: gateway liveness
  status
    flags: none
    purpose: gateway state summary
  inspect
    flags: none
    purpose: gateway/container detail
  logs
    flags: none
    purpose: gateway logs
  update
    flags: --version, --commit
    purpose: self-deploy/update gateway through the service pipeline
  serve
    flags: none
    purpose: run the gateway service process inside the container
  repo refresh
    args: services|runtime|skills|all
    purpose: refresh host-side repo mirrors/caches
  repo seed
    args: <services|runtime|skills>
    flags: --bundle <path>
    purpose: seed a host-side repo mirror from a Git bundle
```

### service

```text
service
  list
    purpose: list deployable services from moltbox-services
  inspect <service>
    purpose: inspect resolved service definition and deployment state
  status <service>
    purpose: inspect container state
  logs <service>
    purpose: fetch service logs
  deploy <service>
    flags: --version, --commit
    purpose: render, deploy, validate, and rollback on failure
  start <service>
    purpose: docker lifecycle wrapper
  stop <service>
    purpose: docker lifecycle wrapper
  restart <service>
    purpose: docker lifecycle wrapper
  rollback <service>
    purpose: redeploy previous successful render
  doctor <service>
    purpose: deployment diagnostics plus validation
```

### skill

```text
skill
  deploy <skill>
    flags: --runtime <openclaw|openclaw-dev|openclaw-test|openclaw-prod>
    purpose: deploy a pure skill or plugin-backed skill into an OpenClaw runtime
```

### Component namespaces implemented in the legacy CLI

```text
openclaw          alias for openclaw-prod
openclaw-dev
openclaw-test
openclaw-prod
caddy
opensearch
ollama
```

Supported component commands:

```text
<component>
  status
  inspect
  logs
  start
  stop
  restart
  doctor
  monitor
  reload
  config sync
  skill deploy <skill>   # runtime components only
```

Examples implemented:
- `moltbox gateway status`
- `moltbox service deploy gateway`
- `moltbox service deploy openclaw-dev --version 1.4.2`
- `moltbox openclaw reload`
- `moltbox openclaw-dev config sync`
- `moltbox skill deploy semantic-router --runtime openclaw-test`
- `moltbox openclaw-test skill deploy semantic-router`

### Parsing and routing approach

Patterns worth preserving conceptually:
- Parse once into typed command objects rather than branching directly on raw argv.
- Keep parser logic, command dispatch, component/resource resolution, and execution handlers separate.
- Use a central dispatcher to route:
  - `GatewayCommand`
  - `ServiceCommand`
  - `ComponentCommand`
  - `SkillCommand`
- Keep resource/component resolution in one registry instead of scattering container names across handlers.

Flag conventions:
- global config/layout flags at the CLI root
- resource-specific flags on the leaf command
- mutually exclusive deployment override flags: `--version` and `--commit`

Important legacy note:
- The implemented component surface still exposed internal runtime names like `openclaw-dev`. The new architecture should prefer user-facing runtime namespaces like `dev`, `test`, and `prod`, with container names treated as implementation details.

## Container and Runtime Assumptions

Legacy expected container names:

```text
gateway
caddy
ollama
opensearch
openclaw-dev
openclaw-test
openclaw-prod
```

Legacy fallback names still appeared in some code paths:

```text
moltbox-openclaw
openclaw
```

Containers the CLI managed directly:
- `gateway`
- `caddy`
- `opensearch`
- `openclaw-dev`
- `openclaw-test`
- `openclaw-prod`
- sometimes `ollama`

Services assumed to exist or be foundational:
- `gateway`
- `caddy`
- `opensearch`
- `ollama`
- at least one OpenClaw runtime

Docker networking assumptions:
- one shared external Docker network named `moltbox_moltbox_internal`
- the gateway container was expected on that network under the container name `gateway`
- caddy expected to reverse proxy:
  - `moltbox-cli` -> `gateway:7474`
  - `moltbox-dev` -> `host.docker.internal:18790`
  - `moltbox-test` -> `host.docker.internal:28789`
  - `moltbox-prod` -> `host.docker.internal:38789`
- caddy expected `host.docker.internal:host-gateway`

Runtime/container conventions in the legacy implementation:
- OpenClaw runtimes were containerized separately as `openclaw-dev`, `openclaw-test`, and `openclaw-prod`
- the runtime service templates exposed:
  - host port `18790` for dev
  - host port `28789` for test
  - host port `38789` for prod
  - internal container port `18789`
- OpenClaw health checks targeted `http://127.0.0.1:18789/healthz`

Persistence assumptions:
- gateway mounted state root, runtime root, logs root, and `/var/run/docker.sock`
- OpenClaw mounted persistent runtime state into `/home/node/.openclaw`
- OpenClaw also mounted rendered config read-only into `/app/config/openclaw`
- caddy persisted its own data/config directories
- opensearch persisted database state separately

## Filesystem and Persistent State

Canonical roots used by the legacy implementation:

```text
/srv/moltbox-state
/srv/moltbox-logs
```

Important state directories:

```text
/srv/moltbox-state/gateway
/srv/moltbox-state/services/<service>
/srv/moltbox-state/runtime/<runtime>
/srv/moltbox-state/deploy/rendered/<service>
/srv/moltbox-state/deploy/runtime-sync/<runtime>
/srv/moltbox-state/repos/<repo>
/srv/moltbox-state/upstream/<repo>
```

Important log locations:

```text
/srv/moltbox-logs/gateway
/srv/moltbox-logs/services
```

Configuration paths:
- canonical gateway config inside the container: `/etc/moltbox/config.yaml`
- OpenClaw mutable runtime state inside runtime containers: `/home/node/.openclaw`
- OpenClaw rendered config mount: `/app/config/openclaw`

State separation used in the legacy implementation:
- rendered deployment artifacts: `/srv/moltbox-state/deploy/rendered/...`
- render-only runtime config staging: `/srv/moltbox-state/deploy/runtime-sync/...`
- runtime mutable state: `/srv/moltbox-state/runtime/...`
- repo mirrors/checkouts: `/srv/moltbox-state/upstream/...` and `/srv/moltbox-state/repos/...`

This separation is worth preserving conceptually even if the greenfield build uses a different implementation.

## Runtime Snapshot Commands

Run these on the host before shutting down legacy containers:

```bash
docker ps -a
docker images
docker volume ls
docker network ls
docker network inspect moltbox_moltbox_internal
docker inspect gateway caddy opensearch openclaw-dev openclaw-test openclaw-prod
docker logs --tail 200 gateway
docker logs --tail 200 caddy
docker logs --tail 200 opensearch
docker logs --tail 200 openclaw-dev
docker logs --tail 200 openclaw-test
docker logs --tail 200 openclaw-prod
```

Useful filesystem snapshots:

```bash
find /srv/moltbox-state -maxdepth 3 -type d | sort
find /srv/moltbox-logs -maxdepth 3 -type d | sort
ls -la /srv/moltbox-state/upstream
ls -la /srv/moltbox-state/repos
```

If OpenClaw runtime state needs to be inspected before retirement:

```bash
docker exec openclaw-dev sh -lc 'ls -la /home/node/.openclaw'
docker exec openclaw-test sh -lc 'ls -la /home/node/.openclaw'
docker exec openclaw-prod sh -lc 'ls -la /home/node/.openclaw'
```

## Implementation Notes Worth Preserving

Useful concepts from the legacy implementation:

- Typed command parsing followed by central dispatch was cleaner than mixing parsing and execution.
- Explicit repo adapters for `moltbox-services`, `moltbox-runtime`, and `remram-skills` were the right boundary.
- Service deployment as a pipeline was the right shape:
  - render
  - snapshot
  - pull/build
  - deploy
  - validate
  - rollback
- Separating rendered config from live runtime state reduced accidental mutation.
- Repo mirror refresh/seed became necessary once private repo access existed on the appliance.
- Validation got materially better once restart loops and Docker-unavailable states were treated as hard failures.

Useful conceptual boundaries:
- CLI interface
- command routing
- service deployment
- runtime operations
- Docker interaction
- external repo access

Those boundaries should be preserved in the new build even if the implementation language changes.

## Mistakes or Architectural Risks

Do not repeat these in the greenfield implementation:

- Do not expose internal container names as the primary operator model.
  The legacy CLI leaked names like `openclaw-dev`. The new user model should center on `dev`, `test`, and `prod`.

- Do not let the CLI mutate runtime internals directly.
  The legacy skill deployment path used `docker exec`, `docker cp`, direct writes into `/home/node/.openclaw`, and in-container config edits. That is too tightly coupled.

- Do not keep generic fallback resource resolution.
  Unknown component names could degrade into generic service handling. The new build should require explicit resource registration.

- Do not hardcode network names, hostnames, or port inference when they should come from an explicit contract.
  `moltbox_moltbox_internal`, `host.docker.internal`, and several port mappings leaked through templates and handlers.

- Do not blur deployment pipeline namespaces with managed resources.
  `service deploy` is a pipeline; runtime environments are resources. Keep those concepts separate.

- Do not couple config sync to ownership of mutable runtime state.
  Render desired state; do not assume the control plane owns all live runtime internals.

- Do not preserve transitional command compatibility longer than necessary.
  Legacy namespaces like `tools`, `runtime`, and `host` created drift and operator confusion.

- Do not treat host-side repo refresh as an operator workaround.
  Private repo access and mirror refresh need a first-class, well-defined control-plane contract.

- Do not allow validation to report success when Docker is unavailable or a container is crash-looping.
  This happened in the legacy implementation and caused false confidence.

- Do not rely on the current Python module layout as a migration base.
  The next build is greenfield and should preserve knowledge, not structure.
