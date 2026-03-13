# Assumptions

- `moltbox <env> reload` is implemented as a restart of the mapped runtime service (`openclaw-dev`, `openclaw-test`, or `openclaw-prod`) followed by container health validation.
- `moltbox gateway update` uses the same deployment engine as `moltbox gateway service deploy gateway`.
- `moltbox-runtime/opensearch/.env` is not present in the current repo split, so the gateway renders an empty `.env` file for Compose compatibility.
- Service passthrough executes the service-native binary name inside the matching container (`ollama`, `opensearch`, `caddy`). If a service image does not provide that binary, the command fails as a normal passthrough execution error.
