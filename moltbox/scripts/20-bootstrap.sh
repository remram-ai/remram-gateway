#!/usr/bin/env bash
set -Eeuo pipefail

# Moltbox bootstrap routine.
# Creates runtime files under ~/.openclaw, starts the stack, and pre-pulls the local routing model.

timestamp() { date +"%Y-%m-%dT%H:%M:%S%z"; }
log_info() { echo "[$(timestamp)] [INFO] $*"; }
log_warn() { echo "[$(timestamp)] [WARN] $*" >&2; }
log_error() { echo "[$(timestamp)] [ERROR] $*" >&2; }

trap 'log_error "Bootstrap failed near line ${BASH_LINENO[0]}."' ERR

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
USER_HOME="$(getent passwd "${SUDO_USER:-${USER}}" | cut -d: -f6 2>/dev/null || printf '%s\n' "${HOME}")"
RUNTIME_ENV_FILE="${RUNTIME_ROOT}/.env"
RUNTIME_OPENCLAW_CONFIG_FILE="${RUNTIME_ROOT}/openclaw.json"
RUNTIME_CONTAINER_ENV_FILE="${RUNTIME_ROOT}/container.env"
RUNTIME_MODEL_RUNTIME_FILE="${RUNTIME_ROOT}/model-runtime.yml"
RUNTIME_OPENSEARCH_FILE="${RUNTIME_ROOT}/opensearch.yml"
RUNTIME_AGENT_DIR="${RUNTIME_ROOT}/agents/main/agent"
RUNTIME_MODELS_FILE="${RUNTIME_AGENT_DIR}/models.json"
RUNTIME_AUTH_PROFILES_FILE="${RUNTIME_AGENT_DIR}/auth-profiles.json"
RUNTIME_AGENT_CONFIG_FILE="${RUNTIME_AGENT_DIR}/agent-config.json"
GIT_WORKSPACE_ROOT="${USER_HOME}/git"

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

upsert_env_key() {
  local file="$1"
  local key="$2"
  local value="$3"
  local tmp_file

  tmp_file="$(mktemp)"

  awk -v key="${key}" -v value="${value}" '
    BEGIN { updated = 0 }
    $0 ~ ("^" key "=") {
      if (updated == 0) {
        print key "=" value
        updated = 1
      }
      next
    }
    { print }
    END {
      if (updated == 0) {
        print key "=" value
      }
    }
  ' "${file}" > "${tmp_file}"

  mv "${tmp_file}" "${file}"
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

ensure_runtime_root_safe() {
  if [[ -z "${RUNTIME_ROOT}" ]]; then
    log_error "Runtime root is empty."
    exit 1
  fi

  case "${RUNTIME_ROOT}" in
    "/"|"/home"|"/root"|"/tmp")
      log_error "Refusing unsafe runtime root: ${RUNTIME_ROOT}"
      exit 1
      ;;
  esac

  if [[ "${RUNTIME_ROOT}" == "${USER_HOME}" || "${RUNTIME_ROOT}" == "${REPO_ROOT}"* ]]; then
    log_error "Refusing unsafe runtime root: ${RUNTIME_ROOT}"
    exit 1
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
  mkdir -p "${RUNTIME_AGENT_DIR}"
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
}

ensure_runtime_env_defaults() {
  upsert_env_key "${RUNTIME_CONTAINER_ENV_FILE}" "OPENSEARCH_JAVA_OPTS" "\"-Xms2g -Xmx2g\""
  log_info "Ensured runtime config: $(display_path "${RUNTIME_CONTAINER_ENV_FILE}") contains OPENSEARCH_JAVA_OPTS=\"-Xms2g -Xmx2g\""
}

