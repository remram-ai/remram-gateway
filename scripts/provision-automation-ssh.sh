#!/bin/sh
set -eu

if [ "$#" -ne 5 ]; then
  printf '%s\n' "usage: provision-automation-ssh.sh <jason-codex.pub> <codex-bootstrap.pub> <cli-path> <cli-wrapper-path> <bootstrap-wrapper-path>" >&2
  exit 2
fi

JASON_CODEX_KEY_PATH="$1"
BOOTSTRAP_KEY_PATH="$2"
CLI_PATH="$3"
CLI_WRAPPER_PATH="$4"
BOOTSTRAP_WRAPPER_PATH="$5"

ensure_user() {
  user="$1"
  home="/home/$user"

  if ! id "$user" >/dev/null 2>&1; then
    useradd --create-home --home-dir "$home" --shell /bin/sh "$user"
  fi

  passwd -l "$user" >/dev/null 2>&1 || usermod -L "$user" >/dev/null 2>&1 || true

  mkdir -p "$home/.ssh"
  chmod 700 "$home/.ssh"
  touch "$home/.ssh/authorized_keys"
  chmod 600 "$home/.ssh/authorized_keys"
  chown -R "$user:$user" "$home/.ssh"
}

upsert_key_entry() {
  user="$1"
  public_key="$2"
  entry="$3"
  auth_file="/home/$user/.ssh/authorized_keys"
  tmp_file="$(mktemp)"

  if [ -f "$auth_file" ]; then
    grep -Fv "$public_key" "$auth_file" > "$tmp_file" || true
  fi
  printf '%s\n' "$entry" >> "$tmp_file"
  mv "$tmp_file" "$auth_file"

  chown "$user:$user" "$auth_file"
  chmod 600 "$auth_file"
}

ensure_user "jason-codex"
ensure_user "codex-bootstrap"

jason_key="$(cat "$JASON_CODEX_KEY_PATH")"
bootstrap_key="$(cat "$BOOTSTRAP_KEY_PATH")"

jason_entry="command=\"$CLI_WRAPPER_PATH\",restrict,no-port-forwarding,no-agent-forwarding,no-pty,no-X11-forwarding $jason_key"
bootstrap_entry="command=\"$BOOTSTRAP_WRAPPER_PATH\",restrict,no-port-forwarding,no-agent-forwarding,no-pty,no-X11-forwarding $bootstrap_key"

upsert_key_entry "jason-codex" "$jason_key" "$jason_entry"
upsert_key_entry "codex-bootstrap" "$bootstrap_key" "$bootstrap_entry"
