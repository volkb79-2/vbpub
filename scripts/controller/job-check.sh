#!/usr/bin/env bash
# Mechanical run/worktree evidence check. Read-only; exits nonzero on red flags.
#
# Completed pi runs carry their terminal class in `.done` and in the stream. Two
# classes of that vocabulary are named here so the controller cannot miss them:
#   CONTEXT-FULL       the assistant stopReason is `length` (the context window
#                      filled). A real end state: start a FRESH successor from
#                      the run's forward-oriented state file; never continue the
#                      full context.
#   TOOL-CALL-NO-TEXT  a settled `stop` whose final message is a tool call with
#                      no text: the run ended mid-work. Inspect the worktree and
#                      the last tool output, then resume or restart.
# Both are red flags here; neither is `SUCCESS` and neither is `EMPTY-RUN`.
# Usage: job-check.sh <run-name> <worktree-path> <luna|sol|deepseek|deepseek-volker|deepseek-github2|deepseek-pi|deepseek-pi-volker|deepseek-pi-github2|opencode|any|none> [absolute-report-path ...]
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"
controller_init
[[ $# -ge 3 ]] || { echo "usage: $0 <run-name> <worktree-path> <luna|sol|deepseek|deepseek-volker|deepseek-github2|deepseek-pi|deepseek-pi-volker|deepseek-pi-github2|opencode|any|none> [absolute-report-path ...]" >&2; exit 2; }
run=$1 worktree=$2 route=$3; shift 3
[[ $run =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && $run != *..* ]] || { echo "invalid run name" >&2; exit 2; }
probe_run_lock() {
  # $1 the run name's lock file. Sets lock_state to `held` (a wrapper holds the
  # name right now), `free` (nobody does) or `unknown` (the file cannot be
  # opened). The kernel lock, not the record, says whether a wrapper is alive:
  # a launch records its identity only once it is real, so a wrapper that is
  # still preflighting holds the lock while the record is empty or still names
  # the PREVIOUS (dead) owner.
  local probe_fd
  lock_state=unknown
  if exec {probe_fd}< "$1" 2>/dev/null; then
    if flock -n "$probe_fd"; then lock_state=free; else lock_state=held; fi
    exec {probe_fd}>&-
  fi
}
recover_thread_from_stream() {
  # The first session/thread id the run stream carries, or nothing: a fact read
  # from the record the run already produced, never an invented id.
  local stream=$1
  [[ -r $stream ]] || return 1
  python3 - "$stream" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8", errors="replace") as source:
    for line in source:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "session" and isinstance(event.get("id"), str):
            print(event["id"])
            raise SystemExit(0)
        if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
            print(event["thread_id"])
            raise SystemExit(0)
raise SystemExit(1)
PY
}

red=0
case $route in
  luna)
    trailer_mode=single
    trailer='Co-Authored-By: Codex gpt-6-luna <noreply@openai.com>'
    ;;
  sol)
    trailer_mode=single
    trailer='Co-Authored-By: Codex gpt-6-sol <noreply@openai.com>'
    ;;
  deepseek|deepseek-volker|deepseek-github2|deepseek-pi|deepseek-pi-volker|deepseek-pi-github2|opencode)
    trailer_mode=single
    trailer='Co-Authored-By: Codex deepseek-v4.1-flash <noreply@openai.com>'
    ;;
  any)
    trailer_mode=allowed_set
    trailer_pattern='Co-Authored-By: (Codex (gpt-6-luna|gpt-6-sol|deepseek-v4.1-flash)|Claude [A-Za-z0-9. ]+) <noreply@(openai|anthropic)\.com>'
    ;;
  none)
    trailer_mode=none
    trailer=''
    ;;
  *) echo "unknown route: $route" >&2; exit 2 ;;
esac
run_file=$CONTROLLER_RUNS_DIR/$run.jsonl
done_file=$CONTROLLER_RUNS_DIR/$run.done

if [[ -e $done_file ]]; then
  done_status=$(head -n 1 "$done_file")
  printf 'done: %s\n' "$done_status"
  if [[ ! $done_status =~ ^EXIT=0([[:space:]]|$) ]]; then echo "CHILD EXIT IS NOT ZERO: $done_status"; red=1; fi
  # pi-review.sh appends REVIEW-MODIFIED-TREE AFTER pi-impl.sh's own marker, and
  # that marker can read `EXIT=0 OUTCOME=SUCCESS` (a reviewer commit leaves a
  # clean tree and a present report). The flag is the run's verdict, so it is
  # read from the whole marker: line 1 alone is a false certificate.
  # The review launcher appends its own verdict line beside pi-impl.sh's marker:
  # REVIEW-MODIFIED-TREE (the tree moved), REVIEW-BUDGET (stopped over budget),
  # REVIEW-NO-REPORT (settled without any review text), REVIEW-NOT-SETTLED
  # (settled in a class that is not SUCCESS/UNCOMMITTED). Each of those can stand
  # beside `EXIT=0`, so every one of them makes the run red. The rule matches
  # ctl-status.sh and job-status.sh: a verdict is any line that contains it.
  review_verdict=''
  for verdict in REVIEW-MODIFIED-TREE REVIEW-BUDGET REVIEW-NO-REPORT REVIEW-NOT-SETTLED; do
    grep -qF -- "$verdict" "$done_file" && review_verdict=$verdict
  done
  if [[ -n $review_verdict ]]; then
    echo "$review_verdict: the review did not complete cleanly (see $done_file)"
    red=1
  fi
