Remote scripts in this directory are explicitly approved for execution through the Moltbox debug service.

Rules:
- Only files in `moltbox/remote/exec/` are executable.
- Script names must be plain `*.sh` filenames.
- Scripts run with `bash`.
- The working directory is the repo root on Moltbox.
- `MOLTBOX_RUNTIME_ROOT` is set to the selected runtime root.
- Arguments are passed as structured argv values, not through a shell string.

Recommended usage:
- Commit script changes to git from your development machine.
- Use the debug service `repo_pull` tool on Moltbox.
- Use `list_remote_scripts` to confirm availability.
- Use `run_remote_script` or `run_remote_script_sync` to execute the synced script.
