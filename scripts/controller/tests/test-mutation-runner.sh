#!/usr/bin/env bash
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/controller-mutation-runner-test.XXXXXX")
cleanup() { rm -rf -- "$ROOT"; }
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

export CONTROLLER_MUTATION_JOBS=2
mutation_runner_init "$ROOT/survivors" || fail 'valid width 2 was refused'
survivor_marker=$ROOT/after-survivor-ran
{
  expect_killed survivor-first bash -c 'printf "first survivor log\n"; exit 0'
  expect_killed survivor-second bash -c 'printf "second survivor log\n"; exit 0'
  mutation_runner_reap_one
  # shellcheck disable=SC2016
  expect_killed after-survivors bash -c 'printf "ran\n" > "$1"; exit 7' _ "$survivor_marker"
  if mutation_runner_finish; then survivor_rc=0; else survivor_rc=$?; fi
} > "$ROOT/survivors.out" 2>&1
[[ $survivor_rc != 0 ]] || fail 'survivors were accepted'
contains "$ROOT/survivors.out" 'SURVIVED: survivor-first'
contains "$ROOT/survivors.out" 'SURVIVED: survivor-second'
not_contains "$ROOT/survivors.out" 'KILLED: after-survivors'
[[ ! -e $survivor_marker ]] || fail 'a new check started after the first observed survivor'
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
late_fifo=$ROOT/late-survivor.fifo
mkfifo "$late_fifo" || fail 'could not create late-survivor FIFO'
exec {late_fifo_fd}<>"$late_fifo" || fail 'could not open late-survivor FIFO'
{
  expect_killed earlier-check bash -c 'printf "earlier check log\n"; exit 5'
  # shellcheck disable=SC2016
  expect_killed late-survivor bash -c \
    'IFS= read -r token < "$1"; [[ $token == release ]] || exit 8; printf "late survivor log\n"; exit 0' \
    _ "$late_fifo"
  mutation_runner_reap_one
  [[ -f $MUTATION_RUNNER_RESULTS/earlier-check.result ]] || fail 'earlier check was not collected first'
  printf 'release\n' >&"$late_fifo_fd"
  mutation_runner_finish
  finish_rc=$?
  reported_at_finish=$MUTATION_RUNNER_REPORTED_COUNT
  late_result_present=0
  [[ -f $MUTATION_RUNNER_RESULTS/late-survivor.result ]] && late_result_present=1
  # If a broken finish returned without collecting the late worker, reap it
  # here so the test itself leaves no child behind before reporting the defect.
  while (( ${#MUTATION_RUNNER_PIDS[@]} > 0 )); do mutation_runner_reap_one || :; done
} > "$ROOT/late-survivor.out" 2>&1
exec {late_fifo_fd}>&-
(( finish_rc != 0 )) || fail 'late survivor passed'
(( late_result_present == 1 )) || fail 'final wait did not collect the late survivor result'
(( reported_at_finish == 2 )) || fail 'final wait returned before reaping the late survivor worker'
contains "$ROOT/late-survivor.out" 'SURVIVED: late-survivor'
contains "$ROOT/late-survivor.out" 'SUMMARY: FAIL checks=2 reported=2 killed=1 elapsed_seconds='

echo 'PASS mutation runner: killed status, exit 127, survivors, stop-after-survivor, missing result, spawn failure, duplicate, invalid width, final wait'
