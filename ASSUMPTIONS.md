# Assumptions

- `moltbox <env> reload` is implemented as a restart of the mapped runtime service (`openclaw-dev`, `openclaw-test`, or `openclaw-prod`) followed by container health validation.
- `moltbox gateway update` uses the same deployment engine as `moltbox gateway service deploy gateway`.
- `moltbox-runtime/opensearch/.env` is not present in the current repo split, so the gateway renders an empty `.env` file for Compose compatibility.
- Service passthrough executes the service-native binary name inside the matching container (`ollama`, `opensearch`, `caddy`). If a service image does not provide that binary, the command fails as a normal passthrough execution error.
- Caddy runtime TLS is rendered as an explicit locally generated certificate/key pair mounted from service assets instead of relying on `tls internal`, because Windows Schannel validation on the operator machine requires a trustable non-revocation-blocked certificate path for `https://moltbox-dev`, `https://moltbox-test`, and `https://moltbox-prod`.
- The initial bootstrap control-plane client certificate identity is `CN=jason-cli, O=Moltbox`, generated alongside the Caddy TLS assets and signed by the same local Moltbox CA used for the HTTPS server certificate.
- `moltbox gateway update` uses a short-lived helper container to replace the running `gateway` container, because the gateway cannot synchronously redeploy itself in-place and keep the initiating CLI request alive.
