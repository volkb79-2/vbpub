#!/usr/bin/env bash
set -uo pipefail
unset NODE_OPTIONS
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-mutation-runner-test.XXXXXX")
SURVIVOR_FIRST_FD=
SURVIVOR_SECOND_FD=
LATE_EARLIER_FD=
LATE_WORKER_FD=
QUEUE_FIRST_FD=
QUEUE_SECOND_FD=
cleanup() {
  if [[ -n $SURVIVOR_FIRST_FD ]]; then printf 'release\n' >&"$SURVIVOR_FIRST_FD" || :; fi
  if [[ -n $SURVIVOR_SECOND_FD ]]; then printf 'release\n' >&"$SURVIVOR_SECOND_FD" || :; fi
  if [[ -n $LATE_EARLIER_FD ]]; then printf 'release\n' >&"$LATE_EARLIER_FD" || :; fi
  if [[ -n $LATE_WORKER_FD ]]; then printf 'release\n' >&"$LATE_WORKER_FD" || :; fi
  if [[ -n $QUEUE_FIRST_FD ]]; then printf 'release\n' >&"$QUEUE_FIRST_FD" || :; fi
  if [[ -n $QUEUE_SECOND_FD ]]; then printf 'release\n' >&"$QUEUE_SECOND_FD" || :; fi
  rm -rf -- "$ROOT"
}
trap cleanup EXIT
RUNNER=${CONTROLLER_MUTATION_RUNNER_SCRIPT:-$SCRIPT_DIR/mutation-runner.sh}
# shellcheck source=mutation-runner.sh
# shellcheck disable=SC1091
source "$RUNNER"

fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -Fq -- "$2" "$1" || fail "missing '$2' in $1"; }
not_contains() { ! grep -Fq -- "$2" "$1" || fail "unexpected '$2' in $1"; }

export CONTROLLER_MUTATION_JOBS=1
mutation_runner_init "$ROOT/nonzero" || fail 'valid width 1 was refused'
{
  expect_killed nonzero-status bash -c 'printf "nonzero log line\n"; exit 9'
  if mutation_runner_finish; then nonzero_rc=0; else nonzero_rc=$?; fi
} > "$ROOT/nonzero.out" 2>&1
[[ $nonzero_rc == 0 ]] || fail 'a completed nonzero check did not pass as KILLED'
contains "$ROOT/nonzero.out" 'KILLED: nonzero-status'
contains "$ROOT/nonzero.out" 'SUMMARY: PASS checks=1 reported=1 killed=1 elapsed_seconds='
contains "$ROOT/nonzero/nonzero-status.log" 'nonzero log line'

mutation_runner_init "$ROOT/status-127" || fail 'valid width 1 was refused for status-127 case'
{
  expect_killed command-status-127 bash -c 'exit 127'
  if mutation_runner_finish; then status_127_rc=0; else status_127_rc=$?; fi
} > "$ROOT/status-127.out" 2>&1
[[ $status_127_rc == 0 ]] || fail 'an executed command returning 127 was not counted as KILLED'
contains "$ROOT/status-127.out" 'KILLED: command-status-127'

mutation_runner_init "$ROOT/override" || fail 'valid width 1 was refused for override case'
{
  # shellcheck disable=SC2016 # the variable is expanded by the child check
  expect_killed unreadable-override env PI_IMPL_SCRIPT=/nonexistent bash -c \
    'printf "ERROR: unreadable override PI_IMPL_SCRIPT: %s\n" "$PI_IMPL_SCRIPT" >&2; exit 2'
  expect_killed override-text-only bash -c 'printf "ERROR: unreadable override quoted by a test\n"; exit 2'
  if mutation_runner_finish; then override_rc=0; else override_rc=$?; fi
} > "$ROOT/override.out" 2>&1
[[ $override_rc != 0 ]] || fail 'an unreadable mutation override passed'
not_contains "$ROOT/override.out" 'KILLED: unreadable-override'
contains "$ROOT/override.out" 'KILLED: override-text-only'
contains "$ROOT/override.out" 'used an unreadable mutation override'
contains "$ROOT/override.out" 'SUMMARY: FAIL checks=2 reported=2 killed=1 elapsed_seconds='

