#!/usr/bin/env bash
set -Eeuo pipefail

timestamp() { date +"%Y-%m-%dT%H:%M:%S%z"; }
log_info() { echo "[$(timestamp)] [INFO] $*"; }
log_error() { echo "[$(timestamp)] [ERROR] $*" >&2; }

trap 'log_error "Escalation agent install failed near line ${BASH_LINENO[0]}."' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOLTBOX_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${MOLTBOX_DIR}/.." && pwd)"
CONFIG_DIR="${MOLTBOX_DIR}/config"
COMPOSE_FILE="${CONFIG_DIR}/docker-compose.yml"

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
PROMPT_SOURCE="${MOLTBOX_DIR}/agents/escalation-mvp/agent-prompt.md"
PLUGIN_SOURCE_DIR="${MOLTBOX_DIR}/agents/escalation-mvp/tools/remram-escalate"
RUNTIME_WORKSPACE_DIR="${RUNTIME_ROOT}/workspace"
RUNTIME_AGENTS_FILE="${RUNTIME_WORKSPACE_DIR}/AGENTS.md"
RUNTIME_EXTENSION_DIR="${RUNTIME_ROOT}/extensions/remram-escalate"
PROMPT_BEGIN="<!-- REMRAM_ESCALATION_MVP:BEGIN -->"
PROMPT_END="<!-- REMRAM_ESCALATION_MVP:END -->"

docker_cmd() {
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
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

require_file() {
  local path="$1"
  [[ -f "${path}" ]] || {
    log_error "Required file not found: ${path}"
    exit 1
  }
}

require_dir() {
  local path="$1"
  [[ -d "${path}" ]] || {
    log_error "Required directory not found: ${path}"
    exit 1
  }
}

install_prompt_block() {
  mkdir -p "${RUNTIME_WORKSPACE_DIR}"

  if [[ ! -f "${RUNTIME_AGENTS_FILE}" ]]; then
    printf '# AGENTS.md - Managed by Moltbox\n\n' > "${RUNTIME_AGENTS_FILE}"
  fi

  python3 - "${RUNTIME_AGENTS_FILE}" "${PROMPT_SOURCE}" "${PROMPT_BEGIN}" "${PROMPT_END}" <<'PY'
from pathlib import Path
import sys

target = Path(sys.argv[1])
source = Path(sys.argv[2])
begin = sys.argv[3]
end = sys.argv[4]

target_text = target.read_text(encoding="utf-8")
block = source.read_text(encoding="utf-8").strip()

start = target_text.find(begin)
finish = target_text.find(end)

if start != -1 and finish != -1 and finish >= start:
    finish += len(end)
    updated = target_text[:start].rstrip() + "\n\n" + block + "\n"
else:
    updated = target_text.rstrip() + "\n\n" + block + "\n"

target.write_text(updated, encoding="utf-8")
PY

  log_info "Installed managed escalation prompt block into ${RUNTIME_AGENTS_FILE}"
}

install_plugin_files() {
  mkdir -p "${RUNTIME_EXTENSION_DIR}"
  cp "${PLUGIN_SOURCE_DIR}/openclaw.plugin.json" "${RUNTIME_EXTENSION_DIR}/openclaw.plugin.json"
  cp "${PLUGIN_SOURCE_DIR}/index.ts" "${RUNTIME_EXTENSION_DIR}/index.ts"
  log_info "Installed remram-escalate plugin files into ${RUNTIME_EXTENSION_DIR}"
}

link_plugin() {
  if openclaw_exec plugins install -l /home/node/.openclaw/extensions/remram-escalate >/dev/null 2>&1; then
    log_info "Linked remram-escalate into OpenClaw plugin load paths"
    return 0
  fi

  log_info "Plugin link will be retried after OpenClaw restart."
}

enable_plugin() {
  if openclaw_exec plugins info remram-escalate >/dev/null 2>&1; then
    log_info "OpenClaw already discovers remram-escalate."
  fi

  if openclaw_exec plugins enable remram-escalate >/dev/null 2>&1; then
    log_info "Enabled OpenClaw plugin remram-escalate"
    return 0
  fi

  log_info "remram-escalate is staged in runtime; enable will be retried after OpenClaw restart."
}

main() {
  require_file "${COMPOSE_FILE}"
  require_file "${RUNTIME_ENV_FILE}"
  require_file "${PROMPT_SOURCE}"
  require_dir "${PLUGIN_SOURCE_DIR}"

  install_prompt_block
  install_plugin_files
  link_plugin
  enable_plugin
}

main "$@"
