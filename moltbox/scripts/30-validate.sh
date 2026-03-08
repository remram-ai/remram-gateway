#!/usr/bin/env bash
set -Eeuo pipefail

# Moltbox validation script.
# Verifies service health, gateway readiness, internal connectivity, and exposure policy.

timestamp() { date +"%Y-%m-%dT%H:%M:%S%z"; }
log_info() { echo "[$(timestamp)] [INFO] $*"; }
log_warn() { echo "[$(timestamp)] [WARN] $*" >&2; }
log_error() { echo "[$(timestamp)] [ERROR] $*" >&2; }

trap 'log_error "Validation failed near line ${BASH_LINENO[0]}."' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOLTBOX_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_DIR="${MOLTBOX_DIR}/config"
COMPOSE_FILE="${CONFIG_DIR}/docker-compose.yml"
OPENCLAW_REDACTED_SECRET="__OPENCLAW_REDACTED__"

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
RUNTIME_ENV_FILE="${RUNTIME_ROOT}/.env"
RUNTIME_CONTAINER_ENV_FILE="${RUNTIME_ROOT}/container.env"
RUNTIME_AGENT_DIR="${RUNTIME_ROOT}/agents/main/agent"
RUNTIME_MODELS_FILE="${RUNTIME_AGENT_DIR}/models.json"
RUNTIME_AUTH_PROFILES_FILE="${RUNTIME_AGENT_DIR}/auth-profiles.json"
RUNTIME_AGENT_CONFIG_FILE="${RUNTIME_AGENT_DIR}/agent-config.json"

docker_cmd() {
  if docker info >/dev/null 2>&1; then
    env "MOLTBOX_RUNTIME_ROOT=${RUNTIME_ROOT}" docker "$@"
  else
    sudo env "MOLTBOX_RUNTIME_ROOT=${RUNTIME_ROOT}" docker "$@"
  fi
}

compose() {
  docker_cmd compose --env-file "${RUNTIME_ENV_FILE}" -f "${COMPOSE_FILE}" "$@"
}

openclaw_exec() {
  compose exec -T openclaw openclaw "$@"
}

ollama_exec() {
  compose exec -T ollama "$@"
}

require_host_cmd() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    log_error "Required command not found on host: ${cmd}"
    exit 1
  fi
}

require_runtime_files() {
  [[ -f "${COMPOSE_FILE}" ]] || { log_error "Missing compose file: ${COMPOSE_FILE}"; exit 1; }
  [[ -f "${RUNTIME_ENV_FILE}" ]] || { log_error "Missing runtime env file: ${RUNTIME_ENV_FILE}"; exit 1; }
  [[ -f "${RUNTIME_CONTAINER_ENV_FILE}" ]] || { log_error "Missing runtime container env file: ${RUNTIME_CONTAINER_ENV_FILE}"; exit 1; }
  [[ -f "${RUNTIME_ROOT}/openclaw.json" ]] || { log_error "Missing runtime config: ${RUNTIME_ROOT}/openclaw.json"; exit 1; }
  [[ -f "${RUNTIME_ROOT}/agents.yaml" ]] || { log_error "Missing runtime config: ${RUNTIME_ROOT}/agents.yaml"; exit 1; }
  [[ -f "${RUNTIME_ROOT}/routing.yaml" ]] || { log_error "Missing runtime config: ${RUNTIME_ROOT}/routing.yaml"; exit 1; }
  [[ -f "${RUNTIME_ROOT}/tools.yaml" ]] || { log_error "Missing runtime config: ${RUNTIME_ROOT}/tools.yaml"; exit 1; }
  [[ -f "${RUNTIME_ROOT}/escalation.yaml" ]] || { log_error "Missing runtime config: ${RUNTIME_ROOT}/escalation.yaml"; exit 1; }
  [[ -f "${RUNTIME_ROOT}/channels.yaml" ]] || { log_error "Missing runtime config: ${RUNTIME_ROOT}/channels.yaml"; exit 1; }
  [[ -f "${RUNTIME_ROOT}/model-runtime.yml" ]] || { log_error "Missing runtime config: ${RUNTIME_ROOT}/model-runtime.yml"; exit 1; }
  [[ -f "${RUNTIME_ROOT}/opensearch.yml" ]] || { log_error "Missing runtime config: ${RUNTIME_ROOT}/opensearch.yml"; exit 1; }
  [[ -f "${RUNTIME_MODELS_FILE}" ]] || { log_error "Missing runtime config: ${RUNTIME_MODELS_FILE}"; exit 1; }
}

assert_file_not_empty_if_present() {
  local path="$1"
  local label="$2"
  if [[ -f "${path}" && ! -s "${path}" ]]; then
    log_error "${label} is zero bytes: ${path}"
    exit 1
  fi
}

