# Remram Control Pane

Phase 1 scaffold for the new `dev-manager` control plane.

Current responsibilities:

- define the stable host filesystem layout under `~/.remram`
- define the `dev`, `test`, and `prod` runtime inventory
- define the shared service inventory for `ollama` and `opensearch`
- define the host-tool primitive contract and source paths

Current commands:

```bash
python -m remram_dev_manager_control_pane describe
python -m remram_dev_manager_control_pane ensure-layout
python -m remram_dev_manager_control_pane list-runtimes
python -m remram_dev_manager_control_pane list-shared-services
python -m remram_dev_manager_control_pane list-primitives
```

The package currently uses the Python standard library only. API and MCP/HTTP
surface area can be added on top of this foundation once the deployment
primitives start doing real work.