else
  # No completion marker. The word RUNNING asserts that a process exists, so it
  # is read from the run name's own owner record (the same `<name>.lock` every
  # wrapper writes on a claim, lib.sh controller_run_claim) instead of assumed.
  # Three distinct real states collapse into "no .done" otherwise: a live
  # wrapper, a wrapper that died (TERM/KILL/OOM/container restart), and a name
  # that no wrapper ever owned.
  owner_record=$CONTROLLER_RUNS_DIR/$run.lock
  owner_pid='' owner_ticks='' owner_boot='' owner_started=''
  if [[ -r $owner_record ]]; then
    owner_line=$(head -n 1 "$owner_record" 2>/dev/null || true)
    # shellcheck disable=SC2086 # the record is one line of space-separated key=value tokens
    for token in $owner_line; do
      case $token in
        pid=*) owner_pid=${token#pid=} ;;
        pid_start_ticks=*) owner_ticks=${token#pid_start_ticks=} ;;
        boot_id=*) owner_boot=${token#boot_id=} ;;
        started_utc=*) owner_started=${token#started_utc=} ;;
      esac
    done
  fi
  dead_reason=''
  if [[ $owner_pid =~ ^[0-9]+$ ]]; then
    current_boot=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)
    if [[ -n $owner_boot && -n $current_boot && $owner_boot != "$current_boot" ]]; then
      # A pid cannot survive a reboot, so the recorded owner is gone even if its
      # number happens to be in use now.
      dead_reason="wrapper pid $owner_pid is from a previous boot ($owner_boot)"
    elif ! kill -0 "$owner_pid" 2>/dev/null; then
      dead_reason="wrapper pid $owner_pid gone"
    else
      live_ticks=$(controller_pid_start_ticks "$owner_pid" 2>/dev/null || printf '%s' '-')
      if [[ $owner_ticks =~ ^[0-9]+$ && $live_ticks =~ ^[0-9]+$ && $owner_ticks != "$live_ticks" ]]; then
        # The pid exists but its kernel start time differs: a recycled pid, not
        # this run's owner.
        dead_reason="wrapper pid $owner_pid recycled (recorded start $owner_ticks, now $live_ticks)"
      fi
    fi
  fi
  lock_state=unknown
  if [[ -n $dead_reason ]]; then probe_run_lock "$owner_record"; fi
  if [[ -n $dead_reason && $lock_state == held ]]; then
    # The record still names the previous owner, but a wrapper holds the name
    # right now: a resume of a DIED name that has not reached its owner record yet.
    echo "RUNNING (preflighting, previous owner gone)"
    printf 'owner record: previous owner %s (%s); the run name is held by a wrapper that is still preflighting (lock %s)\n' \
      "${owner_pid:-<unknown>}" "$dead_reason" "$owner_record"
  elif [[ -n $dead_reason ]]; then
    # A dead owner is never RUNNING: the controller must be able to relaunch.
    echo "DIED ($dead_reason, no .done)"
    echo "RUN-NAME OWNER DIED: <run>.done is missing and the recorded owner is not alive; relaunch the run or retire its records deliberately"
    # A killed wrapper often dies before writing `<run>.thread`, and a controller
    # that then has no thread id cannot resume - it can only start over. The id
    # is already in the run's own stream (pi: the first `session` event, Codex:
    # `thread.started`), so it is READ from there and reported as the recovery it
    # is, with the exact resume command it enables.
    thread_id=''
    if [[ -r $CONTROLLER_RUNS_DIR/$run.thread ]]; then
      thread_id=$(head -n 1 "$CONTROLLER_RUNS_DIR/$run.thread" 2>/dev/null || true)
    fi
    if [[ -z $thread_id ]]; then
      thread_id=$(recover_thread_from_stream "$CONTROLLER_RUNS_DIR/$run.jsonl") || thread_id=''
      if [[ -n $thread_id ]]; then
        printf 'thread: %s (recovered from %s.jsonl; the wrapper died before writing .thread)\n' \
          "$thread_id" "$run"
      else
        printf 'thread: none (no .thread file and no session/thread id in %s.jsonl)\n' "$run"
      fi
    else
      printf 'thread: %s\n' "$thread_id"
    fi
    if [[ -n $thread_id ]]; then
      case $route in
        deepseek-pi|deepseek-pi-volker|deepseek-pi-github2)
          printf 'resume: %s %s <prompt-file> %s resume %s\n' \
            "$SCRIPT_DIR/pi-impl.sh" "$worktree" "$run" "$thread_id"
          ;;
        luna|sol|deepseek|deepseek-volker|deepseek-github2)
          printf 'resume: %s %s <prompt-file> %s resume %s\n' \
            "$SCRIPT_DIR/codex-impl.sh" "$worktree" "$run" "$thread_id"
          ;;
        opencode)
          printf 'resume: %s %s <prompt-file> %s\n' \
            "$SCRIPT_DIR/opencode-impl.sh" "$worktree" "$run"
          ;;
      esac
    fi
    red=1
  elif [[ $owner_pid =~ ^[0-9]+$ ]]; then
    echo "RUNNING (no .done)"
    printf 'owner record: pid %s alive%s (lock %s)\n' "$owner_pid" \
      "${owner_started:+ since $owner_started}" "$owner_record"
  else
    # No owner identity was recorded. An EMPTY lock file with no stream is a name
    # that was CLAIMED and never started: a launch records its identity only once
    # it is real, so a launch refused after the claim (a bad route, blocked
    # accounts, a missing credential) leaves exactly this. The kernel lock tells
    # it from a wrapper that is still preflighting.
    if [[ -e $owner_record && ! -s $owner_record && ! -e $run_file ]]; then
      probe_run_lock "$owner_record"
    fi
    if [[ $lock_state == free ]]; then
      echo "NOT-STARTED (no .done, no stream, no owner record: the run name was claimed but no wrapper started an agent on it)"
      echo "RUN NEVER STARTED: the launch was refused or died before it recorded itself; read that launch's own message, fix the cause and relaunch the run"
      red=1
    elif [[ $lock_state == held ]]; then
      echo "RUNNING (no .done)"
      printf 'owner record: none yet (%s is held by a wrapper that is still preflighting)\n' "$owner_record"
    else
      echo "RUNNING (no .done)"
      printf 'owner record: none (%s absent or unreadable; a live wrapper and a dead one cannot be told apart here)\n' \
        "$owner_record"
    fi
  fi
  if (( red != 0 )); then exit 1; fi
  exit 0
