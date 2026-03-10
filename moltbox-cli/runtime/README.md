# Runtime Domain

`runtime/` contains commands and tests for OpenClaw runtime environments such as `dev`, `test`, and `prod`.

Canonical grammar:

- `moltbox runtime <environment> <verb>`

Runtime deploy/start/restart seed OpenClaw `gateway.controlUi.allowedOrigins` from the MoltBox host LAN identity. Override detection with `MOLTBOX_PUBLIC_HOST_IP`, `MOLTBOX_PUBLIC_HOSTNAME`, or `MOLTBOX_OPENCLAW_ALLOWED_ORIGINS_EXTRA` when needed.