ensure_together_api_key() {
  local existing_value=""
  existing_value="$(grep -E '^TOGETHER_API_KEY=' "${RUNTIME_CONTAINER_ENV_FILE}" | head -n1 | cut -d= -f2- || true)"
  existing_value="${existing_value%$'\r'}"

  if [[ -n "${existing_value//[[:space:]]/}" ]]; then
    return 0
  fi

  local provided_value="${MOLTBOX_TOGETHER_API_KEY:-}"
  if [[ -n "${provided_value//[[:space:]]/}" ]]; then
    upsert_env_key "${RUNTIME_CONTAINER_ENV_FILE}" "TOGETHER_API_KEY" "${provided_value}"
    log_info "Saved Together API key from MOLTBOX_TOGETHER_API_KEY into $(display_path "${RUNTIME_CONTAINER_ENV_FILE}")"
    return 0
  fi

  if [[ ! -t 0 ]]; then
    log_error "TOGETHER_API_KEY is empty in $(display_path "${RUNTIME_CONTAINER_ENV_FILE}")."
    log_error "Set MOLTBOX_TOGETHER_API_KEY in the environment or rerun bootstrap interactively."
    exit 1
  fi

  local prompt_value=""
  while [[ -z "${prompt_value//[[:space:]]/}" ]]; do
    read -r -s -p "Enter Together API key (saved to $(display_path "${RUNTIME_CONTAINER_ENV_FILE}")): " prompt_value
    printf '\n'
    if [[ -z "${prompt_value//[[:space:]]/}" ]]; then
      log_warn "Together API key cannot be empty."
    fi
  done

  upsert_env_key "${RUNTIME_CONTAINER_ENV_FILE}" "TOGETHER_API_KEY" "${prompt_value}"
  log_info "Saved Together API key into $(display_path "${RUNTIME_CONTAINER_ENV_FILE}")"
}

remove_zero_length_file() {
  local path="$1"
  local label="$2"

  if [[ -f "${path}" && ! -s "${path}" ]]; then
    log_warn "Removing zero-byte ${label}: $(display_path "${path}")"
    rm -f "${path}"
  fi
}

remove_invalid_seeded_models_json() {
  if [[ ! -f "${RUNTIME_MODELS_FILE}" ]]; then
    return
  fi

  if grep -Eq '"(local_routing_model|cloud_reasoning_model|deep_thinking_model|coding_model)"' "${RUNTIME_MODELS_FILE}"; then
    local backup_path="${RUNTIME_MODELS_FILE}.moltbox-invalid-template.bak"
    if [[ ! -f "${backup_path}" ]]; then
      cp "${RUNTIME_MODELS_FILE}" "${backup_path}"
      log_warn "Backed up invalid seeded models.json to $(display_path "${backup_path}")"
    fi
    log_warn "Removing invalid seeded models.json so OpenClaw can regenerate it."
    rm -f "${RUNTIME_MODELS_FILE}"
  fi
}

reconcile_agent_runtime_files() {
  remove_zero_length_file "${RUNTIME_MODELS_FILE}" "models registry"
  remove_zero_length_file "${RUNTIME_AUTH_PROFILES_FILE}" "auth profile store"
  remove_zero_length_file "${RUNTIME_AGENT_CONFIG_FILE}" "agent config"
  remove_invalid_seeded_models_json
}

ensure_gateway_mode_local() {
  upsert_env_key "${RUNTIME_ENV_FILE}" "OPENCLAW_GATEWAY_MODE" "local"
  log_info "Ensured runtime config: $(display_path "${RUNTIME_ENV_FILE}") contains OPENCLAW_GATEWAY_MODE=local"
}

detect_host_lan_ip() {
  local host_ip=""

  if command -v hostname >/dev/null 2>&1; then
    host_ip="$(
      hostname -I 2>/dev/null | tr ' ' '\n' | awk '
        /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ && $0 !~ /^127\./ {
          print
          exit
        }
      '
    )"
  fi

  if [[ -z "${host_ip}" ]] && command -v ip >/dev/null 2>&1; then
    host_ip="$(
      ip route get 1.1.1.1 2>/dev/null | awk '
        {
          for (i = 1; i <= NF; i++) {
            if ($i == "src") {
              print $(i + 1)
              exit
            }
          }
        }
      '
    )"
  fi

  if [[ -z "${host_ip}" || "${host_ip}" == 127.* ]]; then
    log_error "Unable to determine a non-loopback host LAN IP for OpenClaw control UI origins."
    exit 1
  fi

  printf '%s\n' "${host_ip}"
}

detect_host_name() {
  local host_name=""

  if command -v hostname >/dev/null 2>&1; then
    host_name="$(hostname -s 2>/dev/null || hostname 2>/dev/null || true)"
  fi

  if [[ -z "${host_name//[[:space:]]/}" ]]; then
    log_error "Unable to determine host name for OpenClaw control UI origins."
    exit 1
  fi

  printf '%s\n' "${host_name}"
}

