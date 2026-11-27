#!/usr/bin/env bash
set -uo pipefail
# The caller's ambient controller configuration is not this check's business:
# every check here builds its own temporary controller root, so an exported
# CONTROLLER_HOME / RUNS_DIR / LOGS_DIR / REPO / HOSTS_FILE must not decide where
# a check reads or writes. Relying on the caller ("run it with env -u") moved the
# same defect into the operator's shell, where it read as a product bug.
unset CONTROLLER_HOME CONTROLLER_RUNS_DIR CONTROLLER_LOGS_DIR CONTROLLER_REPO CONTROLLER_HOSTS_FILE
unset CODEX_ROUTE CODEX_HOME_OVERRIDE CODEX_HOME_LUNA CODEX_HOME_DEEPSEEK_VOLKER CODEX_HOME_DEEPSEEK_GITHUB2 CODEX_HOME_REVIEW CODEX_MODEL CODEX_EFFORT
unset CODEX_REVIEW_MODEL CODEX_REVIEW_EFFORT CODEX_SQLITE_HOME
unset CONTROLLER_OD_STATE CONTROLLER_OD_PREFER_EXPIRING CONTROLLER_OD_UNKNOWN_BLOCK_SECONDS OPENDESIGN_API_KEY_VOLKER OPENDESIGN_API_KEY_GITHUB2
unset OPENCODE_COMMAND OPENCODE_MODEL OPENCODE_PROVIDER
unset FAKE_CODEX_402 FAKE_CODEX_402_UNKNOWN FAKE_CODEX_THREAD_ID FAKE_CODEX_CORRUPT_STATE CODEX_RC FAKE_CODEX_DAEMON
unset FAKE_CODEX_HOLD_ON_CALL FAKE_CODEX_HOLD_UNTIL FAKE_CODEX_HOLD_CALL_COUNT FAKE_CODEX_HOLD_PID FAKE_CODEX_REASONING
unset FAKE_CODEX_RESUME_SEQUENCE FAKE_CODEX_RESUME_TEXT FAKE_CODEX_TEXT_REASONING_EMPTY FAKE_CODEX_TEXT_REASONING_HOLD
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-wrapper-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }
not_contains() { ! grep -qF -- "$2" "$1" || fail "$3 (unexpected: $2)"; }

mkdir -p "$ROOT/home" "$ROOT/bin" "$ROOT/controller" "$ROOT/repo" "$ROOT/venv/bin"
cat > "$ROOT/bin/api-check" <<'SH'
#!/usr/bin/env bash
echo called >> "$API_LOG"
exit "${API_RC:-0}"
SH
cat > "$ROOT/bin/fake-ssh" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$SSH_LOG"
echo ssh-ok
SH
cat > "$ROOT/bin/codex" <<'SH'
#!/usr/bin/env bash
printf 'HOME=%s\nSQLITE=%s\nARGS=%s\n' "$CODEX_HOME" "$CODEX_SQLITE_HOME" "$*" >> "$CODEX_LOG"
hold_this_call=0
if [[ -n ${FAKE_CODEX_HOLD_UNTIL:-} ]]; then
  hold_this_call=1
  if [[ -n ${FAKE_CODEX_HOLD_ON_CALL:-} ]]; then
    # A per-CALL hold: the run's earlier calls finish normally (so the automatic
    # continuation is actually reached) and ONLY the named call keeps working
    # until the check releases it. A duration-based hold cannot express that
    # without a race.
    count=$(cat "$FAKE_CODEX_HOLD_CALL_COUNT" 2>/dev/null || echo 0)
    count=$(( count + 1 ))
    printf '%s\n' "$count" > "$FAKE_CODEX_HOLD_CALL_COUNT"
    (( count == FAKE_CODEX_HOLD_ON_CALL )) || hold_this_call=0
  fi
fi
if [[ -n ${FAKE_CODEX_STREAM:-} ]]; then
  cat -- "$FAKE_CODEX_STREAM"
  exit 42
fi
if [[ ${FAKE_CODEX_RESUME_SEQUENCE:-} == reasoning-text && $1 == exec && $2 == resume ]]; then
  # A resumed invocation whose first turn is reasoning-only and whose second
  # completed turn carries the final assistant text.
  printf '{"type":"turn.started"}\n'
  printf '{"type":"item.completed","item":{"type":"reasoning","text":"thinking"}}\n'
  printf '{"type":"turn.completed"}\n'
  printf '{"type":"turn.started"}\n'
  printf '{"type":"item.completed","item":{"type":"agent_message","text":"%s"}}\n' "${FAKE_CODEX_RESUME_TEXT:-FINAL-B}"
  printf '{"type":"turn.completed"}\n'
  exit "${CODEX_RC:-0}"
fi
if [[ ${FAKE_CODEX_TEXT_REASONING_EMPTY:-0} == 1 ]]; then
  if [[ $1 == exec && $2 == resume ]]; then
    # The continuation produces no stream event at all and fails.
    printf '{"type":"error","message":"continuation failed"}\n' >&2
    exit 42
  fi
  # The primary child completes text and then ends its turn on reasoning, so
  # the wrapper reaches auto-continuation with real text already in the stream.
  printf '{"type":"thread.started","thread_id":"text-reasoning-thread"}\n'
  printf '{"type":"turn.started"}\n'
  printf '{"type":"item.completed","item":{"type":"agent_message","text":"ATTEMPT-1-FINAL"}}\n'
  printf '{"type":"item.completed","item":{"type":"reasoning","text":"trailing thought"}}\n'
  printf '{"type":"turn.completed"}\n'
  exit 0
fi
if [[ ${FAKE_CODEX_402:-0} == 1 ]]; then
  printf '{"error":{"code":"insufficient_balance","details":{"coding_plan_exhausted":true,"reset_at":"2099-11-17T11:44:44Z"}}}\n' >&2
  exit 42
fi
if [[ ${FAKE_CODEX_402_UNKNOWN:-} == missing ]]; then
  printf '{"error":{"code":"insufficient_balance","details":{"coding_plan_exhausted":true}}}\n' >&2
  exit 42
elif [[ ${FAKE_CODEX_402_UNKNOWN:-} == invalid ]]; then
  printf '{"error":{"code":"insufficient_balance","details":{"coding_plan_exhausted":true,"reset_at":"not-a-date"}}}\n' >&2
  exit 42
fi
if [[ -n ${FAKE_CODEX_THREAD_ID:-} ]]; then
  printf '{"type":"thread.started","thread_id":"%s"}\n' "$FAKE_CODEX_THREAD_ID"
fi
if [[ -n ${FAKE_CODEX_DAEMON:-} ]] && (( hold_this_call == 1 )); then
  # A process the agent LEAVES BEHIND: it inherits whatever descriptors the
  # agent had, survives the agent and the wrapper, and is exactly the shape that
  # made a killed wrapper strand its run name.
  setsid sleep 300 >/dev/null 2>&1 &
  printf '%s\n' "$!" > "$FAKE_CODEX_DAEMON"
fi
if (( hold_this_call == 1 )); then
  # A child that keeps working until the CHECK creates this file: "the agent is
  # still running" becomes a state the check chooses instead of a duration it
  # hopes the host will respect. A `sleep N` made every such case a race.
  printf '{"type":"thread.started","thread_id":"slow-thread"}\n'
  # The agent's own pid, published once it is running: the check proves the
  # agent is still alive after its wrapper was killed.
  if [[ -n ${FAKE_CODEX_HOLD_PID:-} ]]; then printf '%s\n' "$BASHPID" > "$FAKE_CODEX_HOLD_PID"; fi
  waited=0
  while [[ ! -e $FAKE_CODEX_HOLD_UNTIL ]]; do
    sleep 0.05
    waited=$(( waited + 1 ))
    (( waited < 6000 )) || break
  done
  exit "${CODEX_RC:-0}"
fi
if [[ ${FAKE_CODEX_TEXT_REASONING_HOLD:-0} == 1 ]]; then
  if [[ $1 == exec && $2 == resume ]]; then
    # The first continuation ends reasoning-only, so the wrapper starts a
    # second continuation. That second call is held by the generic hold block.
    printf '{"type":"turn.started"}\n'
    printf '{"type":"item.completed","item":{"type":"reasoning","text":"still thinking"}}\n'
    printf '{"type":"turn.completed"}\n'
    exit 0
  fi
  printf '{"type":"thread.started","thread_id":"held-source-thread"}\n'
  printf '{"type":"turn.started"}\n'
  printf '{"type":"item.completed","item":{"type":"agent_message","text":"ATTEMPT-1-FINAL"}}\n'
  printf '{"type":"item.completed","item":{"type":"reasoning","text":"trailing thought"}}\n'
  printf '{"type":"turn.completed"}\n'
  exit 0
fi
if [[ -n ${FAKE_CODEX_SLEEP:-} ]]; then
  # A slow child, so a test can observe a run that is still working.
  printf '{"type":"thread.started","thread_id":"slow-thread"}\n'
  sleep "$FAKE_CODEX_SLEEP"
  exit "${CODEX_RC:-0}"
fi
if [[ ${FAKE_CODEX_REASONING:-0} == 1 ]]; then
  # A completed turn that ends on a reasoning item: what the automatic
  # continuation is defined to continue. No clock involved. A thread id is
  # printed because the continuation loop must have one to resume - without it
  # the wrapper stops at "no thread id" and never reaches its exec site.
  printf '{"type":"thread.started","thread_id":"%s"}\n' "${FAKE_CODEX_THREAD_ID:-autoresume-thread}"
  printf '{"type":"turn.started"}\n'
  printf '{"type":"item.completed","item":{"type":"reasoning","text":"thinking"}}\n'
  printf '{"type":"turn.completed"}\n'
  exit "${CODEX_RC:-0}"
fi
printf '{"type":"item.completed","item":{"type":"agent_message","text":"complete"}}\n'
if [[ -n ${FAKE_CODEX_CORRUPT_STATE:-} ]]; then printf '{' > "$FAKE_CODEX_CORRUPT_STATE"; fi
exit "${CODEX_RC:-0}"
SH
cat > "$ROOT/bin/opencode" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$OPENCODE_LOG"
printf '{"text":"opencode-complete"}\n'
SH
cat > "$ROOT/venv/bin/cmru" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" > "$CMRU_LOG"
exit "${CMRU_RC:-0}"
SH
cat > "$ROOT/bin/docker" <<'SH'
#!/usr/bin/env bash
if [[ $1 == save ]]; then
  echo image-stream
  exit "${DOCKER_SAVE_RC:-0}"
fi
echo local-image
SH
cat > "$ROOT/bin/fake-hx" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$2" >> "$HX_LOG"
if [[ $2 == 'gunzip | docker load' ]]; then
  cat >/dev/null
  exit "${HX_LOAD_RC:-0}"
