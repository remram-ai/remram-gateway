#!/usr/bin/env bash
set -Eeuo pipefail

# Moltbox official debug bundle collector.
# Captures host and container runtime state without mutating the system.

timestamp() { date +"%Y-%m-%dT%H:%M:%S%z"; }
log_info() { echo "[$(timestamp)] [INFO] $*"; }
log_warn() { echo "[$(timestamp)] [WARN] $*" >&2; }
log_error() { echo "[$(timestamp)] [ERROR] $*" >&2; }

trap 'log_error "Diagnostics collection failed near line ${BASH_LINENO[0]}."' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOLTBOX_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_DIR="${MOLTBOX_DIR}/config"

resolve_target_user() {
  printf '%s\n' "${SUDO_USER:-${USER}}"
}

resolve_target_home() {
  local target_user="$1"
  local target_home=""

  if command -v getent >/dev/null 2>&1; then
    target_home="$(getent passwd "${target_user}" | cut -d: -f6)"
  fi

  if [[ -z "${target_home}" ]]; then
    target_home="${HOME}"
  fi

  printf '%s\n' "${target_home}"
}

TARGET_USER="$(resolve_target_user)"
TARGET_HOME="$(resolve_target_home "${TARGET_USER}")"
RUNTIME_ROOT="${MOLTBOX_RUNTIME_ROOT:-${TARGET_HOME}/.openclaw}"
RUNTIME_ENV_FILE="${RUNTIME_ROOT}/.env"
PREFERRED_CONFIG_DIR="${TARGET_HOME}/git/remram-gateway/moltbox/config"
REPO_CONFIG_SOURCE="${CONFIG_DIR}"
if [[ -d "${PREFERRED_CONFIG_DIR}" ]]; then
  REPO_CONFIG_SOURCE="${PREFERRED_CONFIG_DIR}"
fi
COMPOSE_FILE="${REPO_CONFIG_SOURCE}/docker-compose.yml"

BUNDLE_TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
WORK_DIR="$(mktemp -d "/tmp/moltbox-debug-${BUNDLE_TIMESTAMP}.XXXXXX")"
BUNDLE_ROOT="${WORK_DIR}/debug-bundle"
ARCHIVE_PATH="/tmp/moltbox-debug-${BUNDLE_TIMESTAMP}.tar.gz"
DOWNLOAD_HOST="$(hostname -f 2>/dev/null || hostname)"

cleanup() {
  rm -rf "${WORK_DIR}"
}

trap cleanup EXIT

redact_stream() {
  perl -0pe '
    s/\b([A-Za-z0-9_]*_(?:KEY|TOKEN|SECRET)|PASSWORD)=("[^"]*"|'\''[^'\'']*'\''|[^[:space:]"\x27,}]+)/$1=REDACTED/g;
    s/^(\s*(?:export\s+)?(?:[A-Za-z0-9_]*_(?:KEY|TOKEN|SECRET)|PASSWORD)\s*=\s*).*$/${1}REDACTED/gm;
    s/^(\s*(?:"(?:[A-Za-z0-9_]*_(?:KEY|TOKEN|SECRET)|PASSWORD)"|(?:[A-Za-z0-9_]*_(?:KEY|TOKEN|SECRET)|PASSWORD))\s*:\s*)"([^"\\]|\\.)*"/${1}"REDACTED"/gm;
    s/^(\s*(?:"(?:[A-Za-z0-9_]*_(?:KEY|TOKEN|SECRET)|PASSWORD)"|(?:[A-Za-z0-9_]*_(?:KEY|TOKEN|SECRET)|PASSWORD))\s*:\s*)'\''([^'\''\\]|\\.)*'\''/${1}'\''REDACTED'\''/gm;
    s/^(\s*(?:"(?:[A-Za-z0-9_]*_(?:KEY|TOKEN|SECRET)|PASSWORD)"|(?:[A-Za-z0-9_]*_(?:KEY|TOKEN|SECRET)|PASSWORD))\s*:\s*)([^#\r\n,}]+)/${1}REDACTED/gm;
  '
}

require_host_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    log_error "Required command not found on host: ${cmd}"
    exit 1
  fi
}

