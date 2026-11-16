#!/usr/bin/env bash
set -uo pipefail
unset PI_ROUTE PI_MODEL PI_THINKING PI_SESSION_DIR OPENDESIGN_API_KEY_VOLKER OPENDESIGN_API_KEY_GITHUB2
# A cockpit export of these would send the fake runs into the real run
# directory; the checks read their own temporary controller root.
unset CONTROLLER_RUNS_DIR CONTROLLER_LOGS_DIR
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SRC=${PI_IMPL_SCRIPT:-$SCRIPT_DIR/../pi-impl.sh}
JOB_STATUS=${JOB_STATUS_SCRIPT:-$SCRIPT_DIR/../job-status.sh}
PILIVE=${PI_LIVE_SCRIPT:-$SCRIPT_DIR/../pi-live.py}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-pi-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }
not_contains() {
  local needle=$1 description=$2
  shift 2
  ! grep -R -qF -- "$needle" "$@" || fail "$description"
}

HOME_ROOT=$ROOT/home
CONTROLLER_ROOT=$ROOT/controller
WORKTREE=$ROOT/worktree
PROMPT=$ROOT/brief.md
STATE=$ROOT/opendesign-state.json
PI_ARGV_LOG=$ROOT/pi-argv.log
PI_ENV_LOG=$ROOT/pi-env.log
PI_PROMPT_LOG=$ROOT/pi-prompt.log
mkdir -p "$HOME_ROOT" "$ROOT/bin" "$CONTROLLER_ROOT" "$WORKTREE"
printf 'brief\n' > "$PROMPT"
# The outcome classifier reads a committed fact from git (a clean worktree and
# the commits ahead of the base), so the fixture worktree is a real repository
# with one commit ahead of its base ref. Each check commits or dirties it.
git -C "$WORKTREE" init -q
git -C "$WORKTREE" config user.name test
git -C "$WORKTREE" config user.email test@example.invalid
printf 'baseline\n' > "$WORKTREE/baseline.txt"
git -C "$WORKTREE" add baseline.txt
git -C "$WORKTREE" commit -qm baseline
git -C "$WORKTREE" update-ref refs/remotes/origin/main HEAD
printf 'task\n' > "$WORKTREE/task.txt"
git -C "$WORKTREE" add task.txt
git -C "$WORKTREE" commit -qm task
cat > "$CONTROLLER_ROOT/controller.local.env" <<'ENV'
PI_ROUTE="deepseek-pi"
PI_MODEL="deepseek-v4.1-flash-eco"
PI_THINKING="high"
PI_SESSION_DIR="$HOME/.pi/agent/sessions"
ENV
cat > "$HOME_ROOT/.env.ai" <<'ENV'
export OPENDESIGN_API_KEY_VOLKER=test-key
export OPENDESIGN_API_KEY_GITHUB2=test-key
ENV

cat > "$ROOT/bin/pi" <<'SH'
#!/usr/bin/env bash
set -uo pipefail
printf '%s\n' "$*" >> "$PI_ARGV_LOG"
printf '%s' "${!#}" > "$PI_PROMPT_LOG"
printf 'VOLKER_PRESENT=%s GITHUB2_PRESENT=%s\n' \
  "${OPENDESIGN_API_KEY_VOLKER:+yes}" "${OPENDESIGN_API_KEY_GITHUB2:+yes}" >> "$PI_ENV_LOG"
# Probe: where this invocation's stdout points and whether the run's .done
# marker exists yet (a marker before the continuations finish is a false verdict).
if [[ -n ${FAKE_PI_PROBE_LOG:-} ]]; then
  printf 'STDOUT=%s DONE=%s\n' "$(readlink "/proc/$$/fd/1")" \
    "$([[ -e ${FAKE_PI_DONE_PROBE:-/nonexistent} ]] && echo present || echo absent)" >> "$FAKE_PI_PROBE_LOG"
fi
# This blocks while stdin remains open. The wrapper must give pi /dev/null.
if [[ -p /dev/stdin && -n ${FAKE_PI_STDIN_OPEN_MARKER:-} ]]; then
  : > "$FAKE_PI_STDIN_OPEN_MARKER"