fi
echo remote-image
SH
chmod +x "$ROOT/bin/api-check" "$ROOT/bin/fake-ssh" "$ROOT/bin/codex" "$ROOT/bin/opencode" \
  "$ROOT/venv/bin/cmru" "$ROOT/bin/docker" "$ROOT/bin/fake-hx"
export API_LOG=$ROOT/api.log SSH_LOG=$ROOT/ssh.log CODEX_LOG=$ROOT/codex.log OPENCODE_LOG=$ROOT/opencode.log CMRU_LOG=$ROOT/cmru.log HX_LOG=$ROOT/hx.log
HX_WRAPPER=${HX_WRAPPER:-$SCRIPT_DIR/../hx.sh}
RELEASE_SCRIPT=${RELEASE_SCRIPT:-$SCRIPT_DIR/../release-from-wt.sh}
PUSH_IMAGE_SCRIPT=${PUSH_IMAGE_SCRIPT:-$SCRIPT_DIR/../push-image.sh}
CODEX_IMPL_SCRIPT=${CODEX_IMPL_SCRIPT:-$SCRIPT_DIR/../codex-impl.sh}
OPENCODE_IMPL_SCRIPT=${OPENCODE_IMPL_SCRIPT:-$SCRIPT_DIR/../opencode-impl.sh}
README_SOURCE=${README_SOURCE:-$SCRIPT_DIR/../README.md}
RULES_SOURCE=${RULES_SOURCE:-$SCRIPT_DIR/../rules.md}
CHANGES_SOURCE=${CHANGES_SOURCE:-$SCRIPT_DIR/../CHANGES.md}
BACKLOG_SOURCE=${BACKLOG_SOURCE:-$SCRIPT_DIR/../KNOWN_ISSUES_TODO_BACKLOG.md}
contains "$README_SOURCE" "\`CODEX_HOME_OVERRIDE\` wins on Luna and an explicit \`CODEX_ROUTE=sol\`" \
  'README states that CODEX_HOME_OVERRIDE applies to Luna and explicit Sol runs'
contains "$RULES_SOURCE" 'normal and signal exits' \
  'rules state that final text is derived on normal and signal exits'
not_contains "$RULES_SOURCE" 'may be stale' 'rules no longer describe stale resumed final text'
contains "$CHANGES_SOURCE" 'unique run names with resumed runs verified from git' \
  'changelog preserves the historical CTL-50 rule'
contains "$BACKLOG_SOURCE" 'resumed runs verified from git until CTL-38' \
  'backlog preserves the historical CTL-50 rule'

python3 - "$ROOT/controller/hosts.local.json" "$ROOT/bin" <<'PY'
import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({"hosts": {"runner-a": {
    "ssh": [str(pathlib.Path(sys.argv[2]) / "fake-ssh"), "runner-a"],
    "api_check": [str(pathlib.Path(sys.argv[2]) / "api-check")],
    "guard": "test $(hostname -s) = runner-a",
    "queue_lanes": []
}}}))
PY

PATH="$ROOT/bin:$PATH" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  API_LOG="$API_LOG" SSH_LOG="$SSH_LOG" \
  "$HX_WRAPPER" runner-a 'echo remote-ok' > "$ROOT/hx.out" 2>&1 || fail 'identity-checked SSH call failed'
contains "$API_LOG" called 'provider identity runs on every SSH call'
contains "$SSH_LOG" 'runner-a' 'configured ssh argv is used'
# shellcheck disable=SC2016
contains "$SSH_LOG" 'test $(hostname -s) = runner-a' 'remote guard is included in the command'
rm -f -- "$ROOT/ssh.log"
if PATH="$ROOT/bin:$PATH" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  API_LOG="$API_LOG" SSH_LOG="$SSH_LOG" API_RC=1 \
  "$HX_WRAPPER" runner-a 'echo must-not-run' > "$ROOT/hx-fail.out" 2>&1; then fail 'failed provider identity was accepted'; fi
[[ ! -e $ROOT/ssh.log ]] || fail 'SSH ran after provider identity refusal'

mkdir -p "$ROOT/worktree"
printf 'brief\n' > "$ROOT/brief.md"

check_ctl38_last_text_all_paths() {
  local runs=$ROOT/controller/runs rc=0

  # Manual resume: an older FINAL-A is replaced by the final completed agent
  # message of this invocation, even though the resumed stream starts with a
  # reasoning-only turn.
  printf 'FINAL-A' > "$runs/ctl38-resume.last.txt"
  PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
    CODEX_ROUTE=luna CODEX_HOME_LUNA="$ROOT/codex-luna" CODEX_SQLITE_HOME="$ROOT/sqlite" \
    CODEX_LOG="$ROOT/ctl38-resume.log" CODEX_AUTO_CONTINUE=0 FAKE_CODEX_RESUME_SEQUENCE=reasoning-text \
    "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" ctl38-resume resume ctl38-thread \
    > "$ROOT/ctl38-resume.out" 2>&1 || rc=$?
  [[ $rc == 0 ]] || { cat "$ROOT/ctl38-resume.out" >&2; fail 'CTL-38 resume failed'; }
  printf 'FINAL-B' > "$ROOT/ctl38-resume.expected"
  cmp -s "$ROOT/ctl38-resume.expected" "$runs/ctl38-resume.last.txt" \
    || fail 'CTL-38 resume did not derive exactly FINAL-B from its stream'
  [[ $(cat "$runs/ctl38-resume.done") == 'EXIT=0 OUTCOME=CODE-EXIT AUTORESUME=0 EFFORT=xhigh' ]] \
    || fail 'CTL-38 resume did not retain the additive done-marker shape'

  # Automatic continuation: the first child ends reasoning-only; the resumed
  # child's own stream supplies the final text.
  PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
    CODEX_ROUTE=luna CODEX_HOME_LUNA="$ROOT/codex-luna" CODEX_SQLITE_HOME="$ROOT/sqlite" \
    CODEX_LOG="$ROOT/ctl38-auto.log" CODEX_AUTO_CONTINUE=1 FAKE_CODEX_REASONING=1 \
    FAKE_CODEX_RESUME_SEQUENCE=reasoning-text \
    "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" ctl38-auto \
    > "$ROOT/ctl38-auto.out" 2>&1 || fail 'CTL-38 automatic continuation failed'
  cmp -s "$ROOT/ctl38-resume.expected" "$runs/ctl38-auto.last.txt" \
    || fail 'CTL-38 auto-resume did not use the continuation stream final text'
  [[ $(cat "$runs/ctl38-auto.done") == 'EXIT=0 OUTCOME=CODE-EXIT AUTORESUME=1 EFFORT=xhigh' ]] \
    || fail 'CTL-38 auto-resume did not record its effort and continuation count'

  # A child with no completed agent message leaves no file, even when a stale
  # FINAL-A already exists. Its EXIT/outcome remains the historical child result.
  printf 'FINAL-STALE' > "$runs/ctl38-empty.last.txt"
  PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
    CODEX_ROUTE=luna CODEX_HOME_LUNA="$ROOT/codex-luna" CODEX_SQLITE_HOME="$ROOT/sqlite" \
    CODEX_LOG="$ROOT/ctl38-empty.log" CODEX_AUTO_CONTINUE=0 FAKE_CODEX_REASONING=1 \
    "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" ctl38-empty resume ctl38-empty-thread \
    > "$ROOT/ctl38-empty.out" 2>&1 || fail 'CTL-38 no-text resume failed'
  [[ ! -e $runs/ctl38-empty.last.txt ]] \
    || fail 'CTL-38 left a stale last.txt for a stream with no final text'
  [[ $(cat "$runs/ctl38-empty.done") == 'EXIT=0 OUTCOME=CODE-EXIT AUTORESUME=0 EFFORT=xhigh' ]] \
    || fail 'CTL-38 changed the done outcome for a no-text stream'

  # Text followed by trailing reasoning reaches auto-continuation. If the next
  # attempt exits with an empty stream, the invocation's earlier final message
  # remains the result rather than being deleted with the attempt.
  rc=0
  PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
    CODEX_ROUTE=luna CODEX_HOME_LUNA="$ROOT/codex-luna" CODEX_SQLITE_HOME="$ROOT/sqlite" \
    CODEX_LOG="$ROOT/ctl38-full-stream.log" CODEX_AUTO_CONTINUE=1 \
    FAKE_CODEX_TEXT_REASONING_EMPTY=1 \
    "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" ctl38-full-stream \
    > "$ROOT/ctl38-full-stream.out" 2>&1 || rc=$?
  [[ $rc == 42 ]] || { cat "$ROOT/ctl38-full-stream.out" >&2; fail "CTL-38 full-stream fixture exited $rc, expected 42"; }
  printf 'ATTEMPT-1-FINAL' > "$ROOT/ctl38-full-stream.expected"
  cmp -s "$ROOT/ctl38-full-stream.expected" "$runs/ctl38-full-stream.last.txt" \
    || fail 'CTL-38 auto-resume erased the invocation text when a later attempt was empty'
  [[ $(cat "$runs/ctl38-full-stream.done") == 'EXIT=42 OUTCOME=CODE-EXIT AUTORESUME=1 EFFORT=xhigh' ]] \
    || fail 'CTL-38 full-stream fixture recorded the wrong completion marker'

  # Hold the SECOND continuation so the check can inspect the file between
  # attempts. The first continuation is reasoning-only and must not erase text
  # already present in the invocation's accumulated stream.
  local held_launcher held_rc=0
  rm -f -- "$ROOT/ctl38-held.hold.pid" "$ROOT/ctl38-held.release" "$ROOT/ctl38-held.count"
  PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
    CODEX_ROUTE=luna CODEX_HOME_LUNA="$ROOT/codex-luna" CODEX_SQLITE_HOME="$ROOT/sqlite" \
    CODEX_LOG="$ROOT/ctl38-held.log" CODEX_AUTO_CONTINUE=2 FAKE_CODEX_TEXT_REASONING_HOLD=1 \
    FAKE_CODEX_HOLD_UNTIL="$ROOT/ctl38-held.release" FAKE_CODEX_HOLD_PID="$ROOT/ctl38-held.hold.pid" \
    FAKE_CODEX_HOLD_ON_CALL=3 FAKE_CODEX_HOLD_CALL_COUNT="$ROOT/ctl38-held.count" \
    "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" ctl38-held > "$ROOT/ctl38-held.out" 2>&1 &
  held_launcher=$!
  for _ in {1..2000}; do
    [[ -s $ROOT/ctl38-held.hold.pid ]] && break
    kill -0 "$held_launcher" 2>/dev/null || break
    sleep 0.05
  done
  [[ -s $ROOT/ctl38-held.hold.pid ]] \
    || { cat "$ROOT/ctl38-held.out" >&2; fail 'CTL-38 held continuation never started'; }
  cmp -s "$ROOT/ctl38-full-stream.expected" "$runs/ctl38-held.last.txt" \
    || fail 'CTL-38 an intervening reasoning-only attempt erased the invocation text'
  : > "$ROOT/ctl38-held.release"
  wait "$held_launcher" || held_rc=$?
  [[ $held_rc == 0 ]] || { cat "$ROOT/ctl38-held.out" >&2; fail "CTL-38 held continuation exited $held_rc"; }
  [[ $(cat "$runs/ctl38-held.done") == 'EXIT=0 OUTCOME=CODE-EXIT AUTORESUME=2 EFFORT=xhigh' ]] \
    || fail 'CTL-38 held continuation recorded the wrong completion marker'

  # A signalled resume must not expose the previous invocation's text beside the
  # fresh marker. The fake child publishes its pid after the stream is open and
  # holds until the check signals the wrapper's private process group.
  local signal_launcher signal_pgid signal_rc
  printf 'FINAL-A' > "$runs/ctl38-signal.last.txt"
  printf 'EXIT=0 OUTCOME=CODE-EXIT AUTORESUME=0 EFFORT=xhigh\n' > "$runs/ctl38-signal.done"
  rm -f -- "$ROOT/ctl38-signal.hold.pid" "$ROOT/ctl38-signal.release"
  setsid --wait env \
    PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
    CODEX_ROUTE=luna CODEX_HOME_LUNA="$ROOT/codex-luna" CODEX_SQLITE_HOME="$ROOT/sqlite" \
    CODEX_LOG="$ROOT/ctl38-signal.log" CODEX_AUTO_CONTINUE=0 \
    FAKE_CODEX_HOLD_UNTIL="$ROOT/ctl38-signal.release" FAKE_CODEX_HOLD_PID="$ROOT/ctl38-signal.hold.pid" \
    "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" ctl38-signal resume ctl38-signal-thread \
    > "$ROOT/ctl38-signal.out" 2>&1 &
  signal_launcher=$!
  for _ in {1..2000}; do
    [[ -s $ROOT/ctl38-signal.hold.pid ]] && break
    kill -0 "$signal_launcher" 2>/dev/null || break
    sleep 0.05
  done
  [[ -s $ROOT/ctl38-signal.hold.pid ]] \
    || { cat "$ROOT/ctl38-signal.out" >&2; fail 'CTL-38 signalled resume child never started'; }
  [[ ! -e $runs/ctl38-signal.last.txt ]] \
    || fail 'CTL-38 resumed child started beside the previous final text'
  # Recreate the old text after launch so the EXIT-trap extraction is observable
  # independently of the pre-launch cleanup above.
  printf 'FINAL-A' > "$runs/ctl38-signal.last.txt"
  signal_pgid=$(ps -o pgid= -p "$(cat "$ROOT/ctl38-signal.hold.pid")" | tr -d '[:space:]')
  [[ $signal_pgid =~ ^[0-9]+$ ]] || fail 'CTL-38 signalled resume published no process group'
  kill -TERM -- "-$signal_pgid" || fail 'CTL-38 signalled resume process group was already gone'
  wait "$signal_launcher" 2>/dev/null
  signal_rc=$?
  (( signal_rc != 0 )) || fail 'CTL-38 signalled resume unexpectedly exited zero'
  [[ $(cat "$runs/ctl38-signal.done") == 'EXIT=143 OUTCOME=CODE-EXIT AUTORESUME=0 EFFORT=xhigh' ]] \
    || fail 'CTL-38 signalled resume did not write its own done marker'
  [[ ! -e $runs/ctl38-signal.last.txt ]] \
    || fail 'CTL-38 signalled resume left the previous final text beside its done marker'
}
check_ctl38_last_text_all_paths

