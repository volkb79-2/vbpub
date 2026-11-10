#!/usr/bin/env bash
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SRC=${QUEUE_SCRIPT:-$SCRIPT_DIR/../host-sat-queue.sh}
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/host-sat-queue-test.XXXXXX")
cleanup() { [[ -n ${KEEP_TEST_ROOT:-} ]] || rm -rf -- "$ROOT"; }
trap cleanup EXIT
mkdir -p "$ROOT/repo"
git -C "$ROOT/repo" init -q

fail() { echo "FAIL: $*" >&2; exit 1; }
contains() { grep -qF -- "$2" "$1" || fail "$3 (missing: $2)"; }
not_contains() { ! grep -qF -- "$2" "$1" || fail "$3 (unexpected: $2)"; }

make_home() {
  local home=$1
  mkdir -p "$home/logs/host-sat-queue/runs"
  printf '{"hosts":{"runner-a":{"queue_lanes":["fake/project|suite"]}}}\n' > "$home/hosts.local.json"
  printf 'utc\thost\tlane\tcommit\trun_name\tverdict\tseconds\tlog\n' > "$home/logs/host-sat-results.tsv"
}

HOME_ROOT=$ROOT/metadata
make_home "$HOME_ROOT"
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$ROOT/repo HOST_SAT_MAX_CONSEC_INFRA=1 HOST_SAT_INFRA_BACKOFF_S=0 \
HOST_SAT_POLL_S=0 bash -c '
  source "$1"
  check_ok() { return 0; }
  run_one() {
    LAST_VERDICT=INFRA; LAST_LANE=fake/project:suite; LAST_RESULT=INFRA:NO_FINISH
    LAST_RUN="sat-fake-$HOST"; LAST_LOG="$BASE/logs/host-sat-$LAST_RUN.log"
    record "$(now)" "$HOST" "$LAST_LANE" deadbeef "$LAST_RUN" "$LAST_RESULT" 1 "$LAST_LOG"
  }
  sleep() { exit 0; }
  loop runner-a
' _ "$SRC" > "$ROOT/metadata-loop.out" 2>&1
flag=$HOME_ROOT/host-sat-queue.disabled.runner-a
[[ -s $flag ]] || fail 'auto-pause did not write structured metadata'
contains "$flag" 'by=queue-auto' 'pause records the actor'
contains "$flag" 'host=runner-a' 'pause records the configured host alias'
contains "$flag" 'streak=1 consecutive INFRA results' 'pause records the INFRA streak'
contains "$flag" 'last_result=INFRA:NO_FINISH' 'pause records the last verdict'
contains "$flag" 'last_run=sat-fake-runner-a' 'pause records the run name'
contains "$HOME_ROOT/logs/host-sat-results.tsv" 'PAUSED:queue-auto' 'pause row is appended to results'
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$ROOT/repo bash "$SRC" status > "$ROOT/status-structured.out" 2>&1
contains "$ROOT/status-structured.out" 'host disabled flag present:' 'status shows the host pause flag'
contains "$ROOT/status-structured.out" 'by=queue-auto' 'status includes structured pause metadata'

rm -f -- "$flag"
touch "$flag"
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$ROOT/repo bash "$SRC" status > "$ROOT/status-empty.out" 2>&1
contains "$ROOT/status-empty.out" 'pause metadata: absent (legacy empty flag)' 'status recognizes an empty legacy flag'
not_contains "$ROOT/status-empty.out" 'by=queue-auto' 'status does not invent missing metadata'
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$ROOT/repo bash "$SRC" resume runner-a > "$ROOT/resume.out" 2>&1
[[ ! -e $flag ]] || fail 'host-specific resume did not clear only its flag'
contains "$ROOT/resume.out" resumed 'resume reports completion'
touch "$flag" "$HOME_ROOT/host-sat-queue.disabled"
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$ROOT/repo bash "$SRC" resume > "$ROOT/global-resume.out" 2>&1
[[ ! -e $HOME_ROOT/host-sat-queue.disabled && -e $flag ]] || fail 'global resume changed a host-specific pause'
CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$ROOT/repo bash "$SRC" resume runner-a > "$ROOT/host-resume.out" 2>&1
[[ ! -e $flag ]] || fail 'host-specific resume left its own pause in place'
if CONTROLLER_HOME=$HOME_ROOT CONTROLLER_REPO=$ROOT/repo bash "$SRC" stop ../outside > "$ROOT/stop-invalid.out" 2>&1; then
  fail 'stop accepted an unconfigured host alias'