fi
while IFS= read -r _line; do :; done
printf 'STDIN_EOF=1\n' >> "$PI_ENV_LOG"
# A per-invocation fixture sequence: each line of FAKE_PI_SEQUENCE names the
# fixture this invocation emits. The last element repeats, so an exhausted
# sequence keeps producing the same ending.
stream=${FAKE_PI_STREAM:-}
if [[ -n ${FAKE_PI_SEQUENCE:-} ]]; then
  read -r -a sequence <<< "$FAKE_PI_SEQUENCE"
  index=0
  if [[ -n ${FAKE_PI_COUNTER:-} && -f $FAKE_PI_COUNTER ]]; then index=$(cat "$FAKE_PI_COUNTER"); fi
  last=$(( ${#sequence[@]} - 1 ))
  (( index > last )) && index=$last
  [[ -z ${FAKE_PI_COUNTER:-} ]] || printf '%s' "$((index + 1))" > "$FAKE_PI_COUNTER"
  stream=${FAKE_FIXTURE_DIR:?}/"${sequence[$index]}".jsonl
fi
python3 - "$stream" "${FAKE_PI_SESSION_ID:-}" <<'PY'
import json
import sys

for line in open(sys.argv[1], encoding="utf-8"):
    event = json.loads(line)
    if event.get("type") == "session" and sys.argv[2]:
        event["id"] = sys.argv[2]
    print(json.dumps(event, separators=(",", ":")))
PY
[[ -z ${FAKE_PI_SLEEP_AFTER:-} ]] || sleep "$FAKE_PI_SLEEP_AFTER"
exit "${FAKE_PI_RC:-0}"
SH
chmod +x "$ROOT/bin/pi"

export CONTROLLER_HOME=$CONTROLLER_ROOT CONTROLLER_REPO=$WORKTREE
export CONTROLLER_OD_STATE=$STATE
export PI_ARGV_LOG PI_ENV_LOG PI_PROMPT_LOG

launch() {
  local run=$1 route=$2 fixture=$3 input=$4
  shift 4
  local fake_session_id="${run}-session"
  if [[ ${1:-} == resume && -n ${2:-} ]]; then fake_session_id=$2; fi
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE="$route" \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/$fixture.jsonl" FAKE_PI_SESSION_ID="$fake_session_id" FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" "$run" "$@" \
    > "$ROOT/$run.launch.log" 2>&1 < "$input"
}

# Same launch driven by a per-invocation fixture sequence. The run's .live
# snapshot interval is one second so a liveness check never waits on the
# default tick.
launch_sequence() {
  local run=$1 route=$2 sequence=$3
  shift 3
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE="$route" \
    CONTROLLER_OD_STATE="${SEQUENCE_OD_STATE:-$STATE}" \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_SEQUENCE="$sequence" \
    FAKE_PI_COUNTER="$ROOT/$run.counter" FAKE_PI_SESSION_ID="${run}-session" FAKE_PI_RC=0 \
    ${SEQUENCE_EXTRA_ENV:-} \
    "$SRC" "$WORKTREE" "$PROMPT" "$run" "$@" \
    > "$ROOT/$run.launch.log" 2>&1 < /dev/null
}

wait_launch_sequence() {
  local run=$1 route=$2 sequence=$3
  shift 3
  launch_sequence "$run" "$route" "$sequence" "$@" &
  local pid=$!
  if ! wait "$pid"; then
    cat "$ROOT/$run.launch.log" >&2
    fail "pi wrapper failed for $run"
  fi
}

# Each invocation starts an argv line; a prompt may itself contain a
# newline, so count the invocations rather than the file's lines.
pi_invocations() {
  local count=0
  [[ -f $PI_ARGV_LOG ]] && count=$(grep -c -- '^-p ' "$PI_ARGV_LOG" || true)
  echo "${count:-0}"
}

clean_worktree() {
  git -C "$WORKTREE" checkout -q -- . 2>/dev/null || :
}

wait_launch() {
  local run=$1 route=$2 fixture=$3 input=$4
  shift 4
  launch "$run" "$route" "$fixture" "$input" "$@" &
  local pid=$!
  if ! wait "$pid"; then
    cat "$ROOT/$run.launch.log" >&2
    fail "pi wrapper failed for $run"
  fi
}

expect_rc() {
  local expected=$1
  shift
  if "$@" > "$ROOT/focused-command.out" 2>&1; then
    fail "command unexpectedly succeeded; expected exit $expected"
  else
    local actual=$?
    [[ $actual == "$expected" ]] || fail "command exit was $actual, expected $expected"
  fi
}

check_xtrace_key() {
  local home=$ROOT/xtrace-home route suffix
  mkdir -p "$home"
  cat > "$home/.env.ai" <<'ENV'
OPENDESIGN_API_KEY_VOLKER=xtrace-marker-volker
OPENDESIGN_API_KEY_GITHUB2=xtrace-marker-github2
ENV
  for route in deepseek-pi-volker deepseek-pi-github2; do
    suffix=${route#deepseek-pi-}
    if ! PATH="$ROOT/bin:$PATH" HOME="$home" PI_ROUTE="$route" \
      CONTROLLER_OD_STATE="$ROOT/xtrace-state.json" \
      FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_SESSION_ID="xtrace-$suffix-session" \
      FAKE_PI_RC=0 bash -x "$SRC" "$WORKTREE" "$PROMPT" "xtrace-$suffix" \
      > "$ROOT/xtrace-$suffix.log" 2>&1 < /dev/null; then
      cat "$ROOT/xtrace-$suffix.log" >&2
      fail "traced pi wrapper failed for $route"
    fi
    ! grep -qF xtrace-marker "$ROOT/xtrace-$suffix.log" || fail "shell tracing leaked a key value for $route"
  done
}

check_outcome_error() {
  local od_state=$ROOT/outcome-error-state.json
  CONTROLLER_OD_STATE=$od_state wait_launch outcome-error deepseek-pi-volker pi-error-other /dev/null
  [[ $(cat "$CONTROLLER_ROOT/runs/outcome-error.done") == 'EXIT=0 OUTCOME=ERROR' ]] \
    || fail 'a pi error that is not a 402 is recorded as a plain failure'
  CONTROLLER_OD_STATE=$od_state wait_launch outcome-402-other deepseek-pi-volker pi-402-other-code /dev/null
  [[ $(cat "$CONTROLLER_ROOT/runs/outcome-402-other.done") == 'EXIT=0 OUTCOME=ERROR' ]] \
    || fail 'a 402 with another error code is recorded as a plain failure'
  python3 - "$od_state" <<'PY' || fail 'a non-quota pi error blocked an account'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["volker"]["blocked_until_epoch"] == 0
assert state["github2"]["blocked_until_epoch"] == 0
PY
}

check_outcome_length() {
  local before after
  before=$(pi_invocations)
  CONTROLLER_OD_STATE=$ROOT/outcome-length-state.json PI_LIVE=0 \
    wait_launch outcome-length deepseek-pi-volker pi-length /dev/null
  after=$(pi_invocations)
  [[ $(head -n 1 "$CONTROLLER_ROOT/runs/outcome-length.done") == 'EXIT=0 OUTCOME=CONTEXT-FULL' ]] \
    || fail 'a settled stopReason=length run is not classified CONTEXT-FULL'
  # CONTEXT-FULL is a real end state: the wrapper must NOT continue it (a full
  # context is the one class a same-session continuation cannot finish).
  [[ $(( after - before )) == 1 ]] || fail "a CONTEXT-FULL run was continued ($(( after - before )) pi invocations, expected 1)"
  grep -qF 'empty-completion-retry' "$CONTROLLER_ROOT/runs/outcome-length.events" 2>/dev/null \
    && fail 'a CONTEXT-FULL run recorded an empty-completion continuation'
}

# A settled stream whose stop reason is neither `stop` nor a named limit: the
# unattributable class, and never a success.
check_outcome_stop_other() {
  CONTROLLER_OD_STATE=$ROOT/outcome-other-stop-state.json PI_LIVE=0 \
    wait_launch outcome-other-stop deepseek-pi-volker pi-settled-other-stop /dev/null
  [[ $(head -n 1 "$CONTROLLER_ROOT/runs/outcome-other-stop.done") == 'EXIT=0 OUTCOME=INCOMPLETE' ]] \
    || fail 'a settled stream whose stop reason is not stop or length is not INCOMPLETE'
}

# A settled stop whose final message is a tool call and no text is not a
# success, and is not the continued EMPTY-COMPLETION class either.
check_outcome_tool_call_no_text() {
  local before after
  before=$(pi_invocations)
  CONTROLLER_OD_STATE=$ROOT/outcome-tool-call-state.json PI_LIVE=0 \
    wait_launch outcome-tool-call deepseek-pi-volker pi-stop-tool-call-no-text /dev/null
  after=$(pi_invocations)
  [[ $(head -n 1 "$CONTROLLER_ROOT/runs/outcome-tool-call.done") == 'EXIT=0 OUTCOME=TOOL-CALL-NO-TEXT' ]] \
    || fail 'a settled stop with a tool call and no text is not TOOL-CALL-NO-TEXT'
  [[ $(( after - before )) == 1 ]] || fail 'a tool-call-no-text run was continued'
  grep -qF 'empty-completion-retry' "$CONTROLLER_ROOT/runs/outcome-tool-call.events" 2>/dev/null \
    && fail 'a tool-call-no-text run recorded an empty-completion continuation'
}

check_outcome_rc() {
  local rc=0
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/outcome-rc-state.json" \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_SESSION_ID=outcome-rc-session FAKE_PI_RC=1 \
    "$SRC" "$WORKTREE" "$PROMPT" outcome-rc > "$ROOT/outcome-rc.launch.log" 2>&1 < /dev/null || rc=$?
  [[ $rc == 1 ]] || fail "pi exit 1 was returned as $rc"
  [[ $(cat "$CONTROLLER_ROOT/runs/outcome-rc.done") == 'EXIT=1 OUTCOME=ERROR' ]] \
    || fail 'a nonzero pi exit with a clean stream is recorded as an error'
}

# --- overwatch: outcome classification, bounded continue, liveness ----------

# A clean worktree with one commit ahead of its base is what SUCCESS requires.
clean_worktree

check_outcome_success_report() {
  local report=$ROOT/report-final.md
  printf 'final report\n' > "$report"
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/outcome-success-state.json" \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_SESSION_ID=outcome-success-session \
    FAKE_PI_RC=0 PI_LIVE=0 \
    "$SRC" "$WORKTREE" "$PROMPT" outcome-success --report "$report" \
    > "$ROOT/outcome-success.launch.log" 2>&1 < /dev/null || {
      cat "$ROOT/outcome-success.launch.log" >&2
      fail 'a clean finished run with its report was not accepted'
    }
  [[ $(cat "$CONTROLLER_ROOT/runs/outcome-success.done") == 'EXIT=0 OUTCOME=SUCCESS' ]] \
    || fail 'a clean finished run with text and its report is not SUCCESS'
}

check_outcome_empty() {
  wait_launch_sequence outcome-empty deepseek-pi-volker "pi-empty-completion pi-thinking-only"
  [[ $(head -n 1 "$CONTROLLER_ROOT/runs/outcome-empty.done") == 'EXIT=0 OUTCOME=EMPTY-COMPLETION-RETRIES-EXHAUSTED' ]] \
    || fail 'an empty final message with an empty continuation is not reported as exhausted'
  contains "$CONTROLLER_ROOT/runs/outcome-empty.done" 'RETRIES=2' 'the exhausted class records its attempt count'
  contains "$CONTROLLER_ROOT/runs/outcome-empty.events" '"ev":"empty-completion-retry"' 'each continuation is recorded in the run events'
  contains "$CONTROLLER_ROOT/runs/outcome-empty.events" '"attempt":2' 'the second continuation is recorded'
  local sessions
  sessions=$(grep -c -- '--session outcome-empty-session' "$PI_ARGV_LOG" 2>/dev/null || true)
  [[ $sessions == 2 ]] || fail "continuations did not resume the same pi session (seen $sessions)"
  contains "$PI_PROMPT_LOG" 'Your last turn ended without a tool call or a message' \
    'the continuation uses the canonical prompt file'
  # The run plus its two recorded continuations are all appended to one stream.
  [[ $(grep -cF '"type":"agent_settled"' "$CONTROLLER_ROOT/runs/outcome-empty.jsonl") == 3 ]] \
    || fail 'the appended stream does not hold the run and both continuations'
}

check_outcome_empty_recovered() {
  local before after
  before=$(pi_invocations)
  wait_launch_sequence outcome-recovered deepseek-pi-github2 "pi-empty-completion pi-success"
  [[ $(head -n 1 "$CONTROLLER_ROOT/runs/outcome-recovered.done") == 'EXIT=0 OUTCOME=SUCCESS' ]] \
    || fail 'a run that recovers on the first continuation is not SUCCESS'
  after=$(pi_invocations)
  [[ $(( after - before )) == 2 ]] || fail "a recovered empty completion used $(( after - before )) pi invocations, expected 2"
  contains "$CONTROLLER_ROOT/runs/outcome-recovered.events" '"attempt":1' 'the recovering continuation is recorded'
}

check_outcome_no_retry_other_classes() {
  local before after
  before=$(pi_invocations)
  wait_launch_sequence no-retry-usage deepseek-pi-volker pi-402
  after=$(pi_invocations)
  [[ $(( after - before )) == 1 ]] || fail 'a usage limit was continued instead of reported'
  [[ $(head -n 1 "$CONTROLLER_ROOT/runs/no-retry-usage.done") == 'EXIT=0 OUTCOME=USAGE-LIMIT' ]] \
    || fail 'a usage limit outcome was not preserved'
  [[ ! -s $CONTROLLER_ROOT/runs/no-retry-usage.events ]] || {
    grep -qF 'empty-completion-retry' "$CONTROLLER_ROOT/runs/no-retry-usage.events" \
      && fail 'a usage limit recorded a continuation'
  }
}

check_outcome_uncommitted() {
  local before after
  printf 'working change\n' > "$WORKTREE/working.txt"
  before=$(pi_invocations)
  wait_launch_sequence outcome-uncommitted deepseek-pi-github2 pi-success
  after=$(pi_invocations)
  [[ $(( after - before )) == 1 ]] || fail 'an uncommitted finish was continued instead of reported'
  [[ $(cat "$CONTROLLER_ROOT/runs/outcome-uncommitted.done") == 'EXIT=0 OUTCOME=UNCOMMITTED' ]] \
    || fail 'an exit-zero finish with uncommitted work is not UNCOMMITTED'
  rm -f "$WORKTREE/working.txt"
  clean_worktree
}

# A worktree whose status cannot be read must not read as clean.
check_outcome_git_unreadable() {
  local shim=$ROOT/git-shim real_git
  real_git=$(command -v git)
  mkdir -p "$shim"
  cat > "$shim/git" <<SH
#!/usr/bin/env bash
for a in "\$@"; do [[ \$a == status ]] && exit 128; done
exec $real_git "\$@"
SH
  chmod +x "$shim/git"
  PATH="$shim:$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/outcome-git-state.json" \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_SESSION_ID=outcome-git-session \
    FAKE_PI_RC=0 PI_LIVE=0 \
    "$SRC" "$WORKTREE" "$PROMPT" outcome-git-unreadable > "$ROOT/outcome-git.launch.log" 2>&1 < /dev/null \
    || { cat "$ROOT/outcome-git.launch.log" >&2; fail 'wrapper failed on an unreadable worktree status'; }
  [[ $(cat "$CONTROLLER_ROOT/runs/outcome-git-unreadable.done") == 'EXIT=0 OUTCOME=INCOMPLETE' ]] \
    || fail 'an unreadable worktree status was classified as clean'
}

check_outcome_no_report() {
  local before after
  before=$(pi_invocations)
  wait_launch_sequence outcome-no-report deepseek-pi-volker pi-success --report "$ROOT/missing-report.md"
  after=$(pi_invocations)
  [[ $(( after - before )) == 1 ]] || fail 'a missing report was continued instead of reported'
  [[ $(cat "$CONTROLLER_ROOT/runs/outcome-no-report.done") == 'EXIT=0 OUTCOME=NO-REPORT' ]] \
    || fail 'a finish without the expected report is not NO-REPORT'
}

check_outcome_empty_retry_bounded() {
  local before after
  before=$(pi_invocations)
  wait_launch_sequence bounded-empty deepseek-pi-volker pi-empty-completion
  after=$(pi_invocations)
  [[ $(( after - before )) == 3 ]] || fail "an unbounded empty-completion loop used $(( after - before )) pi invocations, expected 3 (the run plus two continuations)"
  contains "$CONTROLLER_ROOT/runs/bounded-empty.done" 'OUTCOME=EMPTY-COMPLETION-RETRIES-EXHAUSTED' \
    'the exhausted retry count is the final outcome'
}

# Every continuation writes straight into the run's own stream (so the liveness
# sidecar and any stream reader see it while it runs) and no .done marker exists
# until the last continuation has finished.
check_retry_streams_into_run() {
  local probe=$ROOT/retry-probe.log run=retry-probe
  rm -f "$probe"
  FAKE_PI_PROBE_LOG=$probe FAKE_PI_DONE_PROBE=$CONTROLLER_ROOT/runs/$run.done \
    wait_launch_sequence "$run" deepseek-pi-volker "pi-empty-completion pi-empty-completion pi-success"
  [[ $(wc -l < "$probe") == 3 ]] || fail "expected 3 pi invocations, saw $(wc -l < "$probe")"
  [[ $(grep -cF "STDOUT=$CONTROLLER_ROOT/runs/$run.jsonl DONE=absent" "$probe") == 3 ]] \
    || { cat "$probe" >&2; fail 'a continuation did not write into the run stream, or a .done marker existed before the last continuation finished'; }
  [[ $(head -n 1 "$CONTROLLER_ROOT/runs/$run.done") == 'EXIT=0 OUTCOME=SUCCESS' ]] \
    || fail 'a run that recovers on the second continuation is not SUCCESS'
  [[ -z $(find "$CONTROLLER_ROOT/runs" -maxdepth 1 -name ".$run.retry.*") ]] \
    || fail 'a continuation left a temporary stream behind'
}

check_live_snapshot() {
  local live=$CONTROLLER_ROOT/runs/live-run.live
  rm -f "$CONTROLLER_ROOT/runs/live-run.done"
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/live-state.json" \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" \
    FAKE_PI_SEQUENCE='pi-tool-in-flight' FAKE_PI_COUNTER="$ROOT/live-run.counter" \
    FAKE_PI_SLEEP_AFTER=4 FAKE_PI_SESSION_ID=live-run-session FAKE_PI_RC=0 \
    PI_LIVE_INTERVAL_SECONDS=1 PI_EMPTY_RETRY_LIMIT=0 \
    "$SRC" "$WORKTREE" "$PROMPT" live-run > "$ROOT/live-run.launch.log" 2>&1 < /dev/null &
  local wrapper=$!
  local waited=0
  while (( waited < 100 )) && ! grep -qF '"tool_in_flight": "bash"' "$live" 2>/dev/null; do
    sleep 0.1
    waited=$(( waited + 1 ))
  done
  if (( waited >= 100 )); then
    kill -- "$wrapper" 2>/dev/null || true
    wait "$wrapper" 2>/dev/null || true
    cat "$ROOT/live-run.launch.log" >&2
    fail 'the live sidecar never reported the tool call in flight'
  fi
  kill -- "$wrapper" 2>/dev/null || true
  wait "$wrapper" 2>/dev/null || true
  python3 - "$live" "$wrapper" <<'PY' || fail 'the live snapshot is missing facts'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["run"] == "live-run", body
assert isinstance(body["last_event_epoch"], int), body
assert body["tool_in_flight"] == "bash", body
assert isinstance(body["tool_in_flight_started_epoch"], int), body
assert body["assistant_turns"] >= 1, body
assert body["tool_calls"] >= 1, body
assert body["latest_total_tokens"] == 12220, body
assert body["tool_in_flight_command"] == "sleep 600", body
assert body["commits_ahead"] == 1, body
assert body["uncommitted_files"] == 0, body
assert isinstance(body["last_commit_epoch"], int), body
PY
}

check_live_atomic() {
  local partial anchor twin
  anchor=$ROOT/atomic-anchor.live
  twin="$CONTROLLER_ROOT/runs/atomic-twin.live"
  mkdir -p "$CONTROLLER_ROOT/runs"
  printf '{"run": "before"}\n' > "$anchor"
  ln -f "$anchor" "$twin" || fail 'the atomic-write check could not create its hard link'
  python3 "$PILIVE" --stream "$SCRIPT_DIR/fixtures/pi-success.jsonl" --worktree "$WORKTREE" \
    --out "$twin" --run atomic-twin --wrapper-pid $$ --interval 1 --once > /dev/null 2>&1 \
    || fail 'pi-live could not write an atomic snapshot'
  # A rename leaves the other name on the old inode with the old content; an
  # in-place rewrite would show the new bytes through both names.
  grep -qF '"run": "before"' "$anchor" \
    || fail 'the snapshot was written in place instead of replaced by rename'
  python3 - "$twin" <<'PY' || fail 'the replaced snapshot is not one JSON object'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["run"] == "atomic-twin", body
PY
  rm -f -- "$anchor" "$twin"
  partial=$(ls "$CONTROLLER_ROOT/runs"/.live-run.live.* 2>/dev/null || true)
  [[ -z $partial ]] || fail "live snapshot left a temporary file behind: $partial"
  python3 - "$CONTROLLER_ROOT/runs/live-run.live" <<'PY' || fail 'the live snapshot is not one complete JSON object'
import json, sys
json.load(open(sys.argv[1], encoding="utf-8"))
PY
}

check_job_status() {
  local json=$ROOT/job-status.json
  "$JOB_STATUS" outcome-success > "$ROOT/job-status.out" 2>&1 || {
    cat "$ROOT/job-status.out" >&2
    fail 'job-status failed on a finished run'
  }
  grep -qF 'outcome-success: FINISHED EXIT=0 OUTCOME=SUCCESS' "$ROOT/job-status.out" \
    || { cat "$ROOT/job-status.out" >&2; fail 'job-status did not report the finished run'; }
  python3 - "$json" <<'PY'
import json, sys
now = 1_800_000_000
body = {
    "run": "synthetic",
    "wrapper_pid": 1,
    "updated_epoch": now - 5,
    "last_event_epoch": now - 700,
    "tool_in_flight": None,
    "tool_in_flight_started_epoch": None,
    "assistant_turns": 4,
    "tool_calls": 9,
    "latest_total_tokens": 1200,
    "commits_ahead": 2,
    "uncommitted_files": 1,
    "last_commit_epoch": now - 60,
}
json.dump(body, open(sys.argv[1], "w", encoding="utf-8"))
PY
}

check_job_status_states() {
  local runs=$ROOT/status-runs
  mkdir -p "$runs" "$ROOT/status-home"
  python3 - "$runs" <<'PY'
import json, sys, time
from pathlib import Path
now = int(time.time())
runs = Path(sys.argv[1])
def write(name, tool=None, tool_started=None, last_event=None, progress=None,
          command=None, steer=None, tokens=10, tokens_report=None, ahead=0,
          commit_epoch=None):
    body = {
        "run": name, "wrapper_pid": 1, "updated_epoch": now,
        "last_event_epoch": last_event, "tool_in_flight": tool,
        "tool_in_flight_started_epoch": tool_started,
        "assistant_turns": 1, "tool_calls": 1,
        "latest_total_tokens": tokens,
        "latest_total_tokens_last_report": tokens if tokens_report is None else tokens_report,
        "tool_in_flight_command": command, "steering_children": steer,
        "progress_epoch": progress,
        "commits_ahead": ahead, "uncommitted_files": 0,
        "last_commit_epoch": now if commit_epoch is None else commit_epoch,
    }
    (runs / f"{name}.live").write_text(json.dumps(body), encoding="utf-8")
write("stale", last_event=now - 900)
write("hung", tool="bash", tool_started=now - 2400, last_event=now - 10,
      progress=now - 2400, command="sleep 600")
write("long-tool", tool="bash", tool_started=now - 2400, last_event=now - 10,
      progress=now - 30, command="grep -r needle /workspace",
      steer=[{"pid": 4242, "command": "grep -r needle /workspace"}])
write("progress-unknown", tool="bash", tool_started=now - 2400,
      last_event=now - 10, progress=None)
write("tokens-zero-report", last_event=now - 10, tokens=258492,
      tokens_report=0)
write("no-commits", last_event=now - 10, ahead=0, commit_epoch=now - 86400)
write("own-commit", last_event=now - 10, ahead=2, commit_epoch=now - 60)
write("ahead-unknown", last_event=now - 10, ahead=None, commit_epoch=now - 86400)
write("orphan", last_event=now - 10)
body = json.loads((runs / "orphan.live").read_text(encoding="utf-8"))
body["wrapper_pid"] = 2147483646
(runs / "orphan.live").write_text(json.dumps(body), encoding="utf-8")
(runs / "finished.done").write_text("EXIT=0 OUTCOME=SUCCESS\n", encoding="utf-8")
PY
  CONTROLLER_HOME="$ROOT/status-home" CONTROLLER_RUNS_DIR="$runs" CONTROLLER_REPO="$WORKTREE" \
    JOB_STATUS_STALL_SECONDS=600 JOB_STATUS_HUNG_TOOL_SECONDS=1800 \
    "$JOB_STATUS" stale hung long-tool progress-unknown tokens-zero-report \
      no-commits own-commit ahead-unknown orphan finished absent \
      > "$ROOT/job-status-states.out" 2>&1 || {
      cat "$ROOT/job-status-states.out" >&2
      fail 'job-status failed on synthetic run states'
    }
  grep -qF 'stale: STALLED' "$ROOT/job-status-states.out" || { cat "$ROOT/job-status-states.out" >&2; fail 'a quiet run is not STALLED'; }
  grep -qF 'hung: HUNG-TOOL' "$ROOT/job-status-states.out" || { cat "$ROOT/job-status-states.out" >&2; fail 'a long tool call is not HUNG-TOOL'; }
  grep -qF 'long-tool: LONG-TOOL' "$ROOT/job-status-states.out" || { cat "$ROOT/job-status-states.out" >&2; fail 'a heavy tool call that is still progressing is not LONG-TOOL'; }
  if grep -qF 'long-tool: HUNG-TOOL' "$ROOT/job-status-states.out"; then
    fail 'a progressing tool call was reported as HUNG-TOOL'
  fi
  grep -qF 'cmd=grep -r needle /workspace' "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'the row does not name the tool command'; }
  if ! grep -qF 'steer=4242' "$ROOT/job-status-states.out"; then
    cat "$ROOT/job-status-states.out" >&2
    fail 'the row does not name the exact child pid to steer'
  fi
  # A progress fact that cannot be read is `null`, and an unknown progress fact
  # must not license the stronger word.
  grep -qF 'progress-unknown: LONG-TOOL' "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'an unreadable progress fact was read as a hung tool'; }
  grep -qE '^progress-unknown: .*progress=- ' "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'an unreadable progress fact is not shown as unknown'; }
  # A zero usage report never masquerades as the context size.
  grep -qE '^tokens-zero-report: .*tokens=258492 .*tokens-last-report=0' \
    "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'a zero usage report replaced the measured token figure'; }
  # since-commit names whose commit the age belongs to.
  grep -qF 'no-commits: ACTIVE' "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'the no-commit run is not reported'; }
  if grep -E '^no-commits: .*since-commit=[0-9]+s' "$ROOT/job-status-states.out"; then
    fail 'a run with no commit of its own printed an unqualified commit age'
  fi
  grep -qF 'since-commit=no-commits-yet' "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'a run with no commit of its own does not say no-commits-yet'; }
  grep -qF 'since-commit=60s(ahead=2)' "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'a run with its own commit does not qualify the age with ahead='; }
  grep -qF 'since-commit=null' "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'an unreadable commits_ahead did not suppress the commit age'; }
  grep -qF 'finished: FINISHED EXIT=0 OUTCOME=SUCCESS' "$ROOT/job-status-states.out" || fail 'a finished run is not reported as finished'
  grep -qF 'absent: NO-LIVE (no absent.live sidecar)' "$ROOT/job-status-states.out" || fail 'a run without a snapshot is not NO-LIVE'
  grep -qF 'orphan: WRAPPER-DEAD' "$ROOT/job-status-states.out" || fail 'a run whose wrapper pid is gone is not WRAPPER-DEAD'
  grep -qE 'hung: (ACTIVE|STALLED)' "$ROOT/job-status-states.out" \
    && fail 'HUNG-TOOL did not take precedence over STALLED'
}

# The documented default thresholds (600 s quiet, 1800 s in one tool call), and
# the healthy side of each: a run inside its threshold, or in a long thinking turn
# with no tool in flight, is ACTIVE.
check_job_status_defaults() {
  local runs=$ROOT/status-default-runs
  mkdir -p "$runs" "$ROOT/status-home"
  python3 - "$runs" <<'PY'
import json, sys, time
from pathlib import Path
now = int(time.time())
runs = Path(sys.argv[1])
def write(name, tool=None, tool_started=None, last_event=None, progress=None):
    body = {
        "run": name, "wrapper_pid": 1, "updated_epoch": now,
        "last_event_epoch": last_event, "tool_in_flight": tool,
        "tool_in_flight_started_epoch": tool_started,
        "progress_epoch": progress,
        "assistant_turns": 1, "tool_calls": 1, "latest_total_tokens": 10,
        "commits_ahead": 0, "uncommitted_files": 0, "last_commit_epoch": now,
    }
    (runs / f"{name}.live").write_text(json.dumps(body), encoding="utf-8")
write("quiet500", last_event=now - 500)
write("quiet700", last_event=now - 700)
write("thinking", last_event=now - 100)
write("tool1700", tool="bash", tool_started=now - 1700, last_event=now - 20,
      progress=now - 20)
write("tool1900fresh", tool="bash", tool_started=now - 1900, last_event=now - 20,
      progress=now - 20)
write("tool1900stuck", tool="bash", tool_started=now - 1900, last_event=now - 20,
      progress=now - 1900)
PY
  env -u JOB_STATUS_STALL_SECONDS -u JOB_STATUS_HUNG_TOOL_SECONDS \
    CONTROLLER_HOME="$ROOT/status-home" CONTROLLER_RUNS_DIR="$runs" CONTROLLER_REPO="$WORKTREE" \
    "$JOB_STATUS" quiet500 quiet700 thinking tool1700 tool1900fresh tool1900stuck \
      > "$ROOT/job-status-defaults.out" 2>&1 \
    || { cat "$ROOT/job-status-defaults.out" >&2; fail 'job-status failed on default-threshold states'; }
  local expectation
  for expectation in 'quiet500: ACTIVE' 'quiet700: STALLED' 'thinking: ACTIVE' \
                     'tool1700: ACTIVE' 'tool1900fresh: LONG-TOOL' 'tool1900stuck: HUNG-TOOL'; do
    grep -qF -- "$expectation" "$ROOT/job-status-defaults.out" \
      || { cat "$ROOT/job-status-defaults.out" >&2; fail "default thresholds: expected $expectation"; }
  done
}

check_job_status_readonly() {
  local runs=$ROOT/status-runs
  python3 - "$runs" <<'PY' > "$ROOT/status-before.txt"
import hashlib, sys
from pathlib import Path
for item in sorted(Path(sys.argv[1]).iterdir()):
    print(item.name, hashlib.sha256(item.read_bytes()).hexdigest())
PY
  CONTROLLER_HOME="$ROOT/status-home" CONTROLLER_RUNS_DIR="$runs" CONTROLLER_REPO="$WORKTREE" \
    "$JOB_STATUS" > /dev/null 2>&1 || fail 'job-status failed on a second call'
  python3 - "$runs" <<'PY' > "$ROOT/status-after.txt"
import hashlib, sys
from pathlib import Path
for item in sorted(Path(sys.argv[1]).iterdir()):
    print(item.name, hashlib.sha256(item.read_bytes()).hexdigest())
PY
  cmp -s "$ROOT/status-before.txt" "$ROOT/status-after.txt" || fail 'job-status modified the run sidecars it reads'
}

check_live_usage_trend() {
  # Usage on message_update is the only token figure available before a
  # message completes; the snapshot must read it rather than wait.
  rm -f "$CONTROLLER_ROOT/runs/trend.live"
  python3 "$PILIVE" --stream "$SCRIPT_DIR/fixtures/pi-empty-usage.jsonl" \
    --worktree "$WORKTREE" --out "$CONTROLLER_ROOT/runs/trend.live" --run trend \
    --wrapper-pid $$ --interval 1 --once > /dev/null 2>&1 \
    || fail 'pi-live could not snapshot a usage-only stream'
  python3 - "$CONTROLLER_ROOT/runs/trend.live" <<'PY' || fail 'the usage trend was not read'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["latest_total_tokens"] == 129, body
assert body["latest_total_tokens_last_report"] == 129, body
assert body["assistant_turns"] == 1, body
assert body["tool_calls"] == 1, body
assert body["tool_in_flight"] is None, body
PY
  # A usage report of 0 is a fact, never the context size: the last measured
  # figure stays published, and the zero report stays visible as its own fact.
  rm -f "$CONTROLLER_ROOT/runs/zero-report.live"
  python3 "$PILIVE" --stream "$SCRIPT_DIR/fixtures/pi-long-run-zero-reports.jsonl" \
    --worktree "$WORKTREE" --out "$CONTROLLER_ROOT/runs/zero-report.live" --run zero-report \
    --wrapper-pid $$ --interval 1 --once > /dev/null 2>&1 \
    || fail 'pi-live could not snapshot a stream whose latest usage report is 0'
  python3 - "$CONTROLLER_ROOT/runs/zero-report.live" <<'PY' || fail 'a zero usage report replaced the measured figure'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["latest_total_tokens"] == 258492, body
assert body["latest_total_tokens_last_report"] == 0, body
assert body["tool_in_flight"] == "bash", body
assert body["tool_in_flight_command"] == "grep -r needle /workspace", body
PY
  # A stream that never reported a usage figure publishes null, not 0.
  rm -f "$CONTROLLER_ROOT/runs/no-usage.live"
  printf '{"type":"session","id":"no-usage"}\n' > "$ROOT/no-usage.jsonl"
  python3 "$PILIVE" --stream "$ROOT/no-usage.jsonl" \
    --worktree "$WORKTREE" --out "$CONTROLLER_ROOT/runs/no-usage.live" --run no-usage \
    --wrapper-pid $$ --interval 1 --once > /dev/null 2>&1 \
    || fail 'pi-live could not snapshot a stream with no usage'
  python3 - "$CONTROLLER_ROOT/runs/no-usage.live" <<'PY' || fail 'a stream with no usage invented a token figure'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["latest_total_tokens"] is None, body
assert body["latest_total_tokens_last_report"] is None, body
PY
}

check_live_atomic_partial() {
  # A reader must never see a half-written snapshot: pi-live.py renames one
  # complete file into place, so a torn read is a hard failure.
  local live=$CONTROLLER_ROOT/runs/atomic.live
  rm -f "$live"
  printf '{"type":"session","id":"atomic"}\n' > "$ROOT/atomic-stream.jsonl"
  python3 "$PILIVE" --stream "$ROOT/atomic-stream.jsonl" --worktree "$WORKTREE" \
    --out "$live" --run atomic --wrapper-pid $$ --interval 1 --once > /dev/null 2>&1 \
    || fail 'pi-live could not write one snapshot'
  python3 - "$live" <<'PY' || fail 'the written snapshot is not one complete JSON object'
import json, sys
json.load(open(sys.argv[1], encoding="utf-8"))
PY
  [[ -z $(ls "$CONTROLLER_ROOT/runs"/.atomic.live.* 2>/dev/null || true) ]] \
    || fail 'pi-live left its temporary snapshot file behind'
  local alive_pid_json
  alive_pid_json=$ROOT/atomic-dead.live
  python3 "$PILIVE" --stream "$ROOT/atomic-stream.jsonl" --worktree "$WORKTREE" \
    --out "$alive_pid_json" --run atomic-dead --wrapper-pid 2147483646 --interval 1 --once > /dev/null 2>&1 \
    || fail 'pi-live could not snapshot a run whose wrapper pid is gone'
  python3 - "$alive_pid_json" <<'PY' || fail 'a missing wrapper pid was invented'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["wrapper_pid"] == 2147483646, body
PY
}

check_continue_prompt_file() {
  local prompt_file=$SCRIPT_DIR/../continue-empty-completion.md
  [[ -r $prompt_file ]] || fail 'the canonical continuation prompt file is missing'
  [[ $(wc -l < "$prompt_file") == 1 ]] || fail 'the continuation prompt must be one line'
  contains "$prompt_file" 'Your last turn ended without a tool call or a message.' 'the canonical prompt text'
  # It lives in exactly one file: no other controller source repeats it.
  ! grep -R -qF -- 'ended without a tool call or a message' \
    "$SCRIPT_DIR/../pi-impl.sh" "$SCRIPT_DIR/../lib.sh" "$SCRIPT_DIR/../codex-impl.sh" \
    || fail 'the continuation prompt text is duplicated outside its one file'
  # The wrapper refuses to run when that one file is unreadable, instead of
  # silently continuing with an empty prompt or skipping the continuation.
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    PI_CONTINUE_PROMPT_FILE="$ROOT/no-such-prompt.md" \
    "$SRC" "$WORKTREE" "$PROMPT" prompt-file-validation
  contains "$ROOT/focused-command.out" 'continue prompt file is not readable' \
    'a missing continuation prompt file is refused'
}

check_retry_count_config() {
  local before after
  before=$(pi_invocations)
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/retry-limit-state.json" PI_EMPTY_RETRY_LIMIT=1 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" \
    FAKE_PI_SEQUENCE='pi-empty-completion' FAKE_PI_COUNTER="$ROOT/retry-limit.counter" \
    FAKE_PI_SESSION_ID=retry-limit-session FAKE_PI_RC=0 PI_LIVE=0 \
    "$SRC" "$WORKTREE" "$PROMPT" retry-limit > "$ROOT/retry-limit.launch.log" 2>&1 < /dev/null \
    || fail 'a run configured for one continuation failed'
  after=$(pi_invocations)
  [[ $(( after - before )) == 2 ]] || fail "PI_EMPTY_RETRY_LIMIT=1 used $(( after - before )) pi invocations, expected 2"
  contains "$CONTROLLER_ROOT/runs/retry-limit.done" 'RETRIES=1' 'the configured continuation count is recorded'
}

check_input_validation() {
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    "$SRC" "$WORKTREE" "$PROMPT" report-validation --report relative-report.md
  contains "$ROOT/focused-command.out" 'report path must be absolute' 'a relative report path is refused'
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    "$SRC" "$ROOT/not-a-worktree" "$PROMPT" worktree-validation
}

check_retry_limit_validation() {
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    PI_EMPTY_RETRY_LIMIT=unbounded "$SRC" "$WORKTREE" "$PROMPT" retry-validation
  contains "$ROOT/focused-command.out" 'PI_EMPTY_RETRY_LIMIT must be a non-negative integer' \
    'an unparsable continuation count is refused'
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    PI_LIVE_INTERVAL_SECONDS=0 "$SRC" "$WORKTREE" "$PROMPT" live-validation
  contains "$ROOT/focused-command.out" 'PI_LIVE_INTERVAL_SECONDS must be a positive integer' \
    'a zero live interval is refused'
}

check_repo_not_a_worktree() {
  mkdir -p "$ROOT/plain-dir"
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    "$SRC" "$ROOT/plain-dir" "$PROMPT" plain-dir-run
  contains "$ROOT/focused-command.out" 'worktree is not a git worktree' \
    'a directory that cannot answer the clean-worktree question is refused'
}

# --- run-name ownership (CTL-14) -------------------------------------------

# Start one wrapper in the background and wait until it has claimed the name.
start_owned_run() {
  local run=$1 sleep_after=$2
  shift 2
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/ownership-$run-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID="$run-session" FAKE_PI_RC=0 FAKE_PI_SLEEP_AFTER="$sleep_after" \
    "$SRC" "$WORKTREE" "$PROMPT" "$run" "$@" \
    > "$ROOT/$run.launch.log" 2>&1 < /dev/null &
  OWNED_PID=$!
  # Readiness is the run's own event stream, not an ownership record: the check
  # under test must not need the guard's file to exist to observe a running run.
  wait_for_stream "$run" || { cat "$ROOT/$run.launch.log" >&2; fail "wrapper for $run exited before it started its agent"; }
}

# Wait until the fake agent has started: its first event is already in the run
# stream, so a kill at this point happens while the agent works.
wait_for_stream() {
  local name=$1 waited=0
  while (( waited < 200 )); do
    [[ -s $CONTROLLER_ROOT/runs/$name.jsonl ]] && return 0
    sleep 0.05
    waited=$(( waited + 1 ))
  done
  return 1
}

stop_owned_run() {
  local signal=${1:-TERM}
  kill -"$signal" "$OWNED_PID" 2>/dev/null || true
  wait "$OWNED_PID" 2>/dev/null || true
}

# The name lock is free right now: proven from outside the wrapper, so the check
# does not depend on the wrapper being able to claim it.
name_lock_is_free() {
  local name=$1 fds
  exec {fds}>>"$CONTROLLER_ROOT/runs/$name.lock" || return 2
  if flock -n "$fds"; then exec {fds}>&-; return 0; fi
  exec {fds}>&-
  return 1
}

check_run_name_ownership() {
  local first='' second=0 retired='' started_at=0
  rm -rf "$CONTROLLER_ROOT/runs"; mkdir -p "$CONTROLLER_ROOT/runs"
  # (1) A live owner refuses a second wrapper BY NAME, before it writes anything.
  start_owned_run owned-run 6
  first=$OWNED_PID
  if PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/ownership-second-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=owned-run-second FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" owned-run > "$ROOT/owned-run-second.log" 2>&1 < /dev/null; then
    stop_owned_run TERM
    fail 'a second wrapper ran on a run name a live wrapper owns'
  else
    second=$?
  fi
  [[ $second == 2 ]] || { stop_owned_run TERM; fail "a name-collision refusal exited $second, expected 2"; }
  contains "$ROOT/owned-run-second.log" "run name 'owned-run' is already owned by pid $first" \
    'the refusal names the live owner'
  contains "$ROOT/owned-run-second.log" 'CONTROLLER_RUN_FORCE=1' \
    'the refusal names the supported way forward'
  [[ ! -e $CONTROLLER_ROOT/runs/owned-run.done ]] \
    || { stop_owned_run TERM; fail 'the refused invocation wrote a completion marker'; }
  # (2) A resume of the same run is the same ownership, not a second dimension.
  if PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/ownership-resume-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=owned-run-session FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" owned-run resume owned-run-session \
    > "$ROOT/owned-run-resume.log" 2>&1 < /dev/null; then
    stop_owned_run TERM
    fail 'a resume interleaved with a live wrapper on the same run name'
  fi
  contains "$ROOT/owned-run-resume.log" 'is already owned by pid' 'a resume of a live run is refused by name'
  # (3) A different run name proceeds while the first is still working.
  wait_launch other-run deepseek-pi-volker pi-success /dev/null
  [[ -e $CONTROLLER_ROOT/runs/other-run.done ]] || { stop_owned_run TERM; fail 'a different run name was blocked by the first'; }
  # (4) SIGTERM releases the name; force retires (never overwrites) the records
  #     and names the identity it retired.
  stop_owned_run TERM
  name_lock_is_free owned-run || fail 'a TERMed wrapper stranded its run-name lock'
  CONTROLLER_RUN_FORCE=1 wait_launch owned-run deepseek-pi-volker pi-success /dev/null \
    || fail 'a retired name could not be reused'
  [[ -e $CONTROLLER_ROOT/runs/owned-run.done ]] || fail 'the forced reuse did not run'
  retired=$(find "$CONTROLLER_ROOT/runs/retired/owned-run" -mindepth 1 -maxdepth 1 -type d -print -quit)
  [[ -n $retired ]] || fail 'the forced reuse did not archive the previous records'
  [[ -f $retired/owned-run.jsonl ]] || fail 'the forced reuse deleted the previous run stream'
  contains "$retired/PREVIOUS-OWNER.txt" "pid=$first" 'the retired archive names the previous owner'
  # (5) SIGKILL: the name is free immediately, even while the agent the wrapper
  #     started still runs - a child never holds the wrapper's ownership. Wait
  #     until the fake agent is running (its first event is in the stream) so the
  #     case is "wrapper killed while the agent works", not "wrapper killed before
  #     it reached the agent".
  start_owned_run killed-run 8
  wait_for_stream killed-run || { stop_owned_run KILL; fail 'the killed-run agent never started'; }
  stop_owned_run KILL
  name_lock_is_free killed-run || fail 'a SIGKILLed wrapper left its run-name lock held'
  started_at=$SECONDS
  CONTROLLER_RUN_FORCE=1 wait_launch killed-run deepseek-pi-volker pi-success /dev/null \
    || fail 'a SIGKILLed name could not be reclaimed'
  (( SECONDS - started_at < 6 )) \
    || fail 'reclaiming a SIGKILLed name waited for the killed wrapper agent child to exit'
  contains "$ROOT/killed-run.launch.log" 'retired' 'the forced reclaim reports what it retired'
}

check_job_status_validation() {
  expect_rc 2 env CONTROLLER_HOME="$ROOT/status-home" CONTROLLER_RUNS_DIR="$ROOT/status-runs" \
    JOB_STATUS_STALL_SECONDS=soon "$JOB_STATUS"
  contains "$ROOT/focused-command.out" 'must be integer seconds' 'a non-integer stall threshold is refused'
  expect_rc 2 env CONTROLLER_HOME="$ROOT/status-home" CONTROLLER_RUNS_DIR="$ROOT/status-runs" \
    "$JOB_STATUS" ../escape
  contains "$ROOT/focused-command.out" 'invalid run name' 'a traversing run name is refused by job-status'
}


if [[ -n ${PI_TEST_FOCUS:-} ]]; then
  case $PI_TEST_FOCUS in
    xtrace-key) check_xtrace_key ;;
    outcome-error) check_outcome_error ;;
    outcome-length) clean_worktree; check_outcome_length ;;
    outcome-tool-call) clean_worktree; check_outcome_tool_call_no_text ;;
    outcome-stop-other) clean_worktree; check_outcome_stop_other ;;
    outcome-rc) check_outcome_rc ;;
    git-unreadable) clean_worktree; check_outcome_git_unreadable ;;
    retry-stream) clean_worktree; check_retry_streams_into_run ;;
    status-defaults) check_job_status_defaults ;;
    # One overwatch case per focused name, so a hand mutant is judged against
    # the single check that covers the guard it weakened.
    outcome-success|outcome-empty|outcome-recovered|outcome-uncommitted|outcome-no-report|report-unset|retry-bound)
      clean_worktree
      case $PI_TEST_FOCUS in
        outcome-success) check_outcome_success_report ;;
        outcome-empty)
          wait_launch_sequence focus-empty deepseek-pi-volker "pi-empty-completion pi-thinking-only"
          contains "$CONTROLLER_ROOT/runs/focus-empty.done" 'OUTCOME=EMPTY-COMPLETION-RETRIES-EXHAUSTED' \
            'a still-empty continuation run is reported as exhausted'
          contains "$CONTROLLER_ROOT/runs/focus-empty.events" '"ev":"empty-completion-retry"' \
            'the continuation is recorded in the run events'
          ;;
        outcome-recovered)
          wait_launch_sequence focus-recovered deepseek-pi-github2 "pi-empty-completion pi-success"
          contains "$CONTROLLER_ROOT/runs/focus-recovered.done" 'OUTCOME=SUCCESS' \
            'a recovered empty completion is a success'
          ;;
        outcome-uncommitted)
          printf 'working change\n' > "$WORKTREE/working.txt"
          wait_launch_sequence focus-uncommitted deepseek-pi-github2 pi-success
          contains "$CONTROLLER_ROOT/runs/focus-uncommitted.done" 'OUTCOME=UNCOMMITTED' \
            'uncommitted work is its own class'
          rm -f "$WORKTREE/working.txt"
          clean_worktree
          ;;
        outcome-no-report)
          wait_launch_sequence focus-no-report deepseek-pi-volker pi-success --report "$ROOT/focus-missing.md"
          contains "$CONTROLLER_ROOT/runs/focus-no-report.done" 'OUTCOME=NO-REPORT' \
            'a missing report is its own class'
          ;;
        report-unset)
          # No report path named: the check is skipped, not failed.
          wait_launch_sequence focus-report-unset deepseek-pi-volker pi-success
          [[ $(cat "$CONTROLLER_ROOT/runs/focus-report-unset.done") == 'EXIT=0 OUTCOME=SUCCESS' ]] \
            || fail 'an unset report path was treated as a missing report'
          ;;
        retry-bound)
          # A stream that stays empty forever: the bound must stop at one run
          # plus two continuations, never a fourth invocation.
          wait_launch_sequence focus-retry-bound deepseek-pi-volker pi-empty-completion
          contains "$CONTROLLER_ROOT/runs/focus-retry-bound.done" \
            'OUTCOME=EMPTY-COMPLETION-RETRIES-EXHAUSTED' \
            'the empty-completion continue loop is bounded at two attempts'
          [[ $(pi_invocations) == 3 ]] \
            || fail 'the empty-completion continue loop ran more than one run plus two continuations'
          ;;
      esac
      ;;
    no-retry-classes)
      wait_launch_sequence focus-no-retry deepseek-pi-volker pi-402
      [[ $(pi_invocations) == 1 ]] || fail 'a usage limit was continued'
      contains "$CONTROLLER_ROOT/runs/focus-no-retry.done" 'OUTCOME=USAGE-LIMIT' 'the usage limit class is preserved'
      ;;
    retry-validation)
      expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
        PI_EMPTY_RETRY_LIMIT=unbounded "$SRC" "$WORKTREE" "$PROMPT" focus-retry-validation
      contains "$ROOT/focused-command.out" 'PI_EMPTY_RETRY_LIMIT must be a non-negative integer' \
        'an unparsable continuation count is refused'
      ;;
    prompt-file)
      check_continue_prompt_file
      ;;
    repo-not-a-worktree)
      mkdir -p "$ROOT/focus-plain-dir"
      expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
        "$SRC" "$ROOT/focus-plain-dir" "$PROMPT" focus-plain-dir-run
      contains "$ROOT/focused-command.out" 'worktree is not a git worktree' \
        'a directory that cannot answer the clean-worktree question is refused'
      ;;
    live-usage-trend)
      rm -f "$CONTROLLER_ROOT/runs/focus-trend.live" "$CONTROLLER_ROOT/runs/focus-zero-report.live"
      python3 "$PILIVE" --stream "$SCRIPT_DIR/fixtures/pi-usage-only.jsonl" \
        --worktree "$WORKTREE" --out "$CONTROLLER_ROOT/runs/focus-trend.live" --run focus-trend \
        --wrapper-pid $$ --interval 1 --once > /dev/null 2>&1 \
        || fail 'pi-live could not snapshot a usage-only stream'
      python3 - "$CONTROLLER_ROOT/runs/focus-trend.live" <<'PY' || fail 'the usage trend was not read'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["latest_total_tokens"] == 101, body