check_codex_route_homes_and_override() {
  home_launch() {
    local name=$1 route=$2 log=$3
    shift 3
    PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
      CODEX_ROUTE="$route" CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$log" \
      "$@" "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" "$name" \
      > "$ROOT/$name.out" 2>&1 || {
        cat "$ROOT/$name.out" >&2
        fail "Codex route-home launch failed: $name"
      }
  }

  home_launch route-home-luna luna "$ROOT/route-home-luna.log" env
  contains "$ROOT/route-home-luna.log" "HOME=$ROOT/home/.codex2" \
    'the luna route defaults to the .codex2 home'
  home_launch route-home-sol sol "$ROOT/route-home-sol.log" env
  contains "$ROOT/route-home-sol.log" "HOME=$ROOT/home/.codex" \
    'the sol route defaults to the .codex home'
  home_launch route-home-luna-override luna "$ROOT/route-home-luna-override.log" \
    env CODEX_HOME_OVERRIDE="$ROOT/codex-home-override-luna"
  contains "$ROOT/route-home-luna-override.log" "HOME=$ROOT/codex-home-override-luna" \
    'CODEX_HOME_OVERRIDE wins over the luna default'
  home_launch route-home-sol-override sol "$ROOT/route-home-sol-override.log" \
    env CODEX_HOME_OVERRIDE="$ROOT/codex-home-override-sol"
  contains "$ROOT/route-home-sol-override.log" "HOME=$ROOT/codex-home-override-sol" \
    'CODEX_HOME_OVERRIDE wins over the sol default'
}
check_codex_route_homes_and_override

check_codex_effort_vocabulary() {
  local value rc
  for value in minimal low medium high xhigh max; do
    PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
      CODEX_ROUTE=luna CODEX_HOME_LUNA="$ROOT/codex-luna" CODEX_SQLITE_HOME="$ROOT/sqlite" \
      CODEX_LOG="$ROOT/effort-$value.log" CODEX_EFFORT="$value" FAKE_CODEX_MODE=clean \
      "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" "effort-$value" > "$ROOT/effort-$value.out" 2>&1 \
      || fail "valid CODEX_EFFORT value was rejected: $value"
    contains "$ROOT/controller/runs/effort-$value.done" "EFFORT=$value" \
      "CODEX_EFFORT=$value is recorded in the done marker"
  done
  if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
    CODEX_ROUTE=luna CODEX_HOME_LUNA="$ROOT/codex-luna" CODEX_SQLITE_HOME="$ROOT/sqlite" \
    CODEX_LOG="$ROOT/invalid-effort.log" CODEX_EFFORT='max high' \
    "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" invalid-effort > "$ROOT/invalid-effort.out" 2>&1; then
    fail 'invalid CODEX_EFFORT was accepted'
  else
    rc=$?
    [[ $rc == 2 ]] || fail "invalid CODEX_EFFORT exit was $rc, expected 2"
  fi
  contains "$ROOT/invalid-effort.out" \
    'CODEX_EFFORT must be one of minimal, low, medium, high, xhigh, max' \
    'invalid CODEX_EFFORT names the allowed values'
  [[ ! -e $ROOT/invalid-effort.log ]] || fail 'Codex ran with invalid CODEX_EFFORT'
  [[ ! -s $ROOT/controller/runs/invalid-effort.lock ]] \
    || fail 'invalid CODEX_EFFORT wrote an owner record before the child launch'
  [[ ! -e $ROOT/controller/runs/invalid-effort.done ]] || fail 'invalid CODEX_EFFORT wrote a done marker'
}
check_codex_effort_vocabulary

if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CODEX_HOME_LUNA="$ROOT/codex-luna" CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_RC=7 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" codex-fail > "$ROOT/codex.out" 2>&1; then
  fail 'Codex child failure was not propagated'
else rc=$?; [[ $rc == 7 ]] || fail "Codex exit was $rc, expected 7"; fi
contains "$ROOT/controller/runs/codex-fail.done" 'EXIT=7' 'Codex child exit is persisted'
contains "$ROOT/controller/runs/codex-fail.done" 'EFFORT=xhigh' 'Luna retains its xhigh default in the done marker'
[[ $(cat "$ROOT/controller/runs/codex-fail.last.txt") == complete ]] \
  || fail 'the new-run Codex path did not derive .last.txt from its stream'
contains "$CODEX_LOG" "HOME=$ROOT/codex-luna" 'Luna profile path is explicit'
contains "$CODEX_LOG" 'SQLITE=' 'shared session database path is set'
mkdir -p "$ROOT/codex-deepseek-volker/catalog"
printf '{"models":[{"name":"fixture","tool_mode":"enabled"}]}\n' > "$ROOT/codex-deepseek-volker/catalog/opendesign-models.json"
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CODEX_ROUTE=deepseek-volker CODEX_HOME_DEEPSEEK_VOLKER="$ROOT/codex-deepseek-volker" \
  CONTROLLER_OD_STATE="$ROOT/od-catalog-state.json" CODEX_LOG="$ROOT/deepseek-codex.log" \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" deepseek-catalog > "$ROOT/deepseek-catalog.out" 2>&1; then
  fail 'DeepSeek catalog with tool_mode was accepted'
else rc=$?; [[ $rc == 3 ]] || fail "unsafe DeepSeek catalog exit was $rc, expected 3"; fi
[[ ! -e $ROOT/deepseek-codex.log ]] || fail 'Codex ran after the DeepSeek catalog guard refused the catalog'
python3 - "$ROOT/od-catalog-state.json" <<'PY' || fail 'catalog refusal marked an Open Design account as used'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["volker"]["last_used_epoch"] == 0
assert state["github2"]["last_used_epoch"] == 0
PY

VOLKER_HOME=$ROOT/opendesign-volker
GITHUB2_HOME=$ROOT/opendesign-github2
mkdir -p "$VOLKER_HOME/catalog" "$GITHUB2_HOME/catalog"
printf '{"models":[{"name":"fixture","tool_mode":null}]}\n' > "$VOLKER_HOME/catalog/opendesign-models.json"
printf '{"models":[{"name":"fixture","tool_mode":null}]}\n' > "$GITHUB2_HOME/catalog/opendesign-models.json"
OD_STATE=$ROOT/opendesign-state.json
OD_FUTURE_EPOCH=$(( $(date +%s) + 86400 ))
rm -f -- "$CODEX_LOG"
for run in od-alternation-1 od-alternation-2 od-alternation-3 od-alternation-4; do
  PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
    CONTROLLER_OD_STATE="$OD_STATE" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
    CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek CODEX_AUTO_CONTINUE=0 \
    OPENDESIGN_API_KEY_VOLKER=test-key OPENDESIGN_API_KEY_GITHUB2=test-key \
    "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" "$run" > "$ROOT/$run.out" 2>&1 \
    || fail "automatic DeepSeek start failed: $run"
