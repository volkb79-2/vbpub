#!/usr/bin/env bash
# Launch one Codex implementation or review job and persist its run stream locally.
# Usage: codex-impl.sh <worktree> <prompt-file> <run-name> [resume <session-id>]
#
# A run NAME is owned by one live wrapper: the invocation takes an exclusive
# flock on <runs-dir>/<name>.lock (lib.sh controller_run_claim) before it writes
# any name-owned record and releases it on every exit path, so a second
# invocation on the same name is refused with exit 2 and the live owner's pid.
# The lock file also records the owner's identity (pid, start epoch, the pid's
# kernel start ticks, boot id), which is what lets a reader name a DEAD owner.
#
# Every exit path writes <run>.done as `EXIT=<n> OUTCOME=<class>
# AUTORESUME=<k>` (the historical first-line shape, additive). The class is
# `CODE-EXIT`: the Codex stream carries no pi stop reason, so the one fact this
# wrapper can state is the child's own exit status. A resume that is killed
# before it reaches the marker leaves no `.done`, and `job-check.sh` reports
# that from the owner record as `DIED`, never as `RUNNING`.
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
out=$CONTROLLER_RUNS_DIR
# Ownership first: nothing below may write a name-owned record until this
# invocation holds the name.
controller_run_claim "$name" || exit 2
auto_count=0
codex_launched=0
# shellcheck disable=SC2329 # installed as the EXIT trap below
codex_finish() {
  # Every exit path (normal, child failure, bookkeeping failure, an error path
  # after the claim) states the run's outcome and drops the name. The marker is
  # written once: the normal path below writes the full one, and this fills it in
  # only when the wrapper exits through some other path.
  local status=$?
  # Only a launched child can exit: before the launch the wrapper states no
  # child outcome at all, and an unlaunched name keeps its "no .done" state.
  if (( codex_launched == 1 )) && [[ ! -e $out/$name.done ]]; then
    printf 'EXIT=%s OUTCOME=CODE-EXIT AUTORESUME=%s\n' "$status" "$auto_count" > "$out/$name.done"
  fi
  controller_run_release
}
trap codex_finish EXIT
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

route=${CODEX_ROUTE:-luna}
route_record=$route
od_account=''
od_start_kind=''
od_recorded_account=''
case $route in
  luna)
    route_home=${CODEX_HOME_LUNA:-"$HOME/.codex"}
    default_model=gpt-6-luna
    default_effort=xhigh
    auto_default=0
    ;;
  deepseek)
    od_start_kind=auto
    if [[ $mode == resume ]]; then
      if od_account=$(CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" find-thread-account "$session_id" "$out"); then
        od_recorded_account=$od_account
        od_start_kind=resume
      else
        lookup_rc=$?
        if (( lookup_rc != 1 )); then exit "$lookup_rc"; fi
        printf 'no recorded Open Design account for thread %s; picking a fresh account\n' "$session_id" >&2
        od_start_kind=auto-resume
      fi
    fi
    default_model=deepseek-v4.1-flash-eco
    default_effort=high
    auto_default=2
    ;;
  deepseek-volker|deepseek-github2)
    od_account=${route#deepseek-}
    route_record=$route
    od_start_kind=pinned
    default_model=deepseek-v4.1-flash-eco
    default_effort=high
    auto_default=2
    ;;
  *) echo "unknown CODEX_ROUTE=$route" >&2; exit 2 ;;
esac
auto_limit=${CODEX_AUTO_CONTINUE:-$auto_default}
if [[ ! $auto_limit =~ ^[0-9]+$ ]] || ! python3 - "$auto_limit" <<'PY'
import sys
try:
    value = int(sys.argv[1])
except ValueError:
    raise SystemExit(1)
raise SystemExit(0 if value <= 2147483647 else 1)
PY
then
  echo 'CODEX_AUTO_CONTINUE must be a non-negative integer within the shell counter range' >&2
  exit 2
fi
export CODEX_SQLITE_HOME=${CODEX_SQLITE_HOME:-"$HOME/.codex/sqlite-shared"}
model=${CODEX_MODEL:-$default_model}
effort=${CODEX_EFFORT:-$default_effort}

if [[ -n $od_start_kind ]]; then
  volker_home=${CODEX_HOME_DEEPSEEK_VOLKER:-"$HOME/.codex-opendesign-volker"}
  github2_home=${CODEX_HOME_DEEPSEEK_GITHUB2:-"$HOME/.codex-opendesign-github2"}
  volker_catalog=$volker_home/catalog/opendesign-models.json
  github2_catalog=$github2_home/catalog/opendesign-models.json
  case $od_start_kind in
    auto)
      od_account=$(CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" \
        start auto "$volker_catalog" "$github2_catalog") || exit $?
      ;;
    auto-resume)
      od_account=$(CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" \
        start auto "$volker_catalog" "$github2_catalog" "$session_id") || exit $?
      ;;
    resume)
      od_account=$(CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" \
        start-resume "$od_recorded_account" "$session_id" "$volker_catalog" "$github2_catalog") || exit $?
      ;;
    pinned)
      catalog=$volker_catalog
      [[ $od_account != github2 ]] || catalog=$github2_catalog
      if [[ $mode == resume ]]; then
        CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" \
          start "$od_account" "$catalog" "$session_id" >/dev/null || exit $?
      else
        CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" \
          start "$od_account" "$catalog" >/dev/null || exit $?
      fi
      ;;
  esac
  route_record=deepseek-$od_account
  case $od_account in
    volker) route_home=$volker_home ;;
    github2) route_home=$github2_home ;;
    *) echo 'Open Design account selector returned an invalid account' >&2; exit 2 ;;
  esac
