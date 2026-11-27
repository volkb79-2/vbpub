#!/usr/bin/env bash
set -uo pipefail
# The caller's ambient controller configuration is not this check's business:
# every check here builds its own temporary controller root, so an exported
# CONTROLLER_HOME / RUNS_DIR / LOGS_DIR / REPO / HOSTS_FILE must not decide where
# a check reads or writes. Relying on the caller ("run it with env -u") moved the
# same defect into the operator's shell, where it read as a product bug.
unset CONTROLLER_HOME CONTROLLER_RUNS_DIR CONTROLLER_LOGS_DIR CONTROLLER_REPO CONTROLLER_HOSTS_FILE
unset PI_ROUTE PI_MODEL PI_THINKING PI_SESSION_DIR OPENDESIGN_API_KEY_VOLKER OPENDESIGN_API_KEY_GITHUB2
# The run mode (PI_RUN_MODE) must not be decided by an ambient value: every check
# here names the contract it launches under.
unset PI_RUN_MODE
# The pi seams pi-review.sh exports for its own child, and the review budget: an
# ambient PI_EXTRA_ARGS (which is exactly what a review launcher leaves in a
# controller shell) would refuse or widen every launch this check makes.
unset PI_EXTRA_ARGS PI_REVIEW_MAX_SECONDS PI_REVIEW_MAX_STREAM_BYTES PI_REVIEW_POLL_SECONDS PI_REVIEW_KILL_WAIT_SECONDS
# pi's agent directory decides a launch refusal (pi prepends its bin dir to the
# agent's PATH), so the caller's ambient value must not decide these checks:
# every check that cares names it explicitly.
unset PI_CODING_AGENT_DIR FAKE_PIDNS_WRAPPER_RC
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PI_IMPL_SOURCE=${PI_IMPL_SCRIPT:-$SCRIPT_DIR/../pi-impl.sh}
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
PI_ARGV0_LOG=$ROOT/pi-argv0.log
PI_ENV_LOG=$ROOT/pi-env.log
PI_PROMPT_LOG=$ROOT/pi-prompt.log
TEST_CONTROLLER=$ROOT/controller-source
PI_IMPL_SOURCE_DIR=$(cd -- "$(dirname -- "$PI_IMPL_SOURCE")" && pwd -P)
mkdir -p "$TEST_CONTROLLER"
for sibling in lib.sh pi-live.py opendesign-accounts.py pi-review-argv \
  continue-empty-completion.md implementer-prompt-rules.md review-prompt-rules.md \
  pi-pidns-extension.js; do
  if [[ -e $PI_IMPL_SOURCE_DIR/$sibling ]]; then
    cp -a -- "$PI_IMPL_SOURCE_DIR/$sibling" "$TEST_CONTROLLER/$sibling"
  else
    cp -a -- "$SCRIPT_DIR/../$sibling" "$TEST_CONTROLLER/$sibling"
  fi
done
if [[ -d $PI_IMPL_SOURCE_DIR/pi-shims ]]; then
  cp -a -- "$PI_IMPL_SOURCE_DIR/pi-shims" "$TEST_CONTROLLER/pi-shims"
else
  cp -a -- "$SCRIPT_DIR/../pi-shims" "$TEST_CONTROLLER/pi-shims"
