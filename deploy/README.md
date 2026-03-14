# Deploy

Current deploy path:

```text
host moltbox CLI
  -> http://127.0.0.1:7460
    -> gateway container
      -> /var/run/docker.sock
        -> host Docker Engine
```

Current appliance posture:

- the host CLI is installed at `~/.local/bin/moltbox`
- the long-running control plane is the `gateway` container
- the gateway image tag is `moltbox-gateway:latest`
- service definitions come from `moltbox-services`
- baseline runtime config comes from `moltbox-runtime`
- managed skill packages come from `remram-skills`

Useful verification commands:

- `moltbox gateway status`
- `moltbox gateway service status <service>`
- `moltbox dev openclaw skills list`
- `moltbox gateway docker ping`

The host CLI remains a thin client.
The control-plane behavior lives in the `gateway` container and the service orchestration code in this repository.