done
[[ $(cat "$ROOT/controller/runs/od-alternation-1.route") == deepseek-volker ]] || fail 'automatic DeepSeek selection must start with volker on an empty state'
[[ $(cat "$ROOT/controller/runs/od-alternation-2.route") == deepseek-github2 ]] || fail 'automatic DeepSeek selection must alternate to github2'
[[ $(cat "$ROOT/controller/runs/od-alternation-3.route") == deepseek-volker ]] || fail 'automatic DeepSeek selection must alternate back to volker'
[[ $(cat "$ROOT/controller/runs/od-alternation-4.route") == deepseek-github2 ]] || fail 'automatic DeepSeek selection must alternate back to github2'
contains "$CODEX_LOG" "HOME=$VOLKER_HOME" 'volker route selects the volker Codex home'
contains "$CODEX_LOG" "HOME=$GITHUB2_HOME" 'github2 route selects the github2 Codex home'
contains "$CODEX_LOG" 'deepseek-v4.1-flash-eco' 'DeepSeek route uses the configured default model'
contains "$CODEX_LOG" 'model_reasoning_effort="max"' 'DeepSeek route uses the catalog maximum effort'
for run in od-alternation-1 od-alternation-2 od-alternation-3 od-alternation-4; do
  contains "$ROOT/controller/runs/$run.done" 'EFFORT=max' \
    'DeepSeek route records the maximum effort in its done marker'
done
not_contains "$CODEX_LOG" test-key 'API key values are absent from Codex argv logs'
not_contains "$OD_STATE" test-key 'Open Design account state contains no key material'
for run in od-alternation-1 od-alternation-2 od-alternation-3 od-alternation-4; do
  for extension in jsonl err 'done' autoresume.log; do
    not_contains "$ROOT/controller/runs/$run.$extension" test-key "API key values are absent from $run.$extension"
  done
done
python3 - "$OD_STATE" <<'PY' || fail 'generated Open Design state contains fields outside its key-free schema'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert set(state) == {"volker", "github2", "threads"}
assert isinstance(state["threads"], dict)
assert all(set(state[name]) <= {"last_used_epoch", "blocked_until_epoch", "window_end_epoch"} for name in ("volker", "github2"))
assert all(account in {"volker", "github2"} for account in state["threads"].values())
PY

PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$OD_STATE" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$ROOT/od-effort-low.log" \
  CODEX_ROUTE=deepseek-volker CODEX_AUTO_CONTINUE=0 CODEX_EFFORT=low \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-effort-low > "$ROOT/od-effort-low.out" 2>&1 \
  || fail 'explicit low effort DeepSeek run failed'
contains "$ROOT/od-effort-low.log" 'model_reasoning_effort="low"' \
  'CODEX_EFFORT=low overrides the DeepSeek maximum default'
contains "$ROOT/controller/runs/od-effort-low.done" 'EFFORT=low' \
  'an explicit low effort is recorded in the done marker'

python3 - "$ROOT/od-unsafe-state.json" <<'PY'
import json, sys
json.dump({"volker": {"last_used_epoch": 1, "blocked_until_epoch": 0, "api_key": "test-key"}}, open(sys.argv[1], "w"))
PY
before_calls=$(wc -l < "$CODEX_LOG")
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-unsafe-state.json" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek-volker CODEX_AUTO_CONTINUE=0 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-unsafe-state > "$ROOT/od-unsafe-state.out" 2>&1; then
  fail 'Open Design account state accepted an unrecognized field that could contain key material'
fi
contains "$ROOT/od-unsafe-state.out" 'invalid Open Design account state schema' 'invalid state schema is rejected without echoing its contents'
not_contains "$ROOT/od-unsafe-state.out" test-key 'state validation does not print unrecognized credential fields'
[[ $(wc -l < "$CODEX_LOG") == "$before_calls" ]] || fail 'Codex ran with an invalid account state schema'

python3 - "$ROOT/od-nonnumeric-state.json" <<'PY'
import json, sys
json.dump({"volker": {"last_used_epoch": "not-an-epoch", "blocked_until_epoch": 0}}, open(sys.argv[1], "w"))
PY
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-nonnumeric-state.json" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek-volker CODEX_AUTO_CONTINUE=0 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-nonnumeric-state > "$ROOT/od-nonnumeric-state.out" 2>&1; then
  fail 'Open Design account state accepted a non-numeric last_used_epoch'
fi
contains "$ROOT/od-nonnumeric-state.out" 'invalid last_used_epoch for account volker' 'numeric account-state fields are validated'
[[ $(wc -l < "$CODEX_LOG") == "$before_calls" ]] || fail 'Codex ran with a non-numeric account state'

if (cd "$ROOT" && PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE=relative-state.json CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek-volker CODEX_AUTO_CONTINUE=0 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-relative-state > "$ROOT/od-relative-state.out" 2>&1); then
  fail 'relative Open Design state path was accepted'
fi
contains "$ROOT/od-relative-state.out" 'CONTROLLER_OD_STATE must be an absolute path' 'Open Design state path must be absolute'
[[ $(wc -l < "$CODEX_LOG") == "$before_calls" ]] || fail 'Codex ran after rejecting a relative state path'

if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-invalid-preference.json" CONTROLLER_OD_PREFER_EXPIRING=yes \
  CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" \
  CODEX_ROUTE=deepseek CODEX_AUTO_CONTINUE=0 "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-invalid-preference \
  > "$ROOT/od-invalid-preference.out" 2>&1; then
  fail 'invalid Open Design expiring-window preference was accepted'
fi
contains "$ROOT/od-invalid-preference.out" 'CONTROLLER_OD_PREFER_EXPIRING must be 0 or 1' 'invalid expiring-window preference is rejected'
[[ $(wc -l < "$CODEX_LOG") == "$before_calls" ]] || fail 'Codex ran after rejecting an invalid expiring-window preference'

python3 - "$ROOT/od-blocked.json" "$OD_FUTURE_EPOCH" <<'PY'
import json, sys
json.dump({"volker": {"last_used_epoch": 1, "blocked_until_epoch": int(sys.argv[2])},
           "github2": {"last_used_epoch": 100, "blocked_until_epoch": 0}}, open(sys.argv[1], "w"))
PY
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-blocked.json" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek CODEX_AUTO_CONTINUE=0 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-blocked-skip > "$ROOT/od-blocked-skip.out" 2>&1 \
  || fail 'automatic DeepSeek start failed with one blocked account'
[[ $(cat "$ROOT/controller/runs/od-blocked-skip.route") == deepseek-github2 ]] || fail 'automatic DeepSeek selection did not skip the blocked account'

python3 - "$ROOT/od-expired.json" <<'PY'
import json, sys
json.dump({"volker": {"last_used_epoch": 1, "blocked_until_epoch": 1},
           "github2": {"last_used_epoch": 100, "blocked_until_epoch": 0}}, open(sys.argv[1], "w"))
PY
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-expired.json" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek CODEX_AUTO_CONTINUE=0 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-expired-unblocked > "$ROOT/od-expired-unblocked.out" 2>&1 \
  || fail 'automatic DeepSeek start failed when a block had expired'
[[ $(cat "$ROOT/controller/runs/od-expired-unblocked.route") == deepseek-volker ]] || fail 'a past blocked_until_epoch remained blocked'

OD_EARLIEST_RESET=$((OD_FUTURE_EPOCH + 50))
OD_LATER_RESET=$((OD_FUTURE_EPOCH + 100))
python3 - "$ROOT/od-both-blocked.json" "$OD_LATER_RESET" "$OD_EARLIEST_RESET" <<'PY'
import json, sys
json.dump({"volker": {"last_used_epoch": 1, "blocked_until_epoch": int(sys.argv[2])},
           "github2": {"last_used_epoch": 2, "blocked_until_epoch": int(sys.argv[3])}}, open(sys.argv[1], "w"))
PY
before_calls=$(wc -l < "$CODEX_LOG")
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-both-blocked.json" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek CODEX_AUTO_CONTINUE=0 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-both-blocked > "$ROOT/od-both-blocked.out" 2>&1; then
  fail 'automatic DeepSeek start accepted two blocked accounts'
else rc=$?; [[ $rc == 3 ]] || fail "both-blocked exit was $rc, expected 3"; fi
contains "$ROOT/od-both-blocked.out" "earliest reset is $(date -u -d "@$OD_EARLIEST_RESET" +%Y-%m-%dT%H:%M:%SZ)" 'both-blocked refusal reports the earliest reset'
[[ $(wc -l < "$CODEX_LOG") == "$before_calls" ]] || fail 'Codex ran while both accounts were blocked'

python3 - "$ROOT/od-expiring.json" "$OD_FUTURE_EPOCH" <<'PY'
import json, sys
future = int(sys.argv[2])
json.dump({"volker": {"last_used_epoch": 1, "blocked_until_epoch": 0, "window_end_epoch": future + 100},
           "github2": {"last_used_epoch": 100, "blocked_until_epoch": 0, "window_end_epoch": future + 50}}, open(sys.argv[1], "w"))
PY
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-expiring.json" CONTROLLER_OD_PREFER_EXPIRING=1 \
  CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" CODEX_SQLITE_HOME="$ROOT/sqlite" \
  CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek CODEX_AUTO_CONTINUE=0 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-expiring > "$ROOT/od-expiring.out" 2>&1 \
  || fail 'DeepSeek prefer-expiring start failed'
[[ $(cat "$ROOT/controller/runs/od-expiring.route") == deepseek-github2 ]] || fail 'prefer-expiring did not choose the earliest window end'

python3 - "$ROOT/od-pinned.json" "$OD_FUTURE_EPOCH" <<'PY'
import json, sys
json.dump({"volker": {"last_used_epoch": 1, "blocked_until_epoch": int(sys.argv[2])},
           "github2": {"last_used_epoch": 2, "blocked_until_epoch": 0}}, open(sys.argv[1], "w"))
PY
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-pinned.json" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$ROOT/od-pinned-codex.log" CODEX_ROUTE=deepseek-volker CODEX_AUTO_CONTINUE=0 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-pinned > "$ROOT/od-pinned.out" 2>&1 \
  || fail 'pinned DeepSeek route failed instead of staying on volker'
[[ $(cat "$ROOT/controller/runs/od-pinned.route") == deepseek-volker ]] || fail 'pinned route changed accounts'
contains "$ROOT/od-pinned-codex.log" "HOME=$VOLKER_HOME" 'pinned DeepSeek route uses the named account home'
contains "$ROOT/od-pinned-codex.log" 'model_reasoning_effort="max"' \
  'pinned DeepSeek route uses the catalog maximum effort'
contains "$ROOT/controller/runs/od-pinned.done" 'EFFORT=max' \
  'pinned DeepSeek route records the maximum effort in its done marker'