ensure_openclaw_runtime_json() {
  local host_ip="$1"
  local host_name="$2"
  local gateway_port="$3"

  python3 - "${RUNTIME_OPENCLAW_CONFIG_FILE}" "${host_ip}" "${host_name}" "${gateway_port}" <<'PY'
import json
import pathlib
import sys

config_path = pathlib.Path(sys.argv[1])
host_ip = sys.argv[2]
host_name = sys.argv[3]
gateway_port = sys.argv[4]

required_origins = [
    f"http://127.0.0.1:{gateway_port}",
    f"https://127.0.0.1:{gateway_port}",
    f"http://localhost:{gateway_port}",
    f"https://localhost:{gateway_port}",
    f"http://{host_ip}:{gateway_port}",
    f"https://{host_ip}:{gateway_port}",
    f"http://{host_name}:{gateway_port}",
    f"https://{host_name}:{gateway_port}",
]

try:
    data = json.loads(config_path.read_text(encoding="utf-8"))
except json.JSONDecodeError as exc:
    raise SystemExit(f"Invalid JSON in {config_path}: {exc}") from exc

if not isinstance(data, dict):
    data = {}

gateway = data.setdefault("gateway", {})
if not isinstance(gateway, dict):
    gateway = {}
    data["gateway"] = gateway

gateway["mode"] = "local"

control_ui = gateway.setdefault("controlUi", {})
if not isinstance(control_ui, dict):
    control_ui = {}
    gateway["controlUi"] = control_ui

existing = control_ui.get("allowedOrigins")
if not isinstance(existing, list):
    existing = []

merged = []
for value in [*existing, *required_origins]:
    if isinstance(value, str) and value not in merged:
        merged.append(value)

control_ui["allowedOrigins"] = merged
config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY

  log_info "Ensured runtime config: $(display_path "${RUNTIME_OPENCLAW_CONFIG_FILE}") contains gateway.mode=local and required control UI allowedOrigins"
}

require_key() {
  local file="$1"
  local key="$2"
  if ! grep -Eq "^${key}=" "${file}"; then
    log_error "Missing key '${key}' in ${file}"
    exit 1
  fi
}