assert body["latest_total_tokens_last_report"] == 101, body
assert body["assistant_turns"] == 0, body
assert body["tool_in_flight"] is None, body
PY
      python3 "$PILIVE" --stream "$SCRIPT_DIR/fixtures/pi-long-run-zero-reports.jsonl" \
        --worktree "$WORKTREE" --out "$CONTROLLER_ROOT/runs/focus-zero-report.live" \
        --run focus-zero-report --wrapper-pid $$ --interval 1 --once > /dev/null 2>&1 \
        || fail 'pi-live could not snapshot a zero-report stream'
      python3 - "$CONTROLLER_ROOT/runs/focus-zero-report.live" <<'PY' || fail 'a zero report replaced the measured figure'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["latest_total_tokens"] == 258492, body
assert body["latest_total_tokens_last_report"] == 0, body
PY
      ;;
    live-snapshot)
      # The sidecar itself is under test here, on the fixture stream a hung
      # tool call leaves behind.
      live=$CONTROLLER_ROOT/runs/focus-live.live
      rm -f "$live"
      python3 "$PILIVE" --stream "$SCRIPT_DIR/fixtures/pi-tool-in-flight.jsonl" \
        --worktree "$WORKTREE" --out "$live" --run focus-live \
        --wrapper-pid $$ --interval 1 --once > /dev/null 2>&1 \
        || fail 'pi-live could not snapshot a tool call in flight'
      python3 - "$live" <<'PY' || fail 'the live snapshot lacks a fact'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["tool_in_flight"] == "bash", body