python3 - "$ROOT/od-pinned.json" <<'PY' || fail 'pinned DeepSeek start selected the other account in state'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["volker"]["last_used_epoch"] > 1
assert state["github2"]["last_used_epoch"] == 2
PY

if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-402.json" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek-volker CODEX_AUTO_CONTINUE=0 FAKE_CODEX_402=1 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-402 > "$ROOT/od-402.out" 2>&1; then
  fail 'fake 402 Codex run unexpectedly succeeded'
else rc=$?; [[ $rc == 42 ]] || fail "402 child exit was $rc, expected 42"; fi
contains "$ROOT/od-402.out" 'blocked until 2099-11-17T11:44:44Z' '402 response records the account reset time'
python3 - "$ROOT/od-402.json" <<'PY' || fail '402 did not persist blocked_until_epoch for the run account'
from datetime import datetime, timezone
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
expected = int(datetime(2099, 11, 17, 11, 44, 44, tzinfo=timezone.utc).timestamp())
assert state["volker"]["blocked_until_epoch"] == expected
assert state["github2"]["blocked_until_epoch"] == 0
PY

# The real gateway 402 reaches the run stream as a plain-text message on a final error event.
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-402-text.json" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek-github2 CODEX_AUTO_CONTINUE=0 \
  FAKE_CODEX_STREAM="$SCRIPT_DIR/fixtures/usage-limit-insufficient-balance-text.jsonl" \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-402-text > "$ROOT/od-402-text.out" 2>&1; then
  fail 'fake plain-text 402 Codex run unexpectedly succeeded'
else rc=$?; [[ $rc == 42 ]] || fail "plain-text 402 child exit was $rc, expected 42"; fi
contains "$ROOT/od-402-text.out" 'Open Design account github2 reset time observed; blocked until 2099-11-17T11:44:44Z' 'plain-text 402 records the account reset time'
python3 - "$ROOT/od-402-text.json" <<'PY' || fail 'plain-text 402 did not persist blocked_until_epoch for the run account'
from datetime import datetime, timezone
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
expected = int(datetime(2099, 11, 17, 11, 44, 44, tzinfo=timezone.utc).timestamp())
assert state["github2"]["blocked_until_epoch"] == expected
assert state["volker"]["blocked_until_epoch"] == 0
PY

OD_UNKNOWN_START=$(date +%s)
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-402-unknown-missing.json" CONTROLLER_OD_UNKNOWN_BLOCK_SECONDS=3600 \
  CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" \
  CODEX_ROUTE=deepseek-volker CODEX_AUTO_CONTINUE=0 FAKE_CODEX_402_UNKNOWN=missing \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-402-unknown-missing > "$ROOT/od-402-unknown-missing.out" 2>&1; then
  fail 'fake 402 without reset_at unexpectedly succeeded'
else rc=$?; [[ $rc == 42 ]] || fail "unknown-reset child exit was $rc, expected 42"; fi
OD_UNKNOWN_END=$(date +%s)
contains "$ROOT/od-402-unknown-missing.out" 'reset time unknown; temporary block for 3600 seconds' \
  'missing reset_at uses and logs the configured conservative block'
python3 - "$ROOT/od-402-unknown-missing.json" "$OD_UNKNOWN_START" "$OD_UNKNOWN_END" 3600 <<'PY' || fail 'missing reset_at did not persist the configured default block'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
blocked = state["volker"]["blocked_until_epoch"]
assert int(sys.argv[2]) + int(sys.argv[4]) <= blocked <= int(sys.argv[3]) + int(sys.argv[4]) + 1
PY

OD_DEFAULT_START=$(date +%s)
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-402-unknown-invalid.json" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek-github2 CODEX_AUTO_CONTINUE=0 \
  FAKE_CODEX_402_UNKNOWN=invalid "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-402-unknown-invalid \
  > "$ROOT/od-402-unknown-invalid.out" 2>&1; then
  fail 'fake 402 with an invalid reset_at unexpectedly succeeded'
else rc=$?; [[ $rc == 42 ]] || fail "invalid-reset child exit was $rc, expected 42"; fi
OD_DEFAULT_END=$(date +%s)
contains "$ROOT/od-402-unknown-invalid.out" 'reset time unknown; temporary block for 21600 seconds' \
  'invalid reset_at uses and logs the six-hour default block'
python3 - "$ROOT/od-402-unknown-invalid.json" "$OD_DEFAULT_START" "$OD_DEFAULT_END" <<'PY' || fail 'invalid reset_at did not persist the six-hour default block'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
blocked = state["github2"]["blocked_until_epoch"]
assert int(sys.argv[2]) + 21600 <= blocked <= int(sys.argv[3]) + 21600 + 1
PY

OD_INVALID_KNOB_START=$(date +%s)
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-402-invalid-block-seconds.json" CONTROLLER_OD_UNKNOWN_BLOCK_SECONDS=0 \
  CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" \
  CODEX_ROUTE=deepseek-volker CODEX_AUTO_CONTINUE=0 FAKE_CODEX_402_UNKNOWN=missing \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-402-invalid-block-seconds > "$ROOT/od-402-invalid-block-seconds.out" 2>&1; then
  fail 'fake 402 with an invalid block-seconds override unexpectedly succeeded'
else rc=$?; [[ $rc == 42 ]] || fail "invalid-knob child exit was $rc, expected 42"; fi
OD_INVALID_KNOB_END=$(date +%s)
contains "$ROOT/od-402-invalid-block-seconds.out" 'invalid CONTROLLER_OD_UNKNOWN_BLOCK_SECONDS; using the six-hour default' \
  'invalid unknown-reset block override is logged and replaced with the safe default'
python3 - "$ROOT/od-402-invalid-block-seconds.json" "$OD_INVALID_KNOB_START" "$OD_INVALID_KNOB_END" <<'PY' || fail 'invalid block-seconds override did not keep the account blocked'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
blocked = state["volker"]["blocked_until_epoch"]
assert int(sys.argv[2]) + 21600 <= blocked <= int(sys.argv[3]) + 21600 + 1
PY

python3 - "$ROOT/od-preserve-later.json" <<'PY'
from datetime import datetime, timezone
import json, sys
future = int(datetime(2100, 1, 1, tzinfo=timezone.utc).timestamp())
json.dump({"volker": {"last_used_epoch": 1, "blocked_until_epoch": future},
           "github2": {"last_used_epoch": 2, "blocked_until_epoch": 0}}, open(sys.argv[1], "w"))
PY
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-preserve-later.json" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek-volker CODEX_AUTO_CONTINUE=0 FAKE_CODEX_402=1 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-preserve-later > "$ROOT/od-preserve-later.out" 2>&1; then
  fail 'fake 402 run with an earlier reset unexpectedly succeeded'
else rc=$?; [[ $rc == 42 ]] || fail "preserve-later child exit was $rc, expected 42"; fi
python3 - "$ROOT/od-preserve-later.json" <<'PY' || fail 'block-run shortened an existing later block'
from datetime import datetime, timezone
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["volker"]["blocked_until_epoch"] == int(datetime(2100, 1, 1, tzinfo=timezone.utc).timestamp())
PY
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/od-not-402.json" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$CODEX_LOG" CODEX_ROUTE=deepseek-github2 CODEX_AUTO_CONTINUE=0 \
  FAKE_CODEX_STREAM="$SCRIPT_DIR/fixtures/insufficient-balance-text-not-a-402.jsonl" \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-not-402 > "$ROOT/od-not-402.out" 2>&1
python3 - "$ROOT/od-not-402.json" <<'PY' || fail 'ordinary text blocked an Open Design account'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["github2"]["blocked_until_epoch"] == 0 and state["volker"]["blocked_until_epoch"] == 0
PY

MANUAL_STATE=$ROOT/manual-resume-state.json
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$MANUAL_STATE" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$ROOT/manual-origin.log" CODEX_ROUTE=deepseek CODEX_AUTO_CONTINUE=0 \
  FAKE_CODEX_THREAD_ID=manual-resume-thread \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" manual-origin > "$ROOT/manual-origin.out" 2>&1 \
  || fail 'DeepSeek origin run for manual resume failed'
[[ $(cat "$ROOT/controller/runs/manual-origin.route") == deepseek-volker ]] || fail 'manual resume origin did not select Volker'
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$MANUAL_STATE" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$ROOT/manual-resume.log" CODEX_ROUTE=deepseek CODEX_AUTO_CONTINUE=0 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" manual-resume resume manual-resume-thread \
  > "$ROOT/manual-resume.out" 2>&1 || fail 'manual DeepSeek resume failed'
[[ $(cat "$ROOT/controller/runs/manual-resume.route") == deepseek-volker ]] || fail 'manual resume did not retain its recorded account'
contains "$ROOT/manual-resume.log" "HOME=$VOLKER_HOME" 'manual resume uses the original account home'
contains "$ROOT/manual-resume.log" 'resume -m deepseek-v4.1-flash-eco' 'manual DeepSeek resume passes the original thread'
python3 - "$MANUAL_STATE" <<'PY' || fail 'manual resume did not keep the thread account mapping'
import json, sys
state = json.load(open(sys.argv[1], encoding="utf-8"))
assert state["threads"]["manual-resume-thread"] == "volker"
PY

PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$MANUAL_STATE" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$ROOT/manual-resume-unmapped.log" CODEX_ROUTE=deepseek CODEX_AUTO_CONTINUE=0 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" manual-resume-unmapped resume unrecorded-thread \
  > "$ROOT/manual-resume-unmapped.out" 2>&1 || fail 'unmapped manual DeepSeek resume did not select an account'
contains "$ROOT/manual-resume-unmapped.out" 'no recorded Open Design account for thread unrecorded-thread; picking a fresh account' \
  'unmapped manual resume explains its fresh account pick'
[[ $(cat "$ROOT/controller/runs/manual-resume-unmapped.route") == deepseek-github2 ]] || fail 'unmapped manual resume did not pick the least recently used account'
python3 - "$MANUAL_STATE" "$OD_FUTURE_EPOCH" <<'PY'
import json, sys
path, blocked_until = sys.argv[1], int(sys.argv[2])
state = json.load(open(path, encoding="utf-8"))
state["volker"]["blocked_until_epoch"] = blocked_until
state["github2"]["blocked_until_epoch"] = 0
json.dump(state, open(path, "w"))
PY
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$MANUAL_STATE" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$ROOT/manual-resume-blocked.log" CODEX_ROUTE=deepseek CODEX_AUTO_CONTINUE=0 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" manual-resume-blocked resume manual-resume-thread \
  > "$ROOT/manual-resume-blocked.out" 2>&1 || fail 'blocked recorded-account manual resume did not fall back'
contains "$ROOT/manual-resume-blocked.out" \
  'account fallback from deepseek-volker to deepseek-github2 because the recorded account is blocked' \
  'manual resume logs fallback from a blocked recorded account'
