#!/usr/bin/env bash
set -euo pipefail

# Moltbox bootstrap routine.
# Creates runtime files under ~/.openclaw, starts the stack, and pre-pulls the local routing model.

timestamp() { date +"%Y-%m-%dT%H:%M:%S%z"; }
log_info() { echo "[$(timestamp)] [INFO] $*"; }
log_warn() { echo "[$(timestamp)] [WARN] $*" >&2; }
log_error() { echo "[$(timestamp)] [ERROR] $*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOLTBOX_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${MOLTBOX_DIR}/.." && pwd)"
CONFIG_DIR="${MOLTBOX_DIR}/config"
OPENCLAW_TEMPLATE_DIR="${MOLTBOX_DIR}/.openclaw"
COMPOSE_FILE="${CONFIG_DIR}/docker-compose.yml"
OPENCLAW_CONFIG_TEMPLATE="${CONFIG_DIR}/openclaw.json"
ENV_TEMPLATE="${CONFIG_DIR}/.env.example"
CONTAINER_ENV_TEMPLATE="${CONFIG_DIR}/container.env.example"
MODEL_RUNTIME_TEMPLATE="${CONFIG_DIR}/model-runtime.yml"
OPENSEARCH_TEMPLATE="${CONFIG_DIR}/opensearch.yml"
MODELS_TEMPLATE="${CONFIG_DIR}/models.json"

resolve_runtime_root() {
  local target_user="${SUDO_USER:-${USER}}"
  local target_home=""

  if command -v getent >/dev/null 2>&1; then
    target_home="$(getent passwd "${target_user}" | cut -d: -f6)"
  fi

  if [[ -z "${target_home}" ]]; then
    target_home="${HOME}"
  fi

  printf '%s\n' "${MOLTBOX_RUNTIME_ROOT:-${target_home}/.openclaw}"
}

RUNTIME_ROOT="$(resolve_runtime_root)"
USER_HOME="$(getent passwd "${SUDO_USER:-${USER}}" | cut -d: -f6 2>/dev/null || printf '%s\n' "${HOME}")"
RUNTIME_ENV_FILE="${RUNTIME_ROOT}/.env"
RUNTIME_OPENCLAW_CONFIG_FILE="${RUNTIME_ROOT}/openclaw.json"
RUNTIME_CONTAINER_ENV_FILE="${RUNTIME_ROOT}/container.env"
RUNTIME_MODEL_RUNTIME_FILE="${RUNTIME_ROOT}/model-runtime.yml"
RUNTIME_OPENSEARCH_FILE="${RUNTIME_ROOT}/opensearch.yml"
RUNTIME_MODELS_FILE="${RUNTIME_ROOT}/agents/main/agent/models.json"
GIT_WORKSPACE_ROOT="${USER_HOME}/git"