export CONTROLLER_MUTATION_JOBS=2
mutation_runner_init "$ROOT/survivors" || fail 'valid width 2 was refused'
survivor_marker=$ROOT/survivor-second-started
after_survivor_marker=$ROOT/after-survivor-ran
survivor_first_fifo=$ROOT/survivor-first.fifo
survivor_second_fifo=$ROOT/survivor-second.fifo
mkfifo "$survivor_first_fifo" "$survivor_second_fifo" || fail 'could not create survivor FIFOs'
exec {SURVIVOR_FIRST_FD}<>"$survivor_first_fifo" || fail 'could not open first survivor FIFO'
exec {SURVIVOR_SECOND_FD}<>"$survivor_second_fifo" || fail 'could not open second survivor FIFO'
{
  # The second survivor creates its start file before releasing the first; it
  # then waits on its own FIFO until the parent has observed the first result.
  # shellcheck disable=SC2016
  expect_killed survivor-first bash -c \
    'IFS= read -r token < "$1"; [[ $token == release && -f $2 ]] || exit 8; printf "first survivor log\n"; exit 0' \
    _ "$survivor_first_fifo" "$survivor_marker"
  # shellcheck disable=SC2016
  expect_killed survivor-second bash -c \
    ': > "$1"; printf "release\n" > "$2"; IFS= read -r token < "$3"; [[ $token == release ]] || exit 9; printf "second survivor log\n"; exit 0' \
    _ "$survivor_marker" "$survivor_first_fifo" "$survivor_second_fifo"
  mutation_runner_reap_one
  # shellcheck disable=SC2016
  expect_killed after-survivors bash -c 'printf "ran\n" > "$1"; exit 7' _ "$after_survivor_marker"
  printf 'release\n' >&"$SURVIVOR_SECOND_FD"
  if mutation_runner_finish; then survivor_rc=0; else survivor_rc=$?; fi
} > "$ROOT/survivors.out" 2>&1
exec {SURVIVOR_FIRST_FD}>&-
exec {SURVIVOR_SECOND_FD}>&-
SURVIVOR_FIRST_FD=
SURVIVOR_SECOND_FD=
[[ $survivor_rc != 0 ]] || fail 'survivors were accepted'
contains "$ROOT/survivors.out" 'SURVIVED: survivor-first'
contains "$ROOT/survivors.out" 'SURVIVED: survivor-second'
not_contains "$ROOT/survivors.out" 'KILLED: after-survivors'
[[ ! -e $after_survivor_marker ]] || fail 'a new check started after the first observed survivor'
contains "$ROOT/survivors.out" '1 registered checks were not started after a survivor was observed'
unstarted_summary_line=$(grep -nF '1 registered checks were not started after a survivor was observed' \
  "$ROOT/survivors.out" | cut -d: -f1)
survivor_failure_line=$(grep -nF 'FAIL: survivor-first: mutant survived' "$ROOT/survivors.out" | cut -d: -f1)
survivor_tail_line=$(grep -nF 'first survivor log' "$ROOT/survivors.out" | cut -d: -f1)
(( survivor_failure_line > unstarted_summary_line && survivor_tail_line > unstarted_summary_line )) \
  || fail 'survivor details were printed before the unstarted-check summary'
contains "$ROOT/survivors.out" 'SUMMARY: FAIL checks=3 reported=2 killed=0 elapsed_seconds='

