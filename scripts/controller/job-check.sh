#!/usr/bin/env bash
# Mechanical run/worktree evidence check. Read-only; exits nonzero on red flags.
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
run_file=$CONTROLLER_RUNS_DIR/$run.jsonl
done_file=$CONTROLLER_RUNS_DIR/$run.done

if [[ -e $done_file ]]; then
  done_status=$(head -n 1 "$done_file")
  printf 'done: %s\n' "$done_status"
  if [[ ! $done_status =~ ^EXIT=0([[:space:]]|$) ]]; then echo "CHILD EXIT IS NOT ZERO: $done_status"; red=1; fi
else
  echo "RUNNING (no .done)"
  exit 0
fi
[[ -f $run_file ]] || { echo "RUN FILE MISSING: $run_file"; exit 1; }
python3 - "$run_file" <<'PY' || red=1
import json
import re
import sys

last_message = ""
messages = commands = 0
turn_state = None
turn_last_item = None
usage_reset = None
last_error = ""

def error_text(value):
    if isinstance(value, dict):
        return str(value.get("message") or "")
    return str(value or "")

def usage_reset_time(value):
    match = re.search(
        r"hit your usage limit[\s\S]*?try again at\s+(\d{1,2}:\d{2}(?:\s*[AP]M)?)",
        value,
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else None

for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    try:
        event = json.loads(line)
    except ValueError:
        continue
    event_type = event.get("type")
    item = event.get("item") or {}
    if event_type == "turn.started":
        turn_state = "started"
        turn_last_item = None
        usage_reset = None
        last_error = ""
    elif event_type in ("item.started", "item.completed"):
        turn_state = "in_progress"
        turn_last_item = item.get("type")
        if event_type == "item.completed":
            if item.get("type") == "agent_message":
                last_message = item.get("text") or ""
                messages += 1
            elif item.get("type") == "command_execution":
                commands += 1
    elif event_type == "error":
        last_error = error_text(event.get("error") or event)
    elif event_type == "turn.completed":
        turn_state = "completed"
    elif event_type == "turn.failed":
        turn_state = "failed"
        message = error_text(event.get("error")) or last_error
        usage_reset = usage_reset_time(message)

text = last_message.strip()
low = text.lower()
flags = []
usage_limited = turn_state == "failed" and usage_reset is not None
if not text and not usage_limited:
    flags.append("EMPTY-RUN (no final agent message)")
else:
    if re.search(r"\b(i.?ll|i will|next,? i|now i.?ll|i.?m (now )?(going to|moving|running|rerunning|checking|writing))\b", low[-400:]) and not re.search(r"(report|head|verdict|pass|fail|complete)", low[-250:]):
        flags.append("INTENT-ONLY ending")
    if re.search(r"(<tool_call>|\"name\"\s*:\s*\"(shell|bash|exec)|\*\*\*\s*begin patch|^\$ )", text[-600:], re.M | re.I):
        flags.append("TOOL-CALL-AS-TEXT")
    tail = re.sub(r"(?:\.{2,}|…)+", " ", low[-500:])
    pending_wait = re.search(
        r"(?:^|[\n.!?;]\s*)(?:"
        r"(?:(?:i\s+am|i['’]m|we\s+are|we['’]re)\s+)(?:still\s+)?waiting\b"
        r"|(?:(?:i\s+am|i['’]m|we\s+are|we['’]re)\s+)continuing\s+to\s+wait\b"
        r"|(?:still\s+)?waiting\s+(?:for|on)\b"
        r"|no\s+(?:[a-z]+\s+)?release\s+yet\b[^\n.!?]*\bcontinuing\s+to\s+wait\b"
        r"|(?:the\s+)?(?:gate|lane|job|process|run|task|suite|check|build|test|command)\b"
        r"[^\n.!?]{0,100}\b(?:is|are|remains|remain)\s+(?:still\s+)?(?:running|waiting|in progress|pending|queued)\b"
        r")[^\n.!?]*[.!?]?\s*$",
        tail,
    )
    if pending_wait and re.search(
        r"\b(?:but|however|although|though|yet)\s+(?:the\s+)?(?:gate|lane|job|process|run|task|suite|check|build|test|command|it)\b"
        r"[^\n.!?]{0,100}\b(?:opened|passed|finished|completed|cleared|released|ended|resolved)\b",
        tail[pending_wait.start():],
    ):
        pending_wait = None
    if pending_wait:
        flags.append("MID-WAIT ending (gate/process may be orphaned)")

if usage_limited:
    flags.append(f"USAGE-LIMIT (reset {usage_reset})")
elif turn_state == "completed" and turn_last_item == "reasoning":
    flags.append("REASONING-END (turn completed after reasoning, without a message or tool call)")
elif turn_state != "completed":
    flags.append("KILLED (no turn.completed)")

last_preview = text[-260:].replace("\n", " ") if text else "-"
print(f"messages={messages} commands={commands} last: {last_preview}")
for flag in flags:
    print(f"STALL? {flag}")
sys.exit(1 if flags else 0)
PY

head_full=''
head_short=''
worktree_exists=0
if [[ ! -d $worktree ]]; then
  echo "WORKTREE MISSING: $worktree"
  red=1
else
  worktree_exists=1
  head_full=$(git -C "$worktree" rev-parse HEAD 2>/dev/null || true)
  head_short=$(git -C "$worktree" rev-parse --short HEAD 2>/dev/null || true)
  base=${CONTROLLER_BASE_REF:-origin/main}
  if [[ -z $head_full ]] || ! git -C "$worktree" rev-parse --verify "$base^{commit}" >/dev/null 2>&1; then
    ahead=unknown
    echo "BASE REF MISSING OR HEAD UNREADABLE: $base"
    red=1
  else
    ahead=$(git -C "$worktree" rev-list --count "$base..HEAD" 2>/dev/null || echo unknown)
  fi
  printf 'worktree %s @ %s commits-ahead=%s\n' "$worktree" "${head_short:-unknown}" "$ahead"
  if [[ -n $(git -C "$worktree" status --short 2>/dev/null) ]]; then
    echo 'DIRTY WORKTREE:'; git -C "$worktree" status --short | head -5; red=1
  fi
  if [[ -n ${trailer:-}${trailer_pattern:-} && $ahead != unknown ]]; then
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
  if [[ $ahead =~ ^[0-9]+$ ]] && (( ahead > 0 )); then
    gate_log=''
    while IFS= read -r -d '' candidate; do
      if python3 - "$candidate" "$head_full" <<'PY'
import pathlib
import re
import sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
head = re.escape(sys.argv[2])
bound = re.search(r"(?m)(?:^|[ \t])run-gate\.commit=" + head + r"(?=$|[ \t\r])", text)
verdicts = re.findall(
    r"(?im)^\s*run-gate:\s*lane\b[^\r\n]*\bverdict\s+([A-Z][A-Z_]*)\b",
    text,
)
final_verdict = verdicts[-1].upper() if verdicts else ""
passed = final_verdict == "PASS"
sys.exit(0 if bound and passed else 1)
PY
      then
        gate_log=$candidate
        break
      fi
    done < <(find "$CONTROLLER_LOGS_DIR" -type f -name '*.log' -print0 2>/dev/null)
    if [[ -z $gate_log ]]; then
      echo "GATE EVIDENCE MISSING: no log under $CONTROLLER_LOGS_DIR has run-gate.commit=$head_full and a PASS summary"
      red=1
    else
      printf 'gate evidence: %s (run-gate.commit=%s PASS)\n' "$gate_log" "$head_full"
    fi
  fi
  stray=$(git -C "$worktree" status --short --ignored 2>/dev/null | grep -E '^\?\? (reports|scripts/.*/reports)/' | head -2 || true)
  if [[ -n $stray ]]; then
    echo "STRAY REPORT FILES IN WORKTREE (relative-path litter): $stray"
    red=1
  fi
fi

for report in "$@"; do
  if [[ $report != /* ]]; then echo "REPORT PATH NOT ABSOLUTE: $report"; red=1; continue; fi
  if [[ ! -f $report ]]; then echo "REPORT MISSING: $report"; red=1; continue; fi
  size=$(stat -c %s "$report")
  skeleton=$(grep -ciE '^(pending|tbd)\b|: *pending\b|\| pending \||Verdict: PENDING|in progress\.|^[0-9]+\. *$' "$report" || true)
  printf 'report: %s bytes=%s skeleton-markers=%s\n' "$report" "$size" "$skeleton"
  if (( size < 1500 || skeleton > 3 )); then echo 'REPORT LOOKS LIKE A SKELETON'; red=1; fi
  if (( worktree_exists )) && [[ -n $head_full ]] && ! grep -Fq -- "$head_full" "$report" && ! grep -Fq -- "$head_short" "$report"; then
    echo "REPORT DOES NOT NAME HEAD: $report (expected $head_short or $head_full)"
    red=1
  fi
done

(( red == 0 )) && echo 'JOB-CHECK: green' || echo 'JOB-CHECK: RED FLAGS'
exit "$red"