fi
contains "$ROOT/stop-invalid.out" 'unknown or invalid configured host' 'stop validates a supplied host alias'

INVALID_HOME=$ROOT/invalid-config
mkdir -p "$INVALID_HOME"
printf '{invalid json\n' > "$INVALID_HOME/hosts.local.json"
if CONTROLLER_HOME=$INVALID_HOME CONTROLLER_REPO=$ROOT/repo bash "$SRC" start > "$ROOT/start-invalid-config.out" 2>&1; then
  fail 'start accepted an invalid host table'
fi
contains "$ROOT/start-invalid-config.out" 'invalid host table' 'start propagates host-table parsing failures'
if CONTROLLER_HOME=$INVALID_HOME CONTROLLER_REPO=$ROOT/repo bash "$SRC" stop > "$ROOT/stop-invalid-config.out" 2>&1; then
  fail 'stop accepted an invalid host table'
fi
contains "$ROOT/stop-invalid-config.out" 'invalid host table' 'stop propagates host-table parsing failures'

FALLBACK_HOME=$ROOT/fallback
make_home "$FALLBACK_HOME"
CONTROLLER_HOME=$FALLBACK_HOME CONTROLLER_REPO=$ROOT/repo HOST_SAT_MAX_CONSEC_INFRA=1 HOST_SAT_INFRA_BACKOFF_S=0 \
HOST_SAT_POLL_S=0 bash -c '
  source "$1"
  check_ok() { return 0; }
  run_one() { LAST_VERDICT=INFRA; LAST_LANE=fake/project:suite; LAST_RESULT=INFRA:NO_FINISH; LAST_RUN=sat-fallback; LAST_LOG=fake.log; }
  write_auto_pause() { return 1; }
  sleep() { exit 0; }
  loop runner-a
' _ "$SRC" > "$ROOT/fallback-loop.out" 2>&1
fallback_flag=$FALLBACK_HOME/host-sat-queue.disabled.runner-a
[[ -e $fallback_flag && ! -s $fallback_flag ]] || fail 'metadata failure must keep an empty pause flag'
contains "$FALLBACK_HOME/logs/host-sat-queue/runner-a.log" 'pause metadata write failed; touching empty flag' \
  'metadata write failure is logged'

CHECK_HOME=$ROOT/check
make_home "$CHECK_HOME"
CONTROLLER_HOME=$CHECK_HOME CONTROLLER_REPO=$ROOT/repo bash -c '
  source "$1"
  HOST=runner-a
  probe() { printf "%s\n" "nproc=4" "load1=0.1" "memsome=1.0" "iofull=1.0" "memavail=4000" "swapused=0" "oomkill=0" "rootfree=20"; }
  check_ok || exit 1
  probe() { printf "%s\n" "nproc=4" "load1=0.1" "memsome=26.0" "iofull=1.0" "memavail=4000" "swapused=0" "oomkill=0" "rootfree=20"; }
  reason=$(check_ok 2>&1) && exit 2
  [[ $reason == *"memory PSI some avg10=26.0"* ]] || exit 3
  probe() { printf "%s\n" "nproc=4" "load1=0.1" "memsome=1.0" "iofull=1.0" "memavail=4000" "swapused=0" "oomkill=0"; }
  reason=$(check_ok 2>&1) && exit 4
  [[ $reason == "probe incomplete" ]] || exit 5
' _ "$SRC" || fail 'resource guard did not accept, reject high PSI, and refuse incomplete probe data'

echo 'PASS queue: host-config lanes, resource guard, structured auto-pause, empty-flag fallback, status, resume'