[[ $(cat "$ROOT/controller/runs/manual-resume-blocked.route") == deepseek-github2 ]] || fail 'manual resume fallback did not pin the available account'

BOOKKEEPING_STATE=$ROOT/od-bookkeeping-failure.json
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$BOOKKEEPING_STATE" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_SQLITE_HOME="$ROOT/sqlite" \
  CODEX_LOG="$ROOT/od-bookkeeping-failure.log" CODEX_ROUTE=deepseek-volker CODEX_AUTO_CONTINUE=0 CODEX_RC=0 \
  FAKE_CODEX_CORRUPT_STATE="$BOOKKEEPING_STATE" \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" od-bookkeeping-failure > "$ROOT/od-bookkeeping-failure.out" 2>&1; then
  :
else rc=$?; fail "bookkeeping failure changed a successful Codex exit to $rc"; fi
contains "$ROOT/od-bookkeeping-failure.out" 'block-run bookkeeping failed for run od-bookkeeping-failure' \
  'block-run bookkeeping errors are logged without replacing the Codex status'
contains "$ROOT/controller/runs/od-bookkeeping-failure.done" 'EXIT=0' \
  'successful Codex status remains in the done marker after bookkeeping failure'

# A resume is a second invocation of one run name: it must replace the
# previous invocation's marker (otherwise a reader could report the previous
# resume's exit while this child works) and state its own outcome.
[[ -s $ROOT/controller/runs/manual-resume.done ]] || fail 'the resumed run has no marker before the resume test'
contains "$ROOT/controller/runs/manual-resume.done" 'OUTCOME=CODE-EXIT' \
  'a resumed Codex invocation states its own outcome'
contains "$ROOT/controller/runs/manual-resume-unmapped.done" 'OUTCOME=CODE-EXIT' \
  'a resume that picked a fresh account still wrote its marker'

# A resume REMOVES the previous invocation's marker before its child starts: a
# reader polling the name must not report the older resume's exit while the new
# one works.
FAKE_CODEX_HOLD_UNTIL="$ROOT/manual-resume-again.release" PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$MANUAL_STATE" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$ROOT/manual-resume-again.log" CODEX_ROUTE=deepseek CODEX_AUTO_CONTINUE=0 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" manual-resume resume manual-resume-thread \
  > "$ROOT/manual-resume-again.out" 2>&1 &
resume_pid=$!
# Wait for EVIDENCE that this invocation owns the name (its own pid is in the
# owner record), then for the marker to be gone while its child still works.
saw_owner=0
# Allow a full minute for the owner record under concurrent mutation workers.
for _ in {1..1200}; do
  recorded=$(sed -n 's/^pid=\([0-9][0-9]*\).*/\1/p' "$ROOT/controller/runs/manual-resume.lock" 2>/dev/null | head -n 1)
  if [[ $recorded == "$resume_pid" ]]; then saw_owner=1; break; fi
  sleep 0.05
done
(( saw_owner == 1 )) || fail 'the resumed invocation never recorded itself as the run owner'
saw_absence=0
# Allow a full minute for the resumed child to clear its old marker.
for _ in {1..1200}; do
  if [[ ! -e $ROOT/controller/runs/manual-resume.done ]]; then saw_absence=1; break; fi
  sleep 0.05
done
(( saw_absence == 1 )) \
  || fail 'a resumed run left the previous invocation marker readable while its child worked'
: > "$ROOT/manual-resume-again.release"
wait "$resume_pid" || fail 'the second resume of one name failed'
contains "$ROOT/controller/runs/manual-resume.done" 'OUTCOME=CODE-EXIT' \
  'the second resume wrote its own marker'

# Every Codex exit path states an OUTCOME and its continuation count, in the
# documented additive shape (the historical EXIT= first line is unchanged).
contains "$ROOT/controller/runs/codex-fail.done" 'OUTCOME=CODE-EXIT' \
  'a Codex run states its outcome class'
contains "$ROOT/controller/runs/codex-fail.done" 'AUTORESUME=0' \
  'a Codex run states how many continuations it took'
[[ ! -e $ROOT/controller/runs/bad-route.done ]] \
  || fail 'a run refused before the child launch wrote a child-exit marker'

# A second wrapper on one run name is refused, and the refusal names the live
# owner: two jobs on one name would share `.done`, `.jsonl` and the resume
# bookkeeping.
FAKE_CODEX_HOLD_UNTIL="$ROOT/owned-codex-run.release" PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" \
  CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CODEX_ROUTE=luna CODEX_HOME_LUNA="$ROOT/codex-luna" CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$ROOT/owned.log" \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" owned-codex-run \
  > "$ROOT/owned-codex-run.out" 2>&1 &
owned_pid=$!
# Allow a full minute for the run lock to appear under concurrent mutation workers.
for _ in {1..600}; do [[ -e $ROOT/controller/runs/owned-codex-run.lock ]] && break; sleep 0.1; done
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CODEX_ROUTE=luna CODEX_HOME_LUNA="$ROOT/codex-luna" CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$ROOT/owned2.log" \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" owned-codex-run \
  > "$ROOT/owned-codex-run-2.out" 2>&1; then
  fail 'a second Codex wrapper on one run name was allowed'
else
  rc=$?
  [[ $rc == 2 ]] || fail "the second Codex wrapper exited $rc, expected 2"
fi
contains "$ROOT/owned-codex-run-2.out" "run name 'owned-codex-run' is already owned by pid" \
  'the refusal names the live owner of the run name'
# The owner was HELD while the second wrapper was refused: release it now.
: > "$ROOT/owned-codex-run.release"
wait "$owned_pid" || fail 'the owning Codex wrapper failed'
contains "$ROOT/controller/runs/owned-codex-run.done" 'OUTCOME=CODE-EXIT' \
  'the owning wrapper still wrote its own marker'

# --- CTL-14 for Codex: the AGENT closes the ownership descriptor -------------
#
# The lock is a flock on the wrapper's open descriptor, and a forked child
# inherits it. So a Codex agent (or a daemon the agent leaves behind) holds the
# run name after the wrapper is gone, and the wrapper is the one process whose
# death must free it. Both hands are killed here for real (SIGKILL to the
# wrapper), and the name must be claimable immediately - while the agent is
# still working and while its daemon is still alive.

codex_run_is_free() {
  # Read the lock from OUTSIDE the wrapper: the check itself must not depend on
  # the code under test being able to claim it.
  local fds
  exec {fds}>>"$ROOT/controller/runs/$1.lock" || return 2
  if flock -n "$fds"; then exec {fds}>&-; return 0; fi
  exec {fds}>&-
  return 1
}

# (1) A killed wrapper behind a LIVE agent: the name is free at once, the agent
#     is still running, and a forced relaunch is accepted by the evidence rule.
FAKE_CODEX_DAEMON="$ROOT/codex-daemon.pid" \
FAKE_CODEX_HOLD_UNTIL="$ROOT/killed-codex-run.release" PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" \
  CONTROLLER_REPO="$ROOT/repo" CODEX_ROUTE=luna CODEX_HOME_LUNA="$ROOT/codex-luna" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$ROOT/killed-codex.log" \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" killed-codex-run \
  > "$ROOT/killed-codex-run.out" 2>&1 &
killed_pid=$!
# Sync on EVIDENCE, not on a clock: the fake agent publishes its thread id into
# the run stream once it is running (and starts its daemon first).
saw_agent=0
for _ in {1..2000}; do
  if grep -qF 'slow-thread' "$ROOT/controller/runs/killed-codex-run.jsonl" 2>/dev/null \
    && [[ -s $ROOT/codex-daemon.pid ]]; then
    saw_agent=1; break
  fi
  kill -0 "$killed_pid" 2>/dev/null || break
  sleep 0.05
done
(( saw_agent == 1 )) || { cat "$ROOT/killed-codex-run.out" >&2; fail 'the killed Codex agent never started'; }
kill -9 "$killed_pid" 2>/dev/null || true
wait "$killed_pid" 2>/dev/null || true
codex_run_is_free killed-codex-run \
  || fail 'a SIGKILLed Codex wrapper stranded its run name while its agent lived'
# A relaunch is accepted: nothing refuses on the dead owner, and the evidence
# rule is the only gate left.
CONTROLLER_RUN_FORCE=1 PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" \
  CONTROLLER_REPO="$ROOT/repo" CODEX_ROUTE=luna CODEX_HOME_LUNA="$ROOT/codex-luna" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$ROOT/killed-codex-reclaim.log" \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" killed-codex-run \
  > "$ROOT/killed-codex-reclaim.out" 2>&1 \
  || { cat "$ROOT/killed-codex-reclaim.out" >&2; fail 'a SIGKILLed Codex name could not be reclaimed'; }
contains "$ROOT/killed-codex-reclaim.out" 'retired' \
  'the forced reclaim reports what it retired'
# The left-behind daemon does NOT hold the name: it is alive, it inherited the
# descriptor, and it is killed only after this check has read the lock.
if [[ -s $ROOT/codex-daemon.pid ]]; then
  codex_run_is_free killed-codex-run \
    || fail 'a daemon left behind by the agent held the run name'
  kill -9 "$(cat "$ROOT/codex-daemon.pid")" 2>/dev/null || true
fi
# The stranded agent holds until this file exists; release it so the check
# cannot leave a process behind.
: > "$ROOT/killed-codex-run.release"

# (2) THE RESUME PATH, separately. A `codex exec resume` is the ROUTINE path:
#     every follow-up of a long job runs it, and deleting the descriptor close
#     from THAT line alone must fail this check. The same real SIGKILL applies.
FAKE_CODEX_DAEMON="$ROOT/resume-daemon.pid" \
FAKE_CODEX_HOLD_PID="$ROOT/resume-hold.pid" \
FAKE_CODEX_HOLD_UNTIL="$ROOT/killed-resume.release" PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" \
  CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" CODEX_ROUTE=luna \
  CODEX_HOME_LUNA="$ROOT/codex-luna" CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$ROOT/killed-resume.log" \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" killed-resume resume resume-thread-abc \
  > "$ROOT/killed-resume.out" 2>&1 &
resume_wrapper=$!
saw_resume_agent=0
for _ in {1..2000}; do
  if [[ -s $ROOT/resume-hold.pid && -s $ROOT/resume-daemon.pid ]]; then saw_resume_agent=1; break; fi
  kill -0 "$resume_wrapper" 2>/dev/null || break
  sleep 0.05
