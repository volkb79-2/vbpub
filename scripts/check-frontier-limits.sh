#!/usr/bin/env bash
# check-frontier-limits.sh — cheap probe to test whether native `claude` and/or
# `codex` session limits have reset, so a DeepSeek-hosted controller loop
# (scripts/controller-launch.sh) can poll for reset without spending a real
# frontier dispatch to find out. See docs/controller-workflow-v2.md §5.2.
#
# Prints one line per service: "claude: OK" / "claude: LIMITED (<detail>)" and
# likewise for codex. Exit code is 0 only if BOTH are OK; check per-service
# output if you only care about one.
set -uo pipefail

check_claude() {
  local out
  out="$(timeout 30 claude -p --output-format json 'Reply with exactly ALIAS_OK.' 2>&1)"
  if echo "$out" | grep -qi "session limit"; then
    local reset
    reset="$(echo "$out" | grep -oi "resets [^\"]*" | head -1)"
    echo "claude: LIMITED (${reset:-session limit hit})"
    return 1
  fi
  if echo "$out" | grep -q '"result":"ALIAS_OK"'; then
    echo "claude: OK"
    return 0
  fi
  echo "claude: UNKNOWN (unexpected output, inspect manually)"
  return 2
}

check_codex() {
  local out
  out="$(timeout 30 codex exec --sandbox read-only -m gpt-5.6-terra "Reply with exactly ALIAS_OK." 2>&1)"
  if echo "$out" | grep -qi "session limit\|usage limit\|rate limit"; then
    echo "codex: LIMITED ($(echo "$out" | grep -i "limit" | head -1))"
    return 1
  fi
  if echo "$out" | grep -q "ALIAS_OK"; then
    echo "codex: OK"
    return 0
  fi
  echo "codex: UNKNOWN (unexpected output, inspect manually)"
  return 2
}

claude_status=0; check_claude || claude_status=$?
codex_status=0; check_codex || codex_status=$?

if [[ $claude_status -eq 0 && $codex_status -eq 0 ]]; then
  exit 0
else
  exit 1
fi