export CONTROLLER_MUTATION_JOBS=1
mutation_runner_init "$ROOT/no-result" || fail 'valid width 1 was refused for missing-result case'
chmod 500 "$MUTATION_RUNNER_RESULTS" || fail 'could not make result directory unwritable'
{
  expect_killed missing-result bash -c 'printf "ran without report\n"; exit 1'
  mutation_runner_reap_one
  failures_after_reap=$MUTATION_RUNNER_FAILURE_COUNT
  mutation_runner_finish
} > "$ROOT/no-result.out" 2>&1
no_result_rc=$?
chmod 700 "$MUTATION_RUNNER_RESULTS" || fail 'could not restore result directory permissions'
(( failures_after_reap > 0 )) || fail 'worker without a result was not rejected when reaped'
(( no_result_rc != 0 )) || fail 'worker without a result passed'
not_contains "$ROOT/no-result.out" 'KILLED: missing-result'
contains "$ROOT/no-result.out" 'worker ended without writing a result'
contains "$ROOT/no-result.out" 'worker exited with status 70'

export CONTROLLER_MUTATION_JOBS=1
mutation_runner_init "$ROOT/duplicate" || fail 'valid width 1 was refused for duplicate case'
duplicate_marker=$ROOT/duplicate-runs
{
  # shellcheck disable=SC2016
  expect_killed repeated-label bash -c 'printf "first\n" >> "$1"; exit 4' _ "$duplicate_marker"
  # shellcheck disable=SC2016
  expect_killed repeated-label bash -c 'printf "second\n" >> "$1"; exit 4' _ "$duplicate_marker"
  if mutation_runner_finish; then duplicate_rc=0; else duplicate_rc=$?; fi
} > "$ROOT/duplicate.out" 2>&1
[[ $duplicate_rc != 0 ]] || fail 'duplicate label passed'
[[ $(wc -l < "$duplicate_marker") == 1 ]] || fail 'duplicate label started a second worker'
contains "$ROOT/duplicate.out" 'duplicate check label would share a log and result path'
contains "$ROOT/duplicate.out" 'SUMMARY: FAIL checks=2 reported=1 killed=1 elapsed_seconds='

invalid_width_values=(0 4 not-an-integer '')
invalid_width_names=(zero above-cap non-integer empty)
for index in "${!invalid_width_values[@]}"; do
  invalid_width=${invalid_width_values[index]}
  invalid_name=${invalid_width_names[index]}
  export CONTROLLER_MUTATION_JOBS=$invalid_width
  if mutation_runner_init "$ROOT/invalid-width-$invalid_name" \
    > "$ROOT/invalid-width-$invalid_name.out" 2>&1; then
    fail "$invalid_name worker width was accepted"
  else
    invalid_width_rc=$?
  fi
  [[ $invalid_width_rc == 2 ]] || fail "$invalid_name width returned $invalid_width_rc instead of 2"
  contains "$ROOT/invalid-width-$invalid_name.out" 'CONTROLLER_MUTATION_JOBS must be a positive integer'
  [[ ! -e $ROOT/invalid-width-$invalid_name/mutation-results ]] \
    || fail "$invalid_name width created worker state"
done

export CONTROLLER_MUTATION_JOBS=1
mutation_runner_init "$ROOT/effective-width" || fail 'valid width 1 was refused for effective-width case'
# shellcheck disable=SC2034 # read by the sourced runner's dispatch function
MUTATION_RUNNER_JOBS=0
{
  expect_killed invalid-effective-width bash -c 'exit 9'
  if mutation_runner_finish; then effective_width_rc=0; else effective_width_rc=$?; fi
} > "$ROOT/effective-width.out" 2>&1
[[ $effective_width_rc != 0 ]] || fail 'an effective width of zero passed at dispatch'
contains "$ROOT/effective-width.out" 'effective mutation worker width is outside 1..3 at dispatch'
[[ ! -e $ROOT/effective-width/mutation-started/invalid-effective-width ]] \
  || fail 'a worker started with an effective width of zero'

export CONTROLLER_MUTATION_JOBS=1
mutation_runner_init "$ROOT/empty-wait" || fail 'valid width 1 was refused for empty-wait case'
if mutation_runner_reap_one > "$ROOT/empty-wait.out" 2>&1; then
  fail 'reaping with no worker unexpectedly succeeded'
fi
if mutation_runner_finish >> "$ROOT/empty-wait.out" 2>&1; then empty_wait_rc=0; else empty_wait_rc=$?; fi
[[ $empty_wait_rc != 0 ]] || fail 'an empty-worker wait failure passed'
contains "$ROOT/empty-wait.out" 'attempted to wait with no registered worker'

