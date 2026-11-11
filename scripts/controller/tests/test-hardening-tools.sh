#!/usr/bin/env bash
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
CODEX_IMPL=${CODEX_IMPL_SCRIPT:-$SCRIPT_DIR/../codex-impl.sh}
RESUME_SRC=${RESUME_USAGE_LIMITED_SCRIPT:-$SCRIPT_DIR/../resume-usage-limited.sh}
START_AFTER_SRC=${START_AFTER_SCRIPT:-$SCRIPT_DIR/../start-after.sh}
GATE_RUN_SRC=${GATE_RUN_SCRIPT:-$SCRIPT_DIR/../gate-run.sh}
READBACK_SRC=${BUILD_READBACK_SCRIPT:-$SCRIPT_DIR/../build-readback.py}
LIB_SRC=${CONTROLLER_LIB_SCRIPT:-$SCRIPT_DIR/../lib.sh}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-hardening-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }
not_contains() { grep -qF -- "$2" "$1" && fail "$3 (unexpected: $2)"; }

mkdir -p "$ROOT/home" "$ROOT/bin" "$ROOT/worktree" "$ROOT/controller" "$ROOT/deepseek/catalog"
LIB_TEST_HOME=$ROOT/lib-config-home
LIB_TEST_RUNS=$ROOT/lib-run-dir-override
mkdir -p "$LIB_TEST_HOME" "$LIB_TEST_RUNS"
printf "CONTROLLER_RUNS_DIR=\"\$CONTROLLER_HOME/configured-runs\"\n" > "$LIB_TEST_HOME/controller.local.env"
if HOME="$ROOT/home" CONTROLLER_HOME="$LIB_TEST_HOME" CONTROLLER_RUNS_DIR="$LIB_TEST_RUNS" \
  CONTROLLER_LIB_SCRIPT="$LIB_SRC" EXPECTED_RUNS_DIR="$LIB_TEST_RUNS" \
  bash -c '. "$CONTROLLER_LIB_SCRIPT"; controller_init; [[ $CONTROLLER_RUNS_DIR == "$EXPECTED_RUNS_DIR" ]]' \
  > "$ROOT/run-dir-override.out" 2>&1; then
  :
else
  fail 'environment run directory did not override the local configuration file'
fi
cat > "$ROOT/bin/codex" <<'SH'
#!/usr/bin/env bash
set -uo pipefail
printf '%s\n' "$*" >> "$CODEX_CALL_LOG"
if [[ $* == *'exec resume'* ]]; then
  IFS= read -r resume_prompt
  printf '%s\n' "$resume_prompt" >> "$CODEX_RESUME_PROMPTS"
fi
count=$(cat "$CODEX_CALL_COUNT" 2>/dev/null || echo 0)
count=$((count + 1))
printf '%s\n' "$count" > "$CODEX_CALL_COUNT"
printf '{"type":"thread.started","thread_id":"thread-for-test"}\n'
printf '{"type":"turn.started"}\n'
if [[ ${FAKE_CODEX_MODE:-reasoning} == clean ]]; then
  printf '{"type":"item.completed","item":{"type":"agent_message","text":"Task complete."}}\n'
else
  printf '{"type":"item.completed","item":{"type":"reasoning","text":"Thinking without an action."}}\n'
