# Moltbox Gateway

`moltbox-gateway` owns the Moltbox control plane and the `moltbox` CLI.

This repository is the implementation surface. Architecture, feature definitions, and the canonical CLI taxonomy live in the sibling `remram` repository.

Start with:

- `../remram/docs/ai-context/roles/builders.md`
- `../remram/docs/platform/repositories.md`
- `../remram/docs/platform/cli-architecture.md`
- `../remram/docs/reference/cli-reference.md`

Phase 1 is a bootstrap-only Go rebuild:

- the legacy Python implementation is preserved under `archive/legacy-implementation/`
- the host-installed `moltbox` binary is a thin HTTP client
- the long-running gateway server runs in a Docker container named `gateway`
- the host CLI talks directly to the gateway over `http://127.0.0.1:7460`
- `moltbox gateway docker ping` verifies Docker connectivity from inside the gateway container

Future service images and orchestration flows should come from `moltbox-services`, with gateway treated as a service there.