docker_cmd() {
  if docker info >/dev/null 2>&1; then
    env "MOLTBOX_RUNTIME_ROOT=${RUNTIME_ROOT}" docker "$@"
  else
    sudo env "MOLTBOX_RUNTIME_ROOT=${RUNTIME_ROOT}" docker "$@"
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

display_path() {
  local path="$1"
  if [[ "${path}" == "${RUNTIME_ROOT}"* ]]; then
    printf '%s\n' "~/.openclaw${path#"${RUNTIME_ROOT}"}"
    return
  fi
  printf '%s\n' "${path}"
}

copy_if_missing() {
  local source_path="$1"
  local dest_path="$2"
  local display_dest
  display_dest="$(display_path "${dest_path}")"

  require_file "${source_path}"
  mkdir -p "$(dirname "${dest_path}")"

  if [[ ! -f "${dest_path}" ]]; then
    log_info "Creating runtime config: ${display_dest}"
    cp "${source_path}" "${dest_path}"
  else
    log_info "Skipping existing runtime config: ${display_dest}"
  fi
}

enforce_runtime_outside_git_workspace() {
  if [[ "${RUNTIME_ROOT}" == "${GIT_WORKSPACE_ROOT}"* ]]; then
    log_error "Runtime root cannot be inside ~/git workspace: ${RUNTIME_ROOT}"
    log_error "Set MOLTBOX_RUNTIME_ROOT outside the git directory."
    exit 1
  fi
}

ensure_runtime_dirs() {
  mkdir -p "${RUNTIME_ROOT}"
  mkdir -p "${RUNTIME_ROOT}/agents/main/agent"
  mkdir -p "${RUNTIME_ROOT}/logs"
}

ensure_runtime_templates() {
  ensure_runtime_dirs

  copy_if_missing "${OPENCLAW_CONFIG_TEMPLATE}" "${RUNTIME_OPENCLAW_CONFIG_FILE}"
  copy_if_missing "${ENV_TEMPLATE}" "${RUNTIME_ENV_FILE}"
  copy_if_missing "${CONTAINER_ENV_TEMPLATE}" "${RUNTIME_CONTAINER_ENV_FILE}"
  copy_if_missing "${MODEL_RUNTIME_TEMPLATE}" "${RUNTIME_MODEL_RUNTIME_FILE}"
  copy_if_missing "${OPENSEARCH_TEMPLATE}" "${RUNTIME_OPENSEARCH_FILE}"
  copy_if_missing "${OPENCLAW_TEMPLATE_DIR}/agents.yaml" "${RUNTIME_ROOT}/agents.yaml"
  copy_if_missing "${OPENCLAW_TEMPLATE_DIR}/channels.yaml" "${RUNTIME_ROOT}/channels.yaml"
  copy_if_missing "${OPENCLAW_TEMPLATE_DIR}/routing.yaml" "${RUNTIME_ROOT}/routing.yaml"
  copy_if_missing "${OPENCLAW_TEMPLATE_DIR}/tools.yaml" "${RUNTIME_ROOT}/tools.yaml"
  copy_if_missing "${OPENCLAW_TEMPLATE_DIR}/escalation.yaml" "${RUNTIME_ROOT}/escalation.yaml"
  copy_if_missing "${MODELS_TEMPLATE}" "${RUNTIME_MODELS_FILE}"
}

ensure_gateway_mode_local() {
  local tmp_file
  tmp_file="$(mktemp)"

  awk '
    BEGIN { seen=0 }
    /^OPENCLAW_GATEWAY_MODE=/ {
      if (seen == 0) {
        print "OPENCLAW_GATEWAY_MODE=local"
        seen=1
      }
      next
    }
    { print }
    END {
      if (seen == 0) {
        print "OPENCLAW_GATEWAY_MODE=local"
      }
    }
  ' "${RUNTIME_ENV_FILE}" > "${tmp_file}"

  mv "${tmp_file}" "${RUNTIME_ENV_FILE}"
  log_info "Ensured runtime config: $(display_path "${RUNTIME_ENV_FILE}") contains OPENCLAW_GATEWAY_MODE=local"
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
  require_key "${RUNTIME_ENV_FILE}" "OPENCLAW_IMAGE"
  require_key "${RUNTIME_ENV_FILE}" "OPENCLAW_GATEWAY_BIND"
  require_key "${RUNTIME_ENV_FILE}" "OPENCLAW_GATEWAY_TOKEN"
  require_key "${RUNTIME_ENV_FILE}" "OLLAMA_IMAGE"
  require_key "${RUNTIME_ENV_FILE}" "OPENSEARCH_IMAGE"
  require_key "${RUNTIME_ENV_FILE}" "GATEWAY_PORT"
  require_key "${RUNTIME_ENV_FILE}" "ESCALATION_MAX_TOKENS"
  require_key "${RUNTIME_ENV_FILE}" "ESCALATION_DAILY_USD_CAP"
  require_key "${RUNTIME_CONTAINER_ENV_FILE}" "LOCAL_ROUTING_MODEL"
  require_key "${RUNTIME_CONTAINER_ENV_FILE}" "OLLAMA_BASE_URL"
  require_key "${RUNTIME_CONTAINER_ENV_FILE}" "OPENSEARCH_URL"
  require_key "${RUNTIME_CONTAINER_ENV_FILE}" "CLOUD_PROVIDER"
}

ensure_gateway_token() {
  local token_value
  token_value="$(grep -E '^OPENCLAW_GATEWAY_TOKEN=' "${RUNTIME_ENV_FILE}" | head -n1 | cut -d= -f2-)"
  if [[ -z "${token_value}" || "${token_value}" == "CHANGE_ME_STRONG_TOKEN" ]]; then
    local generated_token=""
    if command -v openssl >/dev/null 2>&1; then
      generated_token="$(openssl rand -hex 24)"
    else
      generated_token="$(date +%s%N | sha256sum | cut -c1-48)"
    fi

    sed -i "s|^OPENCLAW_GATEWAY_TOKEN=.*$|OPENCLAW_GATEWAY_TOKEN=${generated_token}|" "${RUNTIME_ENV_FILE}"
    log_warn "OPENCLAW_GATEWAY_TOKEN was unset/placeholder. Generated and saved a strong token in ${RUNTIME_ENV_FILE}."
  fi
}

load_env() {
  # shellcheck disable=SC1090
  set -a
  source "${RUNTIME_ENV_FILE}"
  # shellcheck disable=SC1090
  source "${RUNTIME_CONTAINER_ENV_FILE}"
  set +a
}

compose() {
  docker_cmd compose --env-file "${RUNTIME_ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
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

  log_info "Starting Moltbox stack using runtime config from ${RUNTIME_ROOT}."
  compose up -d --force-recreate
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
  enforce_runtime_outside_git_workspace
  log_info "Repository root: ${REPO_ROOT}"
  log_info "Runtime root: ${RUNTIME_ROOT}"
  ensure_runtime_templates
  ensure_gateway_mode_local
  validate_required_keys
  ensure_gateway_token
  load_env

  bring_up_stack
  wait_for_ollama
  prepull_model
  wait_for_gateway

  log_info "Runtime root: ${RUNTIME_ROOT}"
  log_info "Gateway token for first login: ${OPENCLAW_GATEWAY_TOKEN}"
  log_info "Open browser on your LAN: http://<MOLTBOX_HOST>:${GATEWAY_PORT:-18789}"
  log_info "Bootstrap complete."
}

main "$@"