export CONTROLLER_MUTATION_JOBS=1
mutation_runner_init "$ROOT/wall-limit" || fail 'valid width 1 was refused for wall-limit case'
# shellcheck disable=SC2034 # read by the sourced runner's worker function
MUTATION_RUNNER_CHECK_TIMEOUT_SECONDS=1
{
  expect_killed wall-limited bash -c 'while :; do :; done'
  if mutation_runner_finish; then wall_limit_rc=0; else wall_limit_rc=$?; fi
} > "$ROOT/wall-limit.out" 2>&1
[[ $wall_limit_rc != 0 ]] || fail 'a check beyond its wall limit passed'
not_contains "$ROOT/wall-limit.out" 'KILLED: wall-limited'
contains "$ROOT/wall-limit.out" 'TIMEOUT: wall-limited exceeded 1 seconds'
contains "$ROOT/wall-limit.out" 'check exceeded the 1s wall limit'
contains "$ROOT/wall-limit.out" 'SUMMARY: FAIL checks=1 reported=1 killed=0 elapsed_seconds='
# shellcheck disable=SC2034 # reset for the sourced runner's later checks
MUTATION_RUNNER_CHECK_TIMEOUT_SECONDS=600

export CONTROLLER_MUTATION_JOBS=1
mutation_runner_init "$ROOT/memory-limit" || fail 'valid width 1 was refused for memory-limit case'
# shellcheck disable=SC2034 # read by the sourced runner's worker function
MUTATION_RUNNER_MEMORY_LIMIT_KB=65536
{
  # The command first proves the small test cap is in force. Its bounded
  # geometric string growth then reaches that cap and must be reported as a
  # resource failure, never as a mutation kill.
  # shellcheck disable=SC2016 # the expression runs in the bounded child shell
  expect_killed memory-limited bash -c \
    '[[ $(ulimit -v) -le 65536 ]] || exit 91; value=x; for ((index = 0; index < 32; index++)); do value=$value$value; done; exit 0'
  if mutation_runner_finish; then memory_limit_rc=0; else memory_limit_rc=$?; fi
} > "$ROOT/memory-limit.out" 2>&1
[[ $memory_limit_rc != 0 ]] || fail 'a check that hit its memory bound passed'
not_contains "$ROOT/memory-limit.out" 'KILLED: memory-limited'
contains "$ROOT/memory-limit.out" 'RESOURCE LIMIT: memory-limited ended with status'
contains "$ROOT/memory-limit.out" 'check hit a per-check resource limit'
contains "$ROOT/memory-limit.out" 'SUMMARY: FAIL checks=1 reported=1 killed=0 elapsed_seconds='
# shellcheck disable=SC2034 # read by the sourced runner's worker function
MUTATION_RUNNER_MEMORY_LIMIT_KB=1048576

export CONTROLLER_MUTATION_JOBS=1
mutation_runner_init "$ROOT/node-address-space" || fail 'valid width 1 was refused for node address-space case'
NODE_CHECK_BIN=$ROOT/node-check-bin
mkdir -p "$NODE_CHECK_BIN"
cat > "$NODE_CHECK_BIN/node" <<'NODECHECK'
#!/usr/bin/env bash
set -uo pipefail
[[ $(ulimit -v) == 2147483648 && ${NODE_OPTIONS-} == --max-old-space-size=256 ]] || exit 91
printf 'TAP version 13\nnot ok 1 - assertion\n  error: AssertionError [ERR_ASSERTION]: expected\n'
exit 1
NODECHECK
chmod 0700 "$NODE_CHECK_BIN/node"
{
  # The explicit large virtual-address cap and heap option apply to this one
  # check. The following ordinary check sees the original 1 GiB cap and no
  # NODE_OPTIONS value from the first command.
  expect_assertion_killed_with_address_space_limit node-assertion 2147483648 \
    env PATH="$NODE_CHECK_BIN:$PATH" NODE_OPTIONS=--max-old-space-size=256 node
  # shellcheck disable=SC2016
  expect_killed default-after-node bash -c \
    '[[ $(ulimit -v) == 1048576 && ! ${NODE_OPTIONS+x} ]] || exit 92; exit 7'
  if mutation_runner_finish; then node_address_rc=0; else node_address_rc=$?; fi
} > "$ROOT/node-address-space.out" 2>&1
[[ $node_address_rc == 0 ]] || fail 'node-only address-space override or its cleanup failed'
contains "$ROOT/node-address-space.out" 'KILLED: node-assertion'
contains "$ROOT/node-address-space.out" 'KILLED: default-after-node'
contains "$ROOT/node-address-space.out" 'SUMMARY: PASS checks=2 reported=2 killed=2 elapsed_seconds='

