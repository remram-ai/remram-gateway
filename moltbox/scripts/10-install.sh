#!/usr/bin/env bash
set -euo pipefail

# Moltbox host install script (Ubuntu LTS baseline).
# Idempotent by design: installs only missing components and reconciles required sysctl.

timestamp() { date +"%Y-%m-%dT%H:%M:%S%z"; }
log_info() { echo "[$(timestamp)] [INFO] $*"; }
log_warn() { echo "[$(timestamp)] [WARN] $*" >&2; }
log_error() { echo "[$(timestamp)] [ERROR] $*" >&2; }

require_linux_ubuntu() {
  if [[ "$(uname -s)" != "Linux" ]]; then
    log_error "This script must run on Linux."
    exit 1
  fi
  if [[ ! -f /etc/os-release ]]; then
    log_error "/etc/os-release not found; cannot verify Ubuntu baseline."
    exit 1
  fi
  # shellcheck disable=SC1091
  source /etc/os-release
  if [[ "${ID:-}" != "ubuntu" ]]; then
    log_error "Unsupported distribution: ${ID:-unknown}. Ubuntu is required."
    exit 1
  fi
  log_info "Detected Ubuntu ${VERSION_ID:-unknown}."
}

SUDO=""
if [[ "${EUID}" -ne 0 ]]; then
  SUDO="sudo"
fi

install_docker_if_missing() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    log_info "Docker Engine and Compose plugin already installed."
    return
  fi

  log_info "Installing Docker Engine and Compose plugin."
  ${SUDO} apt-get update
  ${SUDO} apt-get install -y ca-certificates curl gnupg lsb-release

  ${SUDO} install -m 0755 -d /etc/apt/keyrings
  if [[ ! -f /etc/apt/keyrings/docker.gpg ]]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | ${SUDO} gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    ${SUDO} chmod a+r /etc/apt/keyrings/docker.gpg
  fi

  # shellcheck disable=SC1091
  source /etc/os-release
  arch="$(dpkg --print-architecture)"
  codename="${VERSION_CODENAME}"
  repo_line="deb [arch=${arch} signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${codename} stable"
  if [[ ! -f /etc/apt/sources.list.d/docker.list ]] || ! grep -Fq "${repo_line}" /etc/apt/sources.list.d/docker.list; then
    echo "${repo_line}" | ${SUDO} tee /etc/apt/sources.list.d/docker.list >/dev/null
  fi

  ${SUDO} apt-get update
  ${SUDO} apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  ${SUDO} systemctl enable --now docker
  log_info "Docker installation complete."
}

install_nvidia_toolkit_if_missing() {
  if command -v nvidia-ctk >/dev/null 2>&1; then
    log_info "NVIDIA Container Toolkit already installed."
  else
    log_info "Installing NVIDIA Container Toolkit."
    ${SUDO} apt-get update
    ${SUDO} apt-get install -y curl gnupg

    if [[ ! -f /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg ]]; then
      curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
        ${SUDO} gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
      ${SUDO} chmod a+r /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    fi

    # shellcheck disable=SC1091
    source /etc/os-release
    distribution="${ID}${VERSION_ID}"
    repo_file="/etc/apt/sources.list.d/nvidia-container-toolkit.list"
    curl -fsSL "https://nvidia.github.io/libnvidia-container/${distribution}/libnvidia-container.list" | \
      sed "s#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g" | \
      ${SUDO} tee "${repo_file}" >/dev/null

    ${SUDO} apt-get update
    ${SUDO} apt-get install -y nvidia-container-toolkit
  fi

  if command -v nvidia-ctk >/dev/null 2>&1; then
    log_info "Configuring Docker runtime for NVIDIA."
    ${SUDO} nvidia-ctk runtime configure --runtime=docker
    ${SUDO} systemctl restart docker
  else
    log_warn "nvidia-ctk not available; GPU runtime not configured."
  fi
}

reconcile_vm_max_map_count() {
  local required=262144
  local conf_file="/etc/sysctl.d/99-moltbox-opensearch.conf"
  local current=0
  current="$(sysctl -n vm.max_map_count 2>/dev/null || echo 0)"

  if [[ "${current}" -lt "${required}" ]]; then
    log_info "Setting vm.max_map_count to ${required}."
    ${SUDO} sysctl -w vm.max_map_count="${required}" >/dev/null
  else
    log_info "vm.max_map_count already ${current}."
  fi

  if [[ ! -f "${conf_file}" ]] || ! grep -Eq '^vm\.max_map_count=262144$' "${conf_file}"; then
    log_info "Persisting vm.max_map_count in ${conf_file}."
    echo "vm.max_map_count=262144" | ${SUDO} tee "${conf_file}" >/dev/null
  fi
}

post_checks() {
  log_info "Running post-install checks."
  docker --version
  docker compose version
  if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi >/dev/null || log_warn "nvidia-smi returned non-zero."
  else
    log_warn "nvidia-smi not found on host."
  fi
  sysctl vm.max_map_count
}

main() {
  require_linux_ubuntu
  install_docker_if_missing
  install_nvidia_toolkit_if_missing
  reconcile_vm_max_map_count
  post_checks
  log_info "Moltbox host installation completed."
}

main "$@"