fi
printf '{"type":"turn.completed"}\n'
SH
cat > "$ROOT/bin/fake-impl" <<'SH'
#!/usr/bin/env bash
set -uo pipefail
printf '%s\n' "$*" >> "$RESUME_CALL_LOG"
printf 'CODEX_ROUTE=%s\n' "${CODEX_ROUTE:-unset}" >> "$RESUME_CALL_LOG"
printf 'CONTROLLER_RUNS_DIR=%s\n' "${CONTROLLER_RUNS_DIR:-unset}" >> "$RESUME_CALL_LOG"
printf 'EXIT=0\n' > "$RESUME_RUNS_DIR/$3.done"
SH
cat > "$ROOT/bin/pgrep" <<'SH'
#!/usr/bin/env bash
if [[ -n ${FAKE_PGREP_LIVE:-} && $* == *"$FAKE_PGREP_LIVE"* ]]; then exit 0; fi
exit 1
SH
cat > "$ROOT/bin/after-impl" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" > "$AFTER_IMPL_LOG"
SH
cat > "$ROOT/bin/run-gate" <<'SH'
#!/usr/bin/env bash
set -uo pipefail
printf '%s|%s\n' "$PWD" "$*" >> "$RUN_GATE_LOG"
exit "${FAKE_RUN_GATE_RC:-0}"
SH
chmod +x "$ROOT/bin/codex" "$ROOT/bin/fake-impl" "$ROOT/bin/pgrep" "$ROOT/bin/after-impl" "$ROOT/bin/run-gate"
printf '{"models":[{"slug":"fixture","tool_mode":null}]}\n' > "$ROOT/deepseek/catalog/opendesign-models.json"

unset CODEX_ROUTE CODEX_AUTO_CONTINUE CODEX_HOME_OVERRIDE CODEX_MODEL CODEX_EFFORT CODEX_HOME CODEX_SQLITE_HOME
unset CONTROLLER_RUNS_DIR CONTROLLER_LOGS_DIR CONTROLLER_GATE_LOCK CONTROLLER_GATE2_LOCK RESUME_EXCLUDE_PREFIXES
CODEX_CALL_LOG=$ROOT/codex-calls.log
CODEX_CALL_COUNT=$ROOT/codex-call-count
CODEX_RESUME_PROMPTS=$ROOT/codex-resume-prompts.log
export CODEX_CALL_LOG CODEX_CALL_COUNT CODEX_RESUME_PROMPTS
printf 'brief\n' > "$ROOT/prompt.md"
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" \
  CODEX_HOME_LUNA="$ROOT/luna" CODEX_HOME_DEEPSEEK="$ROOT/deepseek" CODEX_SQLITE_HOME="$ROOT/sqlite" \
  CODEX_ROUTE=deepseek "$CODEX_IMPL" "$ROOT/worktree" "$ROOT/prompt.md" deepseek-bound \
  > "$ROOT/deepseek.out" 2>&1 || fail 'DeepSeek auto-continue run failed'
[[ $(wc -l < "$CODEX_CALL_LOG") == 3 ]] || fail 'DeepSeek default must make one initial call and at most two resumes'
[[ $(grep -c 'exec resume' "$CODEX_CALL_LOG") == 2 ]] || fail 'DeepSeek run did not resume twice'
[[ $(grep -c 'thread-for-test' "$CODEX_CALL_LOG") == 2 ]] || fail 'auto-resume did not reuse the original thread id'
[[ $(wc -l < "$CODEX_RESUME_PROMPTS") == 2 ]] || fail 'auto-resume prompt was not sent on each continuation'
contains "$CODEX_RESUME_PROMPTS" 'You stopped after thinking without acting: continue the task from where you are now, call tools for real' 'auto-resume uses the requested continuation prompt'
contains "$ROOT/controller/runs/deepseek-bound.done" 'EXIT=0 AUTORESUME=2' 'final done marker records bounded resumes'
contains "$ROOT/controller/runs/deepseek-bound.autoresume.log" 'attempt=2 EXIT=0 THREAD=thread-for-test' 'auto-resume log records its second attempt'
contains "$ROOT/controller/runs/deepseek-bound.wt" "$ROOT/worktree" 'Codex run stores its explicit worktree map for later recovery'
contains "$ROOT/controller/runs/deepseek-bound.route" 'deepseek' 'Codex run records its originating route'

rm -f -- "$CODEX_CALL_LOG" "$CODEX_CALL_COUNT"
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" \
  CODEX_HOME_LUNA="$ROOT/luna" CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_ROUTE=luna \
  "$CODEX_IMPL" "$ROOT/worktree" "$ROOT/prompt.md" luna-reasoning > "$ROOT/luna.out" 2>&1 \
  || fail 'Luna reasoning-end run failed'
