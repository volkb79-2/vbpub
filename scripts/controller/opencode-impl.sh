#!/usr/bin/env bash
# Launch an OpenCode job and persist the event stream and last assistant text.
# Usage: opencode-impl.sh <worktree> <prompt-file> <run-name>
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"
controller_init
[[ $# == 3 ]] || { echo "usage: $0 <worktree> <prompt-file> <run-name>" >&2; exit 2; }
worktree=$(realpath -e -- "$1") || exit 2
prompt=$(realpath -e -- "$2") || exit 2
name=$3
[[ $name =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && $name != *..* ]] || { echo "invalid run name: $name" >&2; exit 2; }
opencode_bin=${OPENCODE_COMMAND:-opencode}
if [[ -n ${OPENCODE_MODEL:-} ]]; then
  model=$OPENCODE_MODEL
else
  provider=${OPENCODE_PROVIDER:-opendesign_github2}
  model=$provider/deepseek-v4.1-flash-eco
fi

if (cd -- "$worktree" && nice "$opencode_bin" run -m "$model" --auto --format json --title "$name" "$(<"$prompt")" \
  > "$CONTROLLER_HOME/runs/$name.jsonl" 2> "$CONTROLLER_HOME/runs/$name.err"); then
  rc=0
else
  rc=$?
fi
python3 - "$CONTROLLER_HOME/runs/$name.jsonl" > "$CONTROLLER_HOME/runs/$name.last.txt" <<'PY' 2>/dev/null || true
import json, sys
last = ""
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    try:
        event = json.loads(line)
    except ValueError:
        continue
    value = event.get("text") or (event.get("part") or {}).get("text") or ""
    if value:
        last = value
print(last)
PY
printf 'EXIT=%s\n' "$rc" > "$CONTROLLER_HOME/runs/$name.done"
exit "$rc"