assert isinstance(body["tool_in_flight_started_epoch"], int), body
assert isinstance(body["last_event_epoch"], int), body
assert body["tool_calls"] == 1, body
PY
      # A tool's own partial output is progress evidence: the event is appended
      # to the stream while the tool runs, so the tool in flight carries the
      # epoch of its last output even when the run is otherwise quiet.
      out_live=$CONTROLLER_ROOT/runs/focus-live-output.live
      rm -f "$out_live"
      python3 "$PILIVE" --stream "$SCRIPT_DIR/fixtures/pi-tool-progress.jsonl" \
        --worktree "$WORKTREE" --out "$out_live" --run focus-live-output \
        --wrapper-pid $$ --interval 1 --once > /dev/null 2>&1 \
        || fail 'pi-live could not snapshot a tool that is printing output'
      python3 - "$out_live" <<'PY' || fail 'the tool output evidence is missing from the snapshot'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["tool_in_flight"] == "bash", body
assert body["tool_in_flight_command"] == "pytest -q", body
assert isinstance(body["tool_in_flight_output_epoch"], int), body
assert isinstance(body["progress_epoch"], int), body
PY
      # A git fact that cannot be read stays null; it is never invented as 0.
      orphan_live=$CONTROLLER_ROOT/runs/focus-live-nogit.live
      rm -f "$orphan_live"
      mkdir -p "$ROOT/focus-no-git"
      python3 "$PILIVE" --stream "$SCRIPT_DIR/fixtures/pi-tool-in-flight.jsonl" \
        --worktree "$ROOT/focus-no-git" --out "$orphan_live" --run focus-live-nogit \
        --wrapper-pid $$ --interval 1 --once > /dev/null 2>&1 \
        || fail 'pi-live could not snapshot a worktree outside git'
      python3 - "$orphan_live" <<'PY' || fail 'an unreadable git fact was invented'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["commits_ahead"] is None, body