assert_file_does_not_contain_invalid_seed() {
  local path="$1"
  local label="$2"
  if [[ -f "${path}" ]] && grep -Eq '"(local_routing_model|cloud_reasoning_model|deep_thinking_model|coding_model)"' "${path}"; then
    log_error "${label} contains deprecated Moltbox template keys instead of the OpenClaw schema: ${path}"
    exit 1
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

require_non_empty_env() {
  local key="$1"
  local value="${!key:-}"
  if [[ -z "${value//[[:space:]]/}" ]]; then
    log_error "Required runtime value is empty: ${key}"
    exit 1
  fi
}

validate_runtime_values() {
  require_non_empty_env "OPENCLAW_GATEWAY_BIND"
  require_non_empty_env "OPENCLAW_GATEWAY_TOKEN"
  require_non_empty_env "LOCAL_ROUTING_MODEL"
  require_non_empty_env "OLLAMA_BASE_URL"
  require_non_empty_env "OPENSEARCH_URL"
  require_non_empty_env "CLOUD_PROVIDER"
  require_non_empty_env "CLOUD_REASONING_MODEL"
  require_non_empty_env "TOGETHER_API_KEY"

  if [[ "${OPENCLAW_GATEWAY_BIND}" != "lan" ]]; then
    log_error "Expected OPENCLAW_GATEWAY_BIND=lan, found: ${OPENCLAW_GATEWAY_BIND}"
    exit 1
  fi

  if [[ "${CLOUD_PROVIDER}" != "together" ]]; then
    log_error "Expected CLOUD_PROVIDER=together, found: ${CLOUD_PROVIDER}"
    exit 1
  fi

  if [[ "${OLLAMA_BASE_URL}" != "http://ollama:11434" ]]; then
    log_error "OLLAMA_BASE_URL must be http://ollama:11434 for container DNS routing. Found: ${OLLAMA_BASE_URL}"
    exit 1
  fi

  if [[ "${OPENSEARCH_URL}" != "http://opensearch:9200" ]]; then
    log_error "OPENSEARCH_URL must be http://opensearch:9200 for container DNS routing. Found: ${OPENSEARCH_URL}"
    exit 1
  fi
}

ensure_service_running() {
  local service="$1"
  local cid
  cid="$(compose ps -q "${service}")"
  if [[ -z "${cid}" ]]; then
    log_error "Service '${service}' is not present."
    return 1
  fi
  local running
  running="$(docker_cmd inspect -f '{{.State.Running}}' "${cid}")"
  if [[ "${running}" != "true" ]]; then
    log_error "Service '${service}' is not running."
    return 1
  fi
}

ensure_service_healthy() {
  local service="$1"
  local cid
  cid="$(compose ps -q "${service}")"
  local health
  health="$(docker_cmd inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${cid}")"
  if [[ "${health}" != "healthy" ]]; then
    log_error "Service '${service}' health is '${health}', expected 'healthy'."
    return 1
  fi
}

check_gateway() {
  local port="${GATEWAY_PORT:-18789}"
  log_info "Checking gateway health/readiness endpoints on port ${port}."
  curl -fsS "http://127.0.0.1:${port}/healthz" >/dev/null
  curl -fsS "http://127.0.0.1:${port}/readyz" >/dev/null
}

check_internal_connectivity() {
  log_info "Checking OpenClaw -> Ollama connectivity."
  compose exec -T openclaw node -e "const http=require('http');http.get('${OLLAMA_BASE_URL}/api/tags',r=>{if(r.statusCode&&r.statusCode>=200&&r.statusCode<300){process.exit(0)}process.exit(1)}).on('error',()=>process.exit(1));"

  log_info "Checking OpenClaw -> OpenSearch connectivity."
  compose exec -T openclaw node -e "const http=require('http');http.get('${OPENSEARCH_URL}/_cluster/health',r=>{if(r.statusCode&&r.statusCode>=200&&r.statusCode<300){process.exit(0)}process.exit(1)}).on('error',()=>process.exit(1));"
}

assert_internal_only_ports() {
  log_info "Verifying Ollama/OpenSearch are not published on host."
  local ps_output
  ps_output="$(compose ps)"

  if grep -Eq '->11434/tcp|->9200/tcp' <<<"${ps_output}"; then
    log_error "Detected forbidden published port mapping for Ollama or OpenSearch."
    echo "${ps_output}" >&2
    return 1
  fi
}

check_agent_runtime_files() {
  assert_file_not_empty_if_present "${RUNTIME_MODELS_FILE}" "OpenClaw models.json"
  assert_file_not_empty_if_present "${RUNTIME_AUTH_PROFILES_FILE}" "OpenClaw auth-profiles.json"
  assert_file_not_empty_if_present "${RUNTIME_AGENT_CONFIG_FILE}" "OpenClaw agent-config.json"
  assert_file_does_not_contain_invalid_seed "${RUNTIME_MODELS_FILE}" "OpenClaw models.json"
}