done
(( saw_resume_agent == 1 )) || { cat "$ROOT/killed-resume.out" >&2; fail 'the killed resume agent never started'; }
kill -9 "$resume_wrapper" || fail 'the resume wrapper was already gone before it was killed'
wait "$resume_wrapper" 2>/dev/null
resume_wait_rc=$?
(( resume_wait_rc == 137 )) || fail "the resume wrapper was not killed by SIGKILL (wait status $resume_wait_rc)"
# The agent and the setsid daemon it left behind both still run: the reclaim
# below is judged against live children, not against a tidy tree.
kill -0 "$(cat "$ROOT/resume-hold.pid")" 2>/dev/null \
  || fail 'the killed resume check lost its agent, so the reclaim was not judged against a live one'
kill -0 "$(cat "$ROOT/resume-daemon.pid")" 2>/dev/null \
  || fail 'the killed resume check lost its daemon, so the reclaim was not judged against a live one'
codex_run_is_free killed-resume \
  || fail 'a SIGKILLed Codex RESUME wrapper stranded its run name while its agent lived'
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" \
  CONTROLLER_REPO="$ROOT/repo" CODEX_ROUTE=luna CODEX_HOME_LUNA="$ROOT/codex-luna" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$ROOT/killed-resume-reclaim.log" \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" killed-resume resume resume-thread-abc \
  > "$ROOT/killed-resume-reclaim.out" 2>&1 \
  || { cat "$ROOT/killed-resume-reclaim.out" >&2; fail 'a SIGKILLed Codex resume name could not be reclaimed'; }
# A resume of the SAME thread deliberately keeps the recording name alive (the
# evidence preflight is a NEW-run rule), so what this asserts is that the
# relaunch was ACCEPTED: the ownership refusal - the failure a stranded lock
# causes - never fired, and the invocation wrote its own outcome.
not_contains "$ROOT/killed-resume-reclaim.out" 'is already owned by pid' \
  'a SIGKILLed resume left the run name held by the dead wrapper'
contains "$ROOT/controller/runs/killed-resume.done" 'OUTCOME=CODE-EXIT' \
  'the reclaimed resume wrote its own marker'
# The held agent stops only when this file exists; the daemon is killed last.
: > "$ROOT/killed-resume.release"
kill -9 "$(cat "$ROOT/resume-daemon.pid")" 2>/dev/null

# (3) THE AUTO-RESUME PATH, separately: the continuation loop is a THIRD exec
#     site, and it is the one that runs when the agent stopped without acting.
#     The fake agent holds only on its SECOND call, so the continuation is
#     actually reached before the check kills the wrapper.
FAKE_CODEX_REASONING=1 FAKE_CODEX_HOLD_ON_CALL=2 \
FAKE_CODEX_DAEMON="$ROOT/autoresume-daemon.pid" FAKE_CODEX_HOLD_PID="$ROOT/autoresume-hold.pid" \
FAKE_CODEX_HOLD_CALL_COUNT="$ROOT/autoresume-call-count" \
FAKE_CODEX_HOLD_UNTIL="$ROOT/killed-autoresume.release" PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" \
  CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CONTROLLER_OD_STATE="$ROOT/killed-autoresume-state.json" \
  CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$ROOT/killed-autoresume.log" CODEX_ROUTE=deepseek \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" killed-autoresume \
  > "$ROOT/killed-autoresume.out" 2>&1 &
autoresume_wrapper=$!
saw_autoresume_agent=0
for _ in {1..2000}; do
  if [[ -s $ROOT/autoresume-hold.pid && -s $ROOT/autoresume-daemon.pid ]]; then saw_autoresume_agent=1; break; fi
  kill -0 "$autoresume_wrapper" 2>/dev/null || break
  sleep 0.05
done
(( saw_autoresume_agent == 1 )) || { cat "$ROOT/killed-autoresume.out" >&2; fail 'the killed auto-resume agent never started'; }
kill -9 "$autoresume_wrapper" || fail 'the auto-resume wrapper was already gone before it was killed'
wait "$autoresume_wrapper" 2>/dev/null
autoresume_wait_rc=$?
(( autoresume_wait_rc == 137 )) || fail "the auto-resume wrapper was not killed by SIGKILL (wait status $autoresume_wait_rc)"
contains "$ROOT/controller/runs/killed-autoresume.autoresume.log" 'ROUTE=deepseek' \
  'the continuation loop was reached before the wrapper was killed'
[[ $(cat "$ROOT/autoresume-call-count") == 2 ]] \
  || fail 'the held agent was not the automatic continuation (second codex call)'
kill -0 "$(cat "$ROOT/autoresume-hold.pid")" 2>/dev/null \
  || fail 'the killed auto-resume check lost its agent, so the reclaim was not judged against a live one'
kill -0 "$(cat "$ROOT/autoresume-daemon.pid")" 2>/dev/null \
  || fail 'the killed auto-resume check lost its daemon, so the reclaim was not judged against a live one'
codex_run_is_free killed-autoresume \
  || fail 'a SIGKILLed Codex AUTO-RESUME wrapper stranded its run name while its agent lived'
CONTROLLER_RUN_FORCE=1 PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" \
  CONTROLLER_REPO="$ROOT/repo" CONTROLLER_OD_STATE="$ROOT/killed-autoresume-state.json" \
  CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
  CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$ROOT/killed-autoresume-reclaim.log" CODEX_ROUTE=deepseek \
  CODEX_AUTO_CONTINUE=0 \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" killed-autoresume \
  > "$ROOT/killed-autoresume-reclaim.out" 2>&1 \
  || { cat "$ROOT/killed-autoresume-reclaim.out" >&2; fail 'a SIGKILLed Codex auto-resume name could not be reclaimed'; }
contains "$ROOT/killed-autoresume-reclaim.out" 'retired' \
  'the forced auto-resume reclaim reports what it retired'
: > "$ROOT/killed-autoresume.release"
kill -9 "$(cat "$ROOT/autoresume-daemon.pid")" 2>/dev/null

# --- CTL-23: a REFUSED launch leaves the previous owner record untouched ------
#
# The claim holds the name, but the owner record is this invocation's identity
# and belongs to a launch that really starts. A refusal that happens after the
# claim (a bad route, a bad continuation count, both Open Design accounts
# blocked) must not overwrite the identity of the previous owner: job-check
# would then report DIED for a name that never started and the real previous
# owner's evidence would be gone.
OWNER_LINE='pid=2147483646 started_epoch=1700000000 started_utc=2023-11-14T22:13:20Z pid_start_ticks=12345 boot_id=previous-owner'
refused_mode=()
refused_launch() {
  # $1 run name, $2 expected exit, $3 refusal text, remaining: `env NAME=value`
  # words for this launch. `refused_mode` carries the optional resume arguments.
  local name=$1 want_rc=$2 text=$3 rc=0
  shift 3
  PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
    CODEX_HOME_LUNA="$ROOT/codex-luna" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
    CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$ROOT/refused-$name.log" "$@" \
    "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" "$name" "${refused_mode[@]}" \
    > "$ROOT/refused-$name.out" 2>&1 && rc=0 || rc=$?
  [[ $rc == "$want_rc" ]] || { cat "$ROOT/refused-$name.out" >&2; fail "the $name launch exited $rc, expected $want_rc"; }
  contains "$ROOT/refused-$name.out" "$text" "the $name refusal says why"
}
runs_dir=$ROOT/controller/runs
for case_name in bad-route bad-count both-blocked; do
  printf '%s\n' "$OWNER_LINE" > "$runs_dir/keep-$case_name.lock"
  cp -- "$runs_dir/keep-$case_name.lock" "$ROOT/keep-$case_name.expected"
done
refused_launch keep-bad-route 2 'unknown CODEX_ROUTE=nonsense' env CODEX_ROUTE=nonsense
refused_launch keep-bad-count 2 'CODEX_AUTO_CONTINUE must be a non-negative integer' \
  env CODEX_ROUTE=luna CODEX_AUTO_CONTINUE=many
refused_launch keep-both-blocked 3 'earliest reset' \
  env CODEX_ROUTE=deepseek CONTROLLER_OD_STATE="$ROOT/od-both-blocked.json" CODEX_AUTO_CONTINUE=0
for case_name in bad-route bad-count both-blocked; do
  cmp -s -- "$runs_dir/keep-$case_name.lock" "$ROOT/keep-$case_name.expected" \
    || fail "a refused $case_name launch overwrote the previous owner record"
  for extension in 'done' jsonl err wt route thread; do
    [[ ! -e $runs_dir/keep-$case_name.$extension ]] \
      || fail "a refused $case_name launch left a .$extension behind"
  done
done
# A refused RESUME of a finished run keeps the run's own completion marker and
# its owner record: nothing of the previous run is lost to a launch that never
# started.
printf 'EXIT=0 OUTCOME=CODE-EXIT AUTORESUME=0\n' > "$runs_dir/keep-resume.done"
printf '%s\n' "$OWNER_LINE" > "$runs_dir/keep-resume.lock"
cp -- "$runs_dir/keep-resume.lock" "$ROOT/keep-resume.expected"
refused_mode=(resume keep-resume-thread)
refused_launch keep-resume 2 'unknown CODEX_ROUTE=nonsense' env CODEX_ROUTE=nonsense
refused_mode=()
cmp -s -- "$runs_dir/keep-resume.lock" "$ROOT/keep-resume.expected" \
  || fail 'a refused resume overwrote the previous owner record'
contains "$runs_dir/keep-resume.done" 'EXIT=0 OUTCOME=CODE-EXIT' 'a refused resume kept the previous completion marker'
# A refused launch of a name nobody ever ran leaves only the empty lock file the
# claim created: no owner identity at all, so no reader can mistake it for a
# wrapper that lived and died.
refused_launch keep-fresh 2 'unknown CODEX_ROUTE=nonsense' env CODEX_ROUTE=nonsense
[[ -e $runs_dir/keep-fresh.lock && ! -s $runs_dir/keep-fresh.lock ]] \
  || fail 'a refused launch of a fresh name wrote an owner record'
# A FORCED launch that is then refused keeps the previous records too: the
# retirement waits until the launch is real, so a bad route under
# CONTROLLER_RUN_FORCE=1 moves nothing into retired/ and leaves the owner record.
printf 'EXIT=0 OUTCOME=CODE-EXIT AUTORESUME=0\n' > "$runs_dir/keep-forced.done"
printf '{"type":"thread.started","thread_id":"forced-thread"}\n' > "$runs_dir/keep-forced.jsonl"
printf '%s\n' "$OWNER_LINE" > "$runs_dir/keep-forced.lock"
cp -- "$runs_dir/keep-forced.lock" "$ROOT/keep-forced.expected"
refused_launch keep-forced 2 'unknown CODEX_ROUTE=nonsense' env CODEX_ROUTE=nonsense CONTROLLER_RUN_FORCE=1
cmp -s -- "$runs_dir/keep-forced.lock" "$ROOT/keep-forced.expected" \
  || fail 'a refused forced launch overwrote the previous owner record'
