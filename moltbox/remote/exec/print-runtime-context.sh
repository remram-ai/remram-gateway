#!/usr/bin/env bash
set -Eeuo pipefail

printf 'repo_root=%s\n' "$(pwd)"
printf 'runtime_root=%s\n' "${MOLTBOX_RUNTIME_ROOT:-}"

if [[ -f "${MOLTBOX_RUNTIME_ROOT:-}/.env" ]]; then
  printf 'debug_service_port='
  grep '^DEBUG_SERVICE_PORT=' "${MOLTBOX_RUNTIME_ROOT}/.env" | cut -d= -f2-
fi