require_non_empty_env() {
  local key="$1"
  local value="${!key:-}"
  if [[ -z "${value//[[:space:]]/}" ]]; then
    log_error "Required runtime value is empty: ${key}"
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
  require_key "${RUNTIME_ENV_FILE}" "CLOUD_REASONING_MODEL"
  require_key "${RUNTIME_CONTAINER_ENV_FILE}" "TOGETHER_API_KEY"
  require_key "${RUNTIME_CONTAINER_ENV_FILE}" "LOCAL_ROUTING_MODEL"
  require_key "${RUNTIME_CONTAINER_ENV_FILE}" "OLLAMA_BASE_URL"
  require_key "${RUNTIME_CONTAINER_ENV_FILE}" "OPENSEARCH_URL"
  require_key "${RUNTIME_CONTAINER_ENV_FILE}" "OPENSEARCH_JAVA_OPTS"
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

validate_runtime_values() {
  require_non_empty_env "OPENCLAW_IMAGE"
  require_non_empty_env "OPENCLAW_GATEWAY_BIND"
  require_non_empty_env "OPENCLAW_GATEWAY_TOKEN"
  require_non_empty_env "OLLAMA_IMAGE"
  require_non_empty_env "OPENSEARCH_IMAGE"
  require_non_empty_env "GATEWAY_PORT"
  require_non_empty_env "LOCAL_ROUTING_MODEL"
  require_non_empty_env "OLLAMA_BASE_URL"
  require_non_empty_env "OPENSEARCH_URL"
  require_non_empty_env "OPENSEARCH_JAVA_OPTS"
  require_non_empty_env "CLOUD_PROVIDER"
  require_non_empty_env "CLOUD_REASONING_MODEL"
  require_non_empty_env "TOGETHER_API_KEY"

  if [[ "${OPENCLAW_GATEWAY_BIND}" != "lan" ]]; then
    log_error "Moltbox requires OPENCLAW_GATEWAY_BIND=lan. Found: ${OPENCLAW_GATEWAY_BIND}"
    exit 1
  fi

  if [[ "${CLOUD_PROVIDER}" != "together" ]]; then
    log_error "Moltbox escalation provider must be 'together'. Found: ${CLOUD_PROVIDER}"
    exit 1
  fi

  if [[ "${OLLAMA_BASE_URL}" != "http://ollama:11434" ]]; then
    log_error "OLLAMA_BASE_URL must use Docker service DNS 'http://ollama:11434'. Found: ${OLLAMA_BASE_URL}"
    exit 1
  fi

  if [[ "${OLLAMA_BASE_URL}" == */v1 || "${OLLAMA_BASE_URL}" == */v1/ ]]; then
    log_error "OLLAMA_BASE_URL must use the native Ollama API root, not /v1: ${OLLAMA_BASE_URL}"
    exit 1
  fi

  if [[ "${OPENSEARCH_URL}" != "http://opensearch:9200" ]]; then
    log_error "OPENSEARCH_URL must use Docker service DNS 'http://opensearch:9200'. Found: ${OPENSEARCH_URL}"
    exit 1
  fi

  if [[ "${OPENCLAW_GATEWAY_TOKEN}" == "CHANGE_ME_STRONG_TOKEN" ]]; then
    log_error "OPENCLAW_GATEWAY_TOKEN is still set to the placeholder value."
    exit 1
  fi

  log_warn "Moltbox policy YAML files are not upstream OpenClaw config; bootstrap maps runtime env into real OpenClaw config via CLI."
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
    if ollama_exec ollama list >/dev/null 2>&1; then
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

wait_for_openclaw_container() {
  local sleep_seconds="${BOOTSTRAP_WAIT_INTERVAL_SECONDS:-2}"
  local wait_seconds="${BOOTSTRAP_OPENCLAW_CONTAINER_WAIT_SECONDS:-60}"
  local max_attempts=$(( (wait_seconds + sleep_seconds - 1) / sleep_seconds ))
  local attempt=1

  log_info "Waiting for OpenClaw container to be running (timeout: ${wait_seconds}s)."
  while (( attempt <= max_attempts )); do
    local cid=""
    local running="false"

    cid="$(compose ps -q openclaw 2>/dev/null || true)"
    if [[ -n "${cid}" ]]; then
      running="$(docker_cmd inspect -f '{{.State.Running}}' "${cid}" 2>/dev/null || printf 'false\n')"
    fi

    if [[ "${running}" == "true" ]]; then
      log_info "OpenClaw container is running."
      return 0
    fi

    log_warn "OpenClaw container not running yet (attempt ${attempt}/${max_attempts})."
    sleep "${sleep_seconds}"
    attempt=$((attempt + 1))
  done

  log_error "OpenClaw container did not reach a running state."
  return 1
}

prepull_model() {
  local model="${LOCAL_ROUTING_MODEL}"
  log_info "Pre-pulling local routing model: ${model}"
  ollama_exec ollama pull "${model}"
}

assert_ollama_model_pulled() {
  local model="${LOCAL_ROUTING_MODEL}"
  local model_list=""

  model_list="$(ollama_exec ollama list 2>&1 || true)"
  if ! grep -Eq "^${model}[[:space:]]" <<<"${model_list}"; then
    log_error "Ollama does not report the required routing model '${model}' after bootstrap."
    printf '%s\n' "${model_list}" >&2
    return 1
  fi

  log_info "Confirmed Ollama has ${model}."
}

show_discovery_diagnostics() {
  log_warn "OpenClaw model discovery diagnostics:"
  log_warn "--- openclaw models list --all --provider ollama ---"
  openclaw_exec models list --all --provider ollama || true
  log_warn "--- openclaw models status ---"
  openclaw_exec models status || true
  log_warn "--- openclaw config get agents.defaults.model.primary ---"
  openclaw_exec config get agents.defaults.model.primary || true
  log_warn "--- openclaw config get models.providers.ollama ---"
  openclaw_exec config get models.providers.ollama || true
  log_warn "--- ollama list ---"
  ollama_exec ollama list || true
}

assert_ollama_model_registered() {
  local model_ref="ollama/${LOCAL_ROUTING_MODEL}"
  local model_list=""

  if ! model_list="$(openclaw_exec models list --all --provider ollama 2>&1)"; then
    log_error "Failed to read OpenClaw model registry."
    printf '%s\n' "${model_list}" >&2
    show_discovery_diagnostics
    return 1
  fi

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
    log_error "OpenClaw did not register ${model_ref} in its model registry."
    printf '%s\n' "${model_list}" >&2
    show_discovery_diagnostics
    return 1
  fi

  log_info "Confirmed OpenClaw registered ${model_ref}."
}

assert_openclaw_can_reach_ollama() {
  log_info "Checking OpenClaw -> Ollama native API reachability with curl before model registration."
  compose exec -T openclaw curl -fsS "${OLLAMA_BASE_URL}/api/tags" >/dev/null
}

configure_gateway_runtime() {
  log_info "Configuring OpenClaw runtime model/provider state."
  openclaw_exec config set gateway.mode '"local"'
  openclaw_exec config set models.providers.ollama.apiKey '"ollama-local"'
  openclaw_exec config set models.providers.ollama.baseUrl "\"${OLLAMA_BASE_URL}\""
  openclaw_exec config set models.providers.ollama.api '"ollama"'
  openclaw_exec config set agents.defaults.model.primary "\"ollama/${LOCAL_ROUTING_MODEL}\""
  openclaw_exec config set agents.defaults.model.fallbacks "[\"together/${CLOUD_REASONING_MODEL}\"]"
}

verify_openclaw_config_value() {
  local path="$1"
  local expected="$2"
  local allow_redacted="${3:-false}"
  local actual=""

  if ! actual="$(openclaw_exec config get "${path}" 2>/dev/null)"; then
    log_error "Failed to read OpenClaw config path: ${path}"
    return 1
  fi

  if [[ "${allow_redacted}" == "true" && "${actual}" == "${OPENCLAW_REDACTED_SECRET}" ]]; then
    log_info "Skipping drift validation for redacted secret"
    return 0
  fi

  if [[ "${actual}" != "${expected}" ]]; then
    log_error "OpenClaw config drift at ${path}: expected '${expected}', got '${actual}'."
    return 1
  fi
}

verify_openclaw_runtime_config() {
  log_info "Validating OpenClaw provider configuration"
  verify_openclaw_config_value "gateway.mode" "local"
  verify_openclaw_config_value "models.providers.ollama.apiKey" "ollama-local" "true"
  verify_openclaw_config_value "models.providers.ollama.baseUrl" "${OLLAMA_BASE_URL}"
  verify_openclaw_config_value "models.providers.ollama.api" "ollama"
  verify_openclaw_config_value "agents.defaults.model.primary" "ollama/${LOCAL_ROUTING_MODEL}"
  verify_openclaw_config_value "agents.defaults.model.fallbacks[0]" "together/${CLOUD_REASONING_MODEL}"
}

prime_model_registry() {
  log_info "Priming OpenClaw model registry from Ollama discovery."
  openclaw_exec models list --all --provider ollama >/dev/null
}

probe_together_provider() {
  log_info "Probing Together provider auth and reachability."
  openclaw_exec models status --probe --probe-provider together --probe-concurrency 1 --probe-timeout 10000 --probe-max-tokens 8 >/dev/null
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
  require_host_cmd python3
  require_file "${COMPOSE_FILE}"
  ensure_runtime_root_safe
  enforce_runtime_outside_git_workspace
  log_info "Repository root: ${REPO_ROOT}"
  log_info "Runtime root: ${RUNTIME_ROOT}"
  ensure_runtime_templates
  ensure_runtime_env_defaults
  ensure_together_api_key
  reconcile_agent_runtime_files
  ensure_gateway_mode_local
  validate_required_keys
  ensure_gateway_token
  load_env
  validate_runtime_values
  local host_ip
  local host_name
  host_ip="$(detect_host_lan_ip)"
  host_name="$(detect_host_name)"
  log_info "Detected host LAN IP for control UI origins: ${host_ip}"
  log_info "Detected host name for control UI origins: ${host_name}"
  ensure_openclaw_runtime_json "${host_ip}" "${host_name}" "${GATEWAY_PORT}"

  bring_up_stack
  wait_for_ollama
  prepull_model
  assert_ollama_model_pulled
  wait_for_openclaw_container
  configure_gateway_runtime
  verify_openclaw_runtime_config
  wait_for_gateway
  assert_openclaw_can_reach_ollama
  prime_model_registry
  assert_ollama_model_registered
  probe_together_provider

  log_info "Runtime root: ${RUNTIME_ROOT}"
  log_info "Gateway token for first login: ${OPENCLAW_GATEWAY_TOKEN}"
  log_info "Open browser on your LAN: http://<MOLTBOX_HOST>:${GATEWAY_PORT:-18789}"
  log_info "Bootstrap complete."
}

main "$@"
