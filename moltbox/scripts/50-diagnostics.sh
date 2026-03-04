#!/usr/bin/env bash
set -euo pipefail

# Moltbox diagnostics bundle collector.
# Collects runtime state without mutating appliance configuration.

timestamp() { date +"%Y-%m-%dT%H:%M:%S%z"; }
log_info() { echo "[$(timestamp)] [INFO] $*"; }
log_warn() { echo "[$(timestamp)] [WARN] $*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOLTBOX_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_DIR="${MOLTBOX_DIR}/config"
COMPOSE_FILE="${CONFIG_DIR}/docker-compose.yml"
ENV_FILE="${CONFIG_DIR}/.env"
OUT_DIR="${MOLTBOX_DIR}/logs/diagnostics/$(date +%Y%m%d-%H%M%S)"

compose() {
  docker compose -f "${COMPOSE_FILE}" "$@"
}

capture_cmd() {
  local outfile="$1"
  shift
  {
    echo "# Command: $*"
    echo "# Timestamp: $(timestamp)"
    "$@"
  } >"${outfile}" 2>&1 || true
}

capture_shell() {
  local outfile="$1"
  local cmd="$2"
  {
    echo "# Command: ${cmd}"
    echo "# Timestamp: $(timestamp)"
    bash -lc "${cmd}"
  } >"${outfile}" 2>&1 || true
}

load_gateway_port() {
  local port="18789"
  if [[ -f "${ENV_FILE}" ]]; then
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    port="${GATEWAY_PORT:-18789}"
  fi
  echo "${port}"
}

main() {
  mkdir -p "${OUT_DIR}"
  log_info "Writing diagnostics to ${OUT_DIR}"

  capture_cmd "${OUT_DIR}/compose-ps.txt" compose ps
  capture_cmd "${OUT_DIR}/compose-config.txt" compose config
  capture_cmd "${OUT_DIR}/docker-network-ls.txt" docker network ls

  capture_cmd "${OUT_DIR}/logs-openclaw.txt" compose logs --tail=200 openclaw
  capture_cmd "${OUT_DIR}/logs-ollama.txt" compose logs --tail=200 ollama
  capture_cmd "${OUT_DIR}/logs-opensearch.txt" compose logs --tail=200 opensearch

  capture_shell "${OUT_DIR}/health-openclaw.txt" "curl -fsS http://127.0.0.1:$(load_gateway_port)/healthz"
  capture_shell "${OUT_DIR}/ready-openclaw.txt" "curl -fsS http://127.0.0.1:$(load_gateway_port)/readyz"

  capture_cmd "${OUT_DIR}/ollama-models.txt" compose exec -T ollama ollama list
  capture_cmd "${OUT_DIR}/opensearch-cluster-health.json" compose exec -T opensearch curl -sS http://127.0.0.1:9200/_cluster/health
  capture_cmd "${OUT_DIR}/opensearch-indices.txt" compose exec -T opensearch curl -sS http://127.0.0.1:9200/_cat/indices?v

  capture_cmd "${OUT_DIR}/inspect-openclaw.txt" docker inspect "$(compose ps -q openclaw)"
  capture_cmd "${OUT_DIR}/inspect-ollama.txt" docker inspect "$(compose ps -q ollama)"
  capture_cmd "${OUT_DIR}/inspect-opensearch.txt" docker inspect "$(compose ps -q opensearch)"

  if command -v nvidia-smi >/dev/null 2>&1; then
    capture_cmd "${OUT_DIR}/nvidia-smi.txt" nvidia-smi
  else
    log_warn "nvidia-smi not found; skipping GPU diagnostics."
  fi

  log_info "Diagnostics collection complete."
}

main "$@"
