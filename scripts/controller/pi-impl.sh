#!/usr/bin/env bash
# Launch one pi Open Design job and persist its event stream, overwatch state
# and completion outcome.
# Usage: pi-impl.sh <worktree> <absolute-prompt-file> <run-name> [resume <session-id>] [--report <absolute-path>]
#
# Outcome vocabulary written to <run>.done as `EXIT=<n> OUTCOME=<class>`
# (the first line keeps its historical shape; extra lines are additive):
#
#   SUCCESS          settled `stop` with assistant text, a clean worktree, and
#                    the expected report present when a report path is known.
#   EMPTY-COMPLETION the final assistant message carried no text (empty or
#                    thinking-only) and no tool call. This is a provider/model
#                    anomaly, not a limit: the wrapper continues the same
#                    session (see <run>.events) before giving up.
#   EMPTY-COMPLETION-RETRIES-EXHAUSTED
#                    still empty after the bounded continues; the controller
#                    decides to restart or abandon.
#   UNCOMMITTED      exit 0 and the worktree has uncommitted changes.
#   NO-REPORT        the expected report file is missing.
#   USAGE-LIMIT      a provider 402 `insufficient_balance` stop.
#   ERROR            a provider error stop, or a nonzero pi exit.
#   INCOMPLETE       a settled or unreadable stream that is not a stop.
#
# The expected report is `--report <path>` or `PI_REPORT`; unset means no
# report check. Every run also gets <run>.events (JSON lines, one per
# recorded continue).
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
caller_pi_route=${PI_ROUTE-}
caller_pi_model=${PI_MODEL-}
caller_pi_thinking=${PI_THINKING-}
caller_pi_session_dir=${PI_SESSION_DIR-}
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"
controller_init
[[ -z $caller_pi_route ]] || PI_ROUTE=$caller_pi_route
[[ -z $caller_pi_model ]] || PI_MODEL=$caller_pi_model
[[ -z $caller_pi_thinking ]] || PI_THINKING=$caller_pi_thinking
[[ -z $caller_pi_session_dir ]] || PI_SESSION_DIR=$caller_pi_session_dir

usage() {
  echo "usage: $0 <worktree> <absolute-prompt-file> <run-name> [resume <session-id>] [--report <absolute-path>]" >&2
  exit 2
}