fi
cp -a -- "$PI_IMPL_SOURCE" "$TEST_CONTROLLER/pi-impl.sh"
cat > "$TEST_CONTROLLER/pi-pidns-bash" <<'SH'
#!/usr/bin/env bash
set -uo pipefail
[[ $# == 2 && $1 == -c && $2 == true ]] || exit 91
for arg in "$@"; do printf '%s\0' "$arg" >> "${PI_PIDNS_PROBE_LOG:?}"; done
exit "${FAKE_PIDNS_WRAPPER_RC:-0}"
SH
chmod 0755 "$TEST_CONTROLLER/pi-pidns-bash"
SRC=$TEST_CONTROLLER/pi-impl.sh
# The copied wrapper resolves shims and PID namespace files beside itself.
SHIM_DIR=$TEST_CONTROLLER/pi-shims
PI_PIDNS_PROBE_LOG=$ROOT/pidns-probe.argv
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
PI_THINKING="max"
PI_SESSION_DIR="$HOME/.pi/agent/sessions"
ENV
cat > "$HOME_ROOT/.env.ai" <<'ENV'
export OPENDESIGN_API_KEY_VOLKER=test-key
export OPENDESIGN_API_KEY_GITHUB2=test-key
ENV

# General resume fixtures intentionally seed an exact implementer session
# record. Focused tests that exercise unknown or legacy state invoke the wrapper
# directly and build only the records their scenario allows.
seed_resume_session() {
  local session=$1 records=$CONTROLLER_ROOT/runs/session-modes
  mkdir -p "$records"
  [[ -e $records/$session || -L $records/$session ]] || printf 'implementer\n' > "$records/$session"
}

cat > "$ROOT/bin/pi" <<'SH'
#!/usr/bin/env bash
set -uo pipefail
printf '%s\n' "$*" >> "$PI_ARGV_LOG"
# One argv element per NUL-terminated record, for the checks that must tell an
# ELEMENT from a substring anywhere in the line: the prompt is an argv element
# too, so a space-joined log cannot answer "is this element exactly this?".
for arg in "$@"; do printf '%s\0' "$arg"; done >> "$PI_ARGV0_LOG"
printf '%s' "${!#}" > "$PI_PROMPT_LOG"
printf 'VOLKER_PRESENT=%s GITHUB2_PRESENT=%s\n' \
  "${OPENDESIGN_API_KEY_VOLKER:+yes}" "${OPENDESIGN_API_KEY_GITHUB2:+yes}" >> "$PI_ENV_LOG"
# Signal guard: when FAKE_PI_GUARD_LOG names a file, this is a guarded launch
# check. ASSERT that the three signal spellings resolve INSIDE the shim
# directory before anything else, then call them, each on a pattern this check
# made random and that matches nothing. The real pkill/killall are never called.
if [[ -n ${FAKE_PI_GUARD_LOG:-} ]]; then
  guard_failed=0
  for spelling in pkill killall pgrep; do
    resolved=$(command -v "$spelling" 2>/dev/null) || resolved=
    case $resolved in
      "${FAKE_PI_SHIM_DIR:?}"/*) [[ -x $resolved ]] || guard_failed=1 ;;
      *) guard_failed=1 ;;
    esac
    printf 'PATH-ENTRY %s=%s\n' "$spelling" "${resolved:-none}" >> "$FAKE_PI_GUARD_LOG"
  done
  (( guard_failed == 0 )) || {
    printf 'GUARD-MISSING %s\n' "${FAKE_PI_SHIM_DIR:?}" >> "$FAKE_PI_GUARD_LOG"
    exit 90
  }
  guard_pattern=no-match-${RANDOM:-$(awk 'BEGIN{srand(); print int(rand()*999999)}')}-${PI_GUARD_SEQUENCE_INDEX:-0}
  # The agent's PATH is a fact this check records, not one it assumes: a shim
  # directory prepended with a trailing `:` leaves an EMPTY field, and an empty
  # PATH field means the CURRENT DIRECTORY (the worktree) to every command the
  # agent runs.
  guard_path_rest=${PATH-}
  guard_path_empty=0
  while :; do
    guard_path_field=${guard_path_rest%%:*}
    [[ -n $guard_path_field ]] || guard_path_empty=1
    [[ $guard_path_rest == *:* ]] || break
    guard_path_rest=${guard_path_rest#*:}
  done
  printf 'PATH-EMPTY-FIELD %s\n' "$guard_path_empty" >> "$FAKE_PI_GUARD_LOG"
  # One caller per guard fact, so an assertion names the spelling that failed.
  # $1 labels the case; the rest is the whole command line, `pgrep` included.
  guard_call() {
    local label=$1
    shift
    "$@" > "$FAKE_PI_GUARD_LOG.$label.out" 2>&1
    local rc=$?
    printf 'RC %s=%s OUT=%s\n' "$label" "$rc" \
      "$(tr '\n' ' ' < "$FAKE_PI_GUARD_LOG.$label.out")" >> "$FAKE_PI_GUARD_LOG"
  }
  for spelling in pkill killall; do
    "$spelling" -f "$guard_pattern" > "$FAKE_PI_GUARD_LOG.$spelling.out" 2>&1
    guard_rc=$?
    printf 'RC %s=%s OUT=%s\n' "$spelling" "$guard_rc" \
      "$(tr '\n' ' ' < "$FAKE_PI_GUARD_LOG.$spelling.out")" >> "$FAKE_PI_GUARD_LOG"
  done
  # Every SHORT signal spelling the shim exists for has its own case here:
  # `-<number>` and `-<SIGNAME>` are single letters after pgrep's own parse, so
  # a later edit that lets a signal letter into the no-value set would forward
  # `pgrep -9 -f x` / `pgrep -TERM -f x` to the real pgrep and signal every
  # match. The suite would stay green without these cases.
  guard_call pgrep-signal pgrep --signal TERM -f "$guard_pattern"
  guard_call pgrep-short-number pgrep -9 -f "$guard_pattern"
  guard_call pgrep-short-name pgrep -TERM -f "$guard_pattern"
  guard_call pgrep-kill pgrep --kill -f "$guard_pattern"
  guard_call pgrep-signal-eq pgrep --signal=TERM -f "$guard_pattern"
  guard_call pgrep-cluster-value pgrep -d, -9 -f "$guard_pattern"
  # A PASS-THROUGH with a value that itself starts with `-`: `-d` takes its
  # value out of the rest of the cluster or the next argument, exactly as
  # getopt does, so `-d -` is the delimiter `-`, not an option, and the plain
  # pattern still reaches the real pgrep (rc 1 for a no-match pattern).
  guard_call pgrep-value-dash pgrep -d - -f "$guard_pattern"
  guard_call pgrep-plain pgrep -f "$guard_pattern"
fi
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
# A deterministic hold: the fake agent keeps working until the CHECK creates
# this file. A `sleep N` would make every case that needs "the agent is still
# running" a race against how loaded the host is.
# FAKE_PI_HELD_MARKER appears once the whole stream is out and the fake agent is holding: the check's "the stream is
# complete" event.
if [[ -n ${FAKE_PI_HOLD_UNTIL:-} ]]; then
  [[ -z ${FAKE_PI_HELD_MARKER:-} ]] || : > "$FAKE_PI_HELD_MARKER"
  waited=0
  while [[ ! -e $FAKE_PI_HOLD_UNTIL ]]; do
    sleep 0.05
    waited=$(( waited + 1 ))
  done
fi
[[ -z ${FAKE_PI_SLEEP_AFTER:-} ]] || sleep "$FAKE_PI_SLEEP_AFTER"
exit "${FAKE_PI_RC:-0}"
SH
chmod +x "$ROOT/bin/pi"

export CONTROLLER_HOME=$CONTROLLER_ROOT CONTROLLER_REPO=$WORKTREE
export CONTROLLER_OD_STATE=$STATE
export PI_ARGV_LOG PI_ARGV0_LOG PI_ENV_LOG PI_PROMPT_LOG PI_PIDNS_PROBE_LOG

launch() {
  local run=$1 route=$2 fixture=$3 input=$4
  shift 4
  local fake_session_id="${run}-session"
  if [[ ${1:-} == resume && -n ${2:-} ]]; then
    fake_session_id=$2
    seed_resume_session "$fake_session_id"
  fi
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
  if [[ ${1:-} == resume && -n ${2:-} ]]; then seed_resume_session "$2"; fi
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

# The fake pi's argv, one ELEMENT per record: the checks that must know an argv
# element from a substring (the prompt the wrapper appends a rule paragraph to)
# read this, and an empty argv file means pi never ran.
argv_elements() {
  [[ -s $PI_ARGV0_LOG ]] || fail "pi never ran (no argv log at $PI_ARGV0_LOG)"
  local i last_start=-1
  local -a all=()
  mapfile -d '' -t all < "$PI_ARGV0_LOG"
  for (( i = 0; i < ${#all[@]}; i++ )); do
    [[ ${all[i]} == -p ]] && last_start=$i
  done
  (( last_start >= 0 )) || fail 'pi argv log has no invocation boundary'
  ARGV=("${all[@]:last_start}")
}
last_prompt_element() {
  argv_elements
  printf '%s' "${ARGV[${#ARGV[@]}-1]}"
}
# The wrapper APPENDS its fixed rule paragraph to the caller's brief, so the
# launched prompt must START with the brief byte for byte (a prompt file's own
# trailing newline is part of that) and then carry the paragraph -- never a
# rewritten or re-wrapped brief.
prompt_starts_with() {
  local prompt_file=$1 description=$2 text brief=''
  argv_elements
  text=${ARGV[${#ARGV[@]}-1]}
  # `read -d ''` keeps the file's trailing newlines (`$(<file)` strips them);
  # the quoted operand is compared literally, so only the final `*` is a pattern.
  IFS= read -r -d '' brief < "$prompt_file" || :
  if [[ -z $brief || $text != "$brief"$'\n'* ]]; then
    fail "$description (prompt did not start with the brief: $(printf '%s' "$text" | head -c 120))"
  fi
}
prompt_ends_with() {
  local rules_file=$1 description=$2 text
  argv_elements
  text=${ARGV[${#ARGV[@]}-1]}
  if [[ $text != *$'\n'"$(<"$rules_file")"$'\n' ]]; then fail "$description"; fi
}

# --- the run mode (P-plat-26c) ----------------------------------------------
# The read-only set has ONE production definition. The independent test
# allow-list below makes added argv elements visible instead of trusting that
# definition to certify itself.
REVIEW_ARGV_FILE=$SCRIPT_DIR/../pi-review-argv
IMPLEMENTER_RULES=$SCRIPT_DIR/../implementer-prompt-rules.md
REVIEW_RULES=$SCRIPT_DIR/../review-prompt-rules.md
argv_reset() { : > "$PI_ARGV0_LOG"; }
argv_absent() {
  local element
  argv_elements
  for element in "${ARGV[@]}"; do [[ $element == "$1" ]] && fail "$2 (argv: ${ARGV[*]})"; done
  return 0
}
# Assert the LAST invocation has exactly the allowed review argv elements.
# The explicit allow-list is independent of pi-review-argv, so a new flag in
# that file or in base_pi_args cannot certify itself by appearing in both.
assert_review_argv() {
  local prefix=$1 i model thinking session_dir prompt_arg delimiter_index
  local -a file_argv=() permitted_review=(--tools 'read,grep,find,ls' --exclude-tools 'mcp__*' --no-extensions --no-mcp) expected=()
  mapfile -t file_argv < "$REVIEW_ARGV_FILE"
  if (( ${#file_argv[@]} != ${#permitted_review[@]} )); then
    fail "$prefix: pi-review-argv has ${#file_argv[@]} elements, expected ${#permitted_review[@]}"
  fi
  for (( i = 0; i < ${#permitted_review[@]}; i++ )); do
    [[ ${file_argv[i]} == "${permitted_review[i]}" ]] \
      || fail "$prefix: pi-review-argv element $i is not permitted (${file_argv[i]})"
  done
  argv_elements
  if (( ${#ARGV[@]} != 17 && ${#ARGV[@]} != 19 )); then
    fail "$prefix: review argv has ${#ARGV[@]} elements, expected 17 or 19 (${ARGV[*]})"
  fi
  [[ ${ARGV[0]} == -p && ${ARGV[1]} == --mode && ${ARGV[2]} == json && ${ARGV[3]} == --model \
    && ${ARGV[5]} == --thinking && ${ARGV[7]} == --session-dir ]] \
    || fail "$prefix: the review argv base flags are not in the permitted positions (${ARGV[*]})"
  model=${ARGV[4]}
  thinking=${ARGV[6]}
  session_dir=${ARGV[8]}
  expected=(-p --mode json --model "$model" --thinking "$thinking" --session-dir "$session_dir" "${permitted_review[@]}")
  if (( ${#ARGV[@]} == 19 )); then
    [[ ${ARGV[15]} == --session && -n ${ARGV[16]} ]] \
      || fail "$prefix: the resume suffix is not the permitted --session pair (${ARGV[*]})"
    expected+=(--session "${ARGV[16]}")
  fi
  delimiter_index=$((${#ARGV[@]} - 2))
  [[ ${ARGV[delimiter_index]} == -- ]] \
    || fail "$prefix: the prompt separator is not the last flag (${ARGV[*]})"
  prompt_arg=${ARGV[${#ARGV[@]}-1]}
  expected+=(-- "$prompt_arg")
  if (( ${#ARGV[@]} != ${#expected[@]} )); then
    fail "$prefix: review argv has an unexpected element (${ARGV[*]})"
  fi
  for (( i = 0; i < ${#expected[@]}; i++ )); do
    [[ ${ARGV[i]} == "${expected[i]}" ]] \
      || fail "$prefix: review argv element $i is not permitted (${ARGV[i]})"
  done
}
# The prompt pi received must be the caller's brief PLUS the mode's rule
# paragraph: never rewritten in the middle, never launched without the rule.
assert_mode_prompt() {
  local mode=$1 prefix=$2 rules=$IMPLEMENTER_RULES
  [[ $mode != review ]] || rules=$REVIEW_RULES
  prompt_starts_with "$PROMPT" "$prefix: the brief is launched unchanged at the front"
  prompt_ends_with "$rules" "$prefix: the $mode rule paragraph is the last text pi reads"
}
# An EMPTY-COMPLETION continuation does not resend the brief: pi receives the
# canonical continue prompt, one newline, then the mode's rule paragraph, and
# nothing else. Compared whole and byte for byte.
assert_continuation_prompt() {
  local mode=$1 prefix=$2 rules=$IMPLEMENTER_RULES continue_text='' rules_text=''
  [[ $mode != review ]] || rules=$REVIEW_RULES
  IFS= read -r -d '' continue_text < "$SCRIPT_DIR/../continue-empty-completion.md" || :
  IFS= read -r -d '' rules_text < "$rules" || :
  [[ -n $continue_text && -n $rules_text ]] || fail "$prefix: the continue prompt or the $mode rule file is empty"
  argv_elements
  [[ ${ARGV[${#ARGV[@]}-1]} == "$continue_text"$'\n'"$rules_text" ]] \
    || fail "$prefix: the prompt is not exactly the continue prompt plus the $mode rule paragraph"
}

check_pi_default_thinking_max() {
  local home=$ROOT/default-thinking-controller rc=0 argv=''
  mkdir -p "$home"
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" CONTROLLER_HOME="$home" \
    PI_ROUTE=deepseek-pi-volker CONTROLLER_OD_STATE="$ROOT/default-thinking-state.json" \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=default-thinking-session FAKE_PI_RC=0 PI_LIVE=0 \
    "$SRC" "$WORKTREE" "$PROMPT" default-thinking \
    > "$ROOT/default-thinking.launch.log" 2>&1 < /dev/null || rc=$?
  [[ $rc == 0 ]] || {
    cat "$ROOT/default-thinking.launch.log" >&2
    fail 'pi default-thinking launch failed'
  }
  argv=$(grep -- '^-p ' "$PI_ARGV_LOG" | tail -n 1)
  if [[ $argv != *'--thinking max'* ]]; then
    printf 'argv=%s\n' "$argv" >&2
    cat "$ROOT/default-thinking.launch.log" >&2
    fail 'pi Open Design routes do not default to the catalog maximum thinking level'
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

# A settled `stop` whose final message carries TEXT AND a tool call is SUCCESS:
# the text is the run's final message and the tool call is trailing bookkeeping
# (pi emits it after the narrative in the same message). This is pinned by a
# test, not left to be re-argued: the class exists for a message with a tool call
# and NO text, which is a different fact.
check_outcome_text_and_tool_call() {
  local before after
  before=$(pi_invocations)
  CONTROLLER_OD_STATE=$ROOT/outcome-text-call-state.json PI_LIVE=0 \
    wait_launch outcome-text-call deepseek-pi-volker pi-stop-text-and-tool-call /dev/null
  after=$(pi_invocations)
  [[ $(head -n 1 "$CONTROLLER_ROOT/runs/outcome-text-call.done") == 'EXIT=0 OUTCOME=SUCCESS' ]] \
    || fail 'a settled stop with text and a trailing tool call is not SUCCESS'
  [[ $(( after - before )) == 1 ]] || fail 'a successful text-and-tool-call run was continued'
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
  # The third attempt's message carries text that is only WHITESPACE: "has text"
  # must mean text after stripping, or a blank message reads as a completion.
  wait_launch_sequence outcome-empty deepseek-pi-volker \
    "pi-empty-completion pi-thinking-only pi-whitespace-only"
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

# CTL-55: waits on progress events, never on a clock. The fake agent HOLDS after its whole stream is in the run stream
# (held marker); the sidecar has then consumed it once it has written a snapshot from a tick that started after the
# marker. Snapshots are replaced atomically and each tick changes `updated_epoch` (ticks are >= 1 s apart), so the
# third distinct snapshot seen after the marker is such a tick's. Only a generous hang guard remains, and it is
# reported as inconclusive, never as the real failure.
LIVE_HANG_GUARD_S=300

# $1 wrapper pid, $2 hold file: let the held agent finish and reap the wrapper; a wrapper that still lives at the
# hang guard is killed.
live_reap() {
  local wrapper=$1 hold=$2 deadline=$(( $(date +%s) + LIVE_HANG_GUARD_S ))
  : > "$hold"
  while kill -0 "$wrapper" 2>/dev/null && (( $(date +%s) < deadline )); do sleep 0.1; done
  kill -- "$wrapper" 2>/dev/null || true
  wait "$wrapper" 2>/dev/null || true
}

check_live_snapshot() {
  local live=$CONTROLLER_ROOT/runs/live-run.live hold=$ROOT/live-run.release held=$ROOT/live-run.held
  local snap=$ROOT/live-run.snapshot.json
  rm -f "$CONTROLLER_ROOT/runs/live-run.done" "$live" "$hold" "$held" "$snap"
  # The wrapper starts pi-live.py from its own directory: a mirror whose pi-live.py is $PILIVE lets a mutant reach it.
  local ctl=$ROOT/live-ctl
  rm -rf "$ctl"; mkdir "$ctl"
  ln -s "$(dirname -- "$SRC")"/* "$ctl"/
  ln -sf "$PILIVE" "$ctl/pi-live.py"
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/live-state.json" \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" \
    FAKE_PI_SEQUENCE='pi-tool-in-flight' FAKE_PI_COUNTER="$ROOT/live-run.counter" \
    FAKE_PI_SESSION_ID=live-run-session FAKE_PI_RC=0 \
    FAKE_PI_HOLD_UNTIL="$hold" FAKE_PI_HELD_MARKER="$held" \
    PI_LIVE_INTERVAL_SECONDS=1 PI_EMPTY_RETRY_LIMIT=0 \
    "$ctl/$(basename -- "$SRC")" "$WORKTREE" "$PROMPT" live-run > "$ROOT/live-run.launch.log" 2>&1 < /dev/null &
  local wrapper=$!
  local deadline=$(( $(date +%s) + LIVE_HANG_GUARD_S )) changes=0 current previous=
  # Phases: the agent holds with the whole stream written, then three distinct snapshots are seen.
  while (( changes < 3 )); do
    if (( $(date +%s) >= deadline )); then
      live_reap "$wrapper" "$hold"
      cat "$ROOT/live-run.launch.log" >&2
      fail 'inconclusive: no sidecar progress within the hang guard'
    fi
    if ! kill -0 "$wrapper" 2>/dev/null; then
      : > "$hold"  # an orphaned fake pi would otherwise spin on its hold loop forever
      wait "$wrapper" 2>/dev/null || true
      cat "$ROOT/live-run.launch.log" >&2
      fail 'the wrapper exited before the check released its agent'
    fi
    if [[ -e $held ]]; then
      current=$(cat "$live" 2>/dev/null || true)
      if [[ -n $current && $current != "$previous" ]]; then
        previous=$current
        changes=$(( changes + 1 ))
      fi
    fi
    sleep 0.05
  done
  printf '%s\n' "$current" > "$snap"
  live_reap "$wrapper" "$hold"
  python3 - "$snap" <<'PY' || fail 'the live snapshot is missing facts after the stream was consumed'
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
  # pi-review.sh APPENDS REVIEW-MODIFIED-TREE after pi-impl.sh's own marker, and
  # line 1 can read `EXIT=0 OUTCOME=SUCCESS` for a reviewer commit. job-status
  # must report every marker line's verdict, not line 1 alone.
  printf 'EXIT=0 OUTCOME=SUCCESS\nREVIEW-MODIFIED-TREE\n' > "$CONTROLLER_ROOT/runs/flagged-review.done"
  "$JOB_STATUS" flagged-review > "$ROOT/job-status-flagged.out" 2>&1 || {
    cat "$ROOT/job-status-flagged.out" >&2
    fail 'job-status failed on a flagged review'
  }
  grep -qF 'flagged-review: FINISHED' "$ROOT/job-status-flagged.out" \
    || { cat "$ROOT/job-status-flagged.out" >&2; fail 'job-status did not report the flagged review'; }
  grep -qF 'REVIEW-MODIFIED-TREE' "$ROOT/job-status-flagged.out" \
    || { cat "$ROOT/job-status-flagged.out" >&2; fail 'job-status hid the REVIEW-MODIFIED-TREE flag'; }
  # The review launcher has three more verdicts, and all can stand beside EXIT=0.
  printf 'EXIT=0 OUTCOME=SUCCESS\nREVIEW-NO-REPORT\n' > "$CONTROLLER_ROOT/runs/flagged-review.done"
  "$JOB_STATUS" flagged-review > "$ROOT/job-status-notext.out" 2>&1 || {
    cat "$ROOT/job-status-notext.out" >&2
    fail 'job-status failed on a text-less review'
  }
  grep -qF 'REVIEW-NO-REPORT' "$ROOT/job-status-notext.out" \
    || { cat "$ROOT/job-status-notext.out" >&2; fail 'job-status hid the REVIEW-NO-REPORT verdict'; }
  # The real double shape: pi-impl settled with `EXIT=0`, then the budget stop
  # appended its own verdict line.
  printf 'EXIT=0 OUTCOME=SUCCESS\nREVIEW-BUDGET\n' > "$CONTROLLER_ROOT/runs/flagged-review.done"
  "$JOB_STATUS" flagged-review > "$ROOT/job-status-budget.out" 2>&1 || {
    cat "$ROOT/job-status-budget.out" >&2
    fail 'job-status failed on a budget-stopped review'
  }
  grep -qF 'flagged-review: FINISHED REVIEW-BUDGET EXIT=0 OUTCOME=SUCCESS' "$ROOT/job-status-budget.out" \
    || { cat "$ROOT/job-status-budget.out" >&2; fail 'job-status did not report the budget-stopped review'; }
  # The not-settled verdict (pi-review.sh exit 6: a zero pi exit in a class that
  # is not SUCCESS/UNCOMMITTED) is read by the same rule. It must be reported,
  # including when it is written INTO line 1 by the launcher's no-marker fallback
  # (a whole-line equality test on lines 2+ would miss that shape).
  printf 'EXIT=5 OUTCOME=REVIEW-BUDGET\nREVIEW-NOT-SETTLED\n' > "$CONTROLLER_ROOT/runs/flagged-review.done"
  "$JOB_STATUS" flagged-review > "$ROOT/job-status-notsettled.out" 2>&1 || {
    cat "$ROOT/job-status-notsettled.out" >&2
    fail 'job-status failed on a not-settled review'
  }
  grep -qF 'REVIEW-NOT-SETTLED' "$ROOT/job-status-notsettled.out" \
    || { cat "$ROOT/job-status-notsettled.out" >&2; fail 'job-status hid the REVIEW-NOT-SETTLED verdict'; }
  printf 'EXIT=5 OUTCOME=REVIEW-BUDGET\n' > "$CONTROLLER_ROOT/runs/flagged-review.done"
  "$JOB_STATUS" flagged-review > "$ROOT/job-status-line1-verdict.out" 2>&1 || {
    cat "$ROOT/job-status-line1-verdict.out" >&2
    fail 'job-status failed on a marker whose line 1 carries the verdict'
  }
  grep -qF 'REVIEW-BUDGET' "$ROOT/job-status-line1-verdict.out" \
    || { cat "$ROOT/job-status-line1-verdict.out" >&2; fail 'job-status ignored a verdict written as line 1'; }
  rm -f -- "$CONTROLLER_ROOT/runs/flagged-review.done"
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
          commit_epoch=None, measured=None, baseline=None, reason=None):
    body = {
        "run": name, "wrapper_pid": 1, "updated_epoch": now,
        "last_event_epoch": last_event, "tool_in_flight": tool,
        "tool_in_flight_started_epoch": tool_started,
        "assistant_turns": 1, "tool_calls": 1,
        "latest_total_tokens": tokens,
        "latest_total_tokens_last_report": tokens if tokens_report is None else tokens_report,
        "tool_in_flight_command": command, "steering_children": steer,
        "progress_epoch": progress,
        "progress_baseline_epoch": baseline,
        "progress_reason": reason,
        # The SIDE CAR's own licence, published by pi-live.py. A fixture that
        # measured a progress fact says so; one that did not must never be read
        # as having measured anything.
        "progress_measured": measured,
        "commits_ahead": ahead, "uncommitted_files": 0,
        "last_commit_epoch": now if commit_epoch is None else commit_epoch,
    }
    (runs / f"{name}.live").write_text(json.dumps(body), encoding="utf-8")
write("stale", last_event=now - 900)
write("hung", tool="bash", tool_started=now - 2400, last_event=now - 10,
      progress=now - 2400, measured=True, command="sleep 600")
write("long-tool", tool="bash", tool_started=now - 2400, last_event=now - 10,
      progress=now - 30, measured=True, reason="counters",
      command="grep -r needle /workspace",
      steer=[{"pid": 4242, "command": "grep -r needle /workspace"}])
write("progress-unknown", tool="bash", tool_started=now - 2400,
      last_event=now - 10, progress=None, measured=False, reason="unread")
# What the sidecar publishes after a tick whose tool subtree it could NOT
# locate: the counters were never re-read, so the previous epoch is carried
# forward UNMEASURED. The tool is past its threshold and the carried epoch is
# older than the tool, so age alone would say HUNG-TOOL - exactly the decision
# this field exists to prevent.
write("progress-carried-unmeasured", tool="bash", tool_started=now - 2400,
      last_event=now - 10, progress=now - 3000, measured=False, reason="unread")
# The same row without the flag at all: a payload written before the producer
# published it. Absence is not a licence either.
write("progress-flag-absent", tool="bash", tool_started=now - 2400,
      last_event=now - 10, progress=now - 2400, reason="counters")
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
# L3' (r2): a verdict written INTO line 1 -- the launcher's no-marker fallback
# shape -- must be named by job-status's own rule (the class word in front of
# the row), not only by line 1 being printed verbatim. The second line is an
# ordinary outcome line that carries no verdict, so the row must read
# `FINISHED REVIEW-BUDGET EXIT=5 OUTCOME=REVIEW-BUDGET`.
(runs / "line1-verdict.done").write_text(
    "EXIT=5 OUTCOME=REVIEW-BUDGET\nRETRIES=0\n", encoding="utf-8")
# The same shape with the verdict ONLY on line 2: the rule scans the whole
# marker, so both are found.
(runs / "line2-verdict.done").write_text(
    "EXIT=0 OUTCOME=SUCCESS\nREVIEW-NO-REPORT\n", encoding="utf-8")
# The REAL fallback shape pi-review.sh writes for a budget stop that left no
# marker of its own: the verdict in line 1 and the stop form in line 2. The
# stop form is a qualifier, not a fifth class, so the row names REVIEW-BUDGET
# and nothing else -- and it must never turn the row into a success.
(runs / "line1-stop-verdict.done").write_text(
    "EXIT=5 OUTCOME=REVIEW-BUDGET\nREVIEW-BUDGET-STOP=pid\n", encoding="utf-8")
# A marker with no verdict at all stays unmarked: the rule must not prefix
# every finished row.
(runs / "no-verdict.done").write_text(
    "EXIT=0 OUTCOME=SUCCESS\nRETRIES=1\n", encoding="utf-8")
PY
  CONTROLLER_HOME="$ROOT/status-home" CONTROLLER_RUNS_DIR="$runs" CONTROLLER_REPO="$WORKTREE" \
    JOB_STATUS_STALL_SECONDS=600 JOB_STATUS_HUNG_TOOL_SECONDS=1800 \
    "$JOB_STATUS" stale hung long-tool progress-unknown progress-carried-unmeasured \
      progress-flag-absent tokens-zero-report \
      no-commits own-commit ahead-unknown orphan finished absent \
      line1-verdict line2-verdict no-verdict line1-stop-verdict \
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
  # must not license the stronger word. The `progress-unknown` row here is the
  # reader's own contract; the producer's side of it (that the sidecar really
  # emits null for an in-flight tool with unreadable counters) is asserted in
  # the live-progress check, which reads a payload the sidecar wrote.
  grep -qF 'progress-unknown: LONG-TOOL' "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'an unreadable progress fact was read as a hung tool'; }
  grep -qE '^progress-unknown: .*progress=- ' "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'an unreadable progress fact is not shown as unknown'; }
  # An epoch the sidecar CARRIED forward because it could not re-read the tool's
  # subtree is not a measurement: the tool is past its threshold and the carried
  # epoch is older still, so reading it as a progress age would say HUNG-TOOL on
  # the tool's age alone - the exact defect this flag removes.
  grep -qF 'progress-carried-unmeasured: LONG-TOOL' "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'a carried, unmeasured progress epoch licensed HUNG-TOOL'; }
  grep -qE '^progress-carried-unmeasured: .*progress=- progress-why=unread' \
    "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'a carried, unmeasured epoch was printed as a progress age'; }
  # A payload written before the producer published the flag cannot license the
  # stronger word either: absence of a measurement is not a measurement.
  grep -qF 'progress-flag-absent: LONG-TOOL' "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'a payload without the measured flag licensed HUNG-TOOL'; }
  grep -qE '^progress-flag-absent: .*progress=- ' "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'a payload without the measured flag printed a progress age'; }
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
  # The age is derived from the reader's own clock, so the assertion binds the
  # QUALIFIER and the magnitude, never a whole-second-exact figure (a suite
  # that runs a second after the fixture is written must not go red).
  grep -qE '^own-commit: .*since-commit=[0-9]+s\(ahead=2\)' "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'a run with its own commit does not qualify the age with ahead='; }
  grep -qF 'since-commit=null' "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'an unreadable commits_ahead did not suppress the commit age'; }
  grep -qF 'finished: FINISHED EXIT=0 OUTCOME=SUCCESS' "$ROOT/job-status-states.out" || fail 'a finished run is not reported as finished'
  # L3' (r2): one rule for the marker -- the verdict is any line that contains
  # it. A marker whose line 1 carries it gets the class word in front of the row
  # even though line 1 is already printed, and a verdict on line 2 is found the
  # same way; a clean marker gets no prefix at all.
  grep -qE '^line1-verdict: FINISHED REVIEW-BUDGET EXIT=5 OUTCOME=REVIEW-BUDGET$' \
    "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'a verdict written as line 1 was not named by the whole-marker rule'; }
  grep -qF 'line2-verdict: FINISHED REVIEW-NO-REPORT EXIT=0 OUTCOME=SUCCESS' \
    "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'a verdict on line 2 was not found by the whole-marker rule'; }
  # The stop form is a qualifier of the budget verdict: it may not be printed as
  # a class, and it may not hide the class the row already carries.
  grep -qE '^line1-stop-verdict: FINISHED REVIEW-BUDGET EXIT=5 OUTCOME=REVIEW-BUDGET$' \
    "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'the budget stop-form line changed the class or the row shape'; }
  ! grep -qF 'REVIEW-BUDGET-STOP=' "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'the stop-form line was printed as a class'; }
  grep -qE '^no-verdict: FINISHED EXIT=0 OUTCOME=SUCCESS' "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'a marker with no verdict was prefixed with a class'; }
  grep -qF 'absent: NO-LIVE (no absent.live sidecar)' "$ROOT/job-status-states.out" || fail 'a run without a snapshot is not NO-LIVE'
  grep -qF 'orphan: WRAPPER-DEAD' "$ROOT/job-status-states.out" || fail 'a run whose wrapper pid is gone is not WRAPPER-DEAD'
  # Liveness comes from the OWNER RECORD (the same one job-check reads), and the
  # payload pid is only a fallback - the row says which. A recycled pid is a
  # stranger, so it must not read ACTIVE.
  grep -qF 'owner-record=none' "$ROOT/job-status-states.out" \
    || { cat "$ROOT/job-status-states.out" >&2; fail 'a run without an owner record does not say which fact its liveness rests on'; }
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
def write(name, tool=None, tool_started=None, last_event=None, progress=None,
          measured=None, baseline=None):
    body = {
        "run": name, "wrapper_pid": 1, "updated_epoch": now,
        "last_event_epoch": last_event, "tool_in_flight": tool,
        "tool_in_flight_started_epoch": tool_started,
        "progress_epoch": progress,
        "progress_baseline_epoch": baseline,
        "progress_measured": measured,
        "assistant_turns": 1, "tool_calls": 1, "latest_total_tokens": 10,
        "commits_ahead": 0, "uncommitted_files": 0, "last_commit_epoch": now,
    }
    (runs / f"{name}.live").write_text(json.dumps(body), encoding="utf-8")
write("quiet500", last_event=now - 500)
write("quiet700", last_event=now - 700)
write("thinking", last_event=now - 100)
write("tool1700", tool="bash", tool_started=now - 1700, last_event=now - 20,
      progress=now - 20, measured=True)
write("tool1900fresh", tool="bash", tool_started=now - 1900, last_event=now - 20,
      progress=now - 20, measured=True)
write("tool1900stuck", tool="bash", tool_started=now - 1900, last_event=now - 20,
      progress=now - 1900, measured=True, baseline=now - 1900)
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

# --- progress evidence (CTL-18) -------------------------------------------
#
# These checks judge the RULE, not the host. pi-live.py is driven with
# `--tick-stream` (one clock epoch per line, one tick per line) against a fake
# `/proc` root the check owns (tests/progress-fixture.py), and the check changes
# that root between ticks. Nothing below waits for a wall clock, sleeps for a
# child, or counts how fast the host is: every assertion reads a fact this check
# constructed, through the sidecar's real process-tree code.

progress_start() {
  # Start ONE sidecar for a whole check, driven by a FIFO of clock epochs.
  # $1 = fake proc root, $2 = wrapper pid, rest = extra argv.
  #
  # One process per check matters: the sidecar reads the stream INCREMENTALLY,
  # so a fresh process would re-read the tool call's start every time and
  # classify it as a boundary (which it is). A check that needs N ticks needs
  # one reader and N clock lines.
  local root=$1 wrapper=$2
  shift 2
  PROGRESS_FIFO=$ROOT/progress-ticks.fifo
  rm -f "$PROGRESS_FIFO" "$ROOT/progress-ticks.log"
  mkfifo "$PROGRESS_FIFO"
  mkdir -p "$CONTROLLER_ROOT/runs"
  PI_LIVE_PROC_ROOT="$root" \
    python3 "$PILIVE" --stream "${PROGRESS_STREAM:-$SCRIPT_DIR/fixtures/pi-tool-in-flight.jsonl}" \
      --worktree "$WORKTREE" --out "$CONTROLLER_ROOT/runs/progress.live" --run progress \
      --wrapper-pid "$wrapper" --interval 1 --tick-stream "$PROGRESS_FIFO" \
      --tick-log "$ROOT/progress-ticks.log" "$@" > "$ROOT/progress.err" 2>&1 &
  PROGRESS_PID=$!
  # Opening the write end blocks until the reader opens its end; the reader is
  # the sidecar above, so this is the sync point for "the sidecar is reading".
  exec {PROGRESS_FD}>"$PROGRESS_FIFO"
  PROGRESS_TICKS=0
}

progress_tick() {
  # $1 = this tick's clock epoch. Returns only after the tick FINISHED, so the
  # caller may change the fake tree the next tick reads.
  local epoch=$1 wanted
  printf '%s\n' "$epoch" >&"$PROGRESS_FD"
  wanted=$(( PROGRESS_TICKS + 1 ))
  for _ in {1..2000}; do
    PROGRESS_TICKS=$(progress_tick_count)
    (( PROGRESS_TICKS >= wanted )) && return 0
    # A sidecar that died cannot deliver the tick: say so instead of spinning.
    kill -0 "$PROGRESS_PID" 2>/dev/null || break
    sleep 0.01
  done
  cat "$ROOT/progress.err" >&2 || true
  fail "the sidecar did not complete tick $epoch"
}

progress_finish() {
  # Close the FIFO: the sidecar finishes the ticks it was given and exits.
  # `eval` because the close names the DESCRIPTOR NUMBER held in the variable
  # (the `{name}` form opens a descriptor; it cannot close one, and `exec` with
  # an invalid redirection kills the shell).
  eval "exec ${PROGRESS_FD:-1}>&-" 2>/dev/null || true
  if ! wait "$PROGRESS_PID"; then
    cat "$ROOT/progress.err" >&2 || true
    fail 'the sidecar failed on the ticks it was given'
  fi
  PROGRESS_PID=''
  PROGRESS_FD=''
}

progress_tick_count() {
  if [[ -s $ROOT/progress-ticks.log ]]; then wc -l < "$ROOT/progress-ticks.log"; else echo 0; fi
}

progress_reset_ticks() {
  rm -f "$ROOT/progress-ticks.log" "$CONTROLLER_ROOT/runs/progress.live"
}

check_live_progress() {
  local fixture=$SCRIPT_DIR/progress-fixture.py root=$ROOT/progress-proc wrapper=100
  python3 "$fixture" tree "$root" "$wrapper" || fail 'could not build the fake process tree'

  # (1) QUIESCENCE. One idle tool call (a `sleep 600` with nothing to do) under a
  # wrapper and an agent that are both alive: exactly the state the reviewer
  # measured, where `progress_epoch` stayed 0-1 s old while a tool was blocked.
  # Ten ticks later nothing has been observed, so the fact is still null and the
  # tool's AGE alone must produce HUNG-TOOL at the threshold.
  progress_reset_ticks
  progress_start "$root" "$wrapper"
  progress_tick 1000
  progress_tick 1010
  progress_tick 1020
  progress_tick 1030
  progress_tick 1040
  progress_tick 1050
  progress_tick 1060
  progress_tick 1070
  progress_tick 1080
  progress_tick 1090
  progress_finish
  [[ $(progress_tick_count) == 10 ]] \
    || fail "a quiescent run wrote $(progress_tick_count) tick rows, expected 10"
  python3 - "$ROOT/progress-ticks.log" <<'PY' || fail 'quiescence was read as progress'
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
assert len(rows) == 10, rows
# The tool call's own processes were measured on every tick and none of them
# moved: the counters were read (a baseline exists) and nothing is evidence.
assert all(row["progress"] is None for row in rows), rows
assert all(row["baseline"] is not None for row in rows), rows
assert rows[-1]["nodes"] == 2, rows
assert rows[-1]["truncated"] is False, rows
PY
  python3 - "$CONTROLLER_ROOT/runs/progress.live" <<'PY' || fail 'a quiescent run published a progress epoch'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["progress_epoch"] is None, body
assert body["progress_reason"] in ("none", "idle"), body
assert body["progress_measured"] is True, body
assert body["progress_tree_nodes"] == 2, body
assert body["progress_tree_truncated"] is False, body
PY
  # The row a controller reads: the payload the sidecar really wrote for the
  # quiescent tool, with only the counter epochs rebased onto the reader's clock
  # (the reader derives every age from its own clock; the probe ticks ran on a
  # synthetic one).
  mkdir -p "$ROOT/status-home"
  python3 - "$CONTROLLER_ROOT/runs/progress.live" <<'PY'
import json, sys, time
from pathlib import Path
now = int(time.time())
body = json.load(open(sys.argv[1], encoding="utf-8"))
body["wrapper_pid"] = 1
body["updated_epoch"] = now
body["last_event_epoch"] = now - 10
body["tool_in_flight_started_epoch"] = now - 2400
body["progress_epoch"] = None
body["progress_baseline_epoch"] = now - 2400
body["progress_reason"] = "none"
body["tool_in_flight_command"] = "sleep 600"
body["steering_children"] = [{"pid": 102, "command": "bash -lc sleep 600"}]
Path(sys.argv[1]).with_name("progress-hung.live").write_text(json.dumps(body), encoding="utf-8")
PY
  CONTROLLER_HOME="$ROOT/status-home" CONTROLLER_RUNS_DIR="$CONTROLLER_ROOT/runs" \
    CONTROLLER_REPO="$WORKTREE" JOB_STATUS_STALL_SECONDS=600 JOB_STATUS_HUNG_TOOL_SECONDS=1800 \
    "$JOB_STATUS" progress-hung > "$ROOT/progress-hung.out" 2>&1 \
    || { cat "$ROOT/progress-hung.out" >&2; fail 'job-status failed on a quiescent tool call'; }
  grep -qF 'progress-hung: HUNG-TOOL' "$ROOT/progress-hung.out" \
    || { cat "$ROOT/progress-hung.out" >&2; fail 'a tool with no progress evidence was not HUNG-TOOL'; }
  grep -qF 'cmd=sleep 600' "$ROOT/progress-hung.out" \
    || { cat "$ROOT/progress-hung.out" >&2; fail 'the row does not name the hung tool command'; }
  grep -qE 'progress=[0-9]+s progress-why=none' "$ROOT/progress-hung.out" \
    || { cat "$ROOT/progress-hung.out" >&2; fail 'the row does not show the measured absence of progress'; }

  # (2) A WORKING TOOL advances the fact on exactly the tick after its counters
  # grew. The tool's own child burns CPU and no other process moves.
  python3 "$fixture" tree "$root" "$wrapper" || fail 'could not build the fake process tree'
  progress_reset_ticks
  progress_start "$root" "$wrapper"
  python3 "$fixture" set "$root" 103 cpu=7 || fail 'could not grow the tool counter'
  progress_tick 2000
  progress_tick 2010
  python3 "$fixture" set "$root" 103 cpu=19 || fail 'could not grow the tool counter'
  progress_tick 2020
  progress_tick 2030
  progress_finish
  python3 - "$ROOT/progress-ticks.log" <<'PY' || fail 'a working tool produced no progress evidence'
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
assert len(rows) == 4, rows
# Tick 1 is a baseline (nothing to compare it with), tick 2 the same counters,
# tick 3 strictly greater on the SAME set, tick 4 unchanged again.
assert rows[0]["progress"] is None, rows
assert rows[1]["progress"] is None, rows
assert rows[2]["progress"] == 2020, rows
assert rows[2]["reason"] == "counters", rows
assert rows[3]["progress"] == 2020, rows
assert rows[3]["reason"] == "none", rows
PY

  # (3) THE MEMBERS THAT GROW ON THEIR OWN. In a real run the wrapper writes its
  # records and runs git, and the agent runs a model loop; both grow every tick
  # and neither is the tool. Their fixtures grow here and the tool's does not,
  # so the fact must not move - the case a "sum the wrapper's whole subtree"
  # implementation cannot survive.
  python3 "$fixture" tree "$root" "$wrapper" || fail 'could not build the fake process tree'
  progress_reset_ticks
  progress_start "$root" "$wrapper"
  python3 "$fixture" set "$root" "$wrapper" cpu=40 io=1000 || fail 'could not grow the wrapper counter'
  python3 "$fixture" set "$root" "$((wrapper + 1))" cpu=40 io=5000 || fail 'could not grow the agent counter'
  progress_tick 3000
  python3 "$fixture" set "$root" "$wrapper" cpu=90 io=4000 || fail 'could not grow the wrapper counter'
  python3 "$fixture" set "$root" "$((wrapper + 1))" cpu=90 io=9000 || fail 'could not grow the agent counter'
  progress_tick 3010
  progress_tick 3020
  progress_finish
  python3 - "$ROOT/progress-ticks.log" <<'PY' || fail 'the wrapper or the agent was counted as the tool making progress'
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
assert len(rows) == 3, rows
assert all(row["progress"] is None for row in rows), rows
assert rows[-1]["nodes"] == 2, rows
PY

  # (3b) THE SIDECAR'S OWN COUNTERS. The sidecar is a direct child of the
  # wrapper and its `/proc` scans and git calls grow every tick. It must be
  # excluded by IDENTITY (the one pid known exactly), not by name, and it must
  # not be published as a steering target.
  python3 "$fixture" tree "$root" "$wrapper" || fail 'could not build the fake process tree'
  progress_reset_ticks
  progress_start "$root" "$wrapper" --sidecar-pid 999
  python3 "$fixture" add "$root" 999 "$wrapper" 'python3 pi-live.py' \
    || fail 'could not add the sidecar process'
  progress_tick 4000
  python3 "$fixture" set "$root" 999 cpu=10 io=10 || fail 'could not grow the sidecar counter'
  progress_tick 4010
  python3 "$fixture" set "$root" 999 cpu=80 io=8000 || fail 'could not grow the sidecar counter'
  progress_tick 4020
  progress_finish
  python3 - "$ROOT/progress-ticks.log" <<'PY' || fail "the sidecar's own counters were counted as the tool's progress"
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
assert len(rows) == 3, rows
assert all(row["progress"] is None for row in rows), rows
# The sidecar is a direct child of the wrapper, so it is not in the summed set.
assert rows[-1]["nodes"] == 2, rows
PY
  python3 - "$CONTROLLER_ROOT/runs/progress.live" <<'PY' || fail 'the sidecar was published as a steering target'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
pids = sorted(row["pid"] for row in body["steering_children"])
assert 999 not in pids, body["steering_children"]
assert body["wrapper_pid"] not in pids, body["steering_children"]
assert pids == [102, 103], body["steering_children"]
PY

  # (4) A DECREASE AND A SET CHANGE ARE BASELINES. The counter goes down, then
  # the tool's child is replaced by another process: neither is progress, and
  # neither is silently ignored either (the baseline moves).
  python3 "$fixture" tree "$root" "$wrapper" || fail 'could not build the fake process tree'
  progress_reset_ticks
  progress_start "$root" "$wrapper"
  python3 "$fixture" set "$root" 103 cpu=30 io=3000 || fail 'could not grow the tool counter'
  progress_tick 5000
  progress_tick 5010
  python3 "$fixture" set "$root" 103 cpu=5 io=10 || fail 'could not shrink the tool counter'
  progress_tick 5020
  python3 "$fixture" rm "$root" 103 || fail 'could not remove the tool child'
  python3 "$fixture" add "$root" 104 102 'bash -lc sleep 900' \
    || fail 'could not start a second tool process'
  progress_tick 5030
  progress_finish
  python3 - "$ROOT/progress-ticks.log" <<'PY' || fail 'a counter decrease or a process-set change was read as progress'
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
assert len(rows) == 4, rows
assert all(row["progress"] is None for row in rows), rows
assert [row["nodes"] for row in rows] == [2, 2, 2, 2], rows
PY

  # (4b) CHURN. A tool that forks and reaps workers every tick (`make -j`, a
  # per-test subprocess) changes its process set constantly. The rule is about
  # SURVIVORS, so a set change is neither progress nor a reset of the quiet
  # clock: with idle survivors a silent churning tool still reads quiet, a
  # survivor that burns CPU is progress, and a pid RECYCLED between two ticks
  # (same number, different kernel start ticks) is not a survivor at all.

  # (4b-i) Churn with idle survivors is QUIET, and the baseline never moves:
  # the quiet clock started at the first reading of THIS tool call.
  python3 "$fixture" tree "$root" "$wrapper" || fail 'could not build the fake process tree'
  progress_reset_ticks
  progress_start "$root" "$wrapper"
  progress_tick 4100
  python3 "$fixture" add "$root" 200 102 'make worker' || fail 'could not start a churn worker'
  progress_tick 4110
  python3 "$fixture" rm "$root" 200 || fail 'could not reap a churn worker'
  python3 "$fixture" add "$root" 201 102 'make worker' || fail 'could not start a churn worker'
  progress_tick 4120
  python3 "$fixture" rm "$root" 201 || fail 'could not reap a churn worker'
  python3 "$fixture" add "$root" 202 102 'make worker' || fail 'could not start a churn worker'
  progress_tick 4130
  progress_finish
  python3 - "$ROOT/progress-ticks.log" <<'PY' || fail 'a silent churning tool was read as progressing, or its baseline was reset by the churn'
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
assert len(rows) == 4, rows
# No survivor grew, so nothing is progress - even though the set changed on
# every tick after the first.
assert all(row["progress"] is None for row in rows), rows
assert all(row["measured"] is True for row in rows), rows
# The quiet clock is NOT reset by the churn: the baseline is the tick the
# current tool call's counters were first read, on every row.
assert [row["baseline"] for row in rows] == [4100, 4100, 4100, 4100], rows
PY
  # The row that decides the word: a churning tool whose survivors never moved
  # for 30 minutes is HUNG-TOOL, not LONG-TOOL. Without this, a `make -j` that
  # went silent could sit at LONG-TOOL forever and the controller would never
  # see it - the defect this clause removes.
  mkdir -p "$ROOT/status-home"
  python3 - "$CONTROLLER_ROOT/runs/progress.live" <<'PY'
import json, sys, time
from pathlib import Path
now = int(time.time())
body = json.load(open(sys.argv[1], encoding="utf-8"))
body["wrapper_pid"] = 1
body["updated_epoch"] = now
body["last_event_epoch"] = now - 10
body["tool_in_flight_started_epoch"] = now - 2400
body["progress_baseline_epoch"] = now - 2400
Path(sys.argv[1]).with_name("progress-churn.live").write_text(json.dumps(body), encoding="utf-8")
PY
  CONTROLLER_HOME="$ROOT/status-home" CONTROLLER_RUNS_DIR="$CONTROLLER_ROOT/runs" \
    CONTROLLER_REPO="$WORKTREE" JOB_STATUS_STALL_SECONDS=600 JOB_STATUS_HUNG_TOOL_SECONDS=1800 \
    "$JOB_STATUS" progress-churn > "$ROOT/progress-churn.out" 2>&1 \
    || { cat "$ROOT/progress-churn.out" >&2; fail 'job-status failed on a churning tool'; }
  grep -qF 'progress-churn: HUNG-TOOL' "$ROOT/progress-churn.out" \
    || { cat "$ROOT/progress-churn.out" >&2; fail 'a churning tool with idle survivors was not HUNG-TOOL'; }

  # (4b-ii) Churn WITH a survivor that burns CPU is progress: the survivor's own
  # delta counts even while the set around it changes.
  python3 "$fixture" tree "$root" "$wrapper" || fail 'could not build the fake process tree'
  progress_reset_ticks
  progress_start "$root" "$wrapper"
  progress_tick 4200
  progress_tick 4210
  python3 "$fixture" set "$root" 103 cpu=25 || fail 'could not grow the tool counter'
  python3 "$fixture" add "$root" 210 102 'make worker' || fail 'could not start a churn worker'
  progress_tick 4220
  progress_finish
  python3 - "$ROOT/progress-ticks.log" <<'PY' || fail 'a surviving process that burned CPU was not progress'
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
assert len(rows) == 3, rows
assert rows[0]["progress"] is None, rows
assert rows[1]["progress"] is None, rows
assert rows[2]["progress"] == 4220, rows
assert rows[2]["reason"] == "counters", rows
PY

  # (4b-iii) A PID RECYCLE IS NOT A SURVIVOR: pid 103 stays, but its kernel
  # start ticks change, so it is a different process and its larger counter
  # says nothing about the one measured before.
  python3 "$fixture" tree "$root" "$wrapper" || fail 'could not build the fake process tree'
  progress_reset_ticks
  progress_start "$root" "$wrapper"
  progress_tick 4300
  python3 "$fixture" set "$root" 103 cpu=90 start=9999 \
    || fail 'could not recycle the tool pid'
  progress_tick 4310
  progress_finish
  python3 - "$ROOT/progress-ticks.log" <<'PY' || fail 'a recycled pid was counted as a survivor'
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
assert len(rows) == 2, rows
assert all(row["progress"] is None for row in rows), rows
PY

  # (5) AN UNREADABLE `/proc` LEAVES THE FACT UNKNOWN. An empty process table
  # cannot locate the agent's subtree, so no set is summed: no baseline, no
  # progress, and a reason that says which. The tool's start epoch is still a
  # fact - it is what the reader may show, and never what it may call progress.
  rm -rf "$ROOT/progress-empty"
  mkdir -p "$ROOT/progress-empty"
  progress_reset_ticks
  progress_start "$ROOT/progress-empty" "$wrapper"
  progress_tick 6000
  progress_tick 6010
  progress_finish
  python3 - "$ROOT/progress-ticks.log" <<'PY' || fail 'an unreadable /proc was read as progress'
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
assert len(rows) == 2, rows
assert all(row["progress"] is None for row in rows), rows
assert all(row["nodes"] is None for row in rows), rows
assert all(row["baseline"] is None for row in rows), rows
assert rows[-1]["reason"] == "unread", rows
PY
  python3 - "$CONTROLLER_ROOT/runs/progress.live" <<'PY' || fail 'an unreadable /proc invented a progress fact'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["progress_epoch"] is None, body
assert body["progress_tree_nodes"] is None, body
assert body["progress_reason"] == "unread", body
assert body["tool_in_flight_started_epoch"] is not None, body
PY
  # The row built from THAT payload - what the sidecar really writes for an
  # in-flight tool with unreadable counters, not a hand-written .live - must
  # license the weaker word only: LONG-TOOL, with `progress=-`. Age is not
  # progress when nothing could be measured.
  mkdir -p "$ROOT/status-home"
  python3 - "$CONTROLLER_ROOT/runs/progress.live" <<'PY'
import json, sys, time
from pathlib import Path
now = int(time.time())
body = json.load(open(sys.argv[1], encoding="utf-8"))
body["wrapper_pid"] = 1
body["updated_epoch"] = now
body["last_event_epoch"] = now - 10
body["tool_in_flight_started_epoch"] = now - 2400
Path(sys.argv[1]).with_name("progress-unreadable.live").write_text(
    json.dumps(body), encoding="utf-8"
)
PY
  CONTROLLER_HOME="$ROOT/status-home" CONTROLLER_RUNS_DIR="$CONTROLLER_ROOT/runs" \
    CONTROLLER_REPO="$WORKTREE" JOB_STATUS_STALL_SECONDS=600 JOB_STATUS_HUNG_TOOL_SECONDS=1800 \
    "$JOB_STATUS" progress-unreadable > "$ROOT/progress-unreadable.out" 2>&1 \
    || { cat "$ROOT/progress-unreadable.out" >&2; fail 'job-status failed on an unreadable progress fact'; }
  grep -qF 'progress-unreadable: LONG-TOOL' "$ROOT/progress-unreadable.out" \
    || { cat "$ROOT/progress-unreadable.out" >&2; fail 'an unreadable progress fact licensed HUNG-TOOL'; }
  grep -qE 'progress=- progress-why=unread' "$ROOT/progress-unreadable.out" \
    || { cat "$ROOT/progress-unreadable.out" >&2; fail 'the row did not show the progress fact as unknown'; }

  # (5b) THE STREAM GREW BEFORE THE TOOL STARTED, AND THE TOOL'S SUBTREE CANNOT
  # BE LOCATED. This is the shape the earlier `progress-unknown` check could not
  # see: its fixture's stream bytes WERE the tool's start, so the progress epoch
  # and the tool's start were the same instant and a lifted epoch was
  # indistinguishable. Here the run's own session bytes are progress evidence at
  # tick 1, the tool starts at tick 3, and no `/proc` entry exists for the
  # agent: the carried epoch (tick 1) is OLDER than the tool, so lifting it to
  # the tool's start would make the tool's own age the progress age and
  # HUNG-TOOL would be decided on wall-clock alone - the CTL-18 defect.
  rm -rf "$ROOT/progress-grew" "$ROOT/progress-grew.jsonl"
  mkdir -p "$ROOT/progress-grew"
  printf '{"type":"session","id":"grew-before-tool"}\n' > "$ROOT/progress-grew.jsonl"
  progress_reset_ticks
  PROGRESS_STREAM=$ROOT/progress-grew.jsonl progress_start "$ROOT/progress-grew" "$wrapper"
  progress_tick 6000
  progress_tick 6010
  printf '{"type":"tool_execution_start","toolCallId":"g1","toolName":"bash","args":{"command":"sleep 600"}}\n' \
    >> "$ROOT/progress-grew.jsonl"
  progress_tick 6020
  progress_tick 6030
  progress_finish
  python3 - "$ROOT/progress-ticks.log" <<'PY' || fail 'a carried epoch older than the in-flight tool was not marked unmeasured'
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
assert len(rows) == 4, rows
# Tick 1: the stream grew, which IS a measurement of the run.
assert rows[0]["progress"] == 6000, rows
assert rows[0]["measured"] is True, rows
# Ticks 2+: the stream's only growth is the tool's own ANNOUNCEMENT (a
# boundary), and the tool's subtree cannot be read at all, so every one of
# them carries the old epoch as a leftover and says so.
assert [row["reason"] for row in rows[1:]] == ["unread", "tool-start", "unread"], rows
assert all(row["measured"] is False for row in rows[1:]), rows
PY
  python3 - "$CONTROLLER_ROOT/runs/progress.live" <<'PY' || fail 'an epoch from before the tool started was published as the tool progress'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["tool_in_flight"] == "bash", body
assert body["tool_in_flight_started_epoch"] is not None, body
assert body["progress_epoch"] is None, body
assert body["progress_measured"] is False, body
assert body["progress_reason"] == "unread", body
PY
  # The row a controller reads, from THAT payload: the tool is past the hung
  # threshold and nothing about it was ever measured, so the weaker word.
  mkdir -p "$ROOT/status-home"
  python3 - "$CONTROLLER_ROOT/runs/progress.live" <<'PY'
import json, sys, time
from pathlib import Path
now = int(time.time())
body = json.load(open(sys.argv[1], encoding="utf-8"))
body["wrapper_pid"] = 1
body["updated_epoch"] = now
body["last_event_epoch"] = now - 10
body["tool_in_flight_started_epoch"] = now - 2400
Path(sys.argv[1]).with_name("progress-grew.live").write_text(json.dumps(body), encoding="utf-8")
PY
  CONTROLLER_HOME="$ROOT/status-home" CONTROLLER_RUNS_DIR="$CONTROLLER_ROOT/runs" \
    CONTROLLER_REPO="$WORKTREE" JOB_STATUS_STALL_SECONDS=600 JOB_STATUS_HUNG_TOOL_SECONDS=1800 \
    "$JOB_STATUS" progress-grew > "$ROOT/progress-grew.out" 2>&1 \
    || { cat "$ROOT/progress-grew.out" >&2; fail 'job-status failed on a carried, unmeasured epoch'; }
  grep -qF 'progress-grew: LONG-TOOL' "$ROOT/progress-grew.out" \
    || { cat "$ROOT/progress-grew.out" >&2; fail 'an epoch older than the tool licensed HUNG-TOOL'; }
  grep -qE 'progress=- progress-why=unread' "$ROOT/progress-grew.out" \
    || { cat "$ROOT/progress-grew.out" >&2; fail 'the carried epoch was printed as a progress age'; }

  # (6) A TOOL CALL'S OWN START IS A BOUNDARY, NOT EVIDENCE. The stream grows
  # because the call was announced; publishing that as the tool's progress puts
  # the fact at the tool's own start on every fresh call, which is the
  # wall-clock-only rule in its other form (and it hid the defect: the reviewer
  # measured `progress_epoch` 0-1 s old on a blocked tool).
  rm -f "$ROOT/progress-start.jsonl"
  printf '{"type":"session","id":"start-boundary"}\n' > "$ROOT/progress-start.jsonl"
  python3 "$fixture" tree "$root" "$wrapper" || fail 'could not build the fake process tree'
  progress_reset_ticks
  PROGRESS_STREAM=$ROOT/progress-start.jsonl progress_start "$root" "$wrapper"
  progress_tick 7000
  progress_tick 7010
  printf '{"type":"tool_execution_start","toolCallId":"c1","toolName":"bash","args":{"command":"sleep 600"}}\n' \
    >> "$ROOT/progress-start.jsonl"
  progress_tick 7020
  progress_tick 7030
  progress_finish
  python3 - "$ROOT/progress-ticks.log" <<'PY' || fail 'a tool call start was published as progress'
import json, sys
rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
assert len(rows) == 4, rows
# The session line is real stream growth before any tool call exists.
assert rows[0]["progress"] == 7000, rows
# The announcement of the call is a boundary, and the tick after it must not
# invent an epoch out of the newly-readable counters either.
assert rows[1]["progress"] == 7000, rows
assert rows[2]["reason"] == "tool-start", rows
assert rows[2]["progress"] == 7000, rows
assert rows[3]["progress"] == 7000, rows
assert rows[3]["baseline"] == 7020, rows
PY

  # (7) THE CAP IS DOCUMENTED, NOT SILENT. A tree deeper than the cap is summed
  # as a prefix, and the snapshot says how many processes it summed and that the
  # walk stopped early, so a reader can see the bound instead of reading an
  # idle tool out of a truncated set.
  rm -rf "$ROOT/progress-deep"
  python3 "$fixture" truncation "$ROOT/progress-deep" "$wrapper" 30 \
    || fail 'could not build a tree deeper than the cap'
  progress_reset_ticks
  PI_LIVE_TREE_NODE_LIMIT=5 progress_start "$ROOT/progress-deep" "$wrapper"
  progress_tick 8000
  progress_finish
  python3 - "$CONTROLLER_ROOT/runs/progress.live" <<'PY' \
    || fail 'a capped tree walk was truncated silently'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
assert body["progress_tree_nodes"] == 5, body
assert body["progress_tree_truncated"] is True, body
PY
  # The default cap is a stated constant in the tool, not an implied one.
  python3 - "$PILIVE" <<'PY' || fail 'the node cap is not a stated constant'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("pi_live", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert module.TREE_NODE_LIMIT >= 256, module.TREE_NODE_LIMIT
PY

  # (8) The steering row is the tool call's own processes: never the wrapper,
  # never the agent, each with its command.
  python3 "$fixture" tree "$root" "$wrapper" || fail 'could not build the fake process tree'
  progress_reset_ticks
  progress_start "$root" "$wrapper"
  progress_tick 9000
  progress_finish
  python3 - "$CONTROLLER_ROOT/runs/progress.live" <<'PY' || fail 'the steering row is not the tool processes'
import json, sys
body = json.load(open(sys.argv[1], encoding="utf-8"))
pids = sorted(row["pid"] for row in body["steering_children"])
assert pids == [102, 103], body["steering_children"]
for pid in (100, 101):
    assert pid not in pids, body["steering_children"]
assert all(row["command"] for row in body["steering_children"]), body
assert body["wrapper_pid"] == 100, body
PY
  rm -rf "$ROOT/progress-empty" "$ROOT/progress-deep" "$root" "$ROOT/progress-start.jsonl"
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

# --- signal guard (P-plat-31) ---------------------------------------------
#
# pi-impl.sh puts `pi-shims/` FIRST on the agent's PATH, for the first run, a
# resume and every automatic continuation. The check below drives the REAL
# fake pi: it asserts that `pkill`, `killall` and `pgrep` resolve inside the
# shim directory, and only then calls each one on a pattern it made random and
# that matches nothing. The real pkill/killall are never called anywhere in
# this suite: every call below goes through the shim, which refuses.

guard_launch() {
  # $1 run name, $2 fixture, $3 guard log; rest = extra wrapper arguments.
  local run=$1 fixture=$2 guard=$3
  shift 3
  if [[ ${1:-} == resume && -n ${2:-} ]]; then seed_resume_session "$2"; fi
  # PI_CODING_AGENT_DIR is set EMPTY here on purpose: the caller's ambient value
  # must not decide this check (pi's own default applies to an empty value, and
  # so does the wrapper's), exactly as the check's own bin dir is used below.
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/$run-state.json" \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/$fixture.jsonl" \
    FAKE_PI_SESSION_ID="$run-session" FAKE_PI_RC=0 PI_LIVE=0 \
    FAKE_PI_GUARD_LOG="$guard" FAKE_PI_SHIM_DIR="$SHIM_DIR" \
    PI_CODING_AGENT_DIR='' \
    "$SRC" "$WORKTREE" "$PROMPT" "$run" "$@" \
    > "$ROOT/$run.launch.log" 2>&1 < /dev/null
}

# Assert the guard log of one invocation: the three spellings resolved to the
# shims, pkill and killall (and every pgrep signal spelling the shim exists for)
# refused with rc 126 and their named message, and the two pass-through cases
# (a plain pattern, and a value that itself starts with `-`) reached the real
# pgrep, which answers 1 for a no-match pattern.
guard_log_asserts() {
  local guard=$1 prefix=$2
  local spelling label needles=('pgrep-signal' 'pgrep-short-number' 'pgrep-short-name' \
    'pgrep-kill' 'pgrep-signal-eq' 'pgrep-cluster-value')
  for spelling in pkill killall pgrep; do
    grep -qF "PATH-ENTRY $spelling=$SHIM_DIR/" "$guard" \
      || { cat "$guard" >&2; fail "$prefix: $spelling did not resolve inside the shim directory"; }
  done
  grep -qF 'RC pkill=126 ' "$guard" \
    || { cat "$guard" >&2; fail "$prefix: the pkill shim did not refuse with rc 126"; }
  grep -qF 'pi-shim: refused pkill' "$guard" \
    || { cat "$guard" >&2; fail "$prefix: the pkill refusal does not name itself"; }
  grep -qF 'RC killall=126 ' "$guard" \
    || { cat "$guard" >&2; fail "$prefix: the killall shim did not refuse with rc 126"; }
  grep -qF 'pi-shim: refused killall' "$guard" \
    || { cat "$guard" >&2; fail "$prefix: the killall refusal does not name itself"; }
  # Every signal spelling, not only the long `--signal` form. The short forms
  # are the 24-line cluster branch of the shim: a pgrep that forwards `-9` or
  # `-TERM` to the real one signals every match, so each has its own assertion.
  for label in "${needles[@]}"; do
    grep -qF "RC $label=126 " "$guard" \
      || { cat "$guard" >&2; fail "$prefix: $label was not refused with rc 126"; }
    grep -qF "RC $label=126 OUT=pi-shim: refused pgrep" "$guard" \
      || { cat "$guard" >&2; fail "$prefix: $label was not refused by the pgrep shim itself"; }
  done
  # A value that itself starts with `-` is a VALUE, never an option: `-d -`
  # passes through (the real pgrep answers 1 for a no-match pattern). A shim
  # that read the lone `-` as an option would answer 126 or 127 here.
  grep -qF 'RC pgrep-value-dash=1 ' "$guard" \
    || { cat "$guard" >&2; fail "$prefix: a value starting with '-' was not passed through (expected rc 1)"; }
  # Plain pgrep is not a signal: it passes through to the real pgrep, which
  # answers 1 for a pattern that matches nothing. A 126 here would mean the
  # shim over-refuses; a 127 would mean it could not find the real pgrep.
  grep -qF 'RC pgrep-plain=1 ' "$guard" \
    || { cat "$guard" >&2; fail "$prefix: a plain pgrep was not passed through to the real pgrep (expected rc 1)"; }
  # No empty field in the agent's PATH: the shim directory is prepended with `:`
  # only when the inherited PATH is non-empty, because an empty field is the
  # CURRENT DIRECTORY (the worktree) to the shim's lookup and to every command
  # the agent runs.
  grep -qF 'PATH-EMPTY-FIELD 0' "$guard" \
    || { cat "$guard" >&2; fail "$prefix: the agent's PATH has an empty field (an empty field means the current directory)"; }
  if grep -qF 'GUARD-MISSING' "$guard"; then
    cat "$guard" >&2
    fail "$prefix: the agent did not see the shims on its PATH"
  fi
}

# A RESUME is a launch too: it must get the same guarded PATH (mode=resume is
# the one argv shape that skips the new-run preflight).
check_signal_guard_resume() {
  local guard=$ROOT/guard-resume.log
  rm -f "$guard"
  clean_worktree
  guard_launch guard-resume pi-success "$guard" resume guard-resume-session \
    || fail 'a guarded resume failed'
  guard_log_asserts "$guard" 'resume'
  contains "$PI_ARGV_LOG" '--session guard-resume-session' 'the guarded launch resumed the named session'
}

check_signal_guard_first_run() {
  local guard=$ROOT/guard-first.log
  rm -f "$guard"
  clean_worktree
  guard_launch guard-first pi-success "$guard" \
    || fail 'a guarded first run failed'
  guard_log_asserts "$guard" 'first run'
  [[ $(head -n 1 "$CONTROLLER_ROOT/runs/guard-first.done") == 'EXIT=0 OUTCOME=SUCCESS' ]] \
    || fail 'a guarded first run is not SUCCESS'
}

# The automatic continuation is guarded too: the same environment the agent
# gets on a retry is the environment it got on the first run.
check_signal_guard_continuation() {
  local guard=$ROOT/guard-continuation.log
  rm -f "$guard"
  clean_worktree
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/guard-continuation-state.json" \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" \
    FAKE_PI_SEQUENCE='pi-empty-completion pi-success' \
    FAKE_PI_COUNTER="$ROOT/guard-continuation.counter" \
    FAKE_PI_SESSION_ID=guard-continuation-session FAKE_PI_RC=0 PI_LIVE=0 \
    FAKE_PI_GUARD_LOG="$guard" FAKE_PI_SHIM_DIR="$SHIM_DIR" \
    PI_GUARD_SEQUENCE_INDEX=1 \
    "$SRC" "$WORKTREE" "$PROMPT" guard-continuation \
    > "$ROOT/guard-continuation.launch.log" 2>&1 < /dev/null \
    || fail 'a guarded run with one continuation failed'
  # Both invocations (the run and its continuation) wrote their guard facts to
  # the same log, so each assertion below must hold TWICE.
  local count
  for needle in "PATH-ENTRY pkill=$SHIM_DIR/" 'RC pkill=126 ' \
                "PATH-ENTRY killall=$SHIM_DIR/" 'RC killall=126 ' \
                'RC pgrep-signal=126 ' 'RC pgrep-short-number=126 ' \
                'RC pgrep-short-name=126 ' 'RC pgrep-kill=126 ' \
                'RC pgrep-signal-eq=126 ' 'RC pgrep-cluster-value=126 ' \
                'RC pgrep-value-dash=1 ' 'RC pgrep-plain=1 '; do
    count=$(grep -cF -- "$needle" "$guard")
    [[ $count == 2 ]] || { cat "$guard" >&2; fail "the continuation guard log has $count of '$needle', expected 2 (the run and its continuation)"; }
  done
  if grep -qF 'GUARD-MISSING' "$guard"; then
    cat "$guard" >&2
    fail 'a continuation did not see the shims on its PATH'
  fi
  [[ $(grep -cF 'PATH-EMPTY-FIELD 0' "$guard") == 2 ]] \
    || { cat "$guard" >&2; fail 'a continuation PATH (or its first run) has an empty field'; }
  [[ $(head -n 1 "$CONTROLLER_ROOT/runs/guard-continuation.done") == 'EXIT=0 OUTCOME=SUCCESS' ]] \
    || fail 'a guarded run that recovers on its continuation is not SUCCESS'
}

# Fail closed, part 1: pi's own managed-bin directory shadows the guard.
# pi prepends `$PI_CODING_AGENT_DIR/bin` (default `$HOME/.pi/agent/bin`) to the
# PATH of every command its bash tool runs, so a pkill/killall/pgrep there would
# be found BEFORE the shim. The launch is refused (exit 2) while such a file
# exists; nothing is started and no run name is claimed.
check_signal_guard_agent_bin_shadow() {
  # pi prepends its own `$PI_CODING_AGENT_DIR/bin` to the agent's PATH, so a
  # pkill/killall/pgrep there would be found BEFORE the shims. A launch is refused
  # (exit 2) while such a file exists; nothing is started and the name is free.
  local agent_dir=$ROOT/shadow-agent-dir shim before after rc=0
  rm -rf "$agent_dir"; mkdir -p "$agent_dir/bin"
  before=$(pi_invocations)
  for shim in pkill killall pgrep; do
    # An EMPTY stand-in is enough: the guard refuses on its existence, not on
    # what it would do, so this check never runs it either.
    : > "$agent_dir/bin/$shim"
    rc=0
    PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
      CONTROLLER_OD_STATE="$ROOT/shadow-$shim-state.json" PI_CODING_AGENT_DIR="$agent_dir" \
      FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
      FAKE_PI_SESSION_ID="shadow-$shim-session" FAKE_PI_RC=0 \
      "$SRC" "$WORKTREE" "$PROMPT" "shadow-$shim" \
      > "$ROOT/shadow-$shim.launch.log" 2>&1 < /dev/null || rc=$?
    [[ $rc == 2 ]] || fail "a shadowing $shim in pi's own bin dir exited $rc, expected 2"
    contains "$ROOT/shadow-$shim.launch.log" 'shadows the shim' \
      "a shadowing $shim names the shadowing file"
    rm -f "$agent_dir/bin/$shim"
  done
  after=$(pi_invocations)
  [[ $after == "$before" ]] || fail 'pi started although its own bin dir would shadow a shim'
  [[ -e $CONTROLLER_ROOT/runs/shadow-pkill.lock ]] && fail 'a shadowed launch claimed the run name'
}

# Fail closed, part 1b: pi RESOLVES `$PI_CODING_AGENT_DIR` (dist/config.js:511-513
# through utils/paths.js normalizePath:100-124: a leading `~/` expands against the
# home directory, everything else is taken as written). Bash does not tilde-expand
# a variable's value, so a literal `~/...` test would stat nothing, launch pi, and
# then let pi put the REAL `$HOME/...` directory ahead of the shims -- a silently
# defeated guard. The `~/` spelling is therefore expanded against HOME, and a value
# that is still relative is refused with its named message instead of being joined
# to pi's own working directory (which this wrapper cannot check).
check_signal_guard_agent_dir_forms() {
  # The same HOME_ROOT the wrapper runs with, so `~/x` and `$HOME_ROOT/x` are the
  # same directory. An EMPTY stand-in is enough: the guard refuses on existence.
  local shim before after rc=0 tilde_dir=$HOME_ROOT/agents/pi-relative
  # shellcheck disable=SC2088 # the FIXTURE is literally '~/...': pi's OWN spelling, unexpanded by bash
  local tilde_value='~/agents/pi-relative'
  rm -rf "$tilde_dir"; mkdir -p "$tilde_dir/bin"
  for shim in pkill killall pgrep; do
    : > "$tilde_dir/bin/$shim"
    before=$(pi_invocations)
    rc=0
    PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
      CONTROLLER_OD_STATE="$ROOT/tilde-$shim-state.json" \
      PI_CODING_AGENT_DIR="$tilde_value" \
      FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
      FAKE_PI_SESSION_ID="tilde-$shim-session" FAKE_PI_RC=0 \
      "$SRC" "$WORKTREE" "$PROMPT" "tilde-$shim" \
      > "$ROOT/tilde-$shim.launch.log" 2>&1 < /dev/null || rc=$?
    after=$(pi_invocations)
    # The message names the RESOLVED file: a refusal that quoted the literal
    # `~/agents/...` would name a path that does not decide this launch.
    [[ $rc == 2 ]] || fail "a shadowing $shim under a ~/ agent dir exited $rc, expected 2"
    contains "$ROOT/tilde-$shim.launch.log" "$tilde_dir/bin/$shim shadows the shim" \
      "a ~/ agent dir is expanded to HOME and names the shadowing file"
    [[ $after == "$before" ]] || fail "pi started although ~/agent-dir/bin/$shim shadows the shim"
    [[ -e $CONTROLLER_ROOT/runs/tilde-$shim.lock ]] && fail "a ~/agent-dir launch claimed the run name ($shim)"
    rm -f "$tilde_dir/bin/$shim"
  done
  # A CLEAN `~` directory proves the ordinary spelling is ACCEPTED, not refused:
  # the launch succeeds and the agent still sees the shims. (An untilde-expanded
  # `~/...` would also "succeed" here if it never existed, so this case is paired
  # with the three shadowing ones above, which require the named refusal.)
  rm -rf "$tilde_dir"
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/guard-tilde-clean-state.json" \
    PI_CODING_AGENT_DIR="$tilde_value" \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=guard-tilde-clean-session FAKE_PI_RC=0 PI_LIVE=0 \
    FAKE_PI_GUARD_LOG="$ROOT/guard-tilde-clean.guard.log" FAKE_PI_SHIM_DIR="$SHIM_DIR" \
    "$SRC" "$WORKTREE" "$PROMPT" guard-tilde-clean \
    > "$ROOT/guard-tilde-clean.launch.log" 2>&1 < /dev/null \
    || fail 'a launch with a clean ~/agent dir failed'
  guard_log_asserts "$ROOT/guard-tilde-clean.guard.log" 'tilde-agent-dir launch'
  # A value that is not absolute (and not `~/`) is refused with its own named
  # message, and nothing is started: pi would join it to ITS OWN cwd.
  for form in 'agents/pi-relative' '.pi/agent'; do
    before=$(pi_invocations)
    expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
      CONTROLLER_OD_STATE="$ROOT/relative-state.json" PI_CODING_AGENT_DIR="$form" \
      FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
      FAKE_PI_SESSION_ID=relative-session FAKE_PI_RC=0 \
      "$SRC" "$WORKTREE" "$PROMPT" relative-agent-dir
    after=$(pi_invocations)
    contains "$ROOT/focused-command.out" 'PI_CODING_AGENT_DIR must be an absolute path or start with ~/' \
      "a relative PI_CODING_AGENT_DIR ($form) is refused by name"
    [[ $after == "$before" ]] || fail "pi started with a relative PI_CODING_AGENT_DIR ($form)"
    [[ -e $CONTROLLER_ROOT/runs/relative-agent-dir.lock ]] && fail "a relative PI_CODING_AGENT_DIR claimed the run name ($form)"
  done
}

guard_refuses_when_shim_absent() {
  # Fail closed, part 2: a shim that is MISSING (or not executable) refuses the
  # launch with exit 2 and a named message, and pi is never started. The wrapper
  # on trial runs from a COPY whose pi-shims/ directory is the real set with that
  # one entry removed, so the real directory is never touched.
  local kind=$1 shim=$2 mutilated=$ROOT/mutilated-$1
  rm -rf "$mutilated"; mkdir -p "$mutilated"
  ln -s "$(dirname -- "$SRC")"/* "$mutilated"/
  # `rm -f` and not `rm -rf`: the glob above made pi-shims a SYMLINK to the real
  # directory, and -f removes the link without ever following it.
  rm -f "$mutilated/pi-shims"
  cp -a -- "$SHIM_DIR" "$mutilated/pi-shims"
  case $kind in
    missing) rm -f "$mutilated/pi-shims/$shim" ;;
    not-executable) chmod -x "$mutilated/pi-shims/$shim" ;;
  esac
  local before after rc=0
  before=$(pi_invocations)
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/guard-$kind-state.json" \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=guard-$kind-session FAKE_PI_RC=0 \
    "$mutilated/$(basename -- "$SRC")" "$WORKTREE" "$PROMPT" "guard-$kind" \
    > "$ROOT/guard-$kind.launch.log" 2>&1 < /dev/null || rc=$?
  after=$(pi_invocations)
  [[ $rc == 2 ]] || fail "a $kind shim refused with exit $rc, expected 2"
  contains "$ROOT/guard-$kind.launch.log" 'pi signal guard is incomplete' \
    "a $kind shim names the guard as incomplete"
  [[ $after == "$before" ]] || fail "pi started although the $shim shim was $kind"
  if [[ -e $CONTROLLER_ROOT/runs/guard-$kind.lock ]]; then
    fail "a refused $kind launch claimed the run name"
  fi
}

check_signal_guard_fail_closed() {
  local shim
  for shim in pkill killall pgrep; do
    guard_refuses_when_shim_absent missing "$shim"
    guard_refuses_when_shim_absent not-executable "$shim"
  done
  check_signal_guard_agent_bin_shadow
  check_signal_guard_agent_dir_forms
}

# The wrappers that launch the agent carry the guard: pi-impl.sh is the one
# launcher, and it must not offer a switch to turn the guard off.
check_signal_guard_not_switchable() {
  if grep -qE 'PI_(NO_)?SHIM[S_]?(GUARD)?_(OFF|SKIP|DISABLE)' "$SRC"; then
    fail 'pi-impl.sh offers an environment switch that turns the signal guard off'
  fi
  if grep -Fq -- 'pi_shims_dir=' "$SCRIPT_DIR/../pi-review.sh"; then
    fail 'pi-review.sh duplicates the shim PATH instead of inheriting it from pi-impl.sh'
  fi
}

# The shims are POSIX sh, not just bash: run-suite.sh shellchecks the *.sh entry
# points and cannot reach a file without that suffix, so their syntax is checked
# here, where the gate always runs.
check_signal_guard_shim_syntax() {
  local shim
  for shim in pkill killall pgrep; do
    if ! sh -n "$SHIM_DIR/$shim"; then
      fail "$SHIM_DIR/$shim is not valid POSIX sh"
    fi
    # A shim that is not executable would never be reached by the agent, and the
    # wrapper's preflight (proven by check_signal_guard_fail_closed) would refuse
    # EVERY launch instead.
    [[ -x $SHIM_DIR/$shim ]] || fail "$SHIM_DIR/$shim is not executable"
  done
}

check_continue_prompt_file() {
  local prompt_file=$SCRIPT_DIR/../continue-empty-completion.md
  [[ -r $prompt_file ]] || fail 'the canonical continuation prompt file is missing'
  [[ $(wc -l < "$prompt_file") == 1 ]] || fail 'the continuation prompt must be one line'
  contains "$prompt_file" 'Your last turn ended without a tool call or a message.' 'the canonical prompt text'
  # It lives in exactly one file: no other controller source repeats it.
  if grep -R -qF -- 'ended without a tool call or a message' \
    "$SCRIPT_DIR/../pi-impl.sh" "$SCRIPT_DIR/../lib.sh" "$SCRIPT_DIR/../codex-impl.sh"; then
    fail 'the continuation prompt text is duplicated outside its one file'
  fi
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

# --- the run mode (P-plat-26c): review mode is the wrapper's own switch -----

# A review launched through pi-impl.sh ITSELF (not through pi-review.sh) gets the
# read-only set, and the automatic continuation gets it too: the selection lives
# in the wrapper's own argv, so no path -- first run, continue, resume -- can
# lose it. The run's mode is also recorded beside its other records.
check_review_mode_argv() {
  local before after
  clean_worktree
  argv_reset
  PI_RUN_MODE=review PI_LIVE=0 \
    wait_launch_sequence review-mode deepseek-pi-volker pi-success
  assert_review_argv 'first run'
  assert_mode_prompt review 'first run'
  [[ $(cat "$CONTROLLER_ROOT/runs/review-mode.mode") == review ]] \
    || fail 'a review launch did not record its mode beside the run records'
  contains "$ROOT/review-mode.launch.log" 'run mode review' 'the launch announces the mode it runs under'
  # The automatic EMPTY-COMPLETION continuation is a launch of the same session,
  # so it carries the same read-only set and the same rule paragraph.
  clean_worktree
  before=$(pi_invocations)
  PI_RUN_MODE=review PI_LIVE=0 \
    wait_launch_sequence review-mode-continue deepseek-pi-github2 'pi-empty-completion pi-success'
  after=$(pi_invocations)
  [[ $(( after - before )) == 2 ]] || fail "a review continuation used $(( after - before )) pi invocations, expected 2"
  assert_review_argv 'continuation'
  assert_continuation_prompt review 'continuation'
  [[ $(head -n 1 "$CONTROLLER_ROOT/runs/review-mode-continue.done") == 'EXIT=0 OUTCOME=SUCCESS' ]] \
    || fail 'a review that recovered on its continuation is not SUCCESS'
}

# The implementer side is unchanged: the same launch without the mode carries NO
# review argument, and gets the implementer rule paragraph instead.
check_implementer_mode_unchanged() {
  local argv
  clean_worktree
  argv_reset
  PI_LIVE=0 wait_launch_sequence implementer-mode deepseek-pi-volker pi-success
  argv_has --no-extensions 'an implementer launch disables extension discovery for the fixed backend'
  argv_has_pair -e "$TEST_CONTROLLER/pi-pidns-extension.js" 'an implementer launch loads the fixed namespace extension'
  argv_absent '--no-mcp' 'an implementer launch does not carry the review MCP switch'
  argv_absent 'read,grep,find,ls' 'an implementer launch does not carry the review tool list'
  assert_mode_prompt implementer 'implementer launch'
  [[ $(cat "$CONTROLLER_ROOT/runs/implementer-mode.mode") == implementer ]] \
    || fail 'an implementer launch did not record its mode'
}

# The read-only set has ONE definition and the launcher never carries a second
# copy of it: pi-review.sh sets the mode, it does not export the list.
check_review_argv_single_definition() {
  local review_source=$SCRIPT_DIR/../pi-review.sh
  [[ -r $REVIEW_ARGV_FILE ]] || fail 'the review argv file is missing'
  contains "$REVIEW_ARGV_FILE" 'read,grep,find,ls' 'the one review argv definition names the read-only set'
  if sed 's/#.*$//' "$review_source" | grep -F -- '--tools read,grep,find,ls' >/dev/null; then
    fail 'pi-review.sh carries a second copy of the review tool list instead of setting the mode'
  fi
  contains "$review_source" 'PI_RUN_MODE=review' 'pi-review.sh launches in review mode'
}

# The mode value is a closed vocabulary: a typo must be refused, not read as
# "implementer" (a default that substitutes for a fact).
check_mode_value_refused() {
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=mode-value-session FAKE_PI_RC=0 \
    PI_RUN_MODE=reviewe "$SRC" "$WORKTREE" "$PROMPT" mode-typo
  contains "$ROOT/focused-command.out" "PI_RUN_MODE must be 'implementer' or 'review'" \
    'an unknown run mode is refused by name'
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=mode-value-session FAKE_PI_RC=0 \
    PI_RUN_MODE='' "$SRC" "$WORKTREE" "$PROMPT" mode-empty
  contains "$ROOT/focused-command.out" "PI_RUN_MODE must be 'implementer' or 'review'" \
    'a set-but-empty run mode is refused rather than read as unset'
}

# In review mode the seam may not add ANYTHING: adding a tool is a widening, and
# a vocabulary of allowed additions is the kind of rule that does not hold.
check_mode_extra_args_refused() {
  local before line attempt=0 runs=$CONTROLLER_ROOT/runs run_name
  before=$(pi_invocations)
  while IFS= read -r line; do
    [[ -n $line ]] || continue
    attempt=$(( attempt + 1 ))
    expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
      FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
      FAKE_PI_SESSION_ID=review-extra-session FAKE_PI_RC=0 \
      PI_RUN_MODE=review PI_EXTRA_ARGS="$line" \
      "$SRC" "$WORKTREE" "$PROMPT" "review-extra-$attempt"
    contains "$ROOT/focused-command.out" \
      "PI_EXTRA_ARGS may not set '$line' in review mode" \
      "review mode refuses the widening spelling '$line'"
    run_name=review-extra-$attempt
    [[ ! -e $runs/$run_name.lock && ! -e $runs/$run_name.mode ]] \
      || fail "a refused review extra argument '$line' claimed or wrote its run mode"
    [[ ! -e $runs/session-modes/review-extra-session ]] \
      || fail "a refused review extra argument '$line' wrote a session mode"
  done <<'CASES'
--tools
--tools=read,bash
-t
--exclude-tools
--exclude-tools=read
-xt
--no-tools
-nt
--no-builtin-tools
-nbt
--no-mcp
--skill
--skill=/tmp/skill.md
--no-skills
-ns
--prompt-template
--prompt-template=/tmp/t.md
--no-prompt-templates
-np
--approve
-a
--no-approve
-na
CASES
  [[ $(pi_invocations) == "$before" ]] || fail 'a review launched with a widened PI_EXTRA_ARGS'
  # An argument outside that list is refused too, and by the general rule.
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=review-extra-session FAKE_PI_RC=0 \
    PI_RUN_MODE=review PI_EXTRA_ARGS='--verbose' \
    "$SRC" "$WORKTREE" "$PROMPT" review-extra-verbose
  contains "$ROOT/focused-command.out" 'may not add anything in review mode' \
    'an argument the list does not name is refused by the same edit'
  [[ $(pi_invocations) == "$before" ]] || fail 'a review launched with an unclassified PI_EXTRA_ARGS'
}

# A resume ALWAYS takes the recorded mode: the name was started as a review, so
# it is still a review. That has to hold when the caller says nothing, and an
# explicit implementer request is refused instead of obeyed.
check_mode_sticky_resume() {
  local runs=$CONTROLLER_ROOT/runs
  clean_worktree
  argv_reset
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/mode-sticky-state.json" PI_LIVE=0 \
    PI_RUN_MODE=review FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=mode-sticky-session FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" mode-sticky > "$ROOT/mode-sticky.launch.log" 2>&1 < /dev/null \
    || fail 'the review launch recorded no mode to resume from'
  [[ $(cat "$runs/mode-sticky.mode") == review ]] || fail 'the review launch recorded the wrong mode'
  # (a) A resume that asks for nothing is forced into review.
  # Keep this arm isolated to the run-name record: old runs have no session-mode
  # file, and the run-name sticky refusal/selection must still work for them.
  rm -f -- "$runs/session-modes/mode-sticky-session"
  argv_reset
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/mode-sticky-resume-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=mode-sticky-session FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" mode-sticky resume mode-sticky-session \
    > "$ROOT/mode-sticky-resume.launch.log" 2>&1 < /dev/null \
    || fail 'a resume of a review was not accepted'
  assert_review_argv 'resume'
  assert_mode_prompt review 'resume'
  contains "$ROOT/mode-sticky-resume.launch.log" 'run mode review (record)' \
    'the resume says where its mode came from'
  # (b) An explicit implementer request on that name is refused, and nothing
  # launches: the previous records are byte-identical afterwards.
  local before after
  before=$(pi_invocations)
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=mode-sticky-session FAKE_PI_RC=0 \
    PI_RUN_MODE=implementer "$SRC" "$WORKTREE" "$PROMPT" mode-sticky resume mode-sticky-session
  contains "$ROOT/focused-command.out" 'was started as a REVIEW and cannot be resumed in implementer mode' \
    'an implementer resume of a review is refused by name'
  after=$(pi_invocations)
  [[ $after == "$before" ]] || fail 'a refused implementer resume of a review still launched pi'
  rm -f -- "$runs/session-modes/mode-sticky-session"
  # (c) The reverse direction is allowed: an implementer name may be launched
  # STRICTER, and its record says so afterwards.
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/mode-sticky-narrow-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=mode-sticky-narrow-session FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" mode-sticky-narrow > "$ROOT/mode-sticky-narrow.launch.log" 2>&1 < /dev/null \
    || fail 'the implementer launch for the narrowing case failed'
  [[ $(cat "$runs/mode-sticky-narrow.mode") == implementer ]] || fail 'the implementer launch recorded the wrong mode'
  argv_reset
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/mode-sticky-narrow-state.json" PI_LIVE=0 \
    PI_RUN_MODE=review FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=mode-sticky-narrow-session FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" mode-sticky-narrow resume mode-sticky-narrow-session \
    > "$ROOT/mode-sticky-narrow-resume.launch.log" 2>&1 < /dev/null \
    || fail 'a stricter resume of an implementer name was refused'
  assert_review_argv 'narrowed resume'
  contains "$ROOT/mode-sticky-narrow-resume.launch.log" 'the stricter mode wins' \
    'the narrowing resume says why it runs as a review'
  [[ $(cat "$runs/mode-sticky-narrow.mode") == review ]] \
    || fail 'a narrowing resume did not rewrite the mode record'
  # (d) A record that is neither word is refused: no correct writer produces
  # one, so it is not a fact to guess from.
  printf 'reviewer\n' > "$runs/mode-garbled.mode"
  printf 'mode-garbled-session\n' > "$runs/mode-garbled.thread"
  before=$(pi_invocations)
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=mode-garbled-session FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" mode-garbled resume mode-garbled-session
  contains "$ROOT/focused-command.out" 'cannot establish the run mode of mode-garbled' \
    'an unreadable mode record is refused instead of guessed'
  after=$(pi_invocations)
  [[ $after == "$before" ]] || fail 'a launch ran with a mode record nobody wrote'
}

# A pi resume argument must name one exact, previously recorded session id.
# pi itself accepts a prefix, so the wrapper cannot delegate this decision to
# pi and discover later that a different session opened.
check_mode_resume_exact_id() {
  local runs=$CONTROLLER_ROOT/runs full=mode-prefix-session-full short=mode-prefix-session before
  clean_worktree
  mkdir -p "$runs"
  printf '%s\n' "$full" > "$runs/prefix-source.thread"
  argv_reset
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/prefix-full-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID="$full" FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" prefix-full-resume resume "$full" \
    > "$ROOT/prefix-full-resume.launch.log" 2>&1 < /dev/null \
    || fail 'a resume using the exact recorded session id was refused'
  [[ $(cat "$runs/prefix-full-resume.thread") == "$full" ]] \
    || fail 'the full recorded session id was not retained by its resume'

  before=$(pi_invocations)
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/prefix-short-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID="$short" FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" prefix-short-resume resume "$short"
  contains "$ROOT/focused-command.out" 'no record names this exact id' \
    'a prefix of a recorded session id is refused before pi can resolve it'
  [[ $(pi_invocations) == "$before" ]] || fail 'an unrecorded session prefix launched pi'
  [[ ! -e $runs/prefix-short-resume.lock ]] || fail 'an unrecorded session prefix claimed its run name'
}

check_mode_resume_stream_mismatch() {
  local runs=$CONTROLLER_ROOT/runs requested=mode-mismatch-requested before rc
  clean_worktree
  mkdir -p "$runs"
  printf '%s\n' "$requested" > "$runs/mismatch-source.thread"
  before=$(pi_invocations)
  if PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/mismatch-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=mode-mismatch-other FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" mismatch-resume resume "$requested" \
    > "$ROOT/mismatch-resume.launch.log" 2>&1 < /dev/null; then
    fail 'a resume accepted a stream that reported a different session id'
  else
    rc=$?
    [[ $rc == 2 ]] || fail "a mismatched session stream exited $rc, expected 2"
  fi
  contains "$ROOT/mismatch-resume.launch.log" 'produced stream session mode-mismatch-other' \
    'the mismatched session stream is reported by name'
  [[ $(cat "$runs/mismatch-resume.done") == 'EXIT=2 OUTCOME=ERROR' ]] \
    || fail 'the mismatched stream was not recorded as a failed run'
  [[ $(cat "$runs/mismatch-resume.thread") == "$requested" ]] \
    || fail 'a mismatched stream replaced the requested session thread'
  [[ ! -e $runs/session-modes/mode-mismatch-other ]] \
    || fail 'a mismatched stream wrote a mode record for the other session'
  [[ $(pi_invocations) == $((before + 1)) ]] || fail 'the mismatch case did not exercise one pi launch'
}

# Inject a same-name writer at the successful end of controller_run_claim: the
# pre-claim read saw an implementer session, and the claim now makes a review
# mode visible. The launcher must re-read before it builds the pi argv.
check_mode_claimed_name_reread() {
  local runs=$CONTROLLER_ROOT/runs race_dir=$ROOT/claim-mode-race race_src src_dir session=claim-race-session
  clean_worktree
  mkdir -p "$race_dir" "$runs/session-modes"
  src_dir=$(dirname -- "$SRC")
  for sibling in lib.sh opendesign-accounts.py continue-empty-completion.md \
    implementer-prompt-rules.md review-prompt-rules.md pi-review-argv; do
    cp -- "$src_dir/$sibling" "$race_dir/$sibling"
  done
  cp -a -- "$src_dir/pi-shims" "$race_dir/pi-shims"
  cp -- "$SRC" "$race_dir/pi-impl.sh"
  race_src=$race_dir/pi-impl.sh
  python3 - "$race_dir/lib.sh" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
before = "  CONTROLLER_RUN_LOCK_PATH=$lock\n  return 0\n"
after = "  CONTROLLER_RUN_LOCK_PATH=$lock\n  printf 'review\\n' > \"$CONTROLLER_RUNS_DIR/$name.mode\"\n  return 0\n"
if source.count(before) != 1:
    raise SystemExit("claim-mode race fixture could not find one successful claim return")
path.write_text(source.replace(before, after, 1), encoding="utf-8")
PY
  printf 'implementer\n' > "$runs/session-modes/$session"
  argv_reset
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_HOME="$CONTROLLER_ROOT" CONTROLLER_RUNS_DIR="$runs" \
    CONTROLLER_OD_STATE="$ROOT/claim-race-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID="$session" FAKE_PI_RC=0 \
    "$race_src" "$WORKTREE" "$PROMPT" claim-mode-race resume "$session" \
    > "$ROOT/claim-mode-race.launch.log" 2>&1 < /dev/null \
    || fail 'a resume did not safely handle a review mode written during its claim'
  assert_review_argv 'post-claim mode re-read'
  contains "$ROOT/claim-mode-race.launch.log" 'run mode review (claimed-name-record)' \
    'the post-claim name mode governs the constructed argv'
  [[ $(cat "$runs/claim-mode-race.mode") == review ]] \
    || fail 'the post-claim review mode was overwritten by the stale implementer read'
}

# A session can be resumed under a different run name. Legacy sessions are
# recovered by exact .thread matches; current sessions also have a session-mode
# record. In both cases a review stays review, while an implementer session does
# not become a review just because another id contains its text.
check_mode_session_thread_resume() {
  local runs=$CONTROLLER_ROOT/runs session=mode-session-thread-only bad_session=mode-session-thread-invalid \
    legacy_material_session=mode-session-thread-review-material before after
  clean_worktree
  mkdir -p "$runs/session-modes"
  rm -f -- "$runs/session-modes/$session" "$runs/session-modes/$session.lock"
  printf 'review\n' > "$runs/legacy-review.mode"
  printf '%s\n' "$session" > "$runs/legacy-review.thread"

  # A matching legacy name whose mode record is malformed also refuses; the
  # wrapper cannot ignore an invalid fact just because the session record is
  # missing.
  printf 'reviewer\n' > "$runs/legacy-invalid.mode"
  printf '%s\n' "$bad_session" > "$runs/legacy-invalid.thread"
  before=$(pi_invocations)
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/invalid-thread-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID="$bad_session" FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" fresh-invalid-thread-record resume "$bad_session"
  contains "$ROOT/focused-command.out" 'legacy-invalid.mode is not' \
    'an invalid matching-name mode record refuses the resume'
  after=$(pi_invocations)
  [[ $after == "$before" ]] || fail 'an invalid matching-name mode record launched pi'
  [[ ! -e $runs/fresh-invalid-thread-record.lock ]] \
    || fail 'an invalid matching-name mode record claimed its run name'

  # The review is known only through the old run's .thread + .mode records. An
  # explicit implementer request refuses before the new run name is claimed.
  before=$(pi_invocations)
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    PI_RUN_MODE=implementer PI_LIVE=0 CONTROLLER_OD_STATE="$ROOT/thread-implementer-state.json" \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID="$session" FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" fresh-thread-implementer resume "$session"
  contains "$ROOT/focused-command.out" 'is recorded as a REVIEW and cannot be resumed in implementer mode' \
    'a fresh name cannot resume a thread-recorded review as implementer'
  after=$(pi_invocations)
  [[ $after == "$before" ]] || fail 'a refused fresh-name implementer resume launched pi'
  [[ ! -e $runs/fresh-thread-implementer.lock ]] || fail 'a refused fresh-name implementer resume claimed its name'
  [[ ! -e $runs/session-modes/$session ]] || fail 'a refused resume wrote a session mode record'

  # With no explicit mode, the same fresh name is forced into review. Once it
  # has learned the session, the wrapper stores the mode under that session id.
  argv_reset
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/session-thread-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID="$session" FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" fresh-thread-review resume "$session" \
    > "$ROOT/fresh-thread-review.launch.log" 2>&1 < /dev/null \
    || fail 'a fresh-name resume found only through .thread did not launch as review'
  assert_review_argv 'thread-record resume'
  assert_mode_prompt review 'thread-record resume'
  contains "$ROOT/fresh-thread-review.launch.log" 'run mode review (thread-record)' \
    'the fresh-name resume reports that the matching .thread supplied review mode'
  [[ $(cat "$runs/fresh-thread-review.mode") == review ]] || fail 'the fresh-name thread resume recorded implementer mode'
  [[ $(cat "$runs/session-modes/$session") == review ]] || fail 'the learned session mode was not recorded as review'

  # A pre-session-record review from before `.mode` existed is represented by
  # exactly its old `.thread` and pi-review's before-state material.
  mkdir -p "$runs/legacy-review-material.review"
  printf 'head=legacy\n' > "$runs/legacy-review-material.review/before-state.txt"
  printf '%s\n' "$legacy_material_session" > "$runs/legacy-review-material.thread"
  argv_reset
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/thread-review-material-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID="$legacy_material_session" FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" fresh-thread-review-material resume "$legacy_material_session" \
    > "$ROOT/fresh-thread-review-material.launch.log" 2>&1 < /dev/null \
    || fail 'a legacy thread plus before-state review without a mode record was refused'
  assert_review_argv 'legacy thread review material resume'
  contains "$ROOT/fresh-thread-review-material.launch.log" 'run mode review (thread-review-material)' \
    'legacy before-state material beside a matching thread supplies review mode'
  [[ $(cat "$runs/fresh-thread-review-material.mode") == review ]] \
    || fail 'a legacy thread plus review material was resumed as implementer'
}

check_mode_stream_review_resume() {
  local runs=$CONTROLLER_ROOT/runs killed=mode-killed-review-session old=mode-retired-review-session before
  clean_worktree
  mkdir -p "$runs"

  # A wrapper killed after pi writes its session event has no `.thread` or
  # session-mode record yet. The name mode and bounded JSONL head still identify
  # the active review.
  printf 'review\n' > "$runs/killed-review.mode"
  printf '{"type":"session","id":"%s"}\n' "$killed" > "$runs/killed-review.jsonl"
  argv_reset
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/killed-review-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID="$killed" FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" killed-review-resume resume "$killed" \
    > "$ROOT/killed-review-resume.launch.log" 2>&1 < /dev/null \
    || fail 'a review found through its running stream head was refused'
  assert_review_argv 'killed first review stream resume'
  # shellcheck disable=SC2016 # the backticks are the message's literal text
  contains "$ROOT/killed-review-resume.launch.log" 'run mode review (stream-record)' \
    'a session event without `.thread` or session-mode state finds its run mode'

  # Forced reuse retires the old `.jsonl` link, with no `.thread` sidecar. It first
  # copies the old mode to the monotonic session record, which then remains the
  # sole exact record that permits a fresh-name resume as review.
  printf 'review\n' > "$runs/retired-review.mode"
  printf '{"type":"session","id":"%s"}\n' "$old" > "$runs/retired-review.jsonl"
  argv_reset
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/retired-review-force-state.json" PI_LIVE=0 CONTROLLER_RUN_FORCE=1 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=retired-review-replacement-session FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" retired-review \
    > "$ROOT/retired-review-force.launch.log" 2>&1 < /dev/null \
    || fail 'a forced reuse of the old review name failed'
  [[ $(cat "$runs/session-modes/$old") == review ]] \
    || fail 'forced name reuse did not preserve the old session mode as review'
  [[ $(cat "$runs/retired-review.mode") == implementer ]] \
    || fail 'forced name reuse did not record the replacement run mode'
  [[ -n $(find "$runs/retired/retired-review" -type f -name 'retired-review.jsonl' -print -quit) ]] \
    || fail 'forced reuse did not archive the old session stream'

  argv_reset
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/retired-review-resume-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID="$old" FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" retired-review-fresh resume "$old" \
    > "$ROOT/retired-review-fresh.launch.log" 2>&1 < /dev/null \
    || fail 'the retired review session could not be resumed under a fresh name'
  assert_review_argv 'retired review session resume'
  contains "$ROOT/retired-review-fresh.launch.log" 'run mode review (session-record)' \
    'the preserved session-mode record is the sole review source after retirement'

  # If a session id exists but neither its name nor session records establish
  # a mode, forced reuse cannot safely retire the only identity evidence.
  printf '{"type":"session","id":"mode-retired-unclassified-session"}\n' \
    > "$runs/unclassified-retired.jsonl"
  argv_reset
  before=$(pi_invocations)
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/unclassified-retired-state.json" PI_LIVE=0 CONTROLLER_RUN_FORCE=1 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=unclassified-retired-replacement-session FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" unclassified-retired
  contains "$ROOT/focused-command.out" 'has a session id but no recorded mode or review evidence' \
    'forced reuse refuses to retire a session whose mode cannot be established'
  [[ -e $runs/unclassified-retired.jsonl && ! -e $runs/retired/unclassified-retired ]] \
    || fail 'an unclassified session stream was retired without preserving its mode'
  [[ $(pi_invocations) == "$before" ]] || fail 'pi launched while an unclassified session was being retired'
}

check_mode_session_record_resume() {
  local runs=$CONTROLLER_ROOT/runs session=mode-session-record-only bad_session=mode-session-invalid-record before after
  clean_worktree
  mkdir -p "$runs/session-modes"
  printf 'review\n' > "$runs/session-modes/$session"
  argv_reset
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/session-record-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID="$session" FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" fresh-session-record-review resume "$session" \
    > "$ROOT/fresh-session-record-review.launch.log" 2>&1 < /dev/null \
    || fail 'a fresh-name resume found only through the session record did not launch as review'
  assert_review_argv 'session-record resume'
  assert_mode_prompt review 'session-record resume'
  contains "$ROOT/fresh-session-record-review.launch.log" 'run mode review (session-record)' \
    'the fresh-name resume reports that the session record supplied review mode'
  [[ $(cat "$runs/fresh-session-record-review.mode") == review ]] \
    || fail 'a session-record review resume recorded the wrong name mode'
  [[ $(cat "$runs/session-modes/$session") == review ]] \
    || fail 'a session-record review was overwritten by the resumed launch'

  # Simulate a stale writer that reaches the recording helper with implementer
  # mode after review is already stored. The shared session record must remain
  # review even if a run-name record says implementer.
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/session-stale-writer-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID="$session" FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" stale-implementer-writer \
    > "$ROOT/stale-implementer-writer.launch.log" 2>&1 < /dev/null \
    || fail 'the stale implementer writer fixture did not complete'
  [[ $(cat "$runs/stale-implementer-writer.mode") == implementer ]] \
    || fail 'the stale writer did not exercise an implementer run-name record'
  [[ $(cat "$runs/session-modes/$session") == review ]] \
    || fail 'a stale implementer writer changed the session mode back to implementer'

  before=$(pi_invocations)
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID="$session" FAKE_PI_RC=0 PI_RUN_MODE=implementer \
    "$SRC" "$WORKTREE" "$PROMPT" fresh-session-record-implementer resume "$session"
  contains "$ROOT/focused-command.out" 'is recorded as a REVIEW and cannot be resumed in implementer mode' \
    'a session-recorded review refuses an implementer request under a fresh name'
  after=$(pi_invocations)
  [[ $after == "$before" ]] || fail 'a refused session-record implementer resume launched pi'
  [[ ! -e $runs/fresh-session-record-implementer.lock ]] \
    || fail 'a refused session-record implementer resume claimed its name'

  # An invalid session record is not an absent one and cannot authorize a
  # default implementer launch.
  printf 'reviewer\n' > "$runs/session-modes/$bad_session"
  before=$(pi_invocations)
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/invalid-session-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID="$bad_session" FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" fresh-invalid-session-record resume "$bad_session"
  contains "$ROOT/focused-command.out" "cannot establish the run mode of pi session $bad_session" \
    'an invalid session mode record refuses the resume'
  after=$(pi_invocations)
  [[ $after == "$before" ]] || fail 'an invalid session mode record launched pi'
  [[ ! -e $runs/fresh-invalid-session-record.lock ]] \
    || fail 'an invalid session mode record claimed its run name'
}

check_mode_session_implementer_unchanged() {
  local runs=$CONTROLLER_ROOT/runs session=mode-session-implementer-only
  local extension=$TEST_CONTROLLER/pi-pidns-extension.js
  clean_worktree
  mkdir -p "$runs/session-modes"
  printf 'implementer\n' > "$runs/session-modes/$session"
  # A longer, unrelated thread id has review mode; matching must be exact.
  printf 'review\n' > "$runs/unrelated-session-review.mode"
  printf '%s-suffix\n' "$session" > "$runs/unrelated-session-review.thread"
  argv_reset
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/session-implementer-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID="$session" FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" fresh-session-implementer resume "$session" \
    > "$ROOT/fresh-session-implementer.launch.log" 2>&1 < /dev/null \
    || fail 'a pre-existing implementer session was changed into a review'
  argv_absent '--no-mcp' 'an implementer session does not acquire the review MCP switch'
  argv_absent 'read,grep,find,ls' 'an implementer session does not acquire the review tool set'
  argv_has --no-extensions 'an implementer resume selected by the session record disables extension discovery'
  argv_has_pair -e "$extension" 'an implementer resume selected by the session record loads the fixed extension'
  assert_mode_prompt implementer 'session implementer resume'
  [[ $(cat "$runs/fresh-session-implementer.mode") == implementer ]] \
    || fail 'a pre-existing implementer session recorded review mode'
  [[ $(cat "$runs/session-modes/$session") == implementer ]] \
    || fail 'a pre-existing implementer session record was changed'

  # The caller may narrow that session by resuming it in review mode under a
  # second fresh name; the session record then moves once to review.
  argv_reset
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/session-implementer-narrow-state.json" PI_LIVE=0 PI_RUN_MODE=review \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID="$session" FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" fresh-session-implementer-narrow resume "$session" \
    > "$ROOT/fresh-session-implementer-narrow.launch.log" 2>&1 < /dev/null \
    || fail 'an explicit review request could not narrow an implementer session'
  assert_review_argv 'narrowed session resume'
  [[ $(cat "$runs/fresh-session-implementer-narrow.mode") == review ]] \
    || fail 'the narrowed fresh-name run did not record review mode'
  [[ $(cat "$runs/session-modes/$session") == review ]] \
    || fail 'the narrowed session record did not become review'
}

# A resume of a name that carries review MATERIAL but no mode record is a review:
# the session is known to be one, and the safe direction is failing closed. A
# pre-existing implementer session (no record, no material) resumes unchanged.
check_mode_record_missing_reads_review_evidence() {
  local runs=$CONTROLLER_ROOT/runs before after
  clean_worktree
  mkdir -p "$runs/review-material-only.review"
  printf 'head=abc\n' > "$runs/review-material-only.review/before-state.txt"
  printf 'review-material-session\n' > "$runs/review-material-only.thread"
  before=$(pi_invocations)
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    PI_RUN_MODE=implementer PI_LIVE=0 CONTROLLER_OD_STATE="$ROOT/evidence-implementer-state.json" \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=review-material-session FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" review-material-only resume review-material-session
  contains "$ROOT/focused-command.out" 'has review material and cannot be resumed in implementer mode' \
    'review material refuses an explicit implementer request'
  after=$(pi_invocations)
  [[ $after == "$before" ]] || fail 'an implementer request on review material launched pi'
  [[ ! -e $runs/review-material-only.lock ]] || fail 'an implementer request on review material claimed the run name'
  [[ ! -e $runs/session-modes/review-material-session ]] \
    || fail 'a refused evidence-based resume wrote a session mode record'
  argv_reset
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/review-material-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=review-material-session FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" review-material-only resume review-material-session \
    > "$ROOT/review-material-only.launch.log" 2>&1 < /dev/null \
    || fail 'a resume of review material without a mode record was refused'
  assert_review_argv 'evidence resume'
  contains "$ROOT/review-material-only.launch.log" 'has review material but no mode record' \
    'the evidence-based review resume says why it is a review'
  [[ $(cat "$runs/review-material-only.mode") == review ]] \
    || fail 'an evidence-based review resume recorded the wrong mode'
  # A pre-existing implementer session: no mode record, no review material.
  printf 'mode-pre-existing-session\n' > "$runs/mode-pre-existing.thread"
  argv_reset
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/mode-pre-existing-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=mode-pre-existing-session FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" mode-pre-existing resume mode-pre-existing-session \
    > "$ROOT/mode-pre-existing.launch.log" 2>&1 < /dev/null \
    || fail 'a resume of a pre-existing implementer session was refused'
  argv_absent 'read,grep,find,ls' 'a pre-existing implementer session is NOT silently turned into a review'
  assert_mode_prompt implementer 'pre-existing resume'
}

check_mode_review_material_unreadable() {
  local runs=$CONTROLLER_ROOT/runs session=unreadable-review-material-session before
  clean_worktree
  mkdir -p "$runs"
  printf '%s\n' "$session" > "$runs/unreadable-review-material.thread"
  printf 'not a directory\n' > "$runs/unreadable-review-material.review"
  before=$(pi_invocations)
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/unreadable-review-material-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID="$session" FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" unreadable-review-material resume "$session"
  contains "$ROOT/focused-command.out" 'review material directory is unreadable or not searchable' \
    'a review-material path that cannot be searched is refused as unreadable state'
  [[ $(pi_invocations) == "$before" ]] || fail 'an unreadable review-material path launched pi'
  [[ ! -e $runs/unreadable-review-material.lock ]] \
    || fail 'an unreadable review-material path claimed the run name'
}

# Every launch appends its mode's fixed rule paragraph, and a launch that cannot
# read it is refused: the rule is part of the contract, not a suggestion.
check_rule_file_missing_refused() {
  local rules=$IMPLEMENTER_RULES
  [[ -r $rules ]] || fail 'the implementer rule paragraph file is missing'
  contains "$rules" 'private PID namespace' 'the implementer rule names the container the signal tests belong in'
  contains "$rules" 'host procfs' 'the implementer rule states that /proc remains the host procfs'
  contains "$rules" 'kill -0 1 succeeds' 'the implementer rule documents the namespace PID 1 behavior'
  contains "$rules" 'Never kill by pattern' 'the implementer rule refuses pattern kills'
  contains "$rules" 'no shell experiment' 'the implementer rule refuses shell experiments in the devcontainer'
  [[ -r $REVIEW_RULES ]] || fail 'the review rule paragraph file is missing'
  contains "$REVIEW_RULES" 'execute nothing' 'the review rule refuses every execution'
  local mode mutilated
  for mode in implementer review; do
    mutilated=$ROOT/rules-missing-$mode
    rm -rf "$mutilated"; mkdir -p "$mutilated"
    ln -s "$(dirname -- "$SRC")"/* "$mutilated"/
    rm -f "$mutilated/implementer-prompt-rules.md" "$mutilated/review-prompt-rules.md"
    rm -f "$mutilated/pi-shims"
    cp -a -- "$SHIM_DIR" "$mutilated/pi-shims"
    expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
      FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
      FAKE_PI_SESSION_ID=rules-missing-session FAKE_PI_RC=0 \
      PI_RUN_MODE=$mode "$mutilated/$(basename -- "$SRC")" "$WORKTREE" "$PROMPT" "rules-missing-$mode"
    contains "$ROOT/focused-command.out" "pi $mode prompt rule file is missing or unreadable" \
      "a $mode launch without its rule paragraph is refused"
    [[ ! -e $CONTROLLER_ROOT/runs/rules-missing-$mode.lock ]] \
      || fail "a refused $mode launch claimed the run name"
  done
}

check_mode_not_written_on_refusal() {
  local runs=$CONTROLLER_ROOT/runs home=$ROOT/mode-refusal-home
  clean_worktree
  rm -rf -- "$home"
  mkdir -p "$home" "$runs"
  rm -f -- "$runs/mode-refusal".*
  printf 'review\n' > "$runs/mode-refusal.mode"
  printf 'previous-session\n' > "$runs/mode-refusal.thread"
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$home" PI_ROUTE=deepseek-pi-volker \
    PI_RUN_MODE=implementer CONTROLLER_RUN_FORCE=1 \
    "$SRC" "$WORKTREE" "$PROMPT" mode-refusal
  contains "$ROOT/focused-command.out" 'requires the local' \
    'the missing credential file is what refused the forced reuse'
  grep -qx review "$runs/mode-refusal.mode" \
    || fail 'a refused forced reuse overwrote the previous review mode record'
  [[ ! -e $runs/retired/mode-refusal ]] \
    || fail 'a forced reuse refused at credentials retired the previous review records'
}

check_mode_rule_paragraph() {
  # The paragraph every implementer prompt carries must say the three things
  # an agent can otherwise only be asked politely: the pattern ban, the capped
  # container with a private PID namespace, and "no shell experiments".
  # shellcheck disable=SC2016 # the backticks are the DOCUMENT's literal text
  contains "$IMPLEMENTER_RULES" 'no `pkill`' 'the implementer rule names the pattern kill'
  contains "$IMPLEMENTER_RULES" 'killall' 'the implementer rule names killall'
  contains "$IMPLEMENTER_RULES" 'recorded itself' 'the implementer rule pins the kill to recorded pids'
  contains "$IMPLEMENTER_RULES" 'TERM-trap' 'the implementer rule names the trap tests'
  contains "$IMPLEMENTER_RULES" 'outer PIDs' 'the implementer rule explains the visible host procfs PIDs'
  contains "$IMPLEMENTER_RULES" 'kill -0 1 succeeds' 'the implementer rule documents the namespace PID 1 behavior'
  contains "$REVIEW_RULES" 'The review is read from' 'the review rule names what the review is read from'
  contains "$REVIEW_RULES" 'say that it is unknown' 'the review rule names the honest report of an unreadable fact'
}

# A controller.local.env that names a mode must not survive a caller who named
# the other one: lib.sh sources that file with `set -a`, so this is the seam a
# host setting would otherwise use to flip a review back to a shell. And on a
# RESUME the record decides: a value only the host file supplied is a default
# for launches that name none, so it is announced and not obeyed.
check_mode_local_env_not_weaker() {
  local home=$ROOT/mode-env-home rc=0
  rm -rf "$home"; mkdir -p "$home"
  printf 'PI_RUN_MODE="implementer"\n' > "$home/controller.local.env"
  clean_worktree
  argv_reset
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker CONTROLLER_HOME="$home" \
    CONTROLLER_RUNS_DIR="$CONTROLLER_ROOT/runs" \
    CONTROLLER_OD_STATE="$ROOT/mode-env-state.json" PI_LIVE=0 PI_RUN_MODE=review \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=mode-env-session FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" mode-env > "$ROOT/mode-env.launch.log" 2>&1 < /dev/null || rc=$?
  [[ $rc == 0 ]] || { cat "$ROOT/mode-env.launch.log" >&2; fail 'the caller-named review mode failed to launch'; }
  assert_review_argv 'local env'
  contains "$ROOT/mode-env.launch.log" 'run mode review (caller)' \
    'the launch says the CALLER named the mode, not the host file'
  [[ $(cat "$CONTROLLER_ROOT/runs/mode-env.mode") == review ]] \
    || fail 'a local environment file flipped a caller-named review to implementer'
  # The other direction, on a RESUME: a host file that asks for implementer mode
  # cannot undo the record of a review. The launch stays a review and SAYS so.
  printf 'review\n' > "$CONTROLLER_ROOT/runs/mode-env-resume.mode"
  printf 'mode-env-resume-session\n' > "$CONTROLLER_ROOT/runs/mode-env-resume.thread"
  argv_reset
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker CONTROLLER_HOME="$home" \
    CONTROLLER_RUNS_DIR="$CONTROLLER_ROOT/runs" \
    CONTROLLER_OD_STATE="$ROOT/mode-env-resume-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=mode-env-resume-session FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" mode-env-resume resume mode-env-resume-session \
    > "$ROOT/mode-env-resume.launch.log" 2>&1 < /dev/null || rc=$?
  [[ $rc == 0 ]] || { cat "$ROOT/mode-env-resume.launch.log" >&2; fail 'a local environment file refused a review resume'; }
  assert_review_argv 'local env resume'
  contains "$ROOT/mode-env-resume.launch.log" 'is a recorded review run; the record decides' \
    'a host-file mode request against the record is announced, not obeyed'
  contains "$ROOT/mode-env-resume.launch.log" 'run mode review (record)' \
    'the resumed launch takes its mode from the record'
}

# Every check of the run mode in one place for the full run. The focused names
# above call the same functions one at a time, so a mutant is judged by the single
# case that covers the guard it weakened.
check_run_mode() {
  check_review_argv_single_definition
  check_mode_value_refused
  check_mode_extra_args_refused
  check_rule_file_missing_refused
  check_mode_not_written_on_refusal
  check_mode_rule_paragraph
  check_review_mode_argv
  check_mode_local_env_not_weaker
  check_mode_sticky_resume
  check_mode_resume_exact_id
  check_mode_resume_stream_mismatch
  check_mode_claimed_name_reread
  check_mode_session_thread_resume
  check_mode_stream_review_resume
  check_mode_session_record_resume
  check_mode_session_implementer_unchanged
  check_mode_record_missing_reads_review_evidence
  check_mode_review_material_unreadable
  check_implementer_mode_unchanged
}

pidns_probe_count() {
  local -a probe_args=()
  [[ -s $PI_PIDNS_PROBE_LOG ]] || { echo 0; return; }
  mapfile -d '' -t probe_args < "$PI_PIDNS_PROBE_LOG"
  echo "$(( ${#probe_args[@]} / 2 ))"
}

# The fixed extension must be selected on every pi invocation, while the
# wrapper preflight runs once before the run-name claim. A worktree settings
# file that names a bare shell or another extension cannot replace the CLI
# selection because each implementer launch uses --no-extensions plus -e.
check_pidns_launch_contract() {
  local before after runs=$CONTROLLER_ROOT/runs
  local extension=$TEST_CONTROLLER/pi-pidns-extension.js

  clean_worktree
  : > "$PI_ARGV_LOG"
  argv_reset
  : > "$PI_PIDNS_PROBE_LOG"
  wait_launch pidns-first deepseek-pi-volker pi-success /dev/null
  argv_has --no-extensions 'a first implementer launch disables extension discovery'
  argv_has_pair -e "$extension" 'a first implementer launch loads the fixed PID namespace extension'
  [[ $(pidns_probe_count) == 1 ]] || fail 'the first implementer launch did not probe its shell wrapper exactly once'

  argv_reset
  wait_launch pidns-resume deepseek-pi-volker pi-success /dev/null
  argv_reset
  wait_launch pidns-resume deepseek-pi-volker pi-success /dev/null resume pidns-resume-session
  argv_has --no-extensions 'a resumed implementer launch disables extension discovery'
  argv_has_pair -e "$extension" 'a resumed implementer launch loads the fixed PID namespace extension'

  argv_reset
  : > "$PI_ARGV_LOG"
  before=$(pi_invocations)
  launch_sequence pidns-continuation deepseek-pi-volker 'pi-empty-completion pi-success'
  after=$(pi_invocations)
  [[ $(( after - before )) == 2 ]] || fail 'the PID namespace continuation fixture did not produce two pi invocations'
  [[ $(grep -Fc -- "--no-extensions -e $extension" "$PI_ARGV_LOG") == 2 ]] \
    || fail 'the initial implementer run and its automatic continuation did not both carry the fixed extension'

  mkdir -p "$WORKTREE/.pi"
  cat > "$WORKTREE/.pi/settings.json" <<'JSON'
{"shellPath":"/bin/bash","extensions":["./unsafe-extension.js"]}
JSON
  argv_reset
  wait_launch pidns-project-settings deepseek-pi-volker pi-success /dev/null
  argv_has --no-extensions 'worktree settings cannot enable discovered extensions'
  argv_has_pair -e "$extension" 'worktree shellPath cannot replace the explicit extension backend'
  rm -rf -- "$WORKTREE/.pi"

  # A review continues to use only pi-review-argv. It neither probes nor loads
  # the implementer shell extension, and its read-only tool set stays intact.
  argv_reset
  : > "$PI_PIDNS_PROBE_LOG"
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker PI_RUN_MODE=review \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=pidns-review-session FAKE_PI_RC=0 \
    "$SRC" "$WORKTREE" "$PROMPT" pidns-review > "$ROOT/pidns-review.launch.log" 2>&1 < /dev/null \
    || { cat "$ROOT/pidns-review.launch.log" >&2; fail 'the PID namespace contract changed the review launch'; }
  assert_review_argv 'review remains unchanged'
  argv_has --no-extensions 'review keeps extension discovery disabled'
  argv_absent -e 'review argv never loads an extension with -e'
  argv_absent "$extension" 'review does not load the implementer PID namespace extension'
  [[ $(pidns_probe_count) == 0 ]] || fail 'review mode ran an implementer shell preflight'
}

check_pidns_preflight_refused() {
  local before runs=$CONTROLLER_ROOT/runs
  clean_worktree
  argv_reset
  : > "$PI_PIDNS_PROBE_LOG"
  before=$(pi_invocations)
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    FAKE_PIDNS_WRAPPER_RC=125 FAKE_PI_SESSION_ID=pidns-preflight-session \
    "$SRC" "$WORKTREE" "$PROMPT" pidns-preflight-failed
  contains "$ROOT/focused-command.out" 'pi implementer launch refused before run name claim: PID namespace preflight failed' \
    'a failed namespace preflight refuses the implementer launch'
  [[ $(pi_invocations) == "$before" ]] || fail 'pi started after the PID namespace preflight failed'
  [[ ! -e $runs/pidns-preflight-failed.lock ]] || fail 'a failed PID namespace preflight claimed the run name'
  [[ ! -e $runs/pidns-preflight-failed.mode ]] || fail 'a failed PID namespace preflight wrote a run mode'
  [[ ! -e $runs/session-modes/pidns-preflight-session ]] || fail 'a failed PID namespace preflight wrote a session mode'

  # The same ordering applies to a manual implementer resume: preserve its
  # existing name mode and thread, but do not claim the name or write a session
  # mode while the required shell wrapper fails preflight.
  local resume_session=pidns-resume-preflight-session
  printf 'implementer\n' > "$runs/pidns-resume-preflight.mode"
  printf '%s\n' "$resume_session" > "$runs/pidns-resume-preflight.thread"
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    FAKE_PIDNS_WRAPPER_RC=125 FAKE_PI_SESSION_ID="$resume_session" \
    "$SRC" "$WORKTREE" "$PROMPT" pidns-resume-preflight resume "$resume_session"
  [[ $(pi_invocations) == "$before" ]] || fail 'pi started after the resumed PID namespace preflight failed'
  [[ ! -e $runs/pidns-resume-preflight.lock ]] || fail 'a refused resumed preflight claimed the run name'
  [[ $(cat "$runs/pidns-resume-preflight.mode") == implementer ]] \
    || fail 'a refused resumed preflight changed the existing run mode'
  [[ $(cat "$runs/pidns-resume-preflight.thread") == "$resume_session" ]] \
    || fail 'a refused resumed preflight changed the existing thread record'
  [[ ! -e $runs/session-modes/$resume_session ]] || fail 'a refused resumed preflight wrote a session mode'

  mv -- "$TEST_CONTROLLER/pi-pidns-bash" "$TEST_CONTROLLER/pi-pidns-bash.saved"
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    FAKE_PI_SESSION_ID=pidns-wrapper-missing-session "$SRC" "$WORKTREE" "$PROMPT" pidns-wrapper-missing
  mv -- "$TEST_CONTROLLER/pi-pidns-bash.saved" "$TEST_CONTROLLER/pi-pidns-bash"
  contains "$ROOT/focused-command.out" 'pi PID namespace wrapper is missing or not executable' \
    'an absent shell wrapper refuses before claim'
  [[ $(pi_invocations) == "$before" ]] || fail 'pi started while its PID namespace wrapper was missing'
  [[ ! -e $runs/pidns-wrapper-missing.lock ]] || fail 'a missing PID namespace wrapper claimed the run name'
  [[ ! -e $runs/pidns-wrapper-missing.mode ]] || fail 'a missing PID namespace wrapper wrote a run mode'
  [[ ! -e $runs/session-modes/pidns-wrapper-missing-session ]] \
    || fail 'a missing PID namespace wrapper wrote a session mode'

  mv -- "$TEST_CONTROLLER/pi-pidns-extension.js" "$TEST_CONTROLLER/pi-pidns-extension.js.saved"
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    FAKE_PI_SESSION_ID=pidns-extension-missing-session "$SRC" "$WORKTREE" "$PROMPT" pidns-extension-missing
  mv -- "$TEST_CONTROLLER/pi-pidns-extension.js.saved" "$TEST_CONTROLLER/pi-pidns-extension.js"
  contains "$ROOT/focused-command.out" 'pi PID namespace extension is missing or unreadable' \
    'an absent namespace extension refuses before claim'
  [[ $(pi_invocations) == "$before" ]] || fail 'pi started while its PID namespace extension was missing'
  [[ ! -e $runs/pidns-extension-missing.lock ]] || fail 'a missing PID namespace extension claimed a run name'
  [[ ! -e $runs/pidns-extension-missing.mode ]] || fail 'a missing PID namespace extension wrote a run mode'
  [[ ! -e $runs/session-modes/pidns-extension-missing-session ]] \
    || fail 'a missing PID namespace extension wrote a session mode'
}

check_pidns_extension_args_refused() {
  local line run_mode attempt=0 runs=$CONTROLLER_ROOT/runs run_name session_id
  local -a extension_controls=(--extension --extension=/tmp/override.js -e --no-extensions -ne)
  for run_mode in implementer review; do
    for line in "${extension_controls[@]}"; do
      attempt=$(( attempt + 1 ))
      run_name=pidns-extra-$run_mode-$attempt
      session_id=pidns-extra-$run_mode-$attempt-session
      expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
        PI_RUN_MODE="$run_mode" PI_EXTRA_ARGS="$line" FAKE_PI_SESSION_ID="$session_id" \
        "$SRC" "$WORKTREE" "$PROMPT" "$run_name"
      if [[ $run_mode == review ]]; then
        contains "$ROOT/focused-command.out" "PI_EXTRA_ARGS may not set '$line' in review mode" \
          "review PI_EXTRA_ARGS refuses extension control '$line' through the shared classification"
      else
        contains "$ROOT/focused-command.out" 'PI_EXTRA_ARGS may not load or disable extensions in implementer mode' \
          "implementer PI_EXTRA_ARGS refuses extension control '$line' through the shared classification"
      fi
      [[ ! -e $runs/$run_name.lock && ! -e $runs/$run_name.mode ]] \
        || fail "the refused $run_mode extension argument '$line' claimed or wrote its run mode"
      [[ ! -e $runs/session-modes/$session_id ]] \
        || fail "the refused $run_mode extension argument '$line' wrote a session mode"
    done
  done
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
# The agent HOLDS until the check releases it (a marker file), so "the owner is
# still alive" is a state the check chooses, never a duration it hopes for.
start_owned_run() {
  # $1 run name; the agent HOLDS until the check creates "$run.release" (the
  # second argument is retained so a caller's shape is unchanged).
  local run=$1
  shift 2
  if [[ ${1:-} == resume && -n ${2:-} ]]; then seed_resume_session "$2"; fi
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    CONTROLLER_OD_STATE="$ROOT/ownership-$run-state.json" PI_LIVE=0 \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" \
    FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID="$run-session" FAKE_PI_RC=0 FAKE_PI_HOLD_UNTIL="$ROOT/$run.release" \
    "$SRC" "$WORKTREE" "$PROMPT" "$run" "$@" \
    > "$ROOT/$run.launch.log" 2>&1 < /dev/null &
  OWNED_PID=$!
  # Readiness is the run's own event stream, not an ownership record: the check
  # under test must not need the guard's file to exist to observe a running run.
  wait_for_stream "$run" || { cat "$ROOT/$run.launch.log" >&2; fail "wrapper for $run exited before it started its agent"; }
}

# Release a held agent; without it the wrapper never finishes.
release_owned_run() { : > "$ROOT/$1.release"; }

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

# A name-lock refusal must NOT overwrite the owner record of the owner it
# refused on. Two steps: (a) an owner exists and works; (b) a DIFFERENT
# invocation is refused for EVIDENCE (a `.done` from the first run, so its own
# preflight - not the live lock - is what refuses it), and the owner record still
# names the first owner. A later forced retire then archives the FIRST identity,
# never the refuser's.
check_refusal_keeps_owner_record() {
  local runs=$CONTROLLER_ROOT/runs first_pid=''
  mkdir -p "$runs"
  rm -f "$runs"/refusal-owner.*
  wait_launch refusal-owner deepseek-pi-volker pi-success /dev/null \
    || fail 'the owning run for the refusal check did not complete'
  first_pid=$(sed -n 's/^pid=\([0-9][0-9]*\).*/\1/p' "$runs/refusal-owner.lock" | head -n 1)
  [[ -n $first_pid ]] || fail 'the first run wrote no owner record'
  # The previous owner is dead now (its wrapper exited), so the name is claimable
  # again; the refusal that follows is the EVIDENCE preflight.
  PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
    FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
    FAKE_PI_SESSION_ID=refusal-second FAKE_PI_RC=0 PI_LIVE=0 \
    "$SRC" "$WORKTREE" "$PROMPT" refusal-owner \
    > "$ROOT/refusal-owner-second.log" 2>&1 < /dev/null \
    && fail 'a name with evidence was run without CONTROLLER_RUN_FORCE'
  contains "$ROOT/refusal-owner-second.log" 'controller run name already has evidence' \
    'the evidence preflight is what refused the second invocation'
  # The refuser's pid must NOT be in the record: the file still names the owner
  # whose evidence refuses the name.
  local recorded
  recorded=$(sed -n 's/^pid=\([0-9][0-9]*\).*/\1/p' "$runs/refusal-owner.lock" | head -n 1)
  [[ $recorded == "$first_pid" ]] \
    || fail "a refused invocation overwrote the owner record (was $first_pid, now ${recorded:-<none>})"
}

# CTL-23: a launch refused AFTER the claim (a bad route, blocked accounts, a
# missing credential file or key) leaves every record of the previous run exactly
# as it was - the owner record, the completion marker and the worktree, route and
# thread records - and a refused launch of a fresh name leaves nothing but the
# empty lock file the claim created. Otherwise job-check reads DIED for a name that
# never started, and a refused first launch leaves evidence that refuses the retry.
check_refusal_keeps_records() {
  local runs=$CONTROLLER_ROOT/runs snap=$ROOT/keep-records-snapshot name=keep-records
  local no_env_home=$ROOT/keep-no-env-home no_key_home=$ROOT/keep-no-key-home blocked_state=$ROOT/keep-records-blocked.json
  local record base left
  records_kept() {
    # Every record of the previous run is byte-identical and none was added.
    for record in "$snap"/*; do
      base=${record##*/}
      cmp -s -- "$record" "$runs/$base" || fail "a launch refused at $1 changed the previous run's $base"
    done
    left=$(find "$runs" -maxdepth 1 -name "$name.*" | wc -l)
    [[ $left == "$(find "$snap" -maxdepth 1 -type f | wc -l)" ]] \
      || fail "a launch refused at $1 left an extra record beside the previous run's ($left files)"
  }
  mkdir -p "$runs" "$no_env_home" "$no_key_home"
  : > "$no_key_home/.env.ai"
  rm -f -- "$runs/$name".*
  printf 'EXIT=0 OUTCOME=SUCCESS AUTORESUME=0\n' > "$runs/$name.done"
  printf '%s\n' "$WORKTREE" > "$runs/$name.wt"
  printf 'deepseek-pi-volker\n' > "$runs/$name.route"
  printf 'previous-session\n' > "$runs/$name.thread"
  printf '{"type":"session","id":"previous-session"}\n' > "$runs/$name.jsonl"
  printf 'pid=2147483646 started_epoch=1700000000 started_utc=2023-11-14T22:13:20Z pid_start_ticks=12345 boot_id=previous-owner\n' \
    > "$runs/$name.lock"
  rm -rf -- "$snap"
  mkdir -p "$snap"
  cp -a -- "$runs/$name".* "$snap/"
  python3 - "$blocked_state" <<'PY'
import json, sys
json.dump({
    "volker": {"last_used_epoch": 2, "blocked_until_epoch": 4_102_444_800},
    "github2": {"last_used_epoch": 1, "blocked_until_epoch": 4_102_444_800},
    "threads": {"previous-session": "volker"},
}, open(sys.argv[1], "w", encoding="utf-8"))
PY
  # Each refusal is judged on its own, earliest first, so a record written just
  # before one refusal is caught by that refusal's case and not by a later one.
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=unknown \
    "$SRC" "$WORKTREE" "$PROMPT" "$name" resume previous-session
  contains "$ROOT/focused-command.out" 'unknown PI_ROUTE=unknown' 'the unknown route is what refused the resume'
  records_kept 'unknown-route'
  expect_rc 3 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi CONTROLLER_OD_STATE="$blocked_state" \
    "$SRC" "$WORKTREE" "$PROMPT" "$name" resume previous-session
  contains "$ROOT/focused-command.out" 'both Open Design accounts are blocked' 'the blocked accounts are what refused the resume'
  records_kept 'blocked-accounts'
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$no_env_home" PI_ROUTE=deepseek-pi-volker \
    "$SRC" "$WORKTREE" "$PROMPT" "$name" resume previous-session
  contains "$ROOT/focused-command.out" 'requires the local' 'the missing credential file is what refused the resume'
  records_kept 'missing-credential-file'
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$no_key_home" PI_ROUTE=deepseek-pi-github2 \
    "$SRC" "$WORKTREE" "$PROMPT" "$name" resume previous-session
  contains "$ROOT/focused-command.out" 'OPENDESIGN_API_KEY_GITHUB2 is not set' 'the missing key is what refused the resume'
  records_kept 'missing-key'
  # A FORCED new launch that is then refused keeps everything too: the retirement
  # waits until the launch is real, so nothing moves into retired/ on a refusal.
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=unknown CONTROLLER_RUN_FORCE=1 \
    "$SRC" "$WORKTREE" "$PROMPT" "$name"
  contains "$ROOT/focused-command.out" 'unknown PI_ROUTE=unknown' 'the unknown route is what refused the forced launch'
  records_kept 'forced-unknown-route'
  [[ ! -e $runs/retired/$name ]] || fail 'a forced launch refused at the route retired the previous records'
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$no_env_home" PI_ROUTE=deepseek-pi-volker CONTROLLER_RUN_FORCE=1 \
    "$SRC" "$WORKTREE" "$PROMPT" "$name"
  contains "$ROOT/focused-command.out" 'requires the local' 'the missing credential file is what refused the forced launch'
  records_kept 'forced-missing-credential-file'
  [[ ! -e $runs/retired/$name ]] || fail 'a forced launch refused at the credentials retired the previous records'
  # A refused launch of a name nobody ever ran: only the empty lock file remains.
  rm -f -- "$runs/keep-fresh".*
  expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$no_env_home" PI_ROUTE=deepseek-pi-volker \
    "$SRC" "$WORKTREE" "$PROMPT" keep-fresh
  [[ -e $runs/keep-fresh.lock && ! -s $runs/keep-fresh.lock ]] \
    || fail 'a refused launch of a fresh name wrote an owner record'
  left=$(find "$runs" -maxdepth 1 -name 'keep-fresh.*' | wc -l)
  [[ $left == 1 ]] || fail "a refused launch of a fresh name left evidence beside its lock ($left files)"
}

