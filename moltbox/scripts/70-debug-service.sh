#!/usr/bin/env bash
set -Eeuo pipefail

timestamp() { date +"%Y-%m-%dT%H:%M:%S%z"; }
log_info() { echo "[$(timestamp)] [INFO] $*"; }
log_warn() { echo "[$(timestamp)] [WARN] $*" >&2; }
log_error() { echo "[$(timestamp)] [ERROR] $*" >&2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOLTBOX_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${MOLTBOX_DIR}/.." && pwd)"
SERVICE_DIR="${MOLTBOX_DIR}/debug-service"
HOST_BIN_DIR="${MOLTBOX_DIR}/bin"
SYSTEMD_TEMPLATE="${MOLTBOX_DIR}/systemd/moltbox-debug-service.service"
DEBUG_CONFIG_TEMPLATE="${MOLTBOX_DIR}/config/debug-service.config.json.example"
DEBUG_CLIENTS_TEMPLATE="${MOLTBOX_DIR}/config/debug-service.clients.json.example"
SNAPSHOT_TOOL_SOURCE="${HOST_BIN_DIR}/moltbox-snapshot"
SNAPSHOT_TOOL_TARGET="/usr/local/bin/moltbox-snapshot"
TARGET_USER="${SUDO_USER:-${USER}}"
TARGET_HOME="$(getent passwd "${TARGET_USER}" | cut -d: -f6 2>/dev/null || printf '%s\n' "${HOME}")"
RUNTIME_ROOT="${MOLTBOX_RUNTIME_ROOT:-${TARGET_HOME}/.openclaw}"
VENV_DIR="${SERVICE_DIR}/.venv"
VENV_PYTHON="${VENV_DIR}/bin/python"
UNIT_NAME="moltbox-debug-service.service"
UNIT_PATH="/etc/systemd/system/${UNIT_NAME}"
DEBUG_ROOT="${RUNTIME_ROOT}/debug-service"

usage() {
  cat <<EOF
Usage: $(basename "$0") <install|start|stop|restart|status|logs>
EOF
}

require_file() {
  [[ -f "$1" ]] || { log_error "Required file not found: $1"; exit 1; }
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { log_error "Required command not found: $1"; exit 1; }
}

install_host_tools() {
  require_file "${SNAPSHOT_TOOL_SOURCE}"
  require_cmd sudo
  sudo install -m 0755 "${SNAPSHOT_TOOL_SOURCE}" "${SNAPSHOT_TOOL_TARGET}"
}

ensure_runtime_templates() {
  mkdir -p "${DEBUG_ROOT}"
  [[ -f "${DEBUG_ROOT}/config.json" ]] || cp "${DEBUG_CONFIG_TEMPLATE}" "${DEBUG_ROOT}/config.json"
  [[ -f "${DEBUG_ROOT}/clients.json" ]] || cp "${DEBUG_CLIENTS_TEMPLATE}" "${DEBUG_ROOT}/clients.json"
}

ensure_runtime_token() {
  local existing_value=""
  existing_value="$(grep -E '^DEBUG_SERVICE_TOKEN=' "${RUNTIME_ROOT}/.env" | head -n1 | cut -d= -f2- || true)"
  existing_value="${existing_value%$'\r'}"
  if [[ -z "${existing_value}" || "${existing_value}" == "CHANGE_ME_DEBUG_SERVICE_TOKEN" ]]; then
    local token_value=""
    if command -v openssl >/dev/null 2>&1; then
      token_value="$(openssl rand -hex 24)"
    else
      token_value="$(date +%s%N | sha256sum | cut -c1-48)"
    fi
    if grep -Eq '^DEBUG_SERVICE_TOKEN=' "${RUNTIME_ROOT}/.env"; then
      sed -i "s|^DEBUG_SERVICE_TOKEN=.*$|DEBUG_SERVICE_TOKEN=${token_value}|" "${RUNTIME_ROOT}/.env"
    else
      printf '\nDEBUG_SERVICE_TOKEN=%s\n' "${token_value}" >>"${RUNTIME_ROOT}/.env"
    fi
    log_warn "Generated DEBUG_SERVICE_TOKEN in ${RUNTIME_ROOT}/.env"
  fi

  if ! grep -Eq '^DEBUG_SERVICE_PORT=' "${RUNTIME_ROOT}/.env"; then
    printf '\nDEBUG_SERVICE_PORT=18890\n' >>"${RUNTIME_ROOT}/.env"
  fi
}

install_venv() {
  require_cmd python3
  require_cmd sudo
  python3 -m venv "${VENV_DIR}"
  "${VENV_PYTHON}" -m pip install --upgrade pip
  "${VENV_PYTHON}" -m pip install -e "${SERVICE_DIR}"
}

install_unit() {
  require_file "${SYSTEMD_TEMPLATE}"
  sed \
    -e "s|__MOLTBOX_USER__|${TARGET_USER}|g" \
    -e "s|__MOLTBOX_REPO_ROOT__|${REPO_ROOT}|g" \
    -e "s|__MOLTBOX_RUNTIME_ROOT__|${RUNTIME_ROOT}|g" \
    -e "s|__MOLTBOX_VENV_PYTHON__|${VENV_PYTHON}|g" \
    "${SYSTEMD_TEMPLATE}" | sudo tee "${UNIT_PATH}" >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable "${UNIT_NAME}"
}

install_service() {
  require_file "${RUNTIME_ROOT}/.env"
  require_file "${DEBUG_CONFIG_TEMPLATE}"
  require_file "${DEBUG_CLIENTS_TEMPLATE}"
  ensure_runtime_templates
  ensure_runtime_token
  install_host_tools
  install_venv
  install_unit
  log_info "Installed Moltbox debug service."
}

service_cmd() {
  sudo systemctl "$1" "${UNIT_NAME}"
}

main() {
  local action="${1:-}"
  [[ -n "${action}" ]] || { usage; exit 1; }

  case "${action}" in
    install)
      install_service
      ;;
    start|restart)
      install_host_tools
      service_cmd "${action}"
      ;;
    stop|status)
      service_cmd "${action}"
      ;;
    logs)
      sudo journalctl -u "${UNIT_NAME}" -n 200 --no-pager
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
