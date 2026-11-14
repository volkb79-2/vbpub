#!/usr/bin/env bash
# Launch one pi Open Design job and persist its event stream and completion outcome.
# Usage: pi-impl.sh <worktree> <absolute-prompt-file> <run-name> [resume <session-id>]
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
  echo "usage: $0 <worktree> <absolute-prompt-file> <run-name> [resume <session-id>]" >&2
  exit 2
}

[[ $# -ge 3 && $# -le 5 ]] || usage
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

out=$CONTROLLER_RUNS_DIR
if [[ $mode == new ]]; then
  existing=()
  for extension in 'done' 'jsonl' 'err'; do
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
    [[ -n ${OPENDESIGN_API_KEY_VOLKER:-} ]] || {
      echo "OPENDESIGN_API_KEY_VOLKER is not set by \$HOME/.env.ai" >&2
      exit 2
    }
    export OPENDESIGN_API_KEY_VOLKER
    unset OPENDESIGN_API_KEY_GITHUB2
    provider=opendesign_volker
    ;;
  github2)
    [[ -n ${OPENDESIGN_API_KEY_GITHUB2:-} ]] || {
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
pi_args=(-p --mode json --model "$provider/$model" --thinking "$thinking" --session-dir "$session_dir")
if [[ $mode == resume ]]; then pi_args+=(--session "$session_id"); fi
if (cd -- "$worktree" && nice pi "${pi_args[@]}" -- "$prompt_text" \
  < /dev/null > "$out/$name.jsonl" 2> "$out/$name.err"); then
  rc=0
else
  rc=$?
fi

inspection=$(python3 - "$out/$name.jsonl" "$out/$name.last.txt" <<'PY'
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
print("\t".join((session_id, stop_reason, "1" if usage_limited else "0",
                 "1" if settled else "0", "1" if aborted else "0")))
PY
)
IFS=$'\t' read -r stream_session stop_reason usage_limited settled aborted <<< "$inspection"
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

if CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" block-run "$od_account" \
  "$out/$name.err" "$out/$name.jsonl"; then
  :
else
  bookkeeping_rc=$?
  printf 'block-run bookkeeping failed for run %s (exit %s); preserving pi exit %s\n' \
    "$name" "$bookkeeping_rc" "$rc" >&2
fi

if [[ $stop_reason == error && $usage_limited == 1 ]]; then
  outcome=USAGE-LIMIT
elif [[ $stop_reason == error ]]; then
  outcome=ERROR
elif [[ $rc != 0 ]]; then
  outcome=ERROR
elif [[ $settled == 1 && $aborted == 0 && -n $thread_id && -n $stop_reason ]]; then
  outcome=SUCCESS
else
  outcome=INCOMPLETE
fi
printf 'EXIT=%s OUTCOME=%s\n' "$rc" "$outcome" > "$out/$name.done"
exit "$rc"