# An ACCEPTED resume is the other half of CTL-23: the records the refusals must
# not touch are the ones a real launch DOES write. While the resumed agent works,
# the lock names THIS wrapper (not the dead previous owner), the previous run's
# `.done` is gone (so no reader reports the old exit as this run's) and `.thread`
# is the session being resumed.
check_accepted_resume_records() {
  local runs=$CONTROLLER_ROOT/runs name=accepted-resume recorded=''
  mkdir -p "$runs"
  rm -f -- "$runs/$name".* "$ROOT/$name.release"
  printf 'EXIT=0 OUTCOME=SUCCESS AUTORESUME=0\n' > "$runs/$name.done"
  printf 'previous-session\n' > "$runs/$name.thread"
  printf 'pid=2147483646 started_epoch=1700000000 started_utc=2023-11-14T22:13:20Z pid_start_ticks=12345 boot_id=previous-owner\n' \
    > "$runs/$name.lock"
  start_owned_run "$name" 6 resume "$name-session"
  recorded=$(sed -n 's/^pid=\([0-9][0-9]*\).*/\1/p' "$runs/$name.lock" | head -n 1)
  [[ $recorded == "$OWNED_PID" ]] \
    || { stop_owned_run TERM; fail "an accepted resume did not record its own pid as the owner (lock names ${recorded:-<none>}, wrapper is $OWNED_PID)"; }
  [[ ! -e $runs/$name.done ]] \
    || { stop_owned_run TERM; fail 'an accepted resume left the previous run completion marker readable while its agent works'; }
  [[ $(cat "$runs/$name.thread" 2>/dev/null) == "$name-session" ]] \
    || { stop_owned_run TERM; fail 'an accepted resume did not record the resumed session as its thread'; }
  release_owned_run "$name"
  wait "$OWNED_PID" || fail 'the accepted resume failed'
  contains "$runs/$name.done" 'OUTCOME=SUCCESS' 'the accepted resume wrote its own completion marker'
}

