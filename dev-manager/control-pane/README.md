# Remram Control Plane

Canonical `remram` control-plane package for the inner loop.

Implemented in this phase:

- CLI entrypoint: `remram`
- control-plane service: `remram serve`
- JSON health and target inspection commands
- target registry bootstrap under `~/.remram/state/targets`
- runtime state files under `~/.remram/control-plane`
- host log root under `~/Moltbox/logs`
- thin MCP adapter over CLI commands

Commands:

```bash
python -m remram_dev_manager_control_pane version
python -m remram_dev_manager_control_pane health
python -m remram_dev_manager_control_pane serve
python -m remram_dev_manager_control_pane list-targets
python -m remram_dev_manager_control_pane status --target control
python -m remram_dev_manager_control_pane.mcp_server --host 127.0.0.1 --port 7475
```

Package scripts after install:

```bash
remram version
remram health
remram serve
remram list-targets
remram status --target control
remram-mcp --host 127.0.0.1 --port 7475
```
