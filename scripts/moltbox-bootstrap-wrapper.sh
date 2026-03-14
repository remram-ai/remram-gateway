#!/bin/sh
set -eu

CLI_PATH="__MOLTBOX_CLI_PATH__"

deny() {
  printf '%s\n' "bootstrap access denied: $1" >&2
  exit 126
}

if [ -z "${SSH_ORIGINAL_COMMAND:-}" ]; then
  deny "missing command"
fi

exec "$CLI_PATH" "__ssh-wrapper=bootstrap" "$SSH_ORIGINAL_COMMAND"