assert body["uncommitted_files"] is None, body
assert body["last_commit_epoch"] is None, body
PY
      ;;
    live-progress)
      # Progress evidence is read from the kernel, not guessed from the stream: a
      # tool that keeps working while printing nothing still advances the run's
      # CPU-time and IO counters, and that is what separates LONG-TOOL from
      # HUNG-TOOL. The stream here never grows.
      live=$CONTROLLER_ROOT/runs/focus-progress.live
      rm -f "$live"
      python3 -c 'import time
end = time.time() + 30
while time.time() < end:
    pass' &
      churn_pid=$!
      sleep 30 &
      steer_pid=$!
      python3 "$PILIVE" --stream "$SCRIPT_DIR/fixtures/pi-tool-progress.jsonl" \
        --worktree "$WORKTREE" --out "$live" --run focus-progress \
        --wrapper-pid $$ --interval 1 > /dev/null 2>&1 &
      sidecar_pid=$!
      row=$ROOT/focus-progress-before.json
      for _ in {1..60}; do [[ -s $live ]] && break; sleep 0.1; done
      cp -- "$live" "$row" 2>/dev/null || fail 'pi-live never wrote its first snapshot'
      # Wait for EVIDENCE, never for a clock: the child above burns CPU, so the
      # subtree's counters grow however loaded the host is. The loop's bound is a
      # limit on waiting, not a claim about how fast the host is.
      for _ in {1..100}; do
        cp -- "$live" "$ROOT/focus-progress-after.json" 2>/dev/null || :
        if python3 -c 'import json, sys
before = json.load(open(sys.argv[1], encoding="utf-8"))["progress_epoch"]
after = json.load(open(sys.argv[2], encoding="utf-8"))["progress_epoch"]
sys.exit(0 if isinstance(after, int) and isinstance(before, int) and after > before else 1)' \
          "$row" "$ROOT/focus-progress-after.json"; then
          break
        fi
        sleep 0.3
      done
      kill -- "$sidecar_pid" "$churn_pid" 2>/dev/null || true
      wait "$sidecar_pid" 2>/dev/null || true
      python3 - "$row" "$ROOT/focus-progress-after.json" "$steer_pid" "$churn_pid" <<'PY' \
        || fail 'progress evidence or the steering target is missing from the snapshot'
import json, sys
before = json.load(open(sys.argv[1], encoding="utf-8"))
after = json.load(open(sys.argv[2], encoding="utf-8"))
steer_pid = int(sys.argv[3])
churn_pid = int(sys.argv[4])
assert isinstance(after["progress_epoch"], int), after
assert isinstance(before["progress_epoch"], int), before
assert after["progress_epoch"] > before["progress_epoch"], (before, after)
# A tool's own partial output is progress evidence too.
assert isinstance(after["tool_in_flight_output_epoch"], int), after
# The exact child pid is the steering target, and the wrapper never is.
pids = [row["pid"] for row in after["steering_children"]]
assert any(row["pid"] == steer_pid for row in after["steering_children"]), after["steering_children"]
assert any(row["pid"] == churn_pid for row in after["steering_children"]), after["steering_children"]
assert pids, after
assert all(row.get("command") for row in after["steering_children"] if row["pid"] == steer_pid), after
PY
      [[ $(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['wrapper_pid'])" "$ROOT/focus-progress-after.json") == "$$" ]] \
        || fail 'the snapshot did not keep the wrapper pid'
      python3 - "$ROOT/focus-progress-after.json" "$sidecar_pid" <<'PY' \
        || fail 'the sidecar or wrapper was published as a steering target'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
pids = [row["pid"] for row in body["steering_children"]]
assert body["wrapper_pid"] not in pids, body["steering_children"]
PY
      wait "$steer_pid" 2>/dev/null || true
      kill -- "$steer_pid" 2>/dev/null || true
      wait "$steer_pid" 2>/dev/null || true
      ;;
    live-atomic)
      printf '{"type":"session","id":"atomic"}\n' > "$ROOT/focus-atomic.jsonl"
      atomic_anchor=$ROOT/focus-atomic-anchor.live
      mkdir -p "$CONTROLLER_ROOT/runs"
      printf '{"run": "before"}\n' > "$atomic_anchor"
      ln -f "$atomic_anchor" "$CONTROLLER_ROOT/runs/focus-atomic.live" \
        || fail 'the atomic-write check could not create its hard link'
      python3 "$PILIVE" --stream "$ROOT/focus-atomic.jsonl" --worktree "$WORKTREE" \
        --out "$CONTROLLER_ROOT/runs/focus-atomic.live" --run focus-atomic \
        --wrapper-pid $$ --interval 1 --once > /dev/null 2>&1 \
        || fail 'pi-live could not write a snapshot'
      grep -qF '"run": "before"' "$atomic_anchor" \
        || fail 'the snapshot was written in place instead of replaced by rename'
      rm -f -- "$atomic_anchor"
      python3 - "$CONTROLLER_ROOT/runs/focus-atomic.live" <<'PY' || fail 'the snapshot is not one JSON object'
import json, sys
json.load(open(sys.argv[1], encoding="utf-8"))
PY
      [[ -z $(ls "$CONTROLLER_ROOT/runs"/.focus-atomic.live.* 2>/dev/null || true) ]] \
        || fail 'the snapshot left a temporary file behind'
      ;;
    status-states) check_job_status_states ;;
    prompt-absolute)
      expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi \
        "$SRC" "$WORKTREE" relative-prompt focused-relative
      contains "$ROOT/focused-command.out" 'prompt path must be absolute' 'relative prompt path is refused'
      ;;
    worktree-exists)
      expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi \
        "$SRC" "$ROOT/missing-worktree" "$PROMPT" focused-worktree
      contains "$ROOT/focused-command.out" 'usage: ' 'missing worktree is refused'
      ;;
    session-id)
      expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi \
        "$SRC" "$WORKTREE" "$PROMPT" focused-session resume 'invalid session id'
      contains "$ROOT/focused-command.out" 'usage: ' 'invalid resume session id is refused'
      ;;
    mode)
      expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi \
        "$SRC" "$WORKTREE" "$PROMPT" focused-mode new unused
      contains "$ROOT/focused-command.out" 'usage: ' 'unused mode arguments are refused'
      ;;
    run-name)
      expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi \
        "$SRC" "$WORKTREE" "$PROMPT" ../escape
      contains "$ROOT/focused-command.out" 'invalid run name' 'traversing run names are refused'
      ;;
    run-collision)
      mkdir -p "$CONTROLLER_ROOT/runs"
      : > "$CONTROLLER_ROOT/runs/focused-collision.done"
      expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi \
        "$SRC" "$WORKTREE" "$PROMPT" focused-collision
      contains "$ROOT/focused-command.out" 'controller run name already has evidence' 'existing run evidence is refused'
      ;;
    two-wrappers-one-run) check_run_name_ownership ;;
    ownership-force) check_run_name_ownership ;;
    ownership-release) check_run_name_ownership ;;
    auth-file)
      local_home=$ROOT/focused-no-env
      mkdir -p "$local_home"
      expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$local_home" PI_ROUTE=deepseek-pi-volker \
        "$SRC" "$WORKTREE" "$PROMPT" focused-auth-file
      contains "$ROOT/focused-command.out" "pi route requires the local \$HOME/.env.ai file" 'missing auth file is refused'
      ;;
    auth-source)
      local_home=$ROOT/focused-no-env
      mkdir -p "$local_home"
      printf 'false\n' > "$local_home/.env.ai"
      expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$local_home" PI_ROUTE=deepseek-pi-volker \
        "$SRC" "$WORKTREE" "$PROMPT" focused-auth-source
      contains "$ROOT/focused-command.out" 'could not load the local pi API-key environment' 'auth source failure is refused'
      ;;
    source-env)
      local_home=$ROOT/focused-unexported
      mkdir -p "$local_home"
      cat > "$local_home/.env.ai" <<'ENV'