ensure_bundle_dirs() {
  mkdir -p \
    "${BUNDLE_ROOT}/system" \
    "${BUNDLE_ROOT}/docker" \
    "${BUNDLE_ROOT}/logs" \
    "${BUNDLE_ROOT}/config" \
    "${BUNDLE_ROOT}/runtime" \
    "${BUNDLE_ROOT}/models" \
    "${BUNDLE_ROOT}/network"
}

write_note() {
  local outfile="$1"
  shift

  mkdir -p "$(dirname "${outfile}")"
  {
    echo "# Timestamp: $(timestamp)"
    printf '%s\n' "$@"
  } | redact_stream >"${outfile}"
}

capture_command() {
  local outfile="$1"
  shift

  mkdir -p "$(dirname "${outfile}")"
  {
    echo "# Command: $*"
    echo "# Timestamp: $(timestamp)"
    echo
    if "$@"; then
      :
    else
      local exit_code=$?
      echo
      echo "# Exit status: ${exit_code}"
    fi
  } 2>&1 | redact_stream >"${outfile}"
}

capture_shell() {
  local outfile="$1"
  local cmd="$2"

  mkdir -p "$(dirname "${outfile}")"
  {
    echo "# Command: ${cmd}"
    echo "# Timestamp: $(timestamp)"
    echo
    if bash -lc "${cmd}"; then
      :
    else
      local exit_code=$?
      echo
      echo "# Exit status: ${exit_code}"
    fi
  } 2>&1 | redact_stream >"${outfile}"
}

is_text_file() {
  local path="$1"
  if [[ ! -s "${path}" ]]; then
    return 0
  fi
  grep -Iq . "${path}"
}

copy_file_redacted() {
  local source_path="$1"
  local dest_path="$2"

  mkdir -p "$(dirname "${dest_path}")"

  if is_text_file "${source_path}"; then
    redact_stream <"${source_path}" >"${dest_path}"
  else
    cp -p "${source_path}" "${dest_path}"
  fi
}

copy_tree_redacted() {
  local source_dir="$1"
  local dest_dir="$2"

  mkdir -p "${dest_dir}"

  if [[ ! -e "${source_dir}" ]]; then
    write_note "${dest_dir}/_missing.txt" "Path not found: ${source_dir}"
    return
  fi

  while IFS= read -r -d '' path; do
    local rel_path="${path#"${source_dir}"/}"
    local dest_path="${dest_dir}/${rel_path}"

    if [[ -L "${path}" ]]; then
      local link_target
      link_target="$(readlink "${path}" 2>/dev/null || true)"
      write_note "${dest_path}.symlink.txt" "Symlink target: ${link_target}"
      continue
    fi

    if [[ -d "${path}" ]]; then
      mkdir -p "${dest_path}"
      continue
    fi

    copy_file_redacted "${path}" "${dest_path}"
  done < <(find "${source_dir}" -mindepth 1 -print0)
}

docker_available() {
  command -v docker >/dev/null 2>&1
}

docker_cmd() {
  if docker info >/dev/null 2>&1; then
    env "MOLTBOX_RUNTIME_ROOT=${RUNTIME_ROOT}" docker "$@"
    return
  fi

  sudo env "MOLTBOX_RUNTIME_ROOT=${RUNTIME_ROOT}" docker "$@"
}

compose_available() {
  docker_available && [[ -f "${COMPOSE_FILE}" ]] && [[ -f "${RUNTIME_ENV_FILE}" ]]
}

compose_cmd() {
  docker_cmd compose --env-file "${RUNTIME_ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
}

capture_docker_command() {
  local outfile="$1"
  shift

  if ! docker_available; then
    write_note "${outfile}" "Docker CLI not found on host."
    return
  fi

  capture_command "${outfile}" docker_cmd "$@"
}

capture_compose_command() {
  local outfile="$1"
  shift

  if ! compose_available; then
    write_note "${outfile}" "Docker compose context unavailable (missing docker CLI, compose file, or runtime env file)."
    return
  fi

  capture_command "${outfile}" compose_cmd "$@"
}