export CONTROLLER_MUTATION_JOBS=1
mutation_runner_init "$ROOT/node-invalid-limit" || fail 'valid width 1 was refused for invalid node limit case'
{
  if expect_assertion_killed_with_address_space_limit invalid-node-limit 2147483649 \
    bash -c 'printf ran > "$1"; exit 1' _ "$ROOT/invalid-node-limit-ran"; then
    invalid_limit_rc=0
  else
    invalid_limit_rc=$?
  fi
  if mutation_runner_finish; then invalid_limit_finish_rc=0; else invalid_limit_finish_rc=$?; fi
} > "$ROOT/node-invalid-limit.out" 2>&1
[[ $invalid_limit_rc != 0 ]] || fail 'an address-space limit above the explicit cap was accepted'
[[ $invalid_limit_finish_rc != 0 ]] || fail 'a refused address-space limit passed the runner'
[[ ! -e $ROOT/invalid-node-limit-ran ]] || fail 'a command ran after its address-space limit was refused'
contains "$ROOT/node-invalid-limit.out" 'invalid per-check address-space limit: 2147483649 KiB'
contains "$ROOT/node-invalid-limit.out" 'SUMMARY: FAIL checks=1 reported=0 killed=0 elapsed_seconds='

for node_failure_case in syntax missing-module; do
  export CONTROLLER_MUTATION_JOBS=1
  mutation_runner_init "$ROOT/node-$node_failure_case" \
    || fail "valid width 1 was refused for node $node_failure_case classification"
  {
    if [[ $node_failure_case == syntax ]]; then
      # shellcheck disable=SC2016
      expect_assertion_killed_with_address_space_limit "node-$node_failure_case" 2147483648 \
        bash -c 'printf "TAP version 13\\nnot ok 1 - mutant parse\\n  error: SyntaxError: Unexpected token\\n"; exit 1'
    else
      # shellcheck disable=SC2016
      expect_assertion_killed_with_address_space_limit "node-$node_failure_case" 2147483648 \
        bash -c 'printf "TAP version 13\\nnot ok 1 - mutant import\\n  error: ERR_MODULE_NOT_FOUND\\n"; exit 1'
    fi
    if mutation_runner_finish; then node_failure_rc=0; else node_failure_rc=$?; fi
  } > "$ROOT/node-$node_failure_case.out" 2>&1
  [[ $node_failure_rc != 0 ]] || fail "Node $node_failure_case failure was counted as a killed mutant"
  not_contains "$ROOT/node-$node_failure_case.out" "KILLED: node-$node_failure_case"
  contains "$ROOT/node-$node_failure_case.out" 'failed without a TAP test assertion failure'
  contains "$ROOT/node-$node_failure_case.out" 'SUMMARY: FAIL checks=1 reported=1 killed=0 elapsed_seconds='
done