[[ $(wc -l < "$CODEX_CALL_LOG") == 1 ]] || fail 'Luna default must not auto-continue'
contains "$ROOT/controller/runs/luna-reasoning.done" 'EXIT=0 AUTORESUME=0' 'Luna done marker records zero resumes'

rm -f -- "$CODEX_CALL_LOG" "$CODEX_CALL_COUNT"
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" \
  CODEX_HOME_DEEPSEEK="$ROOT/deepseek" CODEX_SQLITE_HOME="$ROOT/sqlite" CODEX_ROUTE=deepseek \
  FAKE_CODEX_MODE=clean "$CODEX_IMPL" "$ROOT/worktree" "$ROOT/prompt.md" deepseek-clean \
  > "$ROOT/deepseek-clean.out" 2>&1 || fail 'clean DeepSeek run failed'
[[ $(wc -l < "$CODEX_CALL_LOG") == 1 ]] || fail 'clean run should not be auto-resumed'
contains "$ROOT/controller/runs/deepseek-clean.done" 'AUTORESUME=0' 'clean run records no resume attempts'

CUSTOM_WRAPPER_RUNS=$ROOT/custom-wrapper-runs
mkdir -p "$CUSTOM_WRAPPER_RUNS"
printf "CONTROLLER_RUNS_DIR=\"\$CONTROLLER_HOME/configured-runs\"\n" > "$ROOT/controller/controller.local.env"
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" \
  CONTROLLER_RUNS_DIR="$CUSTOM_WRAPPER_RUNS" CODEX_HOME_LUNA="$ROOT/luna" CODEX_ROUTE=luna \
  FAKE_CODEX_MODE=clean "$CODEX_IMPL" "$ROOT/worktree" "$ROOT/prompt.md" custom-run-dir \
  > "$ROOT/custom-run-dir.out" 2>&1 || fail 'Codex wrapper rejected a configured run-record directory'
contains "$CUSTOM_WRAPPER_RUNS/custom-run-dir.done" 'EXIT=0 AUTORESUME=0' 'Codex wrapper writes into its configured runs directory'

rm -f -- "$CODEX_CALL_LOG" "$CODEX_CALL_COUNT"
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" \
  CODEX_HOME_LUNA="$ROOT/luna" CODEX_ROUTE=luna CODEX_AUTO_CONTINUE=invalid \
  "$CODEX_IMPL" "$ROOT/worktree" "$ROOT/prompt.md" invalid-limit > "$ROOT/invalid-limit.out" 2>&1; then
  fail 'invalid CODEX_AUTO_CONTINUE was accepted'
fi
contains "$ROOT/invalid-limit.out" 'CODEX_AUTO_CONTINUE must be a non-negative integer' 'auto-resume limit is validated'
[[ ! -e $CODEX_CALL_LOG ]] || fail 'Codex started after rejecting an invalid auto-resume limit'

if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" \
  CODEX_HOME_LUNA="$ROOT/luna" CODEX_ROUTE=luna CODEX_AUTO_CONTINUE=2147483648 \
  FAKE_CODEX_MODE=clean \
  "$CODEX_IMPL" "$ROOT/worktree" "$ROOT/prompt.md" too-large-limit > "$ROOT/too-large-limit.out" 2>&1; then
  fail 'auto-resume counter range was not enforced'
fi
contains "$ROOT/too-large-limit.out" 'CODEX_AUTO_CONTINUE must be a non-negative integer' 'auto-resume counter bound is validated'