check_run_name_ownership() {
  local first='' second=0 retired=
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
  # (3) A different run name proceeds while the first is still working: the owner
  # is HELD (its release file is not created), so this is not a race.
  wait_launch other-run deepseek-pi-volker pi-success /dev/null
  [[ -e $CONTROLLER_ROOT/runs/other-run.done ]] || { stop_owned_run TERM; fail 'a different run name was blocked by the first'; }
  # The owner has been held through every case above; release it so its own
  # wrapper exits normally and its `.done` exists for the retirement below.
  release_owned_run owned-run
  wait "$OWNED_PID" 2>/dev/null || true
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
  start_owned_run killed-run 0
  wait_for_stream killed-run || { stop_owned_run KILL; fail 'the killed-run agent never started'; }
  # The agent's own pid, so the claim below can be judged on EVIDENCE: the
  # reclaim must not have waited for it, so it is still alive afterwards.
  agent_pid=$(pgrep -P "$OWNED_PID" -f 'bash' 2>/dev/null | head -n 1 || true)
  stop_owned_run KILL
  name_lock_is_free killed-run || fail 'a SIGKILLed wrapper left its run-name lock held'
  CONTROLLER_RUN_FORCE=1 wait_launch killed-run deepseek-pi-volker pi-success /dev/null \
    || fail 'a SIGKILLed name could not be reclaimed'
  # Still held (its release file was never created), so this is the fact that
  # matters: the name was reclaimed WHILE the agent still ran. No clock involved.
  [[ -n ${agent_pid:-} ]] || fail 'the killed run had no agent process to judge the reclaim against'
  kill -0 "$agent_pid" 2>/dev/null \
    || fail 'the reclaim waited for the killed wrapper agent child to exit'
  contains "$ROOT/killed-run.launch.log" 'retired' 'the forced reclaim reports what it retired'
  # Release the stranded agent so it cannot outlive the check.
  release_owned_run killed-run
}