fi
[[ -f $run_file ]] || { echo "RUN FILE MISSING: $run_file"; exit 1; }
usage_retry_hint=$(usage_retry_text "$run_file") || usage_retry_hint=''
python3 - "$run_file" "$usage_retry_hint" <<'PY' || red=1
import json
import re
import sys

last_message = ""
messages = commands = 0
turn_state = None
turn_last_item = None
usage_reset = None
last_error = ""
pi_detected = False
pi_stop_reason = None
pi_usage_limited = False
pi_has_tool_call = False

def pi_text(message):
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return message.get("text", "") if isinstance(message.get("text"), str) else ""

def error_text(value):
    if isinstance(value, dict):
        return str(value.get("message") or "")
    return str(value or "")

def pi_402_error(message):
    if not isinstance(message, str):
        return False
    match = re.match(r"^\s*402:\s*(\{.*\})\s*$", message, re.DOTALL)
    if match is None:
        return False
    try:
        value = json.loads(match.group(1))
    except ValueError:
        return False
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            if current.get("code") == "insufficient_balance":
                return True
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return False

for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    try:
        event = json.loads(line)
    except ValueError:
        continue
    event_type = event.get("type")
    item = event.get("item") or {}
    if event_type == "session":
        pi_detected = True
    elif event_type == "message_end":
        message = event.get("message")
        if isinstance(message, dict) and message.get("role") == "assistant":
            pi_detected = True
            last_message = pi_text(message)
            messages += 1
            pi_stop_reason = message.get("stopReason")
            content = message.get("content")
            pi_has_tool_call = isinstance(content, list) and any(
                isinstance(part, dict) and part.get("type") == "toolCall" for part in content
            )
            if pi_stop_reason == "error":
                usage_reset = sys.argv[2] or None
                pi_usage_limited = pi_402_error(message.get("errorMessage"))
    elif event_type == "tool_execution_start":
        pi_detected = True
        turn_state = "in_progress"
        commands += 1
    elif event_type == "agent_settled":
        pi_detected = True
        turn_state = "failed" if event.get("aborted") is True else "completed"
        turn_last_item = "agent_message"
    elif event_type == "turn.started":
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
        usage_reset = sys.argv[2] or None