OPENDESIGN_API_KEY_VOLKER=test-key
OPENDESIGN_API_KEY_GITHUB2=test-key
ENV
      if ! PATH="$ROOT/bin:$PATH" HOME="$local_home" PI_ROUTE=deepseek-pi-github2 \
        FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_SESSION_ID=focused-source \
        FAKE_PI_RC=0 "$SRC" "$WORKTREE" "$PROMPT" focused-source \
        > "$ROOT/focused-source.launch.log" 2>&1 < /dev/null; then
        cat "$ROOT/focused-source.launch.log" >&2
        fail 'wrapper did not source and export the selected key'
      fi
      grep 'VOLKER_PRESENT=' "$PI_ENV_LOG" | tail -n 1 > "$ROOT/focused-source.names"
      contains "$ROOT/focused-source.names" 'VOLKER_PRESENT= GITHUB2_PRESENT=yes' 'selected key is exported from sourced values'
      ;;
    prompt-newline)
      printf 'brief\n' > "$ROOT/focused-prompt.md"
      wait_launch focused-newline deepseek-pi-volker pi-success /dev/null
      cmp -s "$PI_PROMPT_LOG" "$ROOT/focused-prompt.md" || fail 'prompt file trailing newline was lost'
      ;;
    volker-key-missing|github2-key-missing)
      local_home=$ROOT/focused-no-key
      mkdir -p "$local_home"
      : > "$local_home/.env.ai"
      if [[ $PI_TEST_FOCUS == volker-key-missing ]]; then
        key_message='OPENDESIGN_API_KEY_VOLKER is not set'
        key_route=deepseek-pi-volker
      else
        key_message='OPENDESIGN_API_KEY_GITHUB2 is not set'
        key_route=deepseek-pi-github2
      fi
      expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$local_home" PI_ROUTE="$key_route" \
        "$SRC" "$WORKTREE" "$PROMPT" focused-key-missing
      contains "$ROOT/focused-command.out" "$key_message" 'missing selected provider key is refused'
      ;;
    unknown-route)
      expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=unknown \
        "$SRC" "$WORKTREE" "$PROMPT" focused-unknown-route
      contains "$ROOT/focused-command.out" 'unknown PI_ROUTE=unknown' 'unknown pi route is refused'
      ;;
    pinned-route)
      wait_launch focused-pinned deepseek-pi-github2 pi-success /dev/null
      [[ $(cat "$CONTROLLER_ROOT/runs/focused-pinned.route") == deepseek-pi-github2 ]] \
        || fail 'pinned Pi route sidecar lost its account'
      ;;
    resume-recorded)
      python3 - "$STATE" <<'PY'
import json, sys
json.dump({
    "volker": {"last_used_epoch": 2, "blocked_until_epoch": 0},
    "github2": {"last_used_epoch": 1, "blocked_until_epoch": 0},
    "threads": {"recorded-session": "volker"},
}, open(sys.argv[1], "w", encoding="utf-8"))
PY
      wait_launch focused-resume deepseek-pi pi-success /dev/null resume recorded-session
      [[ $(cat "$CONTROLLER_ROOT/runs/focused-resume.route") == deepseek-pi-volker ]] \
        || fail 'Pi resume did not use the session recorded account'
      ;;
    provider-account)
      wait_launch focused-provider deepseek-pi-volker pi-success /dev/null
      contains "$PI_ARGV_LOG" '--model opendesign_volker/deepseek-v4.1-flash-eco' \
        'Pi model provider follows the selected account'
      ;;
    session-sidecar)
      wait_launch focused-thread deepseek-pi-volker pi-success /dev/null
      contains "$CONTROLLER_ROOT/runs/focused-thread.thread" 'focused-thread-session' \
        'Pi session is saved in its sidecar'
      ;;
    session-event-id)
      wait_launch focused-event-session deepseek-pi-volker pi-success /dev/null
      python3 - "$STATE" <<'PY'
import json, sys
path = sys.argv[1]
state = json.load(open(path, encoding="utf-8"))
state["threads"].pop("focused-event-session-session", None)
json.dump(state, open(path, "w", encoding="utf-8"))
PY
      rm -f "$CONTROLLER_ROOT/runs/focused-event-session.thread"
      helper=$(dirname -- "$SRC")/opendesign-accounts.py
      found_account=$(CONTROLLER_RUNS_DIR="$CONTROLLER_ROOT/runs" CONTROLLER_OD_STATE="$STATE" \
        python3 "$helper" find-thread-account focused-event-session-session "$CONTROLLER_ROOT/runs") \
        || fail 'Pi session event was not usable for account recovery'
      [[ $found_account == volker ]] || fail 'Pi session event recovered the wrong account'
      ;;
    stdin-closed)
      mkfifo "$ROOT/focused-stdin.fifo"
      exec 9<> "$ROOT/focused-stdin.fifo"
      setsid env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
        FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_RC=0 \
        FAKE_PI_STDIN_OPEN_MARKER="$ROOT/focused-stdin-open.marker" \
        "$SRC" "$WORKTREE" "$PROMPT" focused-stdin \
        > "$ROOT/focused-stdin.launch.log" 2>&1 < "$ROOT/focused-stdin.fifo" &
      stdin_pid=$!
      while kill -0 "$stdin_pid" 2>/dev/null && [[ ! -e $ROOT/focused-stdin-open.marker && ! -e $CONTROLLER_ROOT/runs/focused-stdin.done ]]; do
        sleep 0.05
      done
      if [[ -e $ROOT/focused-stdin-open.marker ]]; then
        kill -TERM -- "-$stdin_pid" 2>/dev/null || true
        wait "$stdin_pid" 2>/dev/null || true
        fail 'fake pi remained attached to caller stdin'
      fi
      if ! wait "$stdin_pid"; then
        cat "$ROOT/focused-stdin.launch.log" >&2
        fail 'pi did not complete with open caller stdin'
      fi
      exec 9>&-
      contains "$PI_ENV_LOG" 'STDIN_EOF=1' 'pi stdin is closed'
      ;;
    quota-outcome|quota-block)
      wait_launch focused-quota deepseek-pi-volker pi-402 /dev/null
      [[ $(cat "$CONTROLLER_ROOT/runs/focused-quota.done") == 'EXIT=0 OUTCOME=USAGE-LIMIT' ]] \
        || fail 'Pi 402 event is not recorded as a usage limit'
      if [[ $PI_TEST_FOCUS == quota-block ]]; then
        python3 - "$STATE" <<'PY' || fail 'Pi 402 did not block its account'
import json, sys
assert json.load(open(sys.argv[1], encoding="utf-8"))["volker"]["blocked_until_epoch"] > 4_000_000_000
PY
      fi
      ;;
    quota-reset)
      wait_launch focused-quota-reset deepseek-pi-github2 pi-402-unknown /dev/null
      contains "$ROOT/focused-quota-reset.launch.log" 'reset time unknown; temporary block for 21600 seconds' \
        'Pi 402 without reset uses its bounded unknown-reset block'
      python3 - "$STATE" <<'PY' || fail 'unknown-reset Pi 402 did not persist its bounded block'
import json, sys, time
until = json.load(open(sys.argv[1], encoding="utf-8"))["github2"]["blocked_until_epoch"]
assert until > time.time() + 20_000
PY
      ;;
    github2-key-export|volker-key-export|unselected-key-unset)
      case $PI_TEST_FOCUS in
        github2-key-export) selected_route=deepseek-pi-github2; expected_keys='VOLKER_PRESENT= GITHUB2_PRESENT=yes' ;;
        volker-key-export) selected_route=deepseek-pi-volker; expected_keys='VOLKER_PRESENT=yes GITHUB2_PRESENT=' ;;
        unselected-key-unset) selected_route=deepseek-pi-github2; expected_keys='VOLKER_PRESENT= GITHUB2_PRESENT=yes' ;;
      esac
      if [[ $PI_TEST_FOCUS != unselected-key-unset ]]; then
        cat > "$HOME_ROOT/.env.ai" <<'ENV'
OPENDESIGN_API_KEY_VOLKER=test-key
OPENDESIGN_API_KEY_GITHUB2=test-key
ENV
      fi
      wait_launch focused-keys "$selected_route" pi-success /dev/null
      grep 'VOLKER_PRESENT=' "$PI_ENV_LOG" | tail -n 1 > "$ROOT/focused-key-names"
      contains "$ROOT/focused-key-names" "$expected_keys" 'Pi receives only the selected API-key variable'
      ;;
    blocked-skip)
      python3 - "$STATE" <<'PY'
import json, sys
json.dump({
    "volker": {"last_used_epoch": 1, "blocked_until_epoch": 4_102_444_800},
    "github2": {"last_used_epoch": 2, "blocked_until_epoch": 0},
}, open(sys.argv[1], "w", encoding="utf-8"))
PY
      wait_launch focused-blocked deepseek-pi pi-success /dev/null
      [[ $(cat "$CONTROLLER_ROOT/runs/focused-blocked.route") == deepseek-pi-github2 ]] \
        || fail 'automatic Pi route did not skip the blocked account'
      ;;
    resume-fallback)
      python3 - "$STATE" <<'PY'
import json, sys
json.dump({
    "volker": {"last_used_epoch": 2, "blocked_until_epoch": 4_102_444_800},
    "github2": {"last_used_epoch": 1, "blocked_until_epoch": 0},
    "threads": {"recorded-session": "volker"},
}, open(sys.argv[1], "w", encoding="utf-8"))
PY
      wait_launch focused-fallback deepseek-pi pi-success /dev/null resume recorded-session
      [[ $(cat "$CONTROLLER_ROOT/runs/focused-fallback.route") == deepseek-pi-github2 ]] \
        || fail 'Pi resume did not fall back from its blocked recorded account'
      ;;
    both-blocked-resume)
      python3 - "$STATE" <<'PY'