RUNS=$ROOT/custom-runs
mkdir -p "$RUNS"
for name in limited skipme success nonusage noroute invalidroute naf-owned live-wrapper foreign-owned missingmap; do
  cp "$SCRIPT_DIR/fixtures/usage-limit.jsonl" "$RUNS/$name.jsonl"
  if [[ $name == missingmap ]]; then
    printf '%s\n' "$ROOT/worktree" > "$RUNS/$name.worktree"
  else
    printf '%s\n' "$ROOT/worktree" > "$RUNS/$name.wt"
  fi
  if [[ $name != noroute ]]; then
    if [[ $name == invalidroute ]]; then printf 'unknown\n' > "$RUNS/$name.route"; elif [[ $name == limited ]]; then printf 'deepseek\n' > "$RUNS/$name.route"; else printf 'luna\n' > "$RUNS/$name.route"; fi
  fi
  printf 'EXIT=1\n' > "$RUNS/$name.done"
done
printf 'EXIT=0\n' > "$RUNS/success.done"
printf '{"type":"thread.started","thread_id":"nonusage-thread"}\n{"type":"turn.failed","error":{"message":"connection lost"}}\n' \
  > "$RUNS/nonusage.jsonl"
RESUME_CALL_LOG=$ROOT/resume-calls.log
RESUME_RUNS_DIR=$RUNS
export RESUME_CALL_LOG RESUME_RUNS_DIR
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" \
  CONTROLLER_RUNS_DIR="$RUNS" RESUME_MAX_AGE_MINUTES=invalid CODEX_IMPL_SCRIPT="$ROOT/bin/fake-impl" \
  "$RESUME_SRC" --dry-run > "$ROOT/invalid-age.out" 2>&1; then
  fail 'invalid usage-limit run age was accepted'
fi
contains "$ROOT/invalid-age.out" 'RESUME_MAX_AGE_MINUTES must be a positive integer' 'usage-limit scan age is validated'
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" \
  CODEX_IMPL_SCRIPT="$ROOT/bin/fake-impl" RESUME_SKIP='' \
  "$RESUME_SRC" --dry-run --runs-dir "$RUNS" --skip skipme > "$ROOT/resume-dry.out" 2>&1 \
  || fail 'usage-limit dry-run failed'
contains "$ROOT/resume-dry.out" 'RESUME limited thread=01a1c574-3312-71e1-a10a-16379be028dc reset=3:46 PM' 'dry-run shows detected reset and thread'
contains "$ROOT/resume-dry.out" 'SKIP skipme (RESUME_SKIP)' 'configured skip list is honored'
contains "$ROOT/resume-dry.out" 'SKIP noroute (no route sidecar)' 'usage-limit recovery requires an originating route'
contains "$ROOT/resume-dry.out" 'SKIP invalidroute (invalid route sidecar)' 'usage-limit recovery validates the originating route'
contains "$ROOT/resume-dry.out" 'SKIP naf-owned (excluded controller prefix: naf-)' 'default foreign-controller prefix is excluded'
contains "$ROOT/resume-dry.out" 'SKIP missingmap (no valid missingmap.wt worktree map; refusing to guess)' 'missing .wt map refuses legacy worktree guessing'
not_contains "$ROOT/resume-dry.out" 'RESUME skipme' 'skipped run is not scheduled'
not_contains "$ROOT/resume-dry.out" 'RESUME missingmap' 'run without .wt is never scheduled'
[[ $(cat "$RUNS/missingmap.done") == EXIT=1 ]] || fail 'run without .wt had its completion marker changed'
[[ ! -e $RUNS/missingmap-resumed.resume.lock ]] || fail 'run without .wt had a resume lock created'
[[ ! -e $RESUME_CALL_LOG ]] || fail 'dry-run launched an implementer'

printf 'RESUME_EXCLUDE_PREFIXES="foreign-"\n' > "$ROOT/controller/controller.local.env"
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" \
  CODEX_IMPL_SCRIPT="$ROOT/bin/fake-impl" RESUME_SKIP='' \
  "$RESUME_SRC" --dry-run --runs-dir "$RUNS" --skip skipme --skip limited --skip live-wrapper --skip naf-owned \
  > "$ROOT/resume-config-prefix.out" 2>&1 || fail 'configured exclusion prefix dry-run failed'