contains "$runs_dir/keep-forced.done" 'EXIT=0 OUTCOME=CODE-EXIT' 'a refused forced launch kept the previous completion marker'
[[ -e $runs_dir/keep-forced.jsonl && ! -e $runs_dir/retired/keep-forced ]] \
  || fail 'a refused forced launch retired the previous records'
# The same forced launch with a valid route DOES retire them and proceeds.
refused_launch keep-forced 0 'retired' env CODEX_ROUTE=luna CONTROLLER_RUN_FORCE=1
[[ -d $runs_dir/retired/keep-forced ]] || fail 'an accepted forced launch did not retire the previous records'

if [[ ${CONTROLLER_TEST_PARALLEL_OD_STARTS:-1} == 1 ]]; then
  PARALLEL_DIR=$ROOT/parallel-codex-logs
  mkdir -p "$PARALLEL_DIR"
  parallel_pids=()
  for index in {1..20}; do
    run="parallel-start-$index"
    PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
      CONTROLLER_OD_STATE="$ROOT/parallel-state.json" CODEX_HOME_DEEPSEEK_VOLKER="$VOLKER_HOME" CODEX_HOME_DEEPSEEK_GITHUB2="$GITHUB2_HOME" \
      CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_LOG="$PARALLEL_DIR/$index.log" CODEX_ROUTE=deepseek CODEX_AUTO_CONTINUE=0 \
      OPENDESIGN_API_KEY_VOLKER=test-key OPENDESIGN_API_KEY_GITHUB2=test-key \
      "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" "$run" > "$PARALLEL_DIR/$index.out" 2>&1 &
    parallel_pids+=("$!")
  done
  for pid in "${parallel_pids[@]}"; do
    wait "$pid" || fail 'parallel automatic DeepSeek start failed'
  done
  python3 - "$ROOT/controller/runs" "$ROOT/parallel-state.json" <<'PY' || fail 'parallel starts lost account state or failed to split evenly'
from pathlib import Path
import json, sys
runs = Path(sys.argv[1])
routes = [path.read_text(encoding="utf-8").strip() for path in runs.glob("parallel-start-*.route")]
assert len(routes) == 20
assert routes.count("deepseek-volker") == 10
assert routes.count("deepseek-github2") == 10
state = json.load(open(sys.argv[2], encoding="utf-8"))
assert set(state) == {"volker", "github2", "threads"}
assert state["volker"]["last_used_epoch"] > 0 and state["github2"]["last_used_epoch"] > 0
assert state["threads"] == {}
PY
fi

if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CODEX_ROUTE=unknown CODEX_LOG="$CODEX_LOG" \
  "$CODEX_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" bad-route > "$ROOT/route.out" 2>&1; then
  fail 'unknown Codex route was accepted'
fi
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CODEX_HOME_REVIEW="$ROOT/codex-review" CODEX_HOME_OVERRIDE="$ROOT/codex-hijack" CODEX_LOG="$CODEX_LOG" \
  "$SCRIPT_DIR/../codex-review.sh" "$ROOT/worktree" "$ROOT/brief.md" review-run > "$ROOT/review.out" 2>&1; then :; else fail 'Codex review wrapper failed'; fi
contains "$CODEX_LOG" "HOME=$ROOT/codex-review" 'review uses its separately configured profile'
not_contains "$CODEX_LOG" "HOME=$ROOT/codex-hijack" 'review ignores a caller CODEX_HOME_OVERRIDE'
contains "$CODEX_LOG" 'gpt-6-sol' 'review uses the independent review model default'
contains "$ROOT/controller/runs/review-run.route" sol 'the review wrapper records the sol route'

PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  OPENCODE_COMMAND="$ROOT/bin/opencode" OPENCODE_LOG="$OPENCODE_LOG" \
  "$OPENCODE_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" opencode-run > "$ROOT/opencode.out" 2>&1 \
  || fail 'OpenCode wrapper failed'
contains "$ROOT/controller/runs/opencode-run.last.txt" opencode-complete 'OpenCode final text is extracted'
contains "$ROOT/controller/runs/opencode-run.done" 'EXIT=0' 'OpenCode completion is persisted'
contains "$OPENCODE_LOG" 'run -m opendesign_github2/deepseek-v4.1-flash-eco' 'OpenCode defaults to the github2 provider'
OPENCODE_LOG=$ROOT/opencode-override.log PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  OPENCODE_COMMAND="$ROOT/bin/opencode" OPENCODE_PROVIDER=opendesign_volker \
  "$OPENCODE_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" opencode-provider > "$ROOT/opencode-provider.out" 2>&1 \
  || fail 'OpenCode provider override failed'
contains "$ROOT/opencode-override.log" 'run -m opendesign_volker/deepseek-v4.1-flash-eco' 'OpenCode provider override selects volker'
OPENCODE_LOG=$ROOT/opencode-model.log PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  OPENCODE_COMMAND="$ROOT/bin/opencode" OPENCODE_PROVIDER=opendesign_volker OPENCODE_MODEL=opendesign_github2/custom-model \
  "$OPENCODE_IMPL_SCRIPT" "$ROOT/worktree" "$ROOT/brief.md" opencode-model > "$ROOT/opencode-model.out" 2>&1 \
  || fail 'OpenCode full model override failed'
contains "$ROOT/opencode-model.log" 'run -m opendesign_github2/custom-model' 'full OpenCode model overrides provider selection'

REL_REPO=$ROOT/rel-worktree
mkdir -p "$REL_REPO/project/logs" "$REL_REPO/project/artifacts" "$REL_REPO/project/evidence"
git -C "$REL_REPO" init -q
git -C "$REL_REPO" config user.name test
git -C "$REL_REPO" config user.email test@example.invalid
printf 'log evidence\n' > "$REL_REPO/project/logs/run.log"
printf 'build output\n' > "$REL_REPO/project/artifacts/output.txt"
printf 'gate evidence\n' > "$REL_REPO/project/evidence/gate.txt"
git -C "$REL_REPO" add project && git -C "$REL_REPO" commit -qm baseline
git -C "$REL_REPO" branch -M rel-test
printf 'some avg10=1.00 avg60=1.00 avg300=1.00 total=1\n' > "$ROOT/memory.pressure"
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CMRU_LOG="$CMRU_LOG" CMRU_RC=0 RELEASE_PSI_LIMIT=invalid RELEASE_PSI_FILE="$ROOT/missing.pressure" \
  "$RELEASE_SCRIPT" "$REL_REPO" invalid-psi-limit.log --venv "$ROOT/venv" -- project \
  > "$ROOT/release-invalid-limit.out" 2>&1; then
  fail 'invalid RELEASE_PSI_LIMIT was accepted'
else rc=$?; [[ $rc == 2 ]] || fail "invalid PSI limit exit was $rc, expected 2"; fi
[[ ! -e $CMRU_LOG ]] || fail 'CMRU ran after invalid RELEASE_PSI_LIMIT'
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CMRU_LOG="$CMRU_LOG" CMRU_RC=0 RELEASE_PSI_POLL_SECONDS=invalid RELEASE_PSI_FILE="$ROOT/memory.pressure" \
  "$RELEASE_SCRIPT" "$REL_REPO" invalid-psi-poll.log --venv "$ROOT/venv" -- project \
  > "$ROOT/release-invalid-poll.out" 2>&1; then
  fail 'invalid RELEASE_PSI_POLL_SECONDS was accepted'
else rc=$?; [[ $rc == 2 ]] || fail "invalid PSI poll exit was $rc, expected 2"; fi
[[ ! -e $CMRU_LOG ]] || fail 'CMRU ran after invalid RELEASE_PSI_POLL_SECONDS'
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CMRU_LOG="$CMRU_LOG" CMRU_RC=7 RELEASE_PSI_FILE="$ROOT/memory.pressure" \
  "$RELEASE_SCRIPT" "$REL_REPO" release-test.log --venv "$ROOT/venv" -- project --set-version 1.2.3 \
  > "$ROOT/release.out" 2>&1; then fail 'release child failure was not propagated'; else rc=$?; [[ $rc == 7 ]] || fail "release exit was $rc, expected 7"; fi
contains "$ROOT/controller/logs/release-test.log" 'EXIT=7' 'release wrapper writes the child exit marker'
contains "$CMRU_LOG" 'release project --set-version 1.2.3' 'release argv is preserved'
archive=$(find "$ROOT/controller/releases-archive" -mindepth 1 -maxdepth 1 -type d -print -quit)
[[ -n $archive ]] || fail 'rel-* worktree evidence was not archived'
contains "$archive/project/logs/run.log" 'log evidence' 'project logs are archived before release'
contains "$archive/project/artifacts/output.txt" 'build output' 'project artifacts are archived before release'
contains "$archive/project/evidence/gate.txt" 'gate evidence' 'project evidence is archived before release'
printf 'dirty\n' > "$REL_REPO/untracked.txt"
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" CONTROLLER_REPO="$ROOT/repo" \
  CMRU_LOG="$CMRU_LOG" RELEASE_PSI_FILE="$ROOT/memory.pressure" \
  "$RELEASE_SCRIPT" "$REL_REPO" dirty.log --venv "$ROOT/venv" -- project \
  > "$ROOT/release-dirty.out" 2>&1; then fail 'dirty release worktree was accepted'; fi
contains "$ROOT/release-dirty.out" 'refusing release from dirty worktree' 'release requires a clean worktree'

PATH="$ROOT/bin:$PATH" HX_SCRIPT="$ROOT/bin/fake-hx" HX_LOG="$HX_LOG" \
  "$PUSH_IMAGE_SCRIPT" runner-a tester-unified:local > "$ROOT/push.out" 2>&1 || fail 'image transfer failed'
contains "$ROOT/push.out" 'PUSH-EXIT=0/0/0' 'image transfer reports each pipeline exit'
contains "$ROOT/push.out" 'remote-image' 'image transfer checks the remote image'
rm -f -- "$ROOT/hx.log"
if PATH="$ROOT/bin:$PATH" HX_SCRIPT="$ROOT/bin/fake-hx" HX_LOG="$HX_LOG" HX_LOAD_RC=8 \
  "$PUSH_IMAGE_SCRIPT" runner-a tester-unified:local > "$ROOT/push-fail.out" 2>&1; then
  fail 'failed remote image load was accepted'
fi
contains "$ROOT/push-fail.out" 'PUSH-EXIT=0/0/8' 'pipeline captures the remote loader failure'
if "$PUSH_IMAGE_SCRIPT" runner-a 'image with spaces' > "$ROOT/push-invalid.out" 2>&1; then
  fail 'invalid image reference was accepted'
fi

echo 'PASS wrappers: identity SSH, route handling, child exit markers, release archive, pipeline status'