export CONTROLLER_MUTATION_JOBS=1
mutation_runner_init "$ROOT/node-startup-abort" || fail 'valid width 1 was refused for Node startup-abort classification'
{
  expect_assertion_killed_with_address_space_limit node-startup-abort 2147483648 \
    bash -c 'printf "Fatal process out of memory: SegmentedTable::InitializeTable\\n"; exit 133'
  if mutation_runner_finish; then node_abort_rc=0; else node_abort_rc=$?; fi
} > "$ROOT/node-startup-abort.out" 2>&1
[[ $node_abort_rc != 0 ]] || fail 'a Node startup abort was counted as a killed mutant'
not_contains "$ROOT/node-startup-abort.out" 'KILLED: node-startup-abort'
contains "$ROOT/node-startup-abort.out" 'RESOURCE LIMIT: node-startup-abort ended with status 133'
contains "$ROOT/node-startup-abort.out" 'check hit a per-check resource limit (status 133)'
contains "$ROOT/node-startup-abort.out" 'SUMMARY: FAIL checks=1 reported=1 killed=0 elapsed_seconds='

export CONTROLLER_MUTATION_JOBS=1
mutation_runner_init "$ROOT/spawn-failure" || fail 'valid width 1 was refused for spawn-failure case'
mkdir -p "$ROOT/empty-path"
{
  expect_killed spawn-failure env PATH="$ROOT/empty-path" no-such-mutation-check-command
  if mutation_runner_finish; then spawn_rc=0; else spawn_rc=$?; fi
} > "$ROOT/spawn-failure.out" 2>&1
[[ $spawn_rc != 0 ]] || fail 'a command that could not be executed passed as KILLED'
not_contains "$ROOT/spawn-failure.out" 'KILLED: spawn-failure'
contains "$ROOT/spawn-failure.out" 'test command could not be started'
contains "$ROOT/spawn-failure.out" 'SUMMARY: FAIL checks=1 reported=1 killed=0 elapsed_seconds='