contains "$ROOT/resume-config-prefix.out" 'SKIP foreign-owned (excluded controller prefix: foreign-)' 'configured prefix list is loaded from controller config'
rm -f -- "$ROOT/controller/controller.local.env"

FAKE_PGREP_LIVE=live-wrapper-resumed PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" \
  CODEX_IMPL_SCRIPT="$ROOT/bin/fake-impl" RESUME_SKIP='' \
  "$RESUME_SRC" --runs-dir "$RUNS" --skip skipme --skip limited --skip naf-owned --skip foreign-owned \
  > "$ROOT/resume-live-wrapper.out" 2>&1 || fail 'live-wrapper exclusion failed'
contains "$ROOT/resume-live-wrapper.out" 'SKIP live-wrapper (live-wrapper-resumed wrapper is still running)' 'live resumed wrapper is not double-launched'
not_contains "$RESUME_CALL_LOG" 'live-wrapper-resumed' 'live resumed wrapper does not reach the implementer'

PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" \
  CODEX_IMPL_SCRIPT="$ROOT/bin/fake-impl" RESUME_SKIP='' \
  "$RESUME_SRC" --runs-dir "$RUNS" --skip skipme --skip live-wrapper --skip foreign-owned > "$ROOT/resume-live.out" 2>&1 \
  || fail 'usage-limit resume invocation failed'
contains "$RESUME_CALL_LOG" 'limited-resumed resume 01a1c574-3312-71e1-a10a-16379be028dc' 'resume uses a new run name and the original thread'
contains "$RESUME_CALL_LOG" 'CODEX_ROUTE=deepseek' 'usage-limit recovery preserves the originating route'
contains "$RESUME_CALL_LOG" "CONTROLLER_RUNS_DIR=$RUNS" 'usage-limit recovery writes into its configured runs directory'
not_contains "$RESUME_CALL_LOG" 'skipme' 'skip list prevents a resume call'
not_contains "$RESUME_CALL_LOG" 'naf-owned-resumed' 'other-controller run is not resumed'
not_contains "$RESUME_CALL_LOG" 'missingmap-resumed' 'run without .wt is not resumed'
not_contains "$RESUME_CALL_LOG" 'live-wrapper-resumed' 'live wrapper is not resumed again'
not_contains "$RESUME_CALL_LOG" 'success-resumed' 'successful run is not resumed'
not_contains "$RESUME_CALL_LOG" 'nonusage-resumed' 'non-usage turn failure is not resumed'

cp "$SCRIPT_DIR/fixtures/usage-limit.jsonl" "$RUNS/claimed.jsonl"
printf '%s\n' "$ROOT/worktree" > "$RUNS/claimed.wt"
printf 'luna\n' > "$RUNS/claimed.route"
printf 'EXIT=1\n' > "$RUNS/claimed.done"
mkfifo "$ROOT/claim-ready" "$ROOT/claim-release"
(
  exec 8>"$RUNS/claimed-resumed.resume.lock"
  flock 8
  printf 'locked\n' > "$ROOT/claim-ready"
  IFS= read -r _ < "$ROOT/claim-release"
) &
CLAIM_PID=$!
IFS= read -r _ < "$ROOT/claim-ready" || fail 'resume claim holder did not start'
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$ROOT/controller" \
  CODEX_IMPL_SCRIPT="$ROOT/bin/fake-impl" RESUME_SKIP='' \
  "$RESUME_SRC" --runs-dir "$RUNS" --skip skipme --skip limited --skip live-wrapper --skip foreign-owned \
    --skip naf-owned --skip success --skip nonusage --skip noroute --skip invalidroute \
  > "$ROOT/resume-claim-held.out" 2>&1 || fail 'held resume claim handling failed'
contains "$ROOT/resume-claim-held.out" 'SKIP claimed (claimed-resumed resume claim is held; wrapper may still be running)' 'atomic claim prevents concurrent double resume'
not_contains "$RESUME_CALL_LOG" 'claimed-resumed' 'held resume claim never launches the wrapper'
printf 'release\n' > "$ROOT/claim-release"
wait "$CLAIM_PID" || fail 'resume claim holder did not exit'
rm -f -- "$ROOT/claim-ready" "$ROOT/claim-release"

