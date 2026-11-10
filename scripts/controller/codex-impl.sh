#!/usr/bin/env bash
# Launch one Codex implementation or review job and persist its run stream locally.
# Usage: codex-impl.sh <worktree> <prompt-file> <run-name> [resume <session-id>]
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"
controller_init

usage() { echo "usage: $0 <worktree> <prompt-file> <run-name> [resume <session-id>]" >&2; exit 2; }
[[ $# -ge 3 && $# -le 5 ]] || usage
worktree=$(realpath -e -- "$1") || usage
prompt=$(realpath -e -- "$2") || usage
name=$3
[[ $name =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && $name != *..* ]] || { echo "invalid run name: $name" >&2; exit 2; }
mode=${4:-new}
session_id=${5:-}
if [[ $mode == resume ]]; then [[ -n $session_id ]] || usage
elif [[ $mode != new || $# -gt 4 ]]; then usage
fi
out=$CONTROLLER_HOME/runs

route=${CODEX_ROUTE:-luna}
case $route in
  luna)
    route_home=${CODEX_HOME_LUNA:-"$HOME/.codex"}
    default_model=gpt-6-luna
    default_effort=xhigh
    ;;
  deepseek)
    route_home=${CODEX_HOME_DEEPSEEK:-"$HOME/.codex-opendesign"}
    default_model=deepseek-v4.1-flash-eco
    default_effort=high
    ;;
  *) echo "unknown CODEX_ROUTE=$route" >&2; exit 2 ;;
esac
export CODEX_SQLITE_HOME=${CODEX_SQLITE_HOME:-"$HOME/.codex/sqlite-shared"}
export CODEX_HOME=${CODEX_HOME_OVERRIDE:-$route_home}
model=${CODEX_MODEL:-$default_model}
effort=${CODEX_EFFORT:-$default_effort}

if [[ $route == deepseek ]]; then
  catalog=$CODEX_HOME/catalog/opendesign-models.json
  python3 - "$catalog" <<'PY' || { echo "refusing: deepseek catalog has a non-null tool_mode; repair the local catalog first" >&2; exit 3; }
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
sys.exit(1 if any(m.get("tool_mode") is not None for m in data.get("models", [])) else 0)
PY
fi

if [[ $mode == resume ]]; then
  if (cd -- "$worktree" && nice codex exec resume -m "$model" -c "model_reasoning_effort=\"$effort\"" \
    --skip-git-repo-check --json "$session_id" - < "$prompt" > "$out/$name.jsonl" 2> "$out/$name.err"); then
    rc=0
  else
    rc=$?
  fi
else
  if nice codex exec -C "$worktree" -m "$model" -c "model_reasoning_effort=\"$effort\"" \
    -s danger-full-access --skip-git-repo-check -o "$out/$name.last.txt" --json - \
    < "$prompt" > "$out/$name.jsonl" 2> "$out/$name.err"; then
    rc=0
  else
    rc=$?
  fi
fi
printf 'EXIT=%s\n' "$rc" > "$out/$name.done"
exit "$rc"