# The owner record decides liveness, and a DEAD wrapper with a LIVE agent is
# its own state: the controller needs the agent's pid (and the pid of what that
# agent is running), because nothing else can name what to stop.
check_job_status_dead_wrapper() {
  local runs=$ROOT/dead-wrapper-runs spawn_pid tool_pid=''
  rm -rf "$runs"; mkdir -p "$runs" "$ROOT/status-home"
  # A long-lived "agent" whose cwd IS the run's worktree (that is how the reader
  # recognises it, structurally) and which starts a "tool" child. The wrapper
  # script knows the EXACT pids of both at every exit path, so no check here
  # kills by pattern (never `pkill -f sleep 120`).
  ( cd -- "$WORKTREE" && exec -a pi python3 -c 'import subprocess, sys, time
child = subprocess.Popen(["sleep", "120"])
print(child.pid, flush=True)
time.sleep(120)' > "$ROOT/dead-wrapper-tool.pid" ) &
  spawn_pid=$!
  # Read the tool's exact pid back from the record the agent wrote.
  local waited=0
  while [[ ! -s $ROOT/dead-wrapper-tool.pid ]] && (( waited < 200 )); do
    sleep 0.05
    waited=$(( waited + 1 ))
  done
  [[ -s $ROOT/dead-wrapper-tool.pid ]] || { kill -9 "$spawn_pid" 2>/dev/null; fail 'the dead-wrapper agent never recorded its tool pid'; }
  tool_pid=$(cat "$ROOT/dead-wrapper-tool.pid")
  [[ $tool_pid =~ ^[0-9]+$ ]] || { kill -9 "$spawn_pid" 2>/dev/null; fail 'the dead-wrapper tool pid is not a number'; }
  printf '%s\n' "$WORKTREE" > "$runs/dead-wrapper.wt"
  # The run stream the dead sidecar stopped refreshing: its mtime is the newest
  # event fact, and it is NEWER than the sidecar's last tick.
  printf '{"type":"session","id":"dead-wrapper-thread"}\n' > "$runs/dead-wrapper.jsonl"
  python3 - "$runs/dead-wrapper.live" "$spawn_pid" <<'PY'
import json, sys, time
from pathlib import Path
now = int(time.time())
body = {
    "run": "dead-wrapper", "wrapper_pid": 2147483646, "updated_epoch": now,
    "last_event_epoch": now - 3600, "tool_in_flight": "bash",
    "tool_in_flight_started_epoch": now - 300, "assistant_turns": 3,
    "tool_calls": 9, "latest_total_tokens": 100, "latest_total_tokens_last_report": 100,
    "tool_in_flight_command": "sleep 600", "steering_children": [],
    "progress_epoch": None, "progress_baseline_epoch": None, "progress_reason": "unread",
    "commits_ahead": 0, "uncommitted_files": 0, "last_commit_epoch": now - 600,
}
Path(sys.argv[1]).write_text(json.dumps(body), encoding="utf-8")
PY
  # The wrapper pid is gone, so the record says so - and the agent it started is
  # alive in the run's worktree.
  printf 'pid=%s started_epoch=%s started_utc=%s pid_start_ticks=12345 boot_id=%s\n' \
    2147483646 1700000000 2023-11-14T22:13:20Z \
    "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || printf '%s' '-')" \
    > "$runs/dead-wrapper.lock"
  CONTROLLER_HOME="$ROOT/status-home" CONTROLLER_RUNS_DIR="$runs" CONTROLLER_REPO="$WORKTREE" \
    JOB_STATUS_STALL_SECONDS=600 JOB_STATUS_HUNG_TOOL_SECONDS=1800 \
    "$JOB_STATUS" dead-wrapper > "$ROOT/dead-wrapper.out" 2>&1 \
    || { kill -9 "$spawn_pid" 2>/dev/null; cat "$ROOT/dead-wrapper.out" >&2; fail 'job-status failed on a dead wrapper with a live agent'; }
  grep -qF 'dead-wrapper: WRAPPER-DEAD' "$ROOT/dead-wrapper.out" \
    || { kill -9 "$spawn_pid" 2>/dev/null; cat "$ROOT/dead-wrapper.out" >&2; fail 'a dead wrapper with a live agent is not WRAPPER-DEAD'; }
  grep -qF 'wrapper-dead-agent-alive' "$ROOT/dead-wrapper.out" \
    || { kill -9 "$spawn_pid" 2>/dev/null; cat "$ROOT/dead-wrapper.out" >&2; fail 'the row does not say the agent is alive although the wrapper died'; }
  grep -qF "agent=$spawn_pid(" "$ROOT/dead-wrapper.out" \
    || { kill -9 "$spawn_pid" 2>/dev/null; cat "$ROOT/dead-wrapper.out" >&2; fail "the row does not name the orphaned agent pid ($spawn_pid)"; }
  grep -qE 'tool=[0-9]+' "$ROOT/dead-wrapper.out" \
    || { kill -9 "$spawn_pid" 2>/dev/null; cat "$ROOT/dead-wrapper.out" >&2; fail 'the row does not name what the orphaned agent is running'; }
  # The stale sidecar's own `last_event_epoch` is one hour old; the dead
  # wrapper means it can never be refreshed, so the reader falls back to the run
  # stream's mtime rather than reporting the dead sidecar's last tick forever.
  python3 - "$ROOT/dead-wrapper.out" <<'PY' || { kill -9 "$spawn_pid" 2>/dev/null; fail 'the dead sidecar stale event age was reported as the newest event'; }
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
match = re.search(r"last-event=([0-9]+)s\((stream)\)", text)
assert match, text
assert int(match.group(1)) != 3600, text
PY
  # A RECYCLED wrapper pid: the number exists (it is this test process) but its
  # kernel start ticks differ from the record, so it is not this run's wrapper.
  printf 'pid=%s started_epoch=%s started_utc=%s pid_start_ticks=1 boot_id=%s\n' \
    "$$" "$(date -u +%s)" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || printf '%s' '-')" \
    > "$runs/dead-wrapper.lock"
  python3 - "$runs/dead-wrapper.live" "$$" <<'PY'
import json, sys
from pathlib import Path
body = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
body["wrapper_pid"] = int(sys.argv[2])
Path(sys.argv[1]).write_text(json.dumps(body), encoding="utf-8")
PY
  CONTROLLER_HOME="$ROOT/status-home" CONTROLLER_RUNS_DIR="$runs" CONTROLLER_REPO="$WORKTREE" \
    JOB_STATUS_STALL_SECONDS=600 JOB_STATUS_HUNG_TOOL_SECONDS=1800 \
    "$JOB_STATUS" dead-wrapper > "$ROOT/recycled-wrapper.out" 2>&1 \
    || { kill -9 "$spawn_pid" 2>/dev/null; cat "$ROOT/recycled-wrapper.out" >&2; fail 'job-status failed on a recycled wrapper pid'; }
  if grep -qF 'recycled-wrapper: ACTIVE' "$ROOT/recycled-wrapper.out"; then
    kill -9 "$spawn_pid" 2>/dev/null
    fail 'a recycled wrapper pid read as ACTIVE'
  fi
  grep -qF 'recycled' "$ROOT/recycled-wrapper.out" \
    || { kill -9 "$spawn_pid" 2>/dev/null; cat "$ROOT/recycled-wrapper.out" >&2; fail 'a recycled wrapper pid is not named as recycled'; }
  kill -9 "$spawn_pid" 2>/dev/null || true
  wait "$spawn_pid" 2>/dev/null || true
  kill -9 "$tool_pid" 2>/dev/null || true
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
    default-thinking) check_pi_default_thinking_max ;;
    pidns-argv) check_pidns_launch_contract ;;
    pidns-preflight) check_pidns_preflight_refused ;;
    pidns-extra-args) check_pidns_extension_args_refused ;;
    xtrace-key) check_xtrace_key ;;
    outcome-error) check_outcome_error ;;
    outcome-length) clean_worktree; check_outcome_length ;;
    outcome-tool-call) clean_worktree; check_outcome_tool_call_no_text ;;
    outcome-text-and-call) clean_worktree; check_outcome_text_and_tool_call ;;
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
          wait_launch_sequence focus-empty deepseek-pi-volker \
            "pi-empty-completion pi-whitespace-only"
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
    signal-guard-first-run) check_signal_guard_first_run ;;
    signal-guard-resume) check_signal_guard_resume ;;
    signal-guard-continuation) check_signal_guard_continuation ;;
    signal-guard-fail-closed) check_signal_guard_fail_closed ;;
    signal-guard-not-switchable) check_signal_guard_not_switchable ;;
    signal-guard-shim-syntax) check_signal_guard_shim_syntax ;;
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
    live-wrapper)
      # Only the wrapper-driven live sidecar check (stream consumed, tool in flight).
      check_live_snapshot
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
      check_live_progress
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
    status-dead-wrapper) check_job_status_dead_wrapper ;;
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
        "$SRC" "$WORKTREE" "$PROMPT" focused-session resume '../invalid-session'
      contains "$ROOT/focused-command.out" 'usage: ' 'invalid resume session id is refused'
      expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi \
        "$SRC" "$WORKTREE" "$PROMPT" focused-jsonl-session resume 'session.jsonl'
      contains "$ROOT/focused-command.out" 'usage: ' 'a resume id ending in .jsonl is refused'
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
    refusal-own-record) check_refusal_keeps_owner_record ;;
    refusal-keeps-records) check_refusal_keeps_records ;;
    accepted-resume-records) check_accepted_resume_records ;;
    ownership-force) check_run_name_ownership ;;
    ownership-release) check_run_name_ownership ;;
    # --- the run mode (P-plat-26c): one focused name per refusal ---
    review-argv) clean_worktree; check_review_mode_argv ;;
    implementer-mode) check_implementer_mode_unchanged ;;
    review-argv-definition) check_review_argv_single_definition ;;
    mode-value) check_mode_value_refused ;;
    mode-extra-args) check_mode_extra_args_refused ;;
    mode-sticky) clean_worktree; check_mode_sticky_resume ;;
    mode-resume-exact) clean_worktree; check_mode_resume_exact_id ;;
    mode-resume-mismatch) clean_worktree; check_mode_resume_stream_mismatch ;;
    mode-claimed-name-reread) clean_worktree; check_mode_claimed_name_reread ;;
    mode-session-thread) clean_worktree; check_mode_session_thread_resume ;;
    mode-stream-review) clean_worktree; check_mode_stream_review_resume ;;
    mode-session-record) clean_worktree; check_mode_session_record_resume ;;
    mode-session-implementer) clean_worktree; check_mode_session_implementer_unchanged ;;
    mode-evidence) clean_worktree; check_mode_record_missing_reads_review_evidence ;;
    mode-review-material-unreadable) clean_worktree; check_mode_review_material_unreadable ;;
    mode-rules-file) check_rule_file_missing_refused ;;
    mode-local-env) check_mode_local_env_not_weaker ;;
    mode-prompt|mode-prompt-review)
      # The rule paragraph is appended to EVERY prompt of the mode, and the review
      # one says the opposite of the implementer one among things an agent may do.
      clean_worktree
      argv_reset
      PI_LIVE=0 wait_launch_sequence mode-prompt-review deepseek-pi-volker pi-success
      assert_mode_prompt implementer 'implementer prompt'
      clean_worktree
      argv_reset
      PI_RUN_MODE=review PI_LIVE=0 \
        wait_launch_sequence mode-prompt-review-run deepseek-pi-github2 pi-success
      assert_mode_prompt review 'review prompt'
      ;;
    mode-record)
      # The mode is a name-owned record like the route: written by a launch that
      # is real, retired with the other records on a forced reuse, never inherited
      # by a new run of the same name.
      clean_worktree
      PI_RUN_MODE=review PI_LIVE=0 \
        wait_launch_sequence mode-record deepseek-pi-volker pi-success
      [[ $(cat "$CONTROLLER_ROOT/runs/mode-record.mode") == review ]] \
        || fail 'a review launch wrote no review mode record'
      CONTROLLER_RUN_FORCE=1 PI_RUN_MODE=implementer PI_LIVE=0 \
        wait_launch_sequence mode-record deepseek-pi-volker pi-success
      [[ $(cat "$CONTROLLER_ROOT/runs/mode-record.mode") == implementer ]] \
        || fail 'a forced reuse inherited the retired run mode'
      retired_mode=''
      retired_mode=$(find "$CONTROLLER_ROOT/runs/retired/mode-record" -mindepth 2 -maxdepth 2 \
        -name 'mode-record.mode' -print -quit)
      [[ -n $retired_mode ]] || fail 'a forced reuse did not retire the previous mode record'
      grep -qx review "$retired_mode" || fail 'the retired mode record is not the previous review'
      ;;
    mode-not-written-on-refusal)
      check_mode_not_written_on_refusal
      ;;
    mode-no-argv-file)
      # A review that cannot read its one definition is refused before the name
      # is claimed, instead of launching with a tool set nobody defined.
      before=$(pi_invocations)
      mutilated=$ROOT/mode-no-argv-tree
      rm -rf "$mutilated"; mkdir -p "$mutilated"
      ln -s "$(dirname -- "$SRC")"/* "$mutilated"/
      rm -f "$mutilated/pi-review-argv"
      rm -f "$mutilated/pi-shims"
      cp -a -- "$SHIM_DIR" "$mutilated/pi-shims"
      expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
        PI_RUN_MODE=review PI_LIVE=0 CONTROLLER_OD_STATE="$ROOT/mode-no-argv-state.json" \
        FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
        FAKE_PI_SESSION_ID=mode-no-argv-session FAKE_PI_RC=0 \
        "$mutilated/$(basename -- "$SRC")" "$WORKTREE" "$PROMPT" mode-no-argv
      contains "$ROOT/focused-command.out" 'the review argv file is missing or unreadable' \
        'a review launch without its argv definition is refused'
      [[ $(pi_invocations) == "$before" ]] || fail 'a review launched without its argv definition'
      [[ ! -e $CONTROLLER_ROOT/runs/mode-no-argv.lock ]] || fail 'a refused review claimed the run name'
      ;;
    mode-empty-argv|mode-lists-retired)
      # The review argv definition is emptied: that is refused too, because an
      # empty file would leave pi with its own default (bash, edit, write) tools.
      before=$(pi_invocations)
      mutilated=$ROOT/mode-empty-argv-tree
      rm -rf "$mutilated"; mkdir -p "$mutilated"
      ln -s "$(dirname -- "$SRC")"/* "$mutilated"/
      rm -f "$mutilated/pi-review-argv"; : > "$mutilated/pi-review-argv"
      rm -f "$mutilated/pi-shims"
      cp -a -- "$SHIM_DIR" "$mutilated/pi-shims"
      expect_rc 2 env PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
        PI_RUN_MODE=review PI_LIVE=0 CONTROLLER_OD_STATE="$ROOT/mode-empty-argv-state.json" \
        FAKE_FIXTURE_DIR="$SCRIPT_DIR/fixtures" FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" \
        FAKE_PI_SESSION_ID=mode-empty-argv-session FAKE_PI_RC=0 \
        "$mutilated/$(basename -- "$SRC")" "$WORKTREE" "$PROMPT" mode-empty-argv
      contains "$ROOT/focused-command.out" 'the review argv file is empty' \
        'an empty review argv definition is refused'
      [[ $(pi_invocations) == "$before" ]] || fail 'a review launched with an empty argv definition'
      [[ ! -e $CONTROLLER_ROOT/runs/mode-empty-argv.lock ]] \
        || fail 'a refused empty review argv claimed the run name'
      ;;
    mode-rule-paragraph)
      check_mode_rule_paragraph
      ;;
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
      prompt_starts_with "$ROOT/focused-prompt.md" 'prompt file trailing newline was lost'
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
      printf 'recorded-session\n' > "$CONTROLLER_ROOT/runs/focused-both-blocked.thread"
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
      contains "$ENV_SOURCE" 'PI_THINKING="max"' 'environment example documents Pi maximum thinking'
      contains "$README_SOURCE" 'OUTCOME=NO-REPORT' 'README documents the report outcome'
      contains "$README_SOURCE" "\`CONTEXT-FULL\`" 'README documents the context-full class'
      contains "$README_SOURCE" "\`TOOL-CALL-NO-TEXT\`" 'README documents the tool-call-no-text class'
      # shellcheck disable=SC2016 # the backticks are the DOCUMENT's literal text
      contains "$README_SOURCE" 'TEXT **and** a trailing tool call is `SUCCESS`' \
        'README states that text plus a trailing tool call is a success'
      # shellcheck disable=SC2016 # the backticks are the DOCUMENT's literal text
      contains "$DESIGN_SOURCE" 'Text **plus** a trailing tool call is `SUCCESS`' \
        'design guide states that text plus a trailing tool call is a success'
      contains "$CONSUMER_SOURCE" 'trailing tool call' \
        'consumer guide covers the text-plus-tool-call shape'
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
      contains "$README_SOURCE" "\`EFFORT\` field is Codex-only" \
        'README states that done-marker effort is Codex-only'
      contains "$CONSUMER_SOURCE" "Pi records no thinking level in its \`.done\` marker" \
        'consumer guide states that Pi records no thinking level'
      contains "$DESIGN_SOURCE" 'Continuation is bounded and class-specific' 'design guide explains the continuation bound'
      contains "$DESIGN_SOURCE" 'Why liveness is a file, not a poll of the process' 'design guide explains the liveness sidecar'
      contains "$README_SOURCE" 'job-status.sh' 'README documents the liveness read'
      contains "$CONSUMER_SOURCE" 'JOB_STATUS_STALL_SECONDS' 'consumer guide documents the stall threshold'
      contains "$README_SOURCE" "**\`LONG-TOOL\` and \`HUNG-TOOL\` are different words on purpose**" \
        'README states that the two words are separate'
      contains "$README_SOURCE" "\`HUNG-TOOL\` (the same age" 'README separates long-tool from hung-tool'
      contains "$README_SOURCE" 'progress evidence' 'README names the progress fact'
      contains "$README_SOURCE" 'SURVIVOR' \
        'README states that progress is a surviving process delta, not a set change'
      contains "$DESIGN_SOURCE" "Why progress is a SURVIVOR's delta" \
        'design guide explains why a set change is neither progress nor a baseline reset'
      contains "$CONSUMER_SOURCE" 'does not reset' \
        'consumer guide explains that churn does not reset the quiet clock'
      contains "$README_SOURCE" 'To steer a stuck tool, kill the exact child pid' \
        'README states the steering action'
      contains "$README_SOURCE" "\`steer=\` — never the wrapper" \
        'README forbids killing the wrapper to steer a tool'
      contains "$README_SOURCE" "\`since-commit=no-commits-yet\`" 'README qualifies the since-commit age'
      contains "$README_SOURCE" "\`DIED (wrapper pid <pid> gone, no .done)\`" 'README documents the died state'
      contains "$README_SOURCE" 'wrapper-dead-agent-alive' \
        'README documents the dead-wrapper/live-agent state'
      contains "$README_SOURCE" 'last-event=42s(stream)' \
        'README documents which clock the dead-wrapper event age comes from'
      contains "$README_SOURCE" 'thread: none' \
        'README documents the no-recoverable-thread case'
      contains "$DESIGN_SOURCE" 'wrapper-dead-agent-alive' \
        'design guide explains the dead-wrapper/live-agent state'
      # shellcheck disable=SC2016 # the backticks are the DOCUMENT's literal text
      contains "$DESIGN_SOURCE" 'A stream with no id says `thread: none`' \
        'design guide explains the thread recovery'
      contains "$CONSUMER_SOURCE" 'wrapper-dead-agent-alive' \
        'consumer guide shows the dead-wrapper/live-agent row'
      contains "$CONSUMER_SOURCE" 'thread: none' \
        'consumer guide shows the no-recoverable-thread case'
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
contains "$PI_ARGV_LOG" '--thinking max' 'pi configured default uses maximum thinking'
contains "$PI_ARGV_LOG" "--session-dir $HOME_ROOT/.pi/agent/sessions" 'pi uses its default session directory'
printf 'brief\n' > "$ROOT/prompt-with-newline.md"
PATH="$ROOT/bin:$PATH" HOME="$HOME_ROOT" PI_ROUTE=deepseek-pi-volker \
  FAKE_PI_STREAM="$SCRIPT_DIR/fixtures/pi-success.jsonl" FAKE_PI_SESSION_ID=prompt-newline-session \
  FAKE_PI_RC=0 "$SRC" "$WORKTREE" "$ROOT/prompt-with-newline.md" prompt-newline \
  > "$ROOT/prompt-newline.launch.log" 2>&1 < /dev/null || fail 'pi wrapper rejected a prompt file ending in a newline'
prompt_starts_with "$ROOT/prompt-with-newline.md" \
  'pi prompt argument lost the prompt file trailing newline'
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
check_pi_default_thinking_max
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
check_signal_guard_first_run
check_signal_guard_resume
check_signal_guard_continuation
check_signal_guard_fail_closed
check_signal_guard_not_switchable
check_signal_guard_shim_syntax
check_continue_prompt_file
check_retry_count_config
check_input_validation
check_retry_limit_validation
check_repo_not_a_worktree
check_job_status_validation
check_run_name_ownership
check_refusal_keeps_records
check_accepted_resume_records
check_run_mode
check_pidns_launch_contract
check_pidns_preflight_refused
check_pidns_extension_args_refused

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
CHANGES_SOURCE=${CHANGES_SOURCE:-$SCRIPT_DIR/../CHANGES.md}
contains "$README_SOURCE" "\`deepseek-pi\` | \`pi-impl.sh\` | Favored Open Design route" 'README names pi as the favored route'
contains "$README_SOURCE" 'docs/DESIGN-GUIDE.md#pi-open-design-route' 'README links the pi route design rationale'
contains "$README_SOURCE" 'docs/CONSUMERS.md#run-pi-on-open-design' 'README links the pi adoption guide'
contains "$DESIGN_SOURCE" 'Pi 1.5.0 can report a provider 402' 'design guide explains pi exit-zero 402 classification'
contains "$CONSUMER_SOURCE" "\`deepseek-pi\` is the favored Pi route" 'consumer guide names the favored pi route'
contains "$CONSUMER_SOURCE" 'PI_ROUTE=deepseek-pi' 'consumer guide has a copyable pi launch'
contains "$RULES_SOURCE" 'deepseek-pi-volker' 'controller rules list pinned pi routes'
# The signal guard is a capability an adopter must be able to find: its limit is
# documented where the pi route is, and the changelog records the change.
contains "$README_SOURCE" '**Signal guard.**' 'README documents the pi signal guard'
# shellcheck disable=SC2016 # the backticks are the DOCUMENT's literal text
contains "$README_SOURCE" 'bash builtin `kill`' 'README documents the signal guard known limit'
# The guard has all THREE documents, and each one is checked where the reader
# would look for it: WHAT (README), WHY (the design guide's own subsection) and
# HOW (the consumer guide's two launch refusals).
contains "$README_SOURCE" 'docs/DESIGN-GUIDE.md#why-the-pi-signal-guard-is-mechanical-not-advisory' \
  'README links the signal guard rationale'
contains "$DESIGN_SOURCE" 'Why the pi signal guard is mechanical, not advisory' \
  'design guide explains why the guard is a refusal and not a written ban'
contains "$DESIGN_SOURCE" 'Refuse rather than guess, on' \
  'design guide states the refuse-rather-than-guess decision'
contains "$DESIGN_SOURCE" 'not a security' \
  'design guide states that the guard is not a security boundary'
contains "$CONSUMER_SOURCE" 'signal guard' 'consumer guide tells an adopter a launch can refuse'
contains "$CONSUMER_SOURCE" 'pi signal guard is incomplete' \
  'consumer guide quotes the incomplete-guard refusal an adopter meets'
contains "$CONSUMER_SOURCE" 'pi signal guard cannot be enforced' \
  'consumer guide quotes the shadowing/relative refusal an adopter meets'
contains "$CONSUMER_SOURCE" 'must be an absolute path or start with ~/' \
  'consumer guide quotes the relative PI_CODING_AGENT_DIR refusal'
# Both cross-document anchors resolve: the heading text and the slug it is
# linked by are asserted together, so a renamed heading cannot leave a reader
# with a dead link (the slug of that heading is lower case, comma dropped,
# spaces to hyphens).
contains "$DESIGN_SOURCE" '### Why the pi signal guard is mechanical, not advisory' \
  'design guide has the heading the README links to'
contains "$CONSUMER_SOURCE" 'DESIGN-GUIDE.md#why-the-pi-signal-guard-is-mechanical-not-advisory' \
  'consumer guide links the guard rationale by the same anchor'
contains "$CHANGES_SOURCE" 'Signal guard for every pi run' 'changelog records the signal guard'
contains "$ENV_SOURCE" 'PI_THINKING="max"' 'local environment example documents the maximum thinking default'
if grep -Eq 'OPENDESIGN_API_KEY_[A-Z0-9_]+=' "$ENV_SOURCE"; then
  fail 'controller local environment example contains an API-key assignment'
fi

echo 'PASS pi-impl: detached launch, sessions, account alternation, 402 block/outcome, resume, stdin, key isolation, route and prompt guards'