mkdir -p "$ROOT/fake-clock"
cat > "$ROOT/fake-clock/date" <<'SH'
#!/usr/bin/env bash
count=$(cat "$DATE_CALL_COUNT" 2>/dev/null || echo 0)
count=$((count + 1))
printf '%s\n' "$count" > "$DATE_CALL_COUNT"
if (( count == 1 )); then echo 1200; else echo 1201; fi
SH
cat > "$ROOT/fake-clock/sleep" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$SLEEP_CALL_LOG"
SH
chmod +x "$ROOT/fake-clock/date" "$ROOT/fake-clock/sleep"
DATE_CALL_COUNT=$ROOT/date-count
SLEEP_CALL_LOG=$ROOT/sleep-calls.log
AFTER_IMPL_LOG=$ROOT/after-args.log
export DATE_CALL_COUNT SLEEP_CALL_LOG AFTER_IMPL_LOG
PATH="$ROOT/fake-clock:$PATH" CODEX_IMPL_SCRIPT="$ROOT/bin/after-impl" \
  "$START_AFTER_SRC" 1201 "$ROOT/worktree" "$ROOT/prompt.md" scheduled-run \
  > "$ROOT/start-after.out" 2>&1 || fail 'start-after helper failed'
[[ $(cat "$DATE_CALL_COUNT") == 2 ]] || fail 'start-after did not check the clock again after waiting'
contains "$SLEEP_CALL_LOG" '20' 'start-after waits between UTC clock checks'
contains "$AFTER_IMPL_LOG" 'scheduled-run' 'start-after forwards launch arguments'
mkdir -p "$ROOT/invalid-clock"
cat > "$ROOT/invalid-clock/date" <<'SH'
#!/usr/bin/env bash
printf '2460\n'
SH
chmod +x "$ROOT/invalid-clock/date"
if PATH="$ROOT/invalid-clock:$PATH" CODEX_IMPL_SCRIPT="$ROOT/bin/after-impl" \
  "$START_AFTER_SRC" 2460 "$ROOT/worktree" "$ROOT/prompt.md" bad-time > "$ROOT/invalid-time.out" 2>&1; then
  fail 'invalid UTC launch time was accepted'
fi
contains "$ROOT/invalid-time.out" 'invalid UTC time: 2460' 'start-after validates the scheduled UTC time'

LOCK_HOME=$ROOT/gate-home
LOCK1=$ROOT/locks/gate-one.lock
LOCK2=$ROOT/locks/gate-two.lock
mkdir -p "$LOCK_HOME" "$ROOT/locks"
RUN_GATE_LOG=$ROOT/run-gate.log
export RUN_GATE_LOG
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$LOCK_HOME" \
  CONTROLLER_GATE_LOCK="$LOCK1" CONTROLLER_GATE2_LOCK="$LOCK2" \
  "$GATE_RUN_SRC" suite --verbose > "$ROOT/gate-first.out" 2>&1 \
  || fail 'gate-run failed in the first free slot'
contains "$ROOT/gate-first.out" 'slot gate.lock' 'gate-run uses the first configured slot'

hold_lock() {
  local lock=$1 ready=$2 release=$3
  mkfifo "$ready" "$release"
  (
    exec 8>"$lock"
    flock 8
    printf 'locked\n' > "$ready"
    IFS= read -r _ < "$release"
  ) &
  HOLDER_PID=$!
  IFS= read -r _ < "$ready" || fail "lock holder did not start for $lock"
}
release_lock() {
  local release=$1 pid=$2 ready=$3
  printf 'release\n' > "$release"
  wait "$pid" || fail 'gate lock holder did not exit'
  rm -f -- "$ready" "$release"
}

