#!/usr/bin/env bash
# controller-launch.sh — start the controller session itself over DeepSeek's
# Anthropic-compatible endpoint (via claude-deepseek.sh), so the control loop
# (dispatch/monitor/packet-assembly) survives even when native Claude and/or
# codex are both session-limited. See docs/controller-workflow-v2.md §5.2.
#
# The controller never needs frontier-tier reasoning for its own job (parsing
# handoff headers, preflighting, dispatching, arming monitors, packeting) --
# it only needs to reliably keep running. Routing IT to DeepSeek-direct means
# a native-Claude outage blocks frontier review/implementer dispatches (per
# the pause rule in §5.2) but never blocks the loop that will resume them.
#
# Usage:
#   scripts/controller-launch.sh <dispatch-doc> [extra claude args...]
#
# Example (replaces the old manual invocation):
#   scripts/controller-launch.sh docs/ai-dev/controller-dispatch-2026-07-13.md \
#     --name "controller-vbpub"
#
# To resume a controller session after a restart (of the controller process
# itself, not a worker it dispatched): pass --resume <session-id>, found via
#   ls -t ~/.claude/projects/-workspaces-vbpub/*.jsonl | head -1
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <dispatch-doc> [extra claude args...]" >&2
  exit 1
fi

DISPATCH_DOC="$1"; shift
[[ -f "$DISPATCH_DOC" ]] || { echo "error: dispatch doc not found: $DISPATCH_DOC" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec "$SCRIPT_DIR/claude-deepseek.sh" \
  --append-system-prompt "$(cat "$DISPATCH_DOC")" \
  "$@"
