#!/usr/bin/env bash
set -euo pipefail

# Moltbox bootstrap routine.
# Creates runtime env files, starts the stack, and pre-pulls the local routing model.

timestamp() { date +"%Y-%m-%dT%H:%M:%S%z"; }
log_info() { echo "[$(timestamp)] [INFO] $*"; }
log_warn() { echo "[$(timestamp)] [WARN] $*" >&2; }
log_error() { echo "[$(timestamp)] [ERROR] $*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOLTBOX_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_DIR="${MOLTBOX_DIR}/config"
COMPOSE_FILE="${CONFIG_DIR}/docker-compose.yml"
ENV_FILE="${CONFIG_DIR}/.env"
ENV_EXAMPLE="${CONFIG_DIR}/.env.example"
CONTAINER_ENV_FILE="${CONFIG_DIR}/container.env"
CONTAINER_ENV_EXAMPLE="${CONFIG_DIR}/container.env.example"

require_file() {
  local path="$1"
  if [[ ! -f "${path}" ]]; then
    log_error "Required file not found: ${path}"
    exit 1
  fi
}

ensure_env_files() {
  require_file "${ENV_EXAMPLE}"
  require_file "${CONTAINER_ENV_EXAMPLE}"

  if [[ ! -f "${ENV_FILE}" ]]; then
    cp "${ENV_EXAMPLE}" "${ENV_FILE}"
    log_info "Created ${ENV_FILE} from example."
  else
    log_info "Using existing ${ENV_FILE}."
  fi

  if [[ ! -f "${CONTAINER_ENV_FILE}" ]]; then
    cp "${CONTAINER_ENV_EXAMPLE}" "${CONTAINER_ENV_FILE}"
    log_info "Created ${CONTAINER_ENV_FILE} from example."
  else
    log_info "Using existing ${CONTAINER_ENV_FILE}."
  fi
}

require_key() {
  local file="$1"
  local key="$2"
  if ! grep -Eq "^${key}=" "${file}"; then
    log_error "Missing key '${key}' in ${file}"
    exit 1
  fi
}

validate_required_keys() {
  require_key "${ENV_FILE}" "OPENCLAW_IMAGE"
  require_key "${ENV_FILE}" "OLLAMA_IMAGE"
  require_key "${ENV_FILE}" "OPENSEARCH_IMAGE"
  require_key "${ENV_FILE}" "GATEWAY_PORT"
  require_key "${ENV_FILE}" "ESCALATION_MAX_TOKENS"
  require_key "${ENV_FILE}" "ESCALATION_DAILY_USD_CAP"
  require_key "${CONTAINER_ENV_FILE}" "LOCAL_ROUTING_MODEL"
  require_key "${CONTAINER_ENV_FILE}" "OLLAMA_BASE_URL"
  require_key "${CONTAINER_ENV_FILE}" "OPENSEARCH_URL"
  require_key "${CONTAINER_ENV_FILE}" "CLOUD_PROVIDER"
}

load_env() {
  # shellcheck disable=SC1090
  set -a
  source "${ENV_FILE}"
  # shellcheck disable=SC1090
  source "${CONTAINER_ENV_FILE}"
  set +a
}

compose() {
  docker compose -f "${COMPOSE_FILE}" "$@"
}

bring_up_stack() {
  log_info "Pulling container images."
  compose pull

  log_info "Starting Moltbox stack."
  compose up -d
}

wait_for_ollama() {
  local max_attempts=30
  local attempt=1

  log_info "Waiting for Ollama service readiness."
  while (( attempt <= max_attempts )); do
    # Use native CLI check to avoid assuming curl exists in the image.
    if compose exec -T ollama ollama list >/dev/null 2>&1; then
      log_info "Ollama is ready."
      return 0
    fi
    log_warn "Ollama not ready yet (attempt ${attempt}/${max_attempts})."
    sleep 2
    attempt=$((attempt + 1))
  done

  log_error "Ollama did not become ready."
  return 1
}

prepull_model() {
  local model="${LOCAL_ROUTING_MODEL:-qwen3:8b}"
  log_info "Pre-pulling local routing model: ${model}"
  compose exec -T ollama ollama pull "${model}"
}

main() {
  require_file "${COMPOSE_FILE}"
  ensure_env_files
  validate_required_keys
  load_env

  bring_up_stack
  wait_for_ollama
  prepull_model

  log_info "Bootstrap complete."
}

main "$@"