hold_lock "$LOCK1" "$ROOT/ready-one" "$ROOT/release-one"
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$LOCK_HOME" \
  CONTROLLER_GATE_LOCK="$LOCK1" CONTROLLER_GATE2_LOCK="$LOCK2" \
  "$GATE_RUN_SRC" suite --second > "$ROOT/gate-second.out" 2>&1 \
  || fail 'gate-run failed to use its second slot'
contains "$ROOT/gate-second.out" 'slot gate2.lock' 'busy first slot falls through to the second slot'
release_lock "$ROOT/release-one" "$HOLDER_PID" "$ROOT/ready-one"

hold_lock "$LOCK1" "$ROOT/ready-one" "$ROOT/release-one"
FIRST_HOLDER_PID=$HOLDER_PID
hold_lock "$LOCK2" "$ROOT/ready-two" "$ROOT/release-two"
mkfifo "$ROOT/gate-wait.fifo"
PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$LOCK_HOME" \
  CONTROLLER_GATE_LOCK="$LOCK1" CONTROLLER_GATE2_LOCK="$LOCK2" \
  "$GATE_RUN_SRC" suite --wait > "$ROOT/gate-wait.out" 2> "$ROOT/gate-wait.fifo" &
GATE_PID=$!
exec 7< "$ROOT/gate-wait.fifo"
IFS= read -r wait_line <&7 || fail 'gate-run did not announce its blocking wait'
[[ $wait_line == 'gate-run: both slots busy, waiting on gate.lock' ]] || fail "unexpected wait line: $wait_line"
release_lock "$ROOT/release-one" "$FIRST_HOLDER_PID" "$ROOT/ready-one"
IFS= read -r slot_line <&7 || fail 'gate-run did not announce acquisition of the first slot'
[[ $slot_line == 'gate-run: slot gate.lock' ]] || fail "unexpected acquired-slot line: $slot_line"
wait "$GATE_PID" || fail 'gate-run did not resume after the first slot opened'
exec 7<&-
release_lock "$ROOT/release-two" "$HOLDER_PID" "$ROOT/ready-two"

before=$(wc -l < "$RUN_GATE_LOG")
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$LOCK_HOME" \
  CONTROLLER_GATE_LOCK="$LOCK1" CONTROLLER_GATE2_LOCK="$LOCK1" \
  "$GATE_RUN_SRC" suite > "$ROOT/gate-duplicate.out" 2>&1; then
  fail 'duplicate gate lock paths were accepted'