else
  if [[ $route == luna ]]; then
    export CODEX_HOME=${CODEX_HOME_OVERRIDE:-$route_home}
  fi
fi
if [[ -n $od_start_kind ]]; then
  export CODEX_HOME=$route_home
fi

printf '%s\n' "$worktree" > "$out/$name.wt"
printf '%s\n' "$route_record" > "$out/$name.route"

if [[ $mode == resume ]]; then
  # A resume of a name that already completed must not leave the PREVIOUS
  # run's `.done` readable while this child works: the marker is this
  # invocation's evidence, so it starts absent. The stream needs no such
  # removal because the launch below truncates it.
  rm -f -- "$out/$name.done"
  codex_launched=1
  if (cd -- "$worktree" && nice codex exec resume -m "$model" -c "model_reasoning_effort=\"$effort\"" \
    --skip-git-repo-check --json "$session_id" - < "$prompt" > "$out/$name.jsonl" 2> "$out/$name.err"); then
    rc=0
  else
    rc=$?
  fi
else
  codex_launched=1
  # A new run is refused above when any name-owned record exists (a resume
  # deliberately keeps a recording name alive), so no stream removal is needed.
  if nice codex exec -C "$worktree" -m "$model" -c "model_reasoning_effort=\"$effort\"" \
    -s danger-full-access --skip-git-repo-check -o "$out/$name.last.txt" --json - \
    < "$prompt" > "$out/$name.jsonl" 2> "$out/$name.err"; then
    rc=0
  else
    rc=$?
  fi
fi

reasoning_end() {
  python3 - "$1" <<'PY'
import json
import sys

state = None
last_item = None
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    try:
        event = json.loads(line)
    except ValueError:
        continue
    event_type = event.get("type")
    if event_type == "turn.started":
        state = "started"
        last_item = None
    elif event_type in ("item.started", "item.completed"):
        state = "in_progress"
        last_item = (event.get("item") or {}).get("type")
    elif event_type == "turn.completed":
        state = "completed"
    elif event_type == "turn.failed":
        state = "failed"
sys.exit(0 if state == "completed" and last_item == "reasoning" else 1)
PY
}

thread_id=$session_id
if [[ -z $thread_id ]]; then
  thread_id=$(python3 - "$out/$name.jsonl" <<'PY'
import json
import sys
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    try:
        event = json.loads(line)
    except ValueError:
        continue
    if event.get("type") == "thread.started" and event.get("thread_id"):
        print(event["thread_id"])
        break
PY
)
fi
if [[ -n $thread_id ]]; then printf '%s\n' "$thread_id" > "$out/$name.thread"; fi
if [[ -n $od_account && -n $thread_id ]]; then
  if CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" \
    record-thread "$thread_id" "$od_account"; then
    :
  else
    thread_state_rc=$?
    printf 'Open Design thread-account bookkeeping failed for run %s (exit %s); preserving Codex exit %s\n' \
      "$name" "$thread_state_rc" "$rc" >&2
  fi
fi

continue_prompt='You stopped after thinking without acting: continue the task from where you are now, call tools for real'
auto_log=$out/$name.autoresume.log
printf 'ROUTE=%s LIMIT=%s\n' "$route_record" "$auto_limit" > "$auto_log"
while (( auto_count < auto_limit )) && reasoning_end "$out/$name.jsonl"; do
  if [[ -z $thread_id ]]; then
    echo 'AUTORESUME stopped: no thread id in the Codex stream' >> "$auto_log"
    break
  fi
  auto_count=$((auto_count + 1))
  attempt_stream=$(mktemp "$out/.${name}.autoresume.XXXXXX") || { echo 'could not create auto-resume stream' >&2; rc=1; break; }
  if (cd -- "$worktree" && nice codex exec resume -m "$model" -c "model_reasoning_effort=\"$effort\"" \
    --skip-git-repo-check --json "$thread_id" - <<< "$continue_prompt" > "$attempt_stream" 2>> "$out/$name.err"); then
    attempt_rc=0
  else
    attempt_rc=$?
  fi
  cat -- "$attempt_stream" >> "$out/$name.jsonl"
  rm -f -- "$attempt_stream"
  printf 'AUTORESUME attempt=%s EXIT=%s THREAD=%s\n' "$auto_count" "$attempt_rc" "$thread_id" >> "$auto_log"
  rc=$attempt_rc
done
if [[ -n $od_account ]]; then
  if CONTROLLER_RUNS_DIR="$out" python3 "$SCRIPT_DIR/opendesign-accounts.py" block-run "$od_account" \
    "$out/$name.err" "$out/$name.jsonl"; then
    :
  else
    bookkeeping_rc=$?
    printf 'block-run bookkeeping failed for run %s (exit %s); preserving Codex exit %s\n' \
      "$name" "$bookkeeping_rc" "$rc" >&2
  fi
fi
printf 'EXIT=%s OUTCOME=CODE-EXIT AUTORESUME=%s\n' "$rc" "$auto_count" > "$out/$name.done"
exit "$rc"
