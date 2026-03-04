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

docker_cmd() {
  if docker info >/dev/null 2>&1; then
    docker "$@"
  else
    sudo docker "$@"
  fi
}

require_file() {
  local path="$1"
  if [[ ! -f "${path}" ]]; then
    log_error "Required file not found: ${path}"
    exit 1
  fi
}

require_host_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    log_error "Required command not found on host: ${cmd}"
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
  require_key "${ENV_FILE}" "OPENCLAW_GATEWAY_BIND"
  require_key "${ENV_FILE}" "OPENCLAW_GATEWAY_TOKEN"
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

ensure_gateway_token() {
  local token_value
  token_value="$(grep -E '^OPENCLAW_GATEWAY_TOKEN=' "${ENV_FILE}" | head -n1 | cut -d= -f2-)"
  if [[ -z "${token_value}" || "${token_value}" == "CHANGE_ME_STRONG_TOKEN" ]]; then
    local generated_token=""
    if command -v openssl >/dev/null 2>&1; then
      generated_token="$(openssl rand -hex 24)"
    else
      generated_token="$(date +%s%N | sha256sum | cut -c1-48)"
    fi

    sed -i "s|^OPENCLAW_GATEWAY_TOKEN=.*$|OPENCLAW_GATEWAY_TOKEN=${generated_token}|" "${ENV_FILE}"
    log_warn "OPENCLAW_GATEWAY_TOKEN was unset/placeholder. Generated and saved a strong token in ${ENV_FILE}."
  fi
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
  docker_cmd compose -f "${COMPOSE_FILE}" "$@"
}

ensure_openclaw_image_available() {
  local image="${OPENCLAW_IMAGE}"
  if docker_cmd image inspect "${image}" >/dev/null 2>&1; then
    log_info "OpenClaw image already available locally: ${image}"
    return
  fi

  if [[ "${image}" == "openclaw:local" ]]; then
    log_error "OPENCLAW_IMAGE is '${image}', but that image is not present locally."
    log_error "Build it first from the OpenClaw repository, then rerun bootstrap."
    log_error "Example:"
    log_error "  git clone https://github.com/openclaw/openclaw.git"
    log_error "  cd openclaw"
    log_error "  docker build -t openclaw:local ."
    exit 1
  fi

  log_info "Pulling OpenClaw image: ${image}"
  compose pull openclaw
}

pull_images() {
  # Ollama/OpenSearch are always registry-based in this profile.
  log_info "Pulling Ollama and OpenSearch images."
  compose pull ollama opensearch
  ensure_openclaw_image_available
}

bring_up_stack() {
  pull_images

  log_info "Starting Moltbox stack."
  compose up -d
}

wait_for_ollama() {
  local sleep_seconds="${BOOTSTRAP_WAIT_INTERVAL_SECONDS:-2}"
  local wait_seconds="${BOOTSTRAP_OLLAMA_WAIT_SECONDS:-60}"
  local max_attempts=$(( (wait_seconds + sleep_seconds - 1) / sleep_seconds ))
  local attempt=1

  log_info "Waiting for Ollama service readiness (timeout: ${wait_seconds}s)."
  while (( attempt <= max_attempts )); do
    # Use native CLI check to avoid assuming curl exists in the image.
    if compose exec -T ollama ollama list >/dev/null 2>&1; then
      log_info "Ollama is ready."
      return 0
    fi
    log_warn "Ollama not ready yet (attempt ${attempt}/${max_attempts})."
    sleep "${sleep_seconds}"
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

wait_for_gateway() {
  local port="${GATEWAY_PORT:-18789}"
  local sleep_seconds="${BOOTSTRAP_WAIT_INTERVAL_SECONDS:-2}"
  local wait_seconds="${BOOTSTRAP_GATEWAY_WAIT_SECONDS:-80}"
  local max_attempts=$(( (wait_seconds + sleep_seconds - 1) / sleep_seconds ))
  local attempt=1

  log_info "Waiting for OpenClaw gateway health on http://127.0.0.1:${port}/healthz (timeout: ${wait_seconds}s)"
  while (( attempt <= max_attempts )); do
    if curl -fsS "http://127.0.0.1:${port}/healthz" >/dev/null 2>&1; then
      log_info "OpenClaw gateway is healthy."
      return 0
    fi
    log_warn "Gateway not ready yet (attempt ${attempt}/${max_attempts})."
    sleep "${sleep_seconds}"
    attempt=$((attempt + 1))
  done

  log_error "Gateway did not become healthy on port ${port}."
  return 1
}

main() {
  require_host_cmd docker
  require_host_cmd curl
  require_file "${COMPOSE_FILE}"
  ensure_env_files
  validate_required_keys
  ensure_gateway_token
  load_env

  bring_up_stack
  wait_for_ollama
  prepull_model
  wait_for_gateway

  log_info "Gateway token for first login: ${OPENCLAW_GATEWAY_TOKEN}"
  log_info "Open browser on your LAN: http://<MOLTBOX_HOST>:${GATEWAY_PORT:-18789}"
  log_info "Bootstrap complete."
}

main "$@"