fi
contains "$ROOT/gate-duplicate.out" 'gate lock paths must be distinct' 'gate lock slots must be distinct'
[[ $(wc -l < "$RUN_GATE_LOG") == "$before" ]] || fail 'run-gate started with duplicate lock paths'
if (cd -- "$ROOT" && PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$LOCK_HOME" \
  CONTROLLER_GATE_LOCK=relative-one.lock CONTROLLER_GATE2_LOCK="$LOCK2" \
  "$GATE_RUN_SRC" suite > "$ROOT/gate-relative.out" 2>&1); then
  fail 'relative gate lock path was accepted'
fi
contains "$ROOT/gate-relative.out" 'must both be configured as absolute paths' 'gate lock paths must be absolute'
[[ $(wc -l < "$RUN_GATE_LOG") == "$before" ]] || fail 'run-gate started with a relative lock path'
if PATH="$ROOT/bin:$PATH" HOME="$ROOT/home" CONTROLLER_HOME="$LOCK_HOME" \
  CONTROLLER_GATE_LOCK="$LOCK1" CONTROLLER_GATE2_LOCK="$LOCK2" FAKE_RUN_GATE_RC=7 \
  "$GATE_RUN_SRC" suite > "$ROOT/gate-fail.out" 2>&1; then
  fail 'gate-run hid the gate command exit code'
else rc=$?; [[ $rc == 7 ]] || fail "gate-run returned $rc instead of the gate exit 7"; fi

SESSION=$ROOT/session.jsonl
RUNS_DIR=$ROOT/readback-runs
mkdir -p "$RUNS_DIR"
python3 - "$SESSION" "$RUNS_DIR" <<'PY'
import datetime as dt
import json
import pathlib
import sys

session = pathlib.Path(sys.argv[1])
runs = pathlib.Path(sys.argv[2])
now = dt.datetime.now(dt.timezone.utc)
cutoff = (now - dt.timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
after = now.isoformat().replace("+00:00", "Z")
events = [
    {"uuid": "marker-fixture", "timestamp": cutoff, "type": "user", "message": {"content": "marker"}},
    {"timestamp": after, "type": "user", "message": {"content": [
        {"type": "text", "text": "<task-notification><summary>Worker complete</summary><result>subagent-result-fixture</result></task-notification>"}
    ]}},
    {"timestamp": after, "type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Agent", "input": {"description": "worker", "prompt": "subagent-prompt-fixture"}}
    ]}},
]
session.write_text("\n".join(json.dumps(event) for event in events) + "\n")
(runs / "sample.done").write_text("EXIT=0 AUTORESUME=0\n")
(runs / "sample.last.txt").write_text("readback-run-final-fixture")
PY
cat > "$ROOT/bin/fake-harness" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" > "$HARNESS_ARGS"
python3 -c 'print("session-extract-fixture " * 500)'
SH
chmod +x "$ROOT/bin/fake-harness"
HARNESS_ARGS=$ROOT/harness-args.log
READBACK_OUT=$ROOT/readback.md
export HARNESS_ARGS
PATH="$ROOT/bin:$PATH" python3 "$READBACK_SRC" --since-marker marker-fixture --session "$SESSION" \
  --runs-dir "$RUNS_DIR" --out "$READBACK_OUT" --harness "$ROOT/bin/fake-harness" --budget-chars 1300 \
  > "$ROOT/readback.stdout" 2> "$ROOT/readback.stderr" || fail 'readback builder failed with explicit paths and budget'
[[ $(wc -c < "$READBACK_OUT") -le 1300 ]] || fail 'readback output exceeded the configured character budget'
contains "$READBACK_OUT" 'PART 1:' 'readback contains session extract section'
contains "$READBACK_OUT" 'PART 2:' 'readback contains subagent results section'
contains "$READBACK_OUT" 'PART 3:' 'readback contains subagent prompts section'
contains "$READBACK_OUT" 'PART 4:' 'readback contains Codex run finals section'
contains "$READBACK_OUT" 'subagent-result-fixture' 'readback includes returned subagent result'
contains "$READBACK_OUT" 'subagent-prompt-fixture' 'readback includes sent subagent prompt'
contains "$READBACK_OUT" 'readback-run-final-fixture' 'readback includes run final'
contains "$HARNESS_ARGS" '--since marker-fixture' 'session marker is passed to nyxloom-harness'
if PATH="$ROOT/bin:$PATH" python3 "$READBACK_SRC" --since-marker absent-marker --session "$SESSION" \
  --runs-dir "$RUNS_DIR" --out "$ROOT/missing-marker.md" --harness "$ROOT/bin/fake-harness" \
  > "$ROOT/missing-marker.out" 2>&1; then
  fail 'readback builder accepted a missing session marker'
fi
contains "$ROOT/missing-marker.out" 'session marker not found' 'missing session marker fails loudly'
if PATH="$ROOT/bin:$PATH" python3 "$READBACK_SRC" --since-marker marker-fixture --session "$SESSION" \
  --runs-dir "$RUNS_DIR" --out "$ROOT/tiny-budget.md" --harness "$ROOT/bin/fake-harness" --budget-chars 100 \
  > "$ROOT/tiny-budget.out" 2>&1; then
  fail 'readback builder accepted an unusably small character budget'
fi
contains "$ROOT/tiny-budget.out" '--budget-chars must be at least 512' 'readback budget has a validated minimum'

echo 'PASS hardening tools: bounded resume, usage recovery, UTC scheduling, two-slot gates and bounded readback'
