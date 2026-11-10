#!/usr/bin/env bash
# Mechanical run/worktree hygiene check. Read-only; exits nonzero on red flags.
# Usage: job-check.sh <run-name> <worktree-path> <luna|deepseek|opencode|any|none> [absolute-report-path ...]
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"
controller_init
[[ $# -ge 3 ]] || { echo "usage: $0 <run-name> <worktree-path> <luna|deepseek|opencode|any|none> [absolute-report-path ...]" >&2; exit 2; }
run=$1 worktree=$2 route=$3; shift 3
[[ $run =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && $run != *..* ]] || { echo "invalid run name" >&2; exit 2; }
red=0
case $route in
  luna) trailer='Co-Authored-By: Codex gpt-6-luna <noreply@openai.com>' ;;
  deepseek|opencode) trailer='Co-Authored-By: Codex deepseek-v4.1-flash <noreply@openai.com>' ;;
  any) trailer_pattern='Co-Authored-By: (Codex (gpt-6-luna|deepseek-v4.1-flash)|Claude [A-Za-z0-9. ]+) <noreply@(openai|anthropic)\.com>' ;;
  none) trailer='' ;;
  *) echo "unknown route: $route" >&2; exit 2 ;;
esac
run_file=$CONTROLLER_HOME/runs/$run.jsonl
done_file=$CONTROLLER_HOME/runs/$run.done
stall_seconds=${JOB_STALL_SECONDS:-900}
[[ $stall_seconds =~ ^[0-9]+$ ]] || { echo "JOB_STALL_SECONDS must be decimal seconds" >&2; exit 2; }

if [[ -e $done_file ]]; then
  done_status=$(head -c 40 "$done_file")
  printf 'done: %s\n' "$done_status"
  if [[ $done_status != EXIT=0 ]]; then echo "CHILD EXIT IS NOT ZERO: $done_status"; red=1; fi
else
  if [[ ! -f $run_file ]]; then echo "RUN FILE MISSING: $run_file"; red=1
  else
    mtime=$(stat -c %Y "$run_file" 2>/dev/null || echo 0)
    age=$(( $(date +%s) - mtime )); (( age < 0 )) && age=0
    if (( age >= stall_seconds )); then echo "RUNNING STALLED: last write ${age}s ago"; red=1
    else echo "RUNNING: last write ${age}s ago"; fi
  fi
  exit "$red"
fi
[[ -f $run_file ]] || { echo "RUN FILE MISSING: $run_file"; exit 1; }
python3 - "$run_file" <<'PY' || red=1
import json, re, sys
last = None
messages = commands = 0
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    try:
        event = json.loads(line)
    except ValueError:
        continue
    item = event.get("item") or {}
    if event.get("type") == "item.completed":
        if item.get("type") == "agent_message":
            last = item.get("text") or ""
            messages += 1
        elif item.get("type") == "command_execution":
            commands += 1
    elif event.get("text") or (event.get("part") or {}).get("text"):
        last = event.get("text") or (event.get("part") or {}).get("text") or ""
        messages += 1
text = (last or "").strip()
low = text.lower()
flags = []
if not text:
    flags.append("EMPTY-RUN (no final agent message)")
else:
    if re.search(r"\b(i.?ll|i will|next,? i|now i.?ll|i.?m (now )?(going to|moving|running|rerunning|checking|writing))\b", low[-400:]) and not re.search(r"(report|head|verdict|pass|fail|complete)", low[-250:]):
        flags.append("INTENT-ONLY ending")
    if re.search(r"(<tool_call>|\"name\"\s*:\s*\"(shell|bash|exec)|\*\*\*\s*begin patch|^\$ )", text[-600:], re.M | re.I):
        flags.append("TOOL-CALL-AS-TEXT")
    if re.search(r"(still running|waiting for|in progress)", low[-300:]) and not re.search(r"(complete|done|passed|pass\b)", low[-300:]):
        flags.append("MID-WAIT ending (gate/process may be orphaned)")
last_preview = text[-260:].replace("\n", " ") if text else "-"
print(f"messages={messages} commands={commands} last: {last_preview}")
for flag in flags:
    print(f"STALL? {flag}")
sys.exit(1 if flags else 0)
PY

for report in "$@"; do
  if [[ $report != /* ]]; then echo "REPORT PATH NOT ABSOLUTE: $report"; red=1; continue; fi
  if [[ ! -f $report ]]; then echo "REPORT MISSING: $report"; red=1; continue; fi
  size=$(stat -c %s "$report")
  skeleton=$(grep -ciE '^(pending|tbd)\b|: *pending\b|\| pending \||Verdict: PENDING|in progress\.|^[0-9]+\. *$' "$report" || true)
  printf 'report: %s bytes=%s skeleton-markers=%s\n' "$report" "$size" "$skeleton"
  if (( size < 1500 || skeleton > 3 )); then echo 'REPORT LOOKS LIKE A SKELETON'; red=1; fi
done

if [[ ! -d $worktree ]]; then echo "WORKTREE MISSING: $worktree"; red=1
else
  head=$(git -C "$worktree" rev-parse --short HEAD 2>/dev/null || echo unknown)
  base=${CONTROLLER_BASE_REF:-origin/main}
  if git -C "$worktree" rev-parse --verify "$base^{commit}" >/dev/null 2>&1; then
    base_ok=1
    ahead=$(git -C "$worktree" rev-list --count "$base..HEAD" 2>/dev/null || echo unknown)
  else
    base_ok=0; ahead=unknown
    echo "BASE REF MISSING: $base"
    red=1
  fi
  printf 'worktree %s @ %s commits-ahead=%s\n' "$worktree" "$head" "$ahead"
  if [[ -n $(git -C "$worktree" status --short 2>/dev/null) ]]; then
    echo 'DIRTY WORKTREE:'; git -C "$worktree" status --short | head -5; red=1
  fi
  if [[ -n ${trailer:-}${trailer_pattern:-} && $base_ok == 1 ]]; then
    commits=$(git -C "$worktree" rev-list "$base..HEAD" 2>/dev/null) || { echo "COMMIT RANGE UNREADABLE: $base..HEAD"; commits=''; red=1; }
    bad=0
    while IFS= read -r commit; do
      [[ -n $commit ]] || continue
      if [[ -n ${trailer_pattern:-} ]]; then
        count=$(git -C "$worktree" log -1 --format=%B "$commit" | grep -Ec -- "$trailer_pattern" || true)
      else
        count=$(git -C "$worktree" log -1 --format=%B "$commit" | grep -Fxc -- "$trailer" || true)
      fi
      if [[ $count != 1 ]]; then
        echo "NO/WRONG TRAILER $(git -C "$worktree" log -1 --format='%h %s' "$commit" | cut -c1-90)"
        bad=1
      fi
    done <<< "$commits"
    if (( bad == 0 )); then echo "trailers OK (all commits incl. merges: $route)"; else red=1; fi
  fi
  stray=$(git -C "$worktree" status --short --ignored 2>/dev/null | grep -E '^\?\? (reports|scripts/.*/reports)/' | head -2 || true)
  if [[ -n $stray ]]; then
    echo "STRAY REPORT FILES IN WORKTREE (relative-path litter): $stray"
    red=1
  fi
fi
(( red == 0 )) && echo 'JOB-CHECK: green' || echo 'JOB-CHECK: RED FLAGS'
exit "$red"
