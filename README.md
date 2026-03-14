# Moltbox Gateway

`moltbox-gateway` owns the Moltbox control plane and the `moltbox` CLI.

This repository is the implementation surface. Architecture, feature definitions, and the canonical CLI taxonomy live in the sibling `remram` repository.

Start with:

- `../remram/docs/ai-context/roles/builders.md`
- `../remram/docs/overview/repositories.md`
- `../remram/docs/overview/cli-architecture.md`
- `../remram/docs/reference/cli-reference.md`

Current implementation posture:

- the legacy Python implementation is preserved under `archive/legacy-implementation/`
- the host-installed `moltbox` binary is a thin HTTP client
- the long-running gateway server runs in a Docker container named `gateway`
- builds tag the gateway image as `moltbox-gateway:latest`
- the host CLI talks directly to the gateway over `http://127.0.0.1:7460`
- the gateway orchestrates service deploy, restart, runtime reload, runtime checkpoint, managed skill deploy and rollback, scoped secrets, and MCP tokens

Extra implementation commands currently exist for bootstrap and diagnostics:

- `moltbox gateway docker ping`
- `moltbox gateway docker run <image>`
- `moltbox gateway mcp-stdio`

Future service images and orchestration flows should come from `moltbox-services`, with gateway treated as a service there.