capture_docker_diagnostics() {
  if ! docker_available; then
    write_note "${BUNDLE_ROOT}/docker/_docker-unavailable.txt" "Docker CLI not found on host."
    write_note "${BUNDLE_ROOT}/logs/_docker-unavailable.txt" "Container logs not collected because Docker CLI was not found."
    write_note "${BUNDLE_ROOT}/models/_docker-unavailable.txt" "Model diagnostics not collected because Docker CLI was not found."
    write_note "${BUNDLE_ROOT}/network/_docker-unavailable.txt" "Network diagnostics not collected because Docker CLI was not found."
    return
  fi

  capture_command "${BUNDLE_ROOT}/docker/docker-ps-a.txt" docker_cmd ps -a
  capture_command "${BUNDLE_ROOT}/docker/docker-images.txt" docker_cmd images
  capture_command "${BUNDLE_ROOT}/docker/docker-volume-ls.txt" docker_cmd volume ls
  capture_command "${BUNDLE_ROOT}/docker/docker-volume-inspect.txt" docker_cmd volume inspect moltbox_ollama_data moltbox_opensearch_data
  capture_command "${BUNDLE_ROOT}/docker/docker-network-ls.txt" docker_cmd network ls
  capture_command "${BUNDLE_ROOT}/docker/docker-info.txt" docker_cmd info
  capture_compose_command "${BUNDLE_ROOT}/docker/compose-ps.txt" ps
  capture_compose_command "${BUNDLE_ROOT}/docker/compose-config.txt" config
}

capture_system_diagnostics() {
  capture_command "${BUNDLE_ROOT}/system/uname-a.txt" uname -a
  capture_shell "${BUNDLE_ROOT}/system/os-release.txt" "cat /etc/os-release"
  capture_command "${BUNDLE_ROOT}/system/uptime.txt" uptime
  capture_command "${BUNDLE_ROOT}/system/df-h.txt" df -h
  capture_command "${BUNDLE_ROOT}/system/free-h.txt" free -h
  capture_command "${BUNDLE_ROOT}/system/ip-addr.txt" ip addr
  capture_command "${BUNDLE_ROOT}/system/ip-route.txt" ip route
  capture_command "${BUNDLE_ROOT}/system/ss-tulpn.txt" ss -tulpn
  capture_command "${BUNDLE_ROOT}/system/ps-aux.txt" ps aux
  capture_command "${BUNDLE_ROOT}/system/sysctl-vm-max-map-count.txt" sysctl vm.max_map_count
  capture_command "${BUNDLE_ROOT}/system/docker-compose-version.txt" docker compose version
  capture_command "${BUNDLE_ROOT}/system/nvidia-smi.txt" nvidia-smi

  write_note \
    "${BUNDLE_ROOT}/system/collector-context.txt" \
    "Target user: ${TARGET_USER}" \
    "Target home: ${TARGET_HOME}" \
    "Runtime root: ${RUNTIME_ROOT}" \
    "Runtime env file: ${RUNTIME_ENV_FILE}" \
    "Repository config source: ${REPO_CONFIG_SOURCE}" \
    "Compose file: ${COMPOSE_FILE}" \
    "Script directory: ${SCRIPT_DIR}"
}

capture_container_diagnostics() {
  local container_name

  for container_name in moltbox-openclaw moltbox-ollama moltbox-opensearch; do
    capture_docker_command \
      "${BUNDLE_ROOT}/docker/${container_name}-inspect.json" \
      inspect "${container_name}"

    capture_docker_command \
      "${BUNDLE_ROOT}/logs/${container_name}.log" \
      logs "${container_name}"

    capture_docker_command \
      "${BUNDLE_ROOT}/docker/${container_name}-top.txt" \
      top "${container_name}"

    capture_docker_command \
      "${BUNDLE_ROOT}/network/${container_name}-inspect.json" \
      inspect "${container_name}"

    capture_docker_command \
      "${BUNDLE_ROOT}/docker/${container_name}-env.txt" \
      exec "${container_name}" sh -lc 'env | sort'

    capture_docker_command \
      "${BUNDLE_ROOT}/docker/${container_name}-mounts.txt" \
      inspect -f '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}' "${container_name}"
  done
}

capture_runtime_configuration() {
  copy_tree_redacted "${RUNTIME_ROOT}" "${BUNDLE_ROOT}/runtime"
  copy_tree_redacted "${REPO_CONFIG_SOURCE}" "${BUNDLE_ROOT}/config"
}

