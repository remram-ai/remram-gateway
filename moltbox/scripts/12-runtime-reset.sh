#!/usr/bin/env bash
set -euo pipefail

# Moltbox runtime reset utility.
# Clears runtime state without reinstalling the host or pruning Docker assets.

status() {
  echo "[runtime-reset] $*"
}

error() {
  echo "[runtime-reset] $*" >&2
}

resolve_target_home() {
  local target_user="${SUDO_USER:-${USER}}"
  local target_home=""

  if command -v getent >/dev/null 2>&1; then
    target_home="$(getent passwd "${target_user}" | cut -d: -f6)"
  fi

  if [[ -z "${target_home}" ]]; then
    target_home="${HOME}"
  fi

  printf '%s\n' "${target_home}"
}

TARGET_HOME="$(resolve_target_home)"
RUNTIME_ROOT="${MOLTBOX_RUNTIME_ROOT:-${TARGET_HOME}/.openclaw}"

display_runtime_root() {
  if [[ "${RUNTIME_ROOT}" == "${TARGET_HOME}"* ]]; then
    printf '~%s\n' "${RUNTIME_ROOT#"${TARGET_HOME}"}"
    return
  fi

  printf '%s\n' "${RUNTIME_ROOT}"
}

docker_cmd() {
  if docker info >/dev/null 2>&1; then
    env "MOLTBOX_RUNTIME_ROOT=${RUNTIME_ROOT}" docker "$@"
  else
    sudo env "MOLTBOX_RUNTIME_ROOT=${RUNTIME_ROOT}" docker "$@"
  fi
}

require_host_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    error "required command not found on host: ${cmd}"
    exit 1
  fi
}

stop_containers() {
  status "stopping containers"
  docker_cmd stop moltbox-openclaw moltbox-ollama moltbox-opensearch 2>/dev/null || true
}

remove_containers() {
  status "removing containers"
  docker_cmd rm moltbox-openclaw moltbox-ollama moltbox-opensearch 2>/dev/null || true
}

reset_runtime() {
  status "clearing $(display_runtime_root)"
  mkdir -p "${RUNTIME_ROOT}"
  find "${RUNTIME_ROOT}" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + 2>/dev/null || true
  mkdir -p "${RUNTIME_ROOT}"
}

cleanup_debug_bundles() {
  status "cleaning /tmp debug bundles"
  rm -f /tmp/moltbox-debug-*.tar.gz 2>/dev/null || true
}

print_next_steps() {
  printf 'Next step:\n\n./20-bootstrap.sh\n./30-validate.sh\n'
}

main() {
  require_host_cmd docker

  stop_containers
  remove_containers
  reset_runtime
  cleanup_debug_bundles

  status "runtime reset complete"
  print_next_steps
}

main "$@"