import json, sys
json.dump({
    "volker": {"last_used_epoch": 2, "blocked_until_epoch": 4_102_444_800},
    "github2": {"last_used_epoch": 1, "blocked_until_epoch": 4_102_444_800},
    "threads": {"recorded-session": "volker"},
}, open(sys.argv[1], "w", encoding="utf-8"))
PY
      expect_rc 3 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi \
        "$SRC" "$WORKTREE" "$PROMPT" focused-both-blocked resume recorded-session
      [[ ! -e $PI_ARGV_LOG ]] || [[ ! -s $PI_ARGV_LOG ]] || fail 'Pi ran while both accounts were blocked'
      contains "$ROOT/focused-command.out" 'both Open Design accounts are blocked' 'both-blocked Pi resume is refused'
      ;;
    docs)
      README_SOURCE=${README_SOURCE:-$SCRIPT_DIR/../README.md}
      DESIGN_SOURCE=${DESIGN_SOURCE:-$SCRIPT_DIR/../docs/DESIGN-GUIDE.md}
      CONSUMER_SOURCE=${CONSUMER_SOURCE:-$SCRIPT_DIR/../docs/CONSUMERS.md}
      RULES_SOURCE=${RULES_SOURCE:-$SCRIPT_DIR/../rules.md}
      ENV_SOURCE=${ENV_SOURCE:-$SCRIPT_DIR/../controller.local.env.example}
      contains "$README_SOURCE" "\`deepseek-pi\` | \`pi-impl.sh\` | Favored Open Design route" 'README names the favored Pi route'
      contains "$DESIGN_SOURCE" 'Pi 1.5.0 can report a provider 402' 'design guide documents Pi 402 handling'
      contains "$CONSUMER_SOURCE" "\`deepseek-pi\` is the favored Pi route" 'consumer guide names the favored Pi route'
      contains "$CONSUMER_SOURCE" 'PI_ROUTE=deepseek-pi' 'consumer guide gives a Pi launch example'
      contains "$RULES_SOURCE" 'deepseek-pi-volker' 'rules list the pinned Pi routes'
      contains "$ENV_SOURCE" 'PI_THINKING="high"' 'environment example documents Pi thinking'
      contains "$README_SOURCE" 'OUTCOME=NO-REPORT' 'README documents the report outcome'
      contains "$README_SOURCE" "\`CONTEXT-FULL\`" 'README documents the context-full class'
      contains "$README_SOURCE" "\`TOOL-CALL-NO-TEXT\`" 'README documents the tool-call-no-text class'
      contains "$DESIGN_SOURCE" "\`CONTEXT-FULL\` is the real end state of" \
        'design guide explains why context-full is its own class'
      contains "$DESIGN_SOURCE" "\`TOOL-CALL-NO-TEXT\` is" 'design guide explains the tool-call-no-text class'
      contains "$CONSUMER_SOURCE" "| \`CONTEXT-FULL\` |" 'consumer guide gives the context-full action'
      contains "$CONSUMER_SOURCE" "| \`TOOL-CALL-NO-TEXT\` |" 'consumer guide gives the tool-call-no-text action'
      contains "$README_SOURCE" "\`EMPTY-COMPLETION\`" 'README documents the empty-completion outcome'
      contains "$DESIGN_SOURCE" 'Why the terminal outcome is classified' 'design guide explains the outcome classification'
      contains "$CONSUMER_SOURCE" "\`.done\` outcome" 'consumer guide gives the outcome vocabulary'
      contains "$CONSUMER_SOURCE" 'EMPTY-COMPLETION-RETRIES-EXHAUSTED' 'consumer guide names the exhausted continuation class'
      contains "$README_SOURCE" 'EMPTY-COMPLETION-RETRIES-EXHAUSTED' 'README names the exhausted continuation class'
      contains "$README_SOURCE" 'PI_EMPTY_RETRY_LIMIT' 'README names the continuation bound'
      contains "$DESIGN_SOURCE" 'Continuation is bounded and class-specific' 'design guide explains the continuation bound'
      contains "$DESIGN_SOURCE" 'Why liveness is a file, not a poll of the process' 'design guide explains the liveness sidecar'
      contains "$README_SOURCE" 'job-status.sh' 'README documents the liveness read'
      contains "$CONSUMER_SOURCE" 'JOB_STATUS_STALL_SECONDS' 'consumer guide documents the stall threshold'
      contains "$README_SOURCE" "**\`LONG-TOOL\` and \`HUNG-TOOL\` are different words on purpose**" \
        'README states that the two words are separate'
      contains "$README_SOURCE" "\`HUNG-TOOL\` (the same age" 'README separates long-tool from hung-tool'
      contains "$README_SOURCE" 'progress evidence' 'README names the progress fact'
      contains "$README_SOURCE" 'To steer a stuck tool, kill the exact child pid' \
        'README states the steering action'
      contains "$README_SOURCE" "\`steer=\` — never the wrapper" \
        'README forbids killing the wrapper to steer a tool'
      contains "$README_SOURCE" "\`since-commit=no-commits-yet\`" 'README qualifies the since-commit age'
      contains "$README_SOURCE" "\`DIED (wrapper pid <pid> gone, no .done)\`" 'README documents the died state'
      contains "$DESIGN_SOURCE" "Why \`LONG-TOOL\` exists as its own word" \
        'design guide explains why long-tool is separate'
      contains "$DESIGN_SOURCE" "\`DIED (wrapper pid" 'design guide documents the died classification'
      contains "$CONSUMER_SOURCE" 'steer=' 'consumer guide shows the steering column'
      contains "$CONSUMER_SOURCE" "Kill \`8451\`, not \`8123\`" 'consumer guide says which process to kill'
      contains "$CONSUMER_SOURCE" 'OUTCOME=CODE-EXIT' 'consumer guide documents the Codex marker shape'
      contains "$CONSUMER_SOURCE" 'OUTCOME=CODE-EXIT' 'consumer guide documents the Codex marker shape'
      ;;
    *) fail "unknown PI_TEST_FOCUS=$PI_TEST_FOCUS" ;;
  esac
  exit 0
fi

wait_launch auto-1 deepseek-pi pi-success /dev/null
[[ $(cat "$CONTROLLER_ROOT/runs/auto-1.route") == deepseek-pi-volker ]] || fail 'first automatic pi start must select volker'
if ! grep -qF -- 'EXIT=0 OUTCOME=SUCCESS' "$CONTROLLER_ROOT/runs/auto-1.done"; then
  echo 'pi completion diagnostics:' >&2
  for evidence in auto-1.done auto-1.jsonl auto-1.err; do
    if [[ -f $CONTROLLER_ROOT/runs/$evidence ]]; then
      printf '%s: ' "$evidence" >&2
      cat "$CONTROLLER_ROOT/runs/$evidence" >&2
    fi
  done
  printf 'launch log: ' >&2
  cat "$ROOT/auto-1.launch.log" >&2
  fail 'normal pi completion is recorded as success'
fi
contains "$CONTROLLER_ROOT/runs/auto-1.thread" 'auto-1-session' 'the pi session id is recorded in a sidecar'
contains "$CONTROLLER_ROOT/runs/auto-1.last.txt" 'Completed the fixture task.' 'last assistant text is saved'

wait_launch auto-2 deepseek-pi pi-success /dev/null
[[ $(cat "$CONTROLLER_ROOT/runs/auto-2.route") == deepseek-pi-github2 ]] || fail 'second automatic pi start must alternate to github2'
wait_launch auto-3 deepseek-pi pi-success /dev/null
[[ $(cat "$CONTROLLER_ROOT/runs/auto-3.route") == deepseek-pi-volker ]] || fail 'third automatic pi start must alternate back to volker'
contains "$PI_ARGV_LOG" '--model opendesign_volker/deepseek-v4.1-flash-eco' 'pi uses the selected Volker provider and default model'
contains "$PI_ARGV_LOG" '--thinking high' 'pi defaults to high thinking'
contains "$PI_ARGV_LOG" "--session-dir $HOME_ROOT/.pi/agent/sessions" 'pi uses its default session directory'
printf 'brief\n' > "$ROOT/prompt-with-newline.md"
PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
  FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_SESSION_ID=prompt-newline-session \
  FAKE_PI_RC=0 "$SRC" "$WORKTREE" "$ROOT/prompt-with-newline.md" prompt-newline \
  > "$ROOT/prompt-newline.launch.log" 2>&1 < /dev/null || fail 'pi wrapper rejected a prompt file ending in a newline'
cmp -s "$PI_PROMPT_LOG" "$ROOT/prompt-with-newline.md" \
  || fail 'pi prompt argument lost the prompt file trailing newline'
contains "$PI_ENV_LOG" 'VOLKER_PRESENT=yes GITHUB2_PRESENT=' 'pi receives only the selected Volker API-key variable'
contains "$PI_ENV_LOG" 'VOLKER_PRESENT= GITHUB2_PRESENT=yes' 'pi receives only the selected github2 API-key variable'
python3 - "$STATE" <<'PY'
import json, sys
path = sys.argv[1]
state = json.load(open(path, encoding="utf-8"))
state["threads"].pop("auto-1-session", None)
json.dump(state, open(path, "w", encoding="utf-8"))
PY
rm -f "$CONTROLLER_ROOT/runs/auto-1.thread"
found_account=$(CONTROLLER_RUNS_DIR="$CONTROLLER_ROOT/runs" CONTROLLER_OD_STATE="$STATE" \
  python3 "$SCRIPT_DIR/../opendesign-accounts.py" find-thread-account auto-1-session "$CONTROLLER_ROOT/runs") \
  || fail 'pi session id in the run stream and route sidecar was not found'
[[ $found_account == volker ]] || fail 'pi session lookup returned the wrong recorded account'
wait_launch resume-recorded deepseek-pi pi-success /dev/null resume auto-1-session
[[ $(cat "$CONTROLLER_ROOT/runs/resume-recorded.route") == deepseek-pi-volker ]] || fail 'resume stays on the account recorded for the pi session'
contains "$PI_ARGV_LOG" '--session auto-1-session' 'resume passes the recorded pi session id'

PI_MODEL=deepseek-v4.1-flash PI_THINKING=low wait_launch model-override deepseek-pi pi-success /dev/null
contains "$PI_ARGV_LOG" '--model opendesign_github2/deepseek-v4.1-flash' 'caller model override survives local controller settings'
contains "$PI_ARGV_LOG" '--thinking low' 'caller thinking override survives local controller settings'

wait_launch quota-volker deepseek-pi-volker pi-402 /dev/null
[[ $(cat "$CONTROLLER_ROOT/runs/quota-volker.done") == 'EXIT=0 OUTCOME=USAGE-LIMIT' ]] || fail 'pi exit zero with a final 402 is classified as usage-limited'
[[ $(cat "$CONTROLLER_ROOT/runs/quota-volker.route") == deepseek-pi-volker ]] || fail 'pinned pi route records its selected account'
contains "$ROOT/quota-volker.launch.log" 'reset time observed' 'pi 402 reset is reported by shared account bookkeeping'
python3 - "$STATE" <<'PY' || fail 'pi 402 reset was not recorded for the selected account'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["volker"]["blocked_until_epoch"] > 4_000_000_000
PY

wait_launch blocked-skip deepseek-pi pi-success /dev/null
[[ $(cat "$CONTROLLER_ROOT/runs/blocked-skip.route") == deepseek-pi-github2 ]] || fail 'automatic pi start skips the account blocked by the 402'

wait_launch resume-fallback deepseek-pi pi-success /dev/null resume auto-1-session
[[ $(cat "$CONTROLLER_ROOT/runs/resume-fallback.route") == deepseek-pi-github2 ]] || fail 'resume falls back when its recorded account is blocked'
contains "$ROOT/resume-fallback.launch.log" 'account fallback from deepseek-volker to deepseek-github2 because the recorded account is blocked' \
  'resume logs the shared helper account fallback'

UNKNOWN_STATE=$ROOT/opendesign-unknown-reset.json
if ! PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-github2 \
  CONTROLLER_OD_STATE="$UNKNOWN_STATE" CONTROLLER_OD_UNKNOWN_BLOCK_SECONDS=21600 \
  FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-402-unknown.jsonl" \
  FAKE_PI_SESSION_ID=unknown-reset-session FAKE_PI_RC=0 \
  "$SRC" "$WORKTREE" "$PROMPT" unknown-reset > "$ROOT/unknown-reset.launch.log" 2>&1 \
  < /dev/null; then
  cat "$ROOT/unknown-reset.launch.log" >&2
  fail 'pi 402 without reset_at did not complete with its usage-limit outcome'
fi
contains "$CONTROLLER_ROOT/runs/unknown-reset.done" 'EXIT=0 OUTCOME=USAGE-LIMIT' 'pi 402 without reset_at is still classified as usage-limited'
contains "$ROOT/unknown-reset.launch.log" 'reset time unknown; temporary block for 21600 seconds' \
  'pi 402 without reset_at uses the bounded six-hour default'
python3 - "$UNKNOWN_STATE" <<'PY' || fail 'unknown-reset 402 did not persist a temporary block'
import json, sys, time
state = json.load(open(sys.argv[1], encoding="utf-8"))
until = state["github2"]["blocked_until_epoch"]
assert until > time.time() + 20_000
PY

python3 - "$STATE" <<'PY'
import json, sys
path = sys.argv[1]
state = json.load(open(path, encoding="utf-8"))
state["github2"]["blocked_until_epoch"] = 4_102_444_800
json.dump(state, open(path, "w", encoding="utf-8"))
PY
before=$(wc -l < "$PI_ARGV_LOG")
if PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi \
  "$SRC" "$WORKTREE" "$PROMPT" both-blocked > "$ROOT/both-blocked.log" 2>&1; then
  fail 'automatic pi start ran while both Open Design accounts were blocked'
else rc=$?; [[ $rc == 3 ]] || fail "both-blocked pi exit was $rc, expected 3"; fi
contains "$ROOT/both-blocked.log" 'both Open Design accounts are blocked' 'both blocked pi accounts report their reset'
[[ $(wc -l < "$PI_ARGV_LOG") == "$before" ]] || fail 'pi started even though both accounts were blocked'
if PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi \
  "$SRC" "$WORKTREE" "$PROMPT" both-blocked-resume resume auto-1-session \
  > "$ROOT/both-blocked-resume.log" 2>&1; then
  fail 'pi resumed while both Open Design accounts were blocked'