export CONTROLLER_MUTATION_JOBS=2
mutation_runner_init "$ROOT/late-survivor" || fail 'valid width 2 was refused for late survivor case'
earlier_fifo=$ROOT/earlier-survivor.fifo
late_fifo=$ROOT/late-survivor.fifo
mkfifo "$earlier_fifo" "$late_fifo" || fail 'could not create late-survivor FIFOs'
exec {LATE_EARLIER_FD}<>"$earlier_fifo" || fail 'could not open earlier-survivor FIFO'
exec {LATE_WORKER_FD}<>"$late_fifo" || fail 'could not open late-survivor FIFO'
{
  # Hold the first survivor until both worker slots are registered. The late
  # worker then stays blocked while the parent observes the first survivor.
  # shellcheck disable=SC2016
  expect_killed earlier-check bash -c \
    'IFS= read -r token < "$1"; [[ $token == release ]] || exit 7; printf "earlier survivor log\n"; exit 0' \
    _ "$earlier_fifo"
  # shellcheck disable=SC2016
  expect_killed late-survivor bash -c \
    'IFS= read -r token < "$1"; [[ $token == release ]] || exit 8; printf "late survivor log\n"; exit 0' \
    _ "$late_fifo"
  printf 'release\n' >&"$LATE_EARLIER_FD"
  mutation_runner_reap_one
  [[ -f $MUTATION_RUNNER_RESULTS/earlier-check.result ]] || fail 'earlier check was not collected first'
  printf 'release\n' >&"$LATE_WORKER_FD"
  mutation_runner_finish
  finish_rc=$?
  reported_at_finish=$MUTATION_RUNNER_REPORTED_COUNT
  late_result_present=0
  [[ -f $MUTATION_RUNNER_RESULTS/late-survivor.result ]] && late_result_present=1
  # If a broken finish returned early, cleanup may reap only the recorded
  # workers. Bound that fallback and report an empty-wait error explicitly.
  late_reaps=0
  late_reap_failed=0
  while (( ${#MUTATION_RUNNER_PIDS[@]} > 0 && late_reaps < 2 )); do
    if ! mutation_runner_reap_one; then late_reap_failed=1; break; fi
    late_reaps=$((late_reaps + 1))
  done
  (( ${#MUTATION_RUNNER_PIDS[@]} == 0 )) || late_reap_failed=1
} > "$ROOT/late-survivor.out" 2>&1
exec {LATE_EARLIER_FD}>&-
exec {LATE_WORKER_FD}>&-
LATE_EARLIER_FD=
LATE_WORKER_FD=
(( finish_rc != 0 )) || fail 'late survivor passed'
(( late_result_present == 1 )) || fail 'final wait did not collect the late survivor result'
(( reported_at_finish == 2 )) || fail 'final wait returned before reaping the late survivor worker'
(( late_reap_failed == 0 )) || fail 'late-survivor test cleanup could not drain its recorded worker'
contains "$ROOT/late-survivor.out" 'SURVIVED: late-survivor'
contains "$ROOT/late-survivor.out" 'SUMMARY: FAIL checks=2 reported=2 killed=0 elapsed_seconds='

export CONTROLLER_MUTATION_JOBS=3
mutation_runner_init "$ROOT/eligible-queue" || fail 'valid width 3 was refused for eligible-queue case'
queue_first_fifo=$ROOT/queue-first.fifo
queue_second_fifo=$ROOT/queue-second.fifo
mkfifo "$queue_first_fifo" "$queue_second_fifo" || fail 'could not create eligible-queue FIFOs'
exec {QUEUE_FIRST_FD}<>"$queue_first_fifo" || fail 'could not open first eligible-queue FIFO'
exec {QUEUE_SECOND_FD}<>"$queue_second_fifo" || fail 'could not open second eligible-queue FIFO'
{
  # Both commands name the same serialized suite. The second stays pending,
  # while unrelated commands can occupy the other worker slots.
  # shellcheck disable=SC2016
  expect_killed grouped-first bash -c \
    'IFS= read -r token < "$1"; [[ $token == release ]] || exit 10; exit 7' \
    test-pi-impl.sh "$queue_first_fifo"
  # shellcheck disable=SC2016
  expect_killed grouped-second bash -c \
    'IFS= read -r token < "$1"; [[ $token == release ]] || exit 11; exit 8' \
    test-pi-impl.sh "$queue_second_fifo"
  expect_killed unrelated-one bash -c 'exit 5'
  expect_killed unrelated-two bash -c 'exit 6'
  expect_killed unrelated-late bash -c 'exit 9'
  [[ ! -e $MUTATION_RUNNER_STARTED/grouped-second ]] \
    || fail 'a second check in one serialized suite started concurrently'
  [[ ! -e $MUTATION_RUNNER_STARTED/unrelated-late ]] \
    || fail 'the pool exceeded its configured width'
  mutation_runner_reap_one
  mutation_runner_dispatch_pending
  [[ -e $MUTATION_RUNNER_STARTED/unrelated-late ]] \
    || fail 'a completed worker did not free a slot for an eligible pending check'
  [[ ! -e $MUTATION_RUNNER_STARTED/grouped-second ]] \
    || fail 'a queued same-suite check started while its group was active'
  printf 'release\n' >&"$QUEUE_FIRST_FD"
  printf 'release\n' >&"$QUEUE_SECOND_FD"
  if mutation_runner_finish; then queue_rc=0; else queue_rc=$?; fi
} > "$ROOT/eligible-queue.out" 2>&1
exec {QUEUE_FIRST_FD}>&-
exec {QUEUE_SECOND_FD}>&-
QUEUE_FIRST_FD=
QUEUE_SECOND_FD=
[[ $queue_rc == 0 ]] || fail 'eligible pending checks did not finish cleanly'
contains "$ROOT/eligible-queue.out" 'SUMMARY: PASS checks=5 reported=5 killed=5 elapsed_seconds='

export CONTROLLER_MUTATION_JOBS=3
mutation_runner_init "$ROOT/exclusive-queue" || fail 'valid width 3 was refused for exclusive-queue case'
exclusive_fifo=$ROOT/exclusive.fifo
mkfifo "$exclusive_fifo" || fail 'could not create exclusive FIFO'
exec {QUEUE_FIRST_FD}<>"$exclusive_fifo" || fail 'could not open exclusive FIFO'
{
  # An exclusive check owns the entire pool until it finishes. A normal check
  # queued behind it starts as soon as exclusivity is released.
  # shellcheck disable=SC2016
  expect_killed exclusive-first bash -c \
    'IFS= read -r token < "$1"; [[ $token == release ]] || exit 12; exit 3' \
    test-ctx-hooks.sh "$exclusive_fifo"
  expect_killed ordinary-after-exclusive bash -c 'exit 4'
  [[ ${#MUTATION_RUNNER_PIDS[@]} == 1 ]] \
    || fail 'an ordinary check overlapped an active exclusive check'
  [[ ! -e $MUTATION_RUNNER_STARTED/ordinary-after-exclusive ]] \
    || fail 'an ordinary check started during an exclusive check'
  printf 'release\n' >&"$QUEUE_FIRST_FD"
  if mutation_runner_finish; then exclusive_rc=0; else exclusive_rc=$?; fi
} > "$ROOT/exclusive-queue.out" 2>&1
exec {QUEUE_FIRST_FD}>&-
QUEUE_FIRST_FD=
[[ $exclusive_rc == 0 ]] || fail 'exclusive pending checks did not finish cleanly'
[[ -e $ROOT/exclusive-queue/mutation-started/ordinary-after-exclusive ]] \
  || fail 'an ordinary check was lost behind the exclusive check'
contains "$ROOT/exclusive-queue.out" 'SUMMARY: PASS checks=2 reported=2 killed=2 elapsed_seconds='

export CONTROLLER_MUTATION_JOBS=3
mutation_runner_init "$ROOT/pending-exclusive" || fail 'valid width 3 was refused for pending-exclusive case'
pending_exclusive_first_fifo=$ROOT/pending-exclusive-first.fifo
pending_exclusive_fifo=$ROOT/pending-exclusive.fifo
mkfifo "$pending_exclusive_first_fifo" "$pending_exclusive_fifo" \
  || fail 'could not create pending-exclusive FIFOs'
exec {QUEUE_FIRST_FD}<>"$pending_exclusive_first_fifo" \
  || fail 'could not open pre-exclusive FIFO'
exec {QUEUE_SECOND_FD}<>"$pending_exclusive_fifo" \
  || fail 'could not open pending-exclusive FIFO'
{
  # The exclusive check is queued behind an active worker. A later ordinary
  # check may use a free slot, but the exclusive check still waits to run alone.
  # shellcheck disable=SC2016
  expect_killed ordinary-before-exclusive bash -c \
    'IFS= read -r token < "$1"; [[ $token == release ]] || exit 14; exit 5' \
    _ "$pending_exclusive_first_fifo"
  # shellcheck disable=SC2016
  expect_killed exclusive-after-ordinary bash -c \
    'IFS= read -r token < "$1"; [[ $token == release ]] || exit 15; exit 4' \
    test-ctx-hooks.sh "$pending_exclusive_fifo"
  expect_killed ordinary-after-pending-exclusive bash -c 'exit 6'
  [[ ! -e $MUTATION_RUNNER_STARTED/exclusive-after-ordinary ]] \
    || fail 'an exclusive check started while an earlier worker was active'
  [[ -e $MUTATION_RUNNER_STARTED/ordinary-after-pending-exclusive ]] \
    || fail 'a pending exclusive check stalled an eligible ordinary worker'
  printf 'release\n' >&"$QUEUE_FIRST_FD"
  printf 'release\n' >&"$QUEUE_SECOND_FD"
  if mutation_runner_finish; then pending_exclusive_rc=0; else pending_exclusive_rc=$?; fi
} > "$ROOT/pending-exclusive.out" 2>&1
exec {QUEUE_FIRST_FD}>&-
exec {QUEUE_SECOND_FD}>&-
QUEUE_FIRST_FD=
QUEUE_SECOND_FD=
[[ $pending_exclusive_rc == 0 ]] || fail 'pending exclusive scheduling did not finish cleanly'
contains "$ROOT/pending-exclusive.out" 'SUMMARY: PASS checks=3 reported=3 killed=3 elapsed_seconds='

echo 'PASS mutation runner: bounded outcomes, per-check limits, assertion-only kills, survivors, result guards, queues and final counts'
