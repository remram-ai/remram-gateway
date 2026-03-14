#!/bin/sh
set -eu

CLI_PATH="__MOLTBOX_CLI_PATH__"

deny() {
  printf '%s\n' "automation access denied: $1" >&2
  exit 126
}

if [ -z "${SSH_ORIGINAL_COMMAND:-}" ]; then
  deny "missing command"
fi

set -- ${SSH_ORIGINAL_COMMAND}

if [ "$#" -lt 1 ]; then
  deny "expected a moltbox command"
fi

if [ "$1" != "moltbox" ]; then
  deny "only moltbox commands are allowed"
fi

shift

if [ "$#" -lt 1 ]; then
  deny "missing moltbox arguments"
fi

exec "$CLI_PATH" "$@"