check_ollama_model_installed() {
  local model_list=""

  log_info "Checking Ollama model inventory for ${LOCAL_ROUTING_MODEL}."
  if ! model_list="$(ollama_exec ollama list 2>&1)"; then
    log_error "Failed to read Ollama model inventory."
    printf '%s\n' "${model_list}" >&2
    exit 1
  fi

  if ! grep -Eq "^${LOCAL_ROUTING_MODEL}[[:space:]]" <<<"${model_list}"; then
    log_error "Ollama does not contain the required local routing model: ${LOCAL_ROUTING_MODEL}"
    printf '%s\n' "${model_list}" >&2
    exit 1
  fi
}

check_openclaw_config_value() {
  local path="$1"
  local expected="$2"
  local allow_redacted="${3:-false}"
  local actual=""

  if ! actual="$(openclaw_exec config get "${path}" 2>/dev/null)"; then
    log_error "Failed to read OpenClaw config path: ${path}"
    exit 1
  fi

  if [[ "${allow_redacted}" == "true" && "${actual}" == "${OPENCLAW_REDACTED_SECRET}" ]]; then
    log_info "Skipping drift validation for redacted secret"
    return 0
  fi

  if [[ "${actual}" != "${expected}" ]]; then
    log_error "OpenClaw config drift at ${path}: expected '${expected}', got '${actual}'."
    exit 1
  fi
}

check_openclaw_runtime_mounts() {
  local cid
  local mounts=""

  cid="$(compose ps -q openclaw)"
  mounts="$(docker_cmd inspect -f '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}' "${cid}")"

  grep -F "${RUNTIME_ROOT} -> /home/node/.openclaw" <<<"${mounts}" >/dev/null || {
    log_error "OpenClaw container is not mounted from the runtime root ${RUNTIME_ROOT}."
    printf '%s\n' "${mounts}" >&2
    exit 1
  }
}

check_openclaw_runtime_config() {
  log_info "Validating OpenClaw provider configuration"
  check_openclaw_config_value "gateway.mode" "local"
  check_openclaw_config_value "models.providers.ollama.apiKey" "ollama-local" "true"
  check_openclaw_config_value "models.providers.ollama.baseUrl" "${OLLAMA_BASE_URL}"
  check_openclaw_config_value "models.providers.ollama.api" "ollama"
  check_openclaw_config_value "agents.defaults.model.primary" "ollama/${LOCAL_ROUTING_MODEL}"
  check_openclaw_config_value "agents.defaults.model.fallbacks[0]" "together/${CLOUD_REASONING_MODEL}"
}

check_provider_auth() {
  log_info "Checking configured provider auth state."
  openclaw_exec models status --check >/dev/null

  log_info "Probing Together provider auth and reachability."
  openclaw_exec models status --probe --probe-provider together --probe-concurrency 1 --probe-timeout 10000 --probe-max-tokens 8 >/dev/null
}

check_ollama_model_registry() {
  local model_ref="ollama/${LOCAL_ROUTING_MODEL}"
  local model_list=""

  log_info "Checking Ollama discovery in the OpenClaw model registry for ${model_ref}."
  if ! model_list="$(openclaw_exec models list --all --provider ollama 2>&1)"; then
    log_error "Failed to read OpenClaw model registry."
    printf '%s\n' "${model_list}" >&2
    exit 1
  fi

  printf '%s\n' "${model_list}" | grep -F "${model_ref}" >/dev/null || {
    log_error "OpenClaw model registry does not contain any Ollama models. Provider configuration failed."
    printf '%s\n' "${model_list}" >&2
    exit 1
  }

  if ! awk -v ref="${model_ref}" '
    $1 == ref {
      found = 1
      if ($0 ~ /missing/) {
        missing = 1
      }
    }
    END {
      exit(found == 1 && missing != 1 ? 0 : 1)
    }
  ' <<<"${model_list}"; then
    log_error "OpenClaw model registry did not register ${model_ref}. Ollama provider configuration or discovery failed."
    printf '%s\n' "${model_list}" >&2
    exit 1
  fi
}

main() {
  require_host_cmd docker
  require_host_cmd curl
  require_runtime_files
  load_env
  validate_runtime_values

  log_info "Validating compose service status using runtime root ${RUNTIME_ROOT}."
  compose ps

  ensure_service_running "openclaw"
  ensure_service_running "ollama"
  ensure_service_running "opensearch"

  ensure_service_healthy "openclaw"
  ensure_service_healthy "ollama"
  ensure_service_healthy "opensearch"

  check_gateway
  check_internal_connectivity
  check_openclaw_runtime_mounts
  check_openclaw_runtime_config
  check_agent_runtime_files
  check_ollama_model_installed
  check_ollama_model_registry
  check_provider_auth
  assert_internal_only_ports

  log_info "Validation passed."
}

main "$@"
