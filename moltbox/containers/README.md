# MoltBox Containers

This directory contains appliance container assets.

- `tools/` contains the MoltBox tooling container definition used by the operator stack.
- `shared-services/` contains host service assets such as `caddy`, `ollama`, and `opensearch`.
- `runtimes/` contains OpenClaw runtime container assets.

Service asset directories may include compose templates, Dockerfiles, and service-local config templates.
These are deployment artifacts, not the Python CLI implementation.
