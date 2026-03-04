#!/usr/bin/env bash
set -euo pipefail

# Moltbox maintenance utility.
# Supports safe pull/restart/prune flows and always runs post-maintenance validation.

timestamp() { date +"%Y-%m-%dT%H:%M:%S%z"; }
log_info() { echo "[$(timestamp)] [INFO] $*"; }
log_warn() { echo "[$(timestamp)] [WARN] $*" >&2; }
log_error() { echo "[$(timestamp)] [ERROR] $*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOLTBOX_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_DIR="${MOLTBOX_DIR}/config"
COMPOSE_FILE="${CONFIG_DIR}/docker-compose.yml"
VALIDATE_SCRIPT="${SCRIPT_DIR}/30-validate.sh"

DO_PULL=false
DO_RESTART=false
DO_PRUNE=false
SERVICE=""

usage() {
  cat <<EOF
Usage: $(basename "$0") [--pull] [--restart] [--service <name>] [--prune]

Options:
  --pull               Pull updated images.
  --restart            Restart stack (or a single service with --service).
  --service <name>     Service name for targeted pull/restart.
  --prune              Prune dangling images/build cache (no volume prune).
  -h, --help           Show this help.

If no action flags are provided, defaults to: --pull --restart
EOF
}

compose() {
  docker compose -f "${COMPOSE_FILE}" "$@"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --pull)
        DO_PULL=true
        shift
        ;;
      --restart)
        DO_RESTART=true
        shift
        ;;
      --service)
        SERVICE="${2:-}"
        shift 2
        ;;
      --prune)
        DO_PRUNE=true
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

set_defaults_if_none_selected() {
  if [[ "${DO_PULL}" == "false" && "${DO_RESTART}" == "false" && "${DO_PRUNE}" == "false" ]]; then
    DO_PULL=true
    DO_RESTART=true
    log_info "No action specified; defaulting to --pull --restart."
  fi
}

do_pull() {
  if [[ "${DO_PULL}" != "true" ]]; then
    return
  fi
  if [[ -n "${SERVICE}" ]]; then
    log_info "Pulling updated image for service: ${SERVICE}"
    compose pull "${SERVICE}"
  else
    log_info "Pulling updated images for full stack."
    compose pull
  fi
}

do_restart() {
  if [[ "${DO_RESTART}" != "true" ]]; then
    return
  fi
  if [[ -n "${SERVICE}" ]]; then
    log_info "Restarting service: ${SERVICE}"
    compose up -d "${SERVICE}"
    compose restart "${SERVICE}"
  else
    log_info "Reconciling and restarting full stack."
    compose up -d
    compose restart
  fi
}

do_prune() {
  if [[ "${DO_PRUNE}" != "true" ]]; then
    return
  fi
  log_warn "Running optional prune: dangling images and build cache only."
  docker image prune -f
  docker builder prune -f
}

run_validation() {
  if [[ ! -x "${VALIDATE_SCRIPT}" ]]; then
    log_error "Validation script is missing or not executable: ${VALIDATE_SCRIPT}"
    exit 1
  fi
  log_info "Running post-maintenance validation."
  "${VALIDATE_SCRIPT}"
}

main() {
  parse_args "$@"
  set_defaults_if_none_selected

  [[ -f "${COMPOSE_FILE}" ]] || { log_error "Compose file not found: ${COMPOSE_FILE}"; exit 1; }

  do_pull
  do_restart
  do_prune
  run_validation

  log_info "Maintenance routine completed."
}

main "$@"