else rc=$?; [[ $rc == 3 ]] || fail "both-blocked resume exit was $rc, expected 3"; fi
contains "$ROOT/both-blocked-resume.log" 'both Open Design accounts are blocked' 'both blocked pi resume accounts report their reset'
[[ $(wc -l < "$PI_ARGV_LOG") == "$before" ]] || fail 'pi resume started while both accounts were blocked'

mkfifo "$ROOT/open-stdin.fifo"
exec 9<> "$ROOT/open-stdin.fifo"
setsid env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
  FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_RC=0 \
  FAKE_PI_STDIN_OPEN_MARKER="$ROOT/stdin-open.marker" \
  "$SRC" "$WORKTREE" "$PROMPT" stdin-closed > "$ROOT/stdin-closed.launch.log" 2>&1 \
  < "$ROOT/open-stdin.fifo" &
stdin_pid=$!
while kill -0 "$stdin_pid" 2>/dev/null && [[ ! -e $ROOT/stdin-open.marker && ! -e $CONTROLLER_ROOT/runs/stdin-closed.done ]]; do
  sleep 0.05
done
if [[ -e $ROOT/stdin-open.marker ]]; then
  kill -TERM -- "-$stdin_pid" 2>/dev/null || true
  wait "$stdin_pid" 2>/dev/null || true
  fail 'fake pi remained attached to the caller stdin'
fi
if ! wait "$stdin_pid"; then
  cat "$ROOT/stdin-closed.launch.log" >&2
  fail 'pi did not finish when its caller stdin remained open'
fi
exec 9>&-
contains "$PI_ENV_LOG" 'STDIN_EOF=1' 'fake pi observed closed stdin'

if PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi \
  "$SRC" "$WORKTREE" relative-prompt invalid-relative > "$ROOT/relative-prompt.log" 2>&1; then
  fail 'relative prompt path was accepted'
else rc=$?; [[ $rc == 2 ]] || fail "relative prompt exit was $rc, expected 2"; fi
contains "$ROOT/relative-prompt.log" 'prompt path must be absolute' 'prompt path is required to be absolute'
if PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi \
  "$SRC" "$ROOT/missing-worktree" "$PROMPT" missing-worktree \
  > "$ROOT/missing-worktree.log" 2>&1; then
  fail 'pi wrapper accepted a missing worktree'
else rc=$?; [[ $rc == 2 ]] || fail "missing worktree exit was $rc, expected 2"; fi
contains "$ROOT/missing-worktree.log" 'usage: ' 'pi wrapper requires an existing worktree'
if PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi \
  "$SRC" "$WORKTREE" "$PROMPT" extra-arguments new unused \
  > "$ROOT/extra-arguments.log" 2>&1; then
  fail 'pi wrapper accepted extra arguments for a new run'
else rc=$?; [[ $rc == 2 ]] || fail "extra argument exit was $rc, expected 2"; fi
contains "$ROOT/extra-arguments.log" 'usage: ' 'pi wrapper rejects unused mode arguments'

VALIDATION_STATE=$ROOT/validation-state.json
if PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=unknown \
  CONTROLLER_OD_STATE="$VALIDATION_STATE" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
  FAKE_PI_SESSION_ID=unknown-route-session FAKE_PI_RC=0 \
  "$SRC" "$WORKTREE" "$PROMPT" unknown-route > "$ROOT/unknown-route.log" 2>&1; then
  fail 'unknown pi route was accepted'
else rc=$?; [[ $rc == 2 ]] || fail "unknown route exit was $rc, expected 2"; fi
contains "$ROOT/unknown-route.log" 'unknown PI_ROUTE=unknown' 'unknown pi routes fail closed'
if PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi \
  CONTROLLER_OD_STATE="$VALIDATION_STATE" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
  FAKE_PI_SESSION_ID=invalid-session FAKE_PI_RC=0 \
  "$SRC" "$WORKTREE" "$PROMPT" invalid-session resume 'invalid session id' \
  > "$ROOT/invalid-session.log" 2>&1; then
  fail 'pi wrapper accepted an invalid session id'
else rc=$?; [[ $rc == 2 ]] || fail "invalid session exit was $rc, expected 2"; fi
contains "$ROOT/invalid-session.log" 'usage: ' 'pi resume requires a valid session id'

NO_KEY_HOME=$ROOT/no-key-home
mkdir -p "$NO_KEY_HOME"
: > "$NO_KEY_HOME/.env.ai"
before=$(wc -l < "$PI_ARGV_LOG")
if PATH="$ROOT/bin:$PATH" HOME="$NO_KEY_HOME" PI_ROUTE=deepseek-pi-github2 \
  "$SRC" "$WORKTREE" "$PROMPT" missing-key > "$ROOT/missing-key.log" 2>&1; then
  fail 'pi wrapper started without the selected API-key environment variable'
else rc=$?; [[ $rc == 2 ]] || fail "missing key exit was $rc, expected 2"; fi
contains "$ROOT/missing-key.log" 'OPENDESIGN_API_KEY_GITHUB2 is not set' 'missing selected provider key is refused'
[[ $(wc -l < "$PI_ARGV_LOG") == "$before" ]] || fail 'pi started without the selected provider key'
if PATH="$ROOT/bin:$PATH" HOME="$NO_KEY_HOME" PI_ROUTE=deepseek-pi-volker \
  "$SRC" "$WORKTREE" "$PROMPT" missing-volker-key > "$ROOT/missing-volker-key.log" 2>&1; then
  fail 'pi wrapper started without the selected Volker API-key environment variable'
else rc=$?; [[ $rc == 2 ]] || fail "missing Volker key exit was $rc, expected 2"; fi
contains "$ROOT/missing-volker-key.log" 'OPENDESIGN_API_KEY_VOLKER is not set' 'missing selected Volker key is refused'
[[ $(wc -l < "$PI_ARGV_LOG") == "$before" ]] || fail 'pi started without the selected Volker key'

MISSING_ENV_HOME=$ROOT/missing-env-home
mkdir -p "$MISSING_ENV_HOME"
if PATH="$ROOT/bin:$PATH" HOME="$MISSING_ENV_HOME" PI_ROUTE=deepseek-pi-volker \
  "$SRC" "$WORKTREE" "$PROMPT" missing-env > "$ROOT/missing-env.log" 2>&1; then
  fail 'pi wrapper started without the local .env.ai file'
else rc=$?; [[ $rc == 2 ]] || fail "missing env-file exit was $rc, expected 2"; fi
contains "$ROOT/missing-env.log" "pi route requires the local \$HOME/.env.ai file" 'missing .env.ai is refused'

UNEXPORTED_HOME=$ROOT/unexported-home
mkdir -p "$UNEXPORTED_HOME"
cat > "$UNEXPORTED_HOME/.env.ai" <<'ENV'
OPENDESIGN_API_KEY_VOLKER=test-key
OPENDESIGN_API_KEY_GITHUB2=test-key
ENV
if ! PATH="$ROOT/bin:$PATH" HOME="$UNEXPORTED_HOME" PI_ROUTE=deepseek-pi-github2 \
  FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_SESSION_ID=unexported-session \
  FAKE_PI_RC=0 "$SRC" "$WORKTREE" "$PROMPT" unexported-env > "$ROOT/unexported-env.launch.log" 2>&1 \
  < /dev/null; then
  cat "$ROOT/unexported-env.launch.log" >&2
  fail 'pi wrapper did not export a key loaded from .env.ai'
fi
grep 'VOLKER_PRESENT=' "$PI_ENV_LOG" | tail -n 1 > "$ROOT/unexported-env.names"
contains "$ROOT/unexported-env.names" 'VOLKER_PRESENT= GITHUB2_PRESENT=yes' 'wrapper exports only the selected key name from sourced shell variables'
if ! PATH="$ROOT/bin:$PATH" HOME="$UNEXPORTED_HOME" PI_ROUTE=deepseek-pi-volker \
  FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_SESSION_ID=unexported-volker-session \
  FAKE_PI_RC=0 "$SRC" "$WORKTREE" "$PROMPT" unexported-volker-env > "$ROOT/unexported-volker-env.launch.log" 2>&1 \
  < /dev/null; then
  cat "$ROOT/unexported-volker-env.launch.log" >&2
  fail 'pi wrapper did not export the Volker key loaded from .env.ai'
fi
grep 'VOLKER_PRESENT=' "$PI_ENV_LOG" | tail -n 1 > "$ROOT/unexported-volker-env.names"
contains "$ROOT/unexported-volker-env.names" 'VOLKER_PRESENT=yes GITHUB2_PRESENT=' 'wrapper exports the selected Volker key name'

if PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
  FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_SESSION_ID=collision-session FAKE_PI_RC=0 \
  "$SRC" "$WORKTREE" "$PROMPT" auto-1 > "$ROOT/collision.log" 2>&1; then
  fail 'pi wrapper replaced a prior run name without explicit force'
else rc=$?; [[ $rc == 2 ]] || fail "run collision exit was $rc, expected 2"; fi
contains "$ROOT/collision.log" 'controller run name already has evidence: auto-1' 'pi new-run evidence collision is refused'
if PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
  FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_SESSION_ID=escape-session FAKE_PI_RC=0 \
  "$SRC" "$WORKTREE" "$PROMPT" ../escape > "$ROOT/run-traversal.log" 2>&1; then
  fail 'pi wrapper accepted a traversing run name'
else rc=$?; [[ $rc == 2 ]] || fail "run traversal exit was $rc, expected 2"; fi
contains "$ROOT/run-traversal.log" 'invalid run name' 'pi run name traversal guard fires'

check_xtrace_key
check_outcome_error
check_outcome_length
check_outcome_tool_call_no_text
check_outcome_stop_other
check_outcome_rc

check_outcome_success_report
check_outcome_empty
check_outcome_empty_recovered
check_outcome_no_retry_other_classes
check_outcome_uncommitted
check_outcome_git_unreadable
check_outcome_no_report
check_outcome_empty_retry_bounded
check_retry_streams_into_run
check_live_snapshot
check_live_atomic
check_job_status
check_job_status_states
check_job_status_defaults
check_job_status_readonly
check_live_usage_trend
check_live_atomic_partial
check_continue_prompt_file
check_retry_count_config
check_input_validation
check_retry_limit_validation
check_repo_not_a_worktree
check_job_status_validation
check_run_name_ownership

not_contains test-key 'run evidence contains no key material' "$CONTROLLER_ROOT/runs"
not_contains test-key 'account state contains no key material' "$STATE"
not_contains test-key 'unknown-reset account state contains no key material' "$UNKNOWN_STATE"
not_contains test-key 'pi argv contains no key material' "$PI_ARGV_LOG"
not_contains test-key 'pi environment log contains no key material' "$PI_ENV_LOG"
not_contains test-key 'wrapper logs contain no key material' "$ROOT"/*.launch.log

README_SOURCE=${README_SOURCE:-$SCRIPT_DIR/../README.md}
DESIGN_SOURCE=${DESIGN_SOURCE:-$SCRIPT_DIR/../docs/DESIGN-GUIDE.md}
CONSUMER_SOURCE=${CONSUMER_SOURCE:-$SCRIPT_DIR/../docs/CONSUMERS.md}
RULES_SOURCE=${RULES_SOURCE:-$SCRIPT_DIR/../rules.md}
ENV_SOURCE=${ENV_SOURCE:-$SCRIPT_DIR/../controller.local.env.example}
contains "$README_SOURCE" "\`deepseek-pi\` | \`pi-impl.sh\` | Favored Open Design route" 'README names pi as the favored route'
contains "$README_SOURCE" 'docs/DESIGN-GUIDE.md#pi-open-design-route' 'README links the pi route design rationale'
contains "$README_SOURCE" 'docs/CONSUMERS.md#run-pi-on-open-design' 'README links the pi adoption guide'
contains "$DESIGN_SOURCE" 'Pi 1.5.0 can report a provider 402' 'design guide explains pi exit-zero 402 classification'
contains "$CONSUMER_SOURCE" "\`deepseek-pi\` is the favored Pi route" 'consumer guide names the favored pi route'
contains "$CONSUMER_SOURCE" 'PI_ROUTE=deepseek-pi' 'consumer guide has a copyable pi launch'
contains "$RULES_SOURCE" 'deepseek-pi-volker' 'controller rules list pinned pi routes'
contains "$ENV_SOURCE" 'PI_THINKING="high"' 'local environment example documents the thinking default'
if grep -Eq 'OPENDESIGN_API_KEY_[A-Z0-9_]+=' "$ENV_SOURCE"; then
  fail 'controller local environment example contains an API-key assignment'
fi

echo 'PASS pi-impl: detached launch, sessions, account alternation, 402 block/outcome, resume, stdin, key isolation, route and prompt guards'
