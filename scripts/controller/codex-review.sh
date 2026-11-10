#!/usr/bin/env bash
# Start an independent Codex review; the prompt must require read-only review behavior.
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"
controller_init
export CODEX_MODEL=${CODEX_REVIEW_MODEL:-gpt-6-sol}
export CODEX_EFFORT=${CODEX_REVIEW_EFFORT:-xhigh}
export CODEX_HOME_OVERRIDE=${CODEX_HOME_REVIEW:-"$HOME/.codex"}
exec "$SCRIPT_DIR/codex-impl.sh" "$@"