text = last_message.strip()
low = text.lower()
flags = []
usage_limited = (
    (turn_state == "failed" and usage_reset is not None)
    or (pi_detected and pi_stop_reason == "error" and pi_usage_limited)
)
if not usage_limited:
    if pi_detected and pi_stop_reason == "error":
        flags.append("PI-ERROR (assistant stopReason=error)")
    if pi_detected and pi_stop_reason == "length":
        # The context window filled: a real end state with its own remedy, not
        # an unattributable stream and not a finished job.
        flags.append(
            "CONTEXT-FULL (assistant stopReason=length; start a fresh successor "
            "from the run's state file)"
        )
    elif pi_detected and pi_stop_reason == "stop" and pi_has_tool_call and not text:
        # A final message that is a tool call with no text ended mid-work.
        flags.append(
            "TOOL-CALL-NO-TEXT (final assistant message is a tool call with no "
            "text; inspect the worktree, then resume or restart)"
        )
    elif not text:
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
    flags.append(f"USAGE-LIMIT (reset {usage_reset or 'unknown'})")
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
  if [[ $trailer_mode != none && $ahead != unknown ]]; then
    commits=$(git -C "$worktree" rev-list "$base..HEAD" 2>/dev/null) || { echo "COMMIT RANGE UNREADABLE: $base..HEAD"; commits=''; red=1; }
    bad=0
    while IFS= read -r commit; do
      [[ -n $commit ]] || continue
      if [[ $trailer_mode == allowed_set ]]; then
        count=$(git -C "$worktree" log -1 --format=%B "$commit" | grep -Ec -- "$trailer_pattern" || true)
      else
        count=$(git -C "$worktree" log -1 --format=%B "$commit" | grep -Fxc -- "$trailer" || true)
      fi
      if [[ $trailer_mode == allowed_set ]]; then
        if (( count == 0 )); then
          echo "NO/WRONG TRAILER $(git -C "$worktree" log -1 --format='%h %s' "$commit" | cut -c1-90)"
          bad=1
        elif (( count > 1 )); then
          echo "INFO: multiple agent trailers $(git -C "$worktree" log -1 --format='%h %s' "$commit" | cut -c1-90)"
        fi
      elif [[ $count != 1 ]]; then
        echo "NO/WRONG TRAILER $(git -C "$worktree" log -1 --format='%h %s' "$commit" | cut -c1-90)"
        bad=1
      fi
    done <<< "$commits"
    if (( bad == 0 )); then echo "trailers OK (all commits incl. merges: $route)"; else red=1; fi
  fi
  if [[ $ahead =~ ^[0-9]+$ ]] && (( ahead > 0 )); then
    gate_log=''
    while IFS= read -r -d '' candidate; do
      if python3 - "$candidate" "$head_full" "$CONTROLLER_LOGS_DIR" "$@" <<'PY'
import pathlib
import re
import sys

candidate = pathlib.Path(sys.argv[1])
head_full = sys.argv[2]
logs_dir = pathlib.Path(sys.argv[3])
report_args = sys.argv[4:]
text = candidate.read_text(encoding="utf-8", errors="replace")
head = re.escape(head_full)
bound = bool(re.search(
    r"(?m)(?:^|[ \t])run-gate\.commit=" + head + r"(?=$|[ \t\r])",
    text,
))
verdicts = re.findall(
    r"(?im)^\s*run-gate:\s*lane\b[^\r\n]*\bverdict\s+([A-Z][A-Z_]*)\b",
    text,
)
final_verdict = verdicts[-1].upper() if verdicts else ""
passed = final_verdict == "PASS"

if passed and not bound:
    collected = re.findall(
        r"(?m)^\s*run-gate:\s*collected\s+([A-Za-z0-9][A-Za-z0-9_.-]*)"
        r"\s+\(exited\b[^\r\n]*\)\s*$",
        text,
    )
    reattach_ok = False
    if len(set(collected)) == 1:
        container = collected[0]
        head_short = head_full[:8]
        if f"-{head_short}-" in container:
            label_log = None
            label_pattern = re.compile(
                r"(?:^|[ \t])run-gate\.commit=" + head + r"(?=$|[ \t\r])"
            )
            for log in sorted(logs_dir.rglob("*.log")):
                if log == candidate or not log.is_file():
                    continue
                try:
                    earlier = log.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for line in earlier.splitlines():
                    if container in line and label_pattern.search(line):
                        label_log = log
                        break
                if label_log is not None:
                    break
            reports = [pathlib.Path(p) for p in report_args if p.startswith("/")]
            named_report = None
            for report in reports:
                if not report.is_file():
                    continue
                try:
                    report_text = report.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if container in report_text:
                    named_report = report
                    break
            reattach_ok = label_log is not None and named_report is not None
            if reattach_ok:
                bound = True
                print(
                    "run-gate: re-attach gate evidence: "
                    f"container={container} label_log={label_log} "
                    f"report={named_report} run-gate.commit={head_full} PASS"
                )
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
