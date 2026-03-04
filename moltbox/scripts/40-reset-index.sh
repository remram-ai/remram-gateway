#!/usr/bin/env bash
set -euo pipefail

# Moltbox OpenSearch index reset utility.
# Safe by default: explicit target + confirmation are required.

timestamp() { date +"%Y-%m-%dT%H:%M:%S%z"; }
log_info() { echo "[$(timestamp)] [INFO] $*"; }
log_warn() { echo "[$(timestamp)] [WARN] $*" >&2; }
log_error() { echo "[$(timestamp)] [ERROR] $*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOLTBOX_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_DIR="${MOLTBOX_DIR}/config"
COMPOSE_FILE="${CONFIG_DIR}/docker-compose.yml"

INDEX=""
CONFIRM=false
ALLOW_ALL=false
CREATE_IF_MISSING=false

usage() {
  cat <<EOF
Usage: $(basename "$0") --index <name> --confirm [--allow-all] [--create-if-missing]

Required:
  --index <name>         Target index name.
  --confirm              Acknowledges destructive action.

Optional:
  --allow-all            Allow wildcard/all-index target (* or _all).
  --create-if-missing    Create index after reset if missing.
EOF
}

compose() {
  docker compose -f "${COMPOSE_FILE}" "$@"
}

os_http_code() {
  local method="$1"
  local path="$2"
  compose exec -T opensearch sh -lc "curl -sS -o /dev/null -w '%{http_code}' -X ${method} 'http://127.0.0.1:9200${path}'"
}

os_request() {
  local method="$1"
  local path="$2"
  local data="${3:-}"

  if [[ -n "${data}" ]]; then
    compose exec -T opensearch sh -lc "curl -sS -X ${method} 'http://127.0.0.1:9200${path}' -H 'Content-Type: application/json' -d '${data}'"
  else
    compose exec -T opensearch sh -lc "curl -sS -X ${method} 'http://127.0.0.1:9200${path}'"
  fi
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --index)
        INDEX="${2:-}"
        shift 2
        ;;
      --confirm)
        CONFIRM=true
        shift
        ;;
      --allow-all)
        ALLOW_ALL=true
        shift
        ;;
      --create-if-missing)
        CREATE_IF_MISSING=true
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        log_error "Unknown argument: $1"
        usage
        exit 1
        ;;
    esac
  done
}

validate_args() {
  if [[ -z "${INDEX}" ]]; then
    log_error "--index is required."
    usage
    exit 1
  fi

  if [[ "${CONFIRM}" != "true" ]]; then
    log_error "--confirm is required."
    usage
    exit 1
  fi

  if [[ "${INDEX}" == "_all" || "${INDEX}" == "*" || "${INDEX}" == *"*"* ]]; then
    if [[ "${ALLOW_ALL}" != "true" ]]; then
      log_error "Wildcard/all-index target requires --allow-all."
      exit 1
    fi
  fi
}

print_index_state() {
  local phase="$1"
  log_info "${phase} index state for '${INDEX}':"
  compose exec -T opensearch sh -lc "curl -sS 'http://127.0.0.1:9200/_cat/indices/${INDEX}?v'" || true
}

main() {
  parse_args "$@"
  validate_args

  if [[ ! -f "${COMPOSE_FILE}" ]]; then
    log_error "Compose file not found: ${COMPOSE_FILE}"
    exit 1
  fi

  print_index_state "Before"

  local exists_code
  exists_code="$(os_http_code GET "/${INDEX}")"

  if [[ "${exists_code}" == "404" ]]; then
    log_warn "Index '${INDEX}' does not exist."
    if [[ "${CREATE_IF_MISSING}" == "true" ]]; then
      log_info "Creating missing index '${INDEX}'."
      local create_code
      create_code="$(os_http_code PUT "/${INDEX}")"
      if [[ "${create_code}" != "200" && "${create_code}" != "201" ]]; then
        log_error "Failed to create index '${INDEX}' (HTTP ${create_code})."
        exit 1
      fi
    else
      log_info "No action taken (idempotent no-op)."
    fi
  else
    log_info "Deleting index '${INDEX}'."
    local delete_code
    delete_code="$(os_http_code DELETE "/${INDEX}")"
    if [[ "${delete_code}" != "200" ]]; then
      log_error "Failed to delete index '${INDEX}' (HTTP ${delete_code})."
      exit 1
    fi

    if [[ "${CREATE_IF_MISSING}" == "true" ]]; then
      log_info "Recreating index '${INDEX}'."
      local recreate_code
      recreate_code="$(os_http_code PUT "/${INDEX}")"
      if [[ "${recreate_code}" != "200" && "${recreate_code}" != "201" ]]; then
        log_error "Failed to recreate index '${INDEX}' (HTTP ${recreate_code})."
        exit 1
      fi
    fi
  fi

  print_index_state "After"
  log_info "Index reset routine completed."
}

main "$@"
