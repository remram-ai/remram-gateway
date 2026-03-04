#!/usr/bin/env bash
set -euo pipefail

# Moltbox validation script.
# Verifies service health, gateway readiness, internal connectivity, and exposure policy.

timestamp() { date +"%Y-%m-%dT%H:%M:%S%z"; }
log_info() { echo "[$(timestamp)] [INFO] $*"; }
log_warn() { echo "[$(timestamp)] [WARN] $*" >&2; }
log_error() { echo "[$(timestamp)] [ERROR] $*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOLTBOX_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_DIR="${MOLTBOX_DIR}/config"
COMPOSE_FILE="${CONFIG_DIR}/docker-compose.yml"
ENV_FILE="${CONFIG_DIR}/.env"
CONTAINER_ENV_FILE="${CONFIG_DIR}/container.env"

compose() {
  docker compose -f "${COMPOSE_FILE}" "$@"
}

require_runtime_files() {
  [[ -f "${COMPOSE_FILE}" ]] || { log_error "Missing compose file: ${COMPOSE_FILE}"; exit 1; }
  [[ -f "${ENV_FILE}" ]] || { log_error "Missing env file: ${ENV_FILE}"; exit 1; }
  [[ -f "${CONTAINER_ENV_FILE}" ]] || { log_error "Missing container env file: ${CONTAINER_ENV_FILE}"; exit 1; }
}

load_env() {
  # shellcheck disable=SC1090
  set -a
  source "${ENV_FILE}"
  # shellcheck disable=SC1090
  source "${CONTAINER_ENV_FILE}"
  set +a
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
  running="$(docker inspect -f '{{.State.Running}}' "${cid}")"
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
  health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${cid}")"
  if [[ "${health}" != "healthy" ]]; then
    log_error "Service '${service}' health is '${health}', expected 'healthy'."
    return 1
  fi
}

check_gateway() {
  local port="${GATEWAY_PORT:-18789}"
  log_info "Checking gateway health endpoint on port ${port}."
  curl -fsS "http://127.0.0.1:${port}/healthz" >/dev/null
}

check_internal_connectivity() {
  log_info "Checking OpenClaw -> Ollama connectivity."
  compose exec -T openclaw node -e "const http=require('http');http.get('http://ollama:11434/api/tags',r=>{if(r.statusCode&&r.statusCode<500){process.exit(0)}process.exit(1)}).on('error',()=>process.exit(1));"

  log_info "Checking OpenClaw -> OpenSearch connectivity."
  compose exec -T openclaw node -e "const http=require('http');http.get('http://opensearch:9200/_cluster/health',r=>{if(r.statusCode&&r.statusCode<500){process.exit(0)}process.exit(1)}).on('error',()=>process.exit(1));"
}

assert_internal_only_ports() {
  log_info "Verifying Ollama/OpenSearch are not published on host."
  local ps_output
  ps_output="$(compose ps)"

  if grep -Eq '11434->11434/tcp|9200->9200/tcp' <<<"${ps_output}"; then
    log_error "Detected forbidden published port mapping for Ollama or OpenSearch."
    echo "${ps_output}" >&2
    return 1
  fi
}

main() {
  require_runtime_files
  load_env

  log_info "Validating compose service status."
  compose ps

  ensure_service_running "openclaw"
  ensure_service_running "ollama"
  ensure_service_running "opensearch"

  ensure_service_healthy "openclaw"
  ensure_service_healthy "ollama"
  ensure_service_healthy "opensearch"

  check_gateway
  check_internal_connectivity
  assert_internal_only_ports

  log_info "Validation passed."
}

main "$@"
