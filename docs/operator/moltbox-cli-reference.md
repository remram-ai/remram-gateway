# Moltbox CLI Reference

This document describes the current operator CLI implemented in `moltbox-cli/` on the `main` branch.

## Command Shape

The canonical command is:

```text
moltbox <domain> ...
```

Domains:

- `tools`
- `host`
- `runtime`

Canonical forms:

```text
moltbox tools <verb>
moltbox host <service> <verb>
moltbox runtime <environment> <verb>
```

## Global Options

These options can be passed before the domain:

```text
--config-path
--policy-path
--state-root
--runtime-artifacts-root
--internal-host
--internal-port
--cli-path
```

These are primarily for advanced operator override, container bootstrap, and testing. In normal host operation, defaults are usually sufficient.

## Tools Domain

Syntax:

```text
moltbox tools <verb>
```

Supported verbs:

- `version`
- `health`
- `serve`
- `status`
- `inspect`
- `update`
- `rollback`
- `logs`

Common examples:

```bash
moltbox tools version
moltbox tools health
moltbox tools status
moltbox tools inspect
moltbox tools update
moltbox tools rollback
moltbox tools logs
```

Notes:

- `serve` is the long-running control-plane process used inside the tools container.
- `update` is the normal operator path for redeploying the tools service from the current repo state.

## Host Domain

Syntax:

```text
moltbox host <service> <verb>
```

Supported services:

- `ssl`
- `ollama`
- `opensearch`

Compatibility alias:

- `caddy` resolves to `ssl`

Supported verbs:

- `deploy`
- `rollback`
- `status`
- `inspect`
- `logs`
- `start`
- `stop`
- `restart`

Common examples:

```bash
moltbox host ssl deploy
moltbox host ssl status
moltbox host ssl inspect
moltbox host ssl logs

moltbox host ollama deploy
moltbox host ollama status

moltbox host opensearch deploy
moltbox host opensearch status
```

Notes:

- Use `ssl` in operator docs and commands. `caddy` is compatibility-only.
- `inspect` returns deployment and container metadata.
- `logs` returns container log output through the control plane.

## Runtime Domain

Syntax:

```text
moltbox runtime <environment> <verb>
```

Supported environments:

- `dev`
- `test`
- `prod`

Supported verbs:

- `deploy`
- `rollback`
- `status`
- `inspect`
- `logs`
- `start`
- `stop`
- `restart`

Common examples:

```bash
moltbox runtime dev deploy
moltbox runtime dev status
moltbox runtime dev inspect
moltbox runtime dev logs
moltbox runtime dev rollback

moltbox runtime test deploy
moltbox runtime test status
moltbox runtime test logs

moltbox runtime prod inspect
```

Operational notes:

- `dev` is the full-feature integration environment.
- `test` is the current UAT candidate environment.
- `prod` is part of the CLI surface even if no managed prod runtime is currently deployed.

## Output Behavior

The CLI emits structured JSON responses for both success and failure.

Typical success payloads include fields such as:

- `target`
- `verb`
- `status`
- `exit_code`

Error payloads include structured recovery guidance, for example:

- `error_type`
- `error_message`
- `recovery_message`

This makes the CLI suitable for both manual use and automation.

## Operator Usage Pattern

Recommended normal workflow:

1. Check tool health:

```bash
moltbox tools health
```

2. Check shared services:

```bash
moltbox host ssl status
moltbox host ollama status
moltbox host opensearch status
```

3. Check runtime state:

```bash
moltbox runtime dev status
moltbox runtime test status
```

4. Use `inspect` before risky changes:

```bash
moltbox runtime dev inspect
moltbox host ssl inspect
```

5. Use `deploy` or `rollback` as needed:

```bash
moltbox runtime dev deploy
moltbox runtime dev rollback
```

## MCP Boundary

The local CLI on the host is trusted operator access and is not permission-gated.

Remote access happens through HTTPS MCP and may expose only a subset of these operations depending on the current policy file:

```text
/home/jpekovitch/.remram/tools/control-plane-policy.yaml
```
