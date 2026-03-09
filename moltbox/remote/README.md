This directory is the git-synced remote script area for Moltbox.

Rules:
- Only files in `moltbox/remote/exec/` are executable through the debug service.
- Other files under `moltbox/remote/` are documentation or support assets only.

Recommended usage:
- Commit script changes to git from your development machine.
- Use the debug service `repo_pull` tool on Moltbox.
- Put executable scripts in `moltbox/remote/exec/`.
- Use `list_remote_scripts` to confirm availability.
- Use `run_remote_script` or `run_remote_script_sync` to execute the synced script.