capture_model_diagnostics() {
  capture_docker_command \
    "${BUNDLE_ROOT}/models/openclaw-doctor.txt" \
    exec "moltbox-openclaw" openclaw doctor

  capture_docker_command \
    "${BUNDLE_ROOT}/models/openclaw-models-list.txt" \
    exec "moltbox-openclaw" openclaw models list --all --json

  capture_docker_command \
    "${BUNDLE_ROOT}/models/openclaw-models-status.txt" \
    exec "moltbox-openclaw" openclaw models status --json

  capture_docker_command \
    "${BUNDLE_ROOT}/models/openclaw-models-status-probe.txt" \
    exec "moltbox-openclaw" openclaw models status --probe --probe-provider together --probe-concurrency 1 --probe-timeout 10000 --probe-max-tokens 8

  capture_docker_command \
    "${BUNDLE_ROOT}/models/openclaw-agents-list.txt" \
    exec "moltbox-openclaw" openclaw agents list

  capture_docker_command \
    "${BUNDLE_ROOT}/models/openclaw-config-file.txt" \
    exec "moltbox-openclaw" openclaw config file

  capture_docker_command \
    "${BUNDLE_ROOT}/models/openclaw-config-primary-model.txt" \
    exec "moltbox-openclaw" openclaw config get agents.defaults.model.primary

  capture_docker_command \
    "${BUNDLE_ROOT}/models/openclaw-config-gateway-mode.txt" \
    exec "moltbox-openclaw" openclaw config get gateway.mode

  capture_docker_command \
    "${BUNDLE_ROOT}/models/openclaw-config-gateway-bind.txt" \
    exec "moltbox-openclaw" openclaw config get gateway.bind

  capture_docker_command \
    "${BUNDLE_ROOT}/models/openclaw-config-gateway-auth-mode.txt" \
    exec "moltbox-openclaw" openclaw config get gateway.auth.mode

  capture_docker_command \
    "${BUNDLE_ROOT}/models/openclaw-config-ollama-provider.txt" \
    exec "moltbox-openclaw" openclaw config get models.providers.ollama

  capture_docker_command \
    "${BUNDLE_ROOT}/models/openclaw-config-together-fallback.txt" \
    exec "moltbox-openclaw" openclaw config get agents.defaults.model.fallbacks[0]

  capture_docker_command \
    "${BUNDLE_ROOT}/models/ollama-tags.json" \
    exec "moltbox-ollama" curl -fsS http://localhost:11434/api/tags

  capture_docker_command \
    "${BUNDLE_ROOT}/models/ollama-list.txt" \
    exec "moltbox-ollama" ollama list

  capture_docker_command \
    "${BUNDLE_ROOT}/models/opensearch-cluster-health-from-opensearch.txt" \
    exec "moltbox-opensearch" curl -fsS http://localhost:9200/_cluster/health

  capture_docker_command \
    "${BUNDLE_ROOT}/models/opensearch-cluster-health-from-openclaw.txt" \
    exec "moltbox-openclaw" node -e "const http=require('http');http.get('http://opensearch:9200/_cluster/health',r=>{r.pipe(process.stdout);r.on('end',()=>process.exit(r.statusCode&&r.statusCode<400?0:1));}).on('error',e=>{console.error(e.message);process.exit(1);});"
}

create_archive() {
  while [[ -e "${ARCHIVE_PATH}" ]]; do
    BUNDLE_TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
    ARCHIVE_PATH="/tmp/moltbox-debug-${BUNDLE_TIMESTAMP}.tar.gz"
    sleep 1
  done
  tar -C "${WORK_DIR}" -czf "${ARCHIVE_PATH}" debug-bundle
}

print_completion() {
  log_info "Debug bundle archive: ${ARCHIVE_PATH}"
  echo "${ARCHIVE_PATH}"
  echo "scp ${TARGET_USER}@${DOWNLOAD_HOST}:${ARCHIVE_PATH} ."
}

main() {
  require_host_cmd tar
  require_host_cmd mktemp
  require_host_cmd find
  require_host_cmd perl
  ensure_bundle_dirs

  log_info "Collecting Moltbox diagnostics bundle."
  capture_system_diagnostics
  capture_docker_diagnostics
  capture_container_diagnostics
  capture_runtime_configuration
  capture_model_diagnostics
  create_archive
  print_completion
}

main "$@"