report_path=''
positional=()
while (( $# > 0 )); do
  case $1 in
    --report)
      (( $# >= 2 )) || usage
      report_path=$2
      shift 2
      ;;
    --report=*)
      report_path=${1#--report=}
      shift
      ;;
    *)
      positional+=("$1")
      shift
      ;;
  esac
done
[[ -z ${PI_REPORT-} ]] || report_path=${report_path:-$PI_REPORT}
set -- "${positional[@]}"
[[ $# -ge 3 && $# -le 5 ]] || usage
if [[ -n $report_path ]]; then
  [[ $report_path == /* ]] || { echo 'report path must be absolute' >&2; exit 2; }
fi
[[ $2 == /* ]] || { echo 'prompt path must be absolute' >&2; exit 2; }
worktree=$(realpath -e -- "$1") || usage
prompt=$(realpath -e -- "$2") || usage
name=$3
[[ $name =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && $name != *..* ]] || {
  echo "invalid run name: $name" >&2
  exit 2
}
mode=${4:-new}
session_id=${5:-}
if [[ $mode == resume ]]; then
  [[ -n $session_id && $session_id =~ ^[A-Za-z0-9._-]+$ && $session_id != *..* ]] || usage
elif [[ $mode != new || $# -gt 4 ]]; then
  usage
fi

# The clean/committed classification below is a fact read from git, not a
# guess: a worktree that is not a git worktree cannot answer it, so refuse.
if ! git -C "$worktree" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "worktree is not a git worktree: $worktree" >&2
  exit 2
fi

retry_limit=${PI_EMPTY_RETRY_LIMIT:-2}
[[ $retry_limit =~ ^[0-9]+$ ]] || {
  echo 'PI_EMPTY_RETRY_LIMIT must be a non-negative integer' >&2
  exit 2
}
continue_prompt_file=${PI_CONTINUE_PROMPT_FILE:-"$SCRIPT_DIR/continue-empty-completion.md"}
if [[ ! -r $continue_prompt_file ]]; then
  echo "continue prompt file is not readable: $continue_prompt_file" >&2
  exit 2
fi
out=$CONTROLLER_RUNS_DIR
if [[ $mode == new ]]; then
  existing=()
  for extension in 'done' 'jsonl' 'err' 'events'; do
    [[ -e $out/$name.$extension ]] && existing+=("$out/$name.$extension")
  done
  if [[ ${CONTROLLER_RUN_FORCE:-0} != 1 ]] && (( ${#existing[@]} > 0 )); then
    echo "controller run name already has evidence: $name" >&2
    printf '  %s\n' "${existing[@]}" >&2
    echo 'set CONTROLLER_RUN_FORCE=1 only to replace an intentionally abandoned or failed run' >&2
    exit 2
  fi
fi

route=${PI_ROUTE:-deepseek-pi}
route_record=$route
od_account=''
od_start_kind=''
od_recorded_account=''
case $route in
  deepseek-pi)
    od_start_kind=auto
    if [[ $mode == resume ]]; then
      if od_account=$(CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" \
        find-thread-account "$session_id" "$out"); then
        od_recorded_account=$od_account
        od_start_kind=resume
      else
        lookup_rc=$?
        if (( lookup_rc != 1 )); then exit "$lookup_rc"; fi
        printf 'no recorded Open Design account for session %s; picking a fresh account\n' "$session_id" >&2
        od_start_kind=auto-resume
      fi
    fi
    ;;
  deepseek-pi-volker|deepseek-pi-github2)
    od_account=${route#deepseek-pi-}
    od_start_kind=pinned
    ;;
  *) echo "unknown PI_ROUTE=$route" >&2; exit 2 ;;
esac

model=${PI_MODEL:-deepseek-v4.1-flash-eco}
thinking=${PI_THINKING:-high}
session_dir=${PI_SESSION_DIR:-"$HOME/.pi/agent/sessions"}

if [[ -n $od_start_kind ]]; then
  case $od_start_kind in
    auto)
      od_account=$(CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" \
        start-pi auto) || exit $?
      ;;
    auto-resume)
      od_account=$(CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" \
        start-pi auto "$session_id") || exit $?
      ;;
    resume)
      od_account=$(CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" \
        start-pi-resume "$od_recorded_account" "$session_id") || exit $?
      ;;
    pinned)
      if [[ $mode == resume ]]; then
        CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" \
          start-pi "$od_account" "$session_id" >/dev/null || exit $?
      else
        CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" \
          start-pi "$od_account" >/dev/null || exit $?
      fi
      ;;
  esac
  case $od_account in
    volker|github2) route_record=deepseek-pi-$od_account ;;
    *) echo 'Open Design account selector returned an invalid account' >&2; exit 2 ;;
  esac
else
  echo 'pi route did not select an Open Design account' >&2
  exit 2
fi

printf '%s\n' "$worktree" > "$out/$name.wt"
printf '%s\n' "$route_record" > "$out/$name.route"
if [[ $mode == resume ]]; then
  rm -f -- "$out/$name.done"
  printf '%s\n' "$session_id" > "$out/$name.thread"
fi

if [[ ! -r $HOME/.env.ai ]]; then
  echo "pi route requires the local \$HOME/.env.ai file" >&2
  exit 2
fi
# shellcheck disable=SC1091
if ! . "$HOME/.env.ai" >/dev/null 2>&1; then
  echo 'could not load the local pi API-key environment' >&2
  exit 2
fi
case $od_account in
  volker)
    # Length test, never a value test: `bash -x` would trace the value into stderr.
    # shellcheck disable=SC2015 # intended: any failed test (unset or empty) takes the error branch
    [[ -v OPENDESIGN_API_KEY_VOLKER ]] && (( ${#OPENDESIGN_API_KEY_VOLKER} > 0 )) || {
      echo "OPENDESIGN_API_KEY_VOLKER is not set by \$HOME/.env.ai" >&2
      exit 2
    }
    export OPENDESIGN_API_KEY_VOLKER
    unset OPENDESIGN_API_KEY_GITHUB2
    provider=opendesign_volker
    ;;
  github2)
    # shellcheck disable=SC2015 # intended: any failed test (unset or empty) takes the error branch
    [[ -v OPENDESIGN_API_KEY_GITHUB2 ]] && (( ${#OPENDESIGN_API_KEY_GITHUB2} > 0 )) || {
      echo "OPENDESIGN_API_KEY_GITHUB2 is not set by \$HOME/.env.ai" >&2
      exit 2
    }
    export OPENDESIGN_API_KEY_GITHUB2
    unset OPENDESIGN_API_KEY_VOLKER
    provider=opendesign_github2
    ;;
esac

prompt_text=''
IFS= read -r -d '' prompt_text < "$prompt" || :
continue_prompt=''
IFS= read -r -d '' continue_prompt < "$continue_prompt_file" || :
base_pi_args=(-p --mode json --model "$provider/$model" --thinking "$thinking" --session-dir "$session_dir")
resume_target=''
[[ $mode != resume ]] || resume_target=$session_id

# One pi invocation. $3 is 1 for the first run (truncate stderr) and 0 for a
# continuation that appends to the run's stream and stderr.
invoke_pi() {
  local prompt_text=$1 stream=$2 stderr_mode=$3
  local -a argv=("${base_pi_args[@]}")
  [[ -z $resume_target ]] || argv+=(--session "$resume_target")
  if [[ $stderr_mode == 1 ]]; then
    (cd -- "$worktree" && nice pi "${argv[@]}" -- "$prompt_text" \
      < /dev/null > "$stream" 2> "$out/$name.err")
  else
    (cd -- "$worktree" && nice pi "${argv[@]}" -- "$prompt_text" \
      < /dev/null > "$stream" 2>> "$out/$name.err")
  fi
}

# Classify the last assistant message of the (possibly appended) run stream.
# Fields, tab separated: session id, stop reason, usage-limited flag, settled
# flag, aborted flag, has-text flag, has-tool-call flag.
inspect_run() {
  python3 - "$out/$name.jsonl" "$out/$name.last.txt" <<'PY'
import json
import sys
from pathlib import Path

stream = Path(sys.argv[1])
last_text = ""
session_id = ""
stop_reason = ""
usage_limited = False
settled = False
aborted = False
has_tool_call = False

def coding_plan(value):
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

def message_text(message):
    value = message.get("content")
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        chunks = []
        for part in value:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
        return "".join(chunks)
    return message.get("text", "") if isinstance(message.get("text"), str) else ""

def has_tool_calls(message):
    value = message.get("content")
    if isinstance(value, list):
        for part in value:
            if isinstance(part, dict) and part.get("type") == "toolCall":
                return True
    return False

with stream.open(encoding="utf-8", errors="replace") as source:
    for line in source:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "session" and not session_id:
            value = event.get("id") or event.get("sessionId")
            if isinstance(value, str):
                session_id = value
        if event.get("type") == "message_end":
            message = event.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                stop_reason = str(message.get("stopReason") or "")
                last_text = message_text(message)
                has_tool_call = has_tool_calls(message)
                error_message = message.get("errorMessage")
                if stop_reason == "error" and isinstance(error_message, str):
                    prefix, separator, encoded = error_message.partition(":")
                    if separator and prefix.strip() == "402":
                        try:
                            usage_limited = coding_plan(json.loads(encoded.strip()))
                        except ValueError:
                            usage_limited = False
        if event.get("type") == "agent_settled":
            settled = True
            aborted = event.get("aborted") is True

Path(sys.argv[2]).write_text(last_text + "\n", encoding="utf-8")
print("\t".join((
    session_id, stop_reason, "1" if usage_limited else "0",
    "1" if settled else "0", "1" if aborted else "0",
    "1" if last_text.strip() else "0", "1" if has_tool_call else "0",
)))
PY
}

# Report the last outcome class. $1 is the pi exit status; the remaining
# classification facts come from inspect_run.
classify_run() {
  local rc=$1
  if [[ $stop_reason == error && $usage_limited == 1 ]]; then
    echo USAGE-LIMIT
  elif [[ $stop_reason == error ]]; then
    echo ERROR
  elif [[ $rc != 0 ]]; then
    echo ERROR
  elif [[ $settled != 1 || $aborted != 0 || -z $thread_id || $stop_reason != stop ]]; then
    echo INCOMPLETE
  elif [[ $has_text != 1 && $has_tool_call != 1 ]]; then
    # No text and no tool call: pi treats that as the end of the run, so a
    # provider/model anomaly shows up as an exit-0 stop with nothing said.
    echo EMPTY-COMPLETION
  elif [[ -n $(git -C "$worktree" status --porcelain 2>/dev/null) ]]; then
    echo UNCOMMITTED
  elif [[ -n $report_path && ! -f $report_path ]]; then
    echo NO-REPORT
  else
    echo SUCCESS
  fi
}

: > "$out/$name.events"
if invoke_pi "$prompt_text" "$out/$name.jsonl" 1; then
  rc=0
else
  rc=$?
fi

inspection=$(inspect_run)
IFS=$'\t' read -r stream_session stop_reason usage_limited settled aborted \
  has_text has_tool_call <<< "$inspection"
thread_id=${stream_session:-$session_id}
if [[ -n $thread_id ]]; then
  printf '%s\n' "$thread_id" > "$out/$name.thread"
  if CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" \
    record-thread "$thread_id" "$od_account"; then
    :
  else
    thread_state_rc=$?
    printf 'Open Design session-account bookkeeping failed for run %s (exit %s); preserving pi exit %s\n' \
      "$name" "$thread_state_rc" "$rc" >&2
  fi
fi
outcome=$(classify_run "$rc")

# Continue the SAME session only for the EMPTY-COMPLETION class, which is a
# provider/model anomaly. Every other class is a real state the controller must
# see; nothing is retried on a count of turns, tokens or tool calls.
attempt=0
resume_target=$thread_id
while (( attempt < retry_limit )) && [[ $outcome == EMPTY-COMPLETION ]]; do
  if [[ -z $thread_id ]]; then
    printf '{"ev":"continue-skipped","reason":"no pi session id in the stream"}\n' \
      >> "$out/$name.events"
    break
  fi
  attempt=$((attempt + 1))
  attempt_stream=$(mktemp "$out/.$name.retry.XXXXXX") || {
    printf 'could not create a continuation stream for run %s\n' "$name" >&2
    break
  }
  if invoke_pi "$continue_prompt" "$attempt_stream" 0; then
    attempt_rc=0
  else
    attempt_rc=$?
  fi
  cat -- "$attempt_stream" >> "$out/$name.jsonl"
  rm -f -- "$attempt_stream"
  rc=$attempt_rc
  inspection=$(inspect_run)
  IFS=$'\t' read -r stream_session stop_reason usage_limited settled aborted \
    has_text has_tool_call <<< "$inspection"
  thread_id=${stream_session:-$thread_id}
  outcome=$(classify_run "$rc")
  printf '{"ev":"empty-completion-retry","attempt":%s,"exit":%s,"outcome":"%s"}\n' \
    "$attempt" "$rc" "$outcome" >> "$out/$name.events"
done
if (( attempt > 0 )) && [[ $outcome == EMPTY-COMPLETION ]]; then
  outcome=EMPTY-COMPLETION-RETRIES-EXHAUSTED
fi

if CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" block-run "$od_account" \
  "$out/$name.err" "$out/$name.jsonl"; then
  :
else
  bookkeeping_rc=$?
  printf 'block-run bookkeeping failed for run %s (exit %s); preserving pi exit %s\n' \
    "$name" "$bookkeeping_rc" "$rc" >&2
fi

printf '{"ev":"outcome","exit":%s,"outcome":"%s","retries":%s}\n' \
  "$rc" "$outcome" "$attempt" >> "$out/$name.events"
printf 'EXIT=%s OUTCOME=%s\n' "$rc" "$outcome" > "$out/$name.done"
if (( attempt > 0 )); then printf 'RETRIES=%s\n' "$attempt" >> "$out/$name.done"; fi
exit "$rc"
