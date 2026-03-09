#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MOLTBOX_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE_DIR="${MOLTBOX_DIR}/debug-service"
VENV_PYTHON="${SERVICE_DIR}/.venv/bin/python"

if [[ -x "${VENV_PYTHON}" ]]; then
  PYTHON_BIN="${VENV_PYTHON}"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

export PYTHONPATH="${SERVICE_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "warning: ${VENV_PYTHON} is missing; using ${PYTHON_BIN}" >&2
  echo "warning: run bash ./scripts/70-debug-service.sh install if Python dependencies are missing" >&2
fi

exec "${PYTHON_BIN}" -m moltbox_debug_service publish-flow "$@"
