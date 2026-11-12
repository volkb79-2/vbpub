#!/usr/bin/env bash
# Run a gate in one of the two configured controller slots.
# Usage: gate-run.sh <run-gate arguments...> (run from the project directory)
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"
controller_init

[[ $# -gt 0 ]] || { echo "usage: $0 <run-gate arguments...>" >&2; exit 2; }
for lock in "${CONTROLLER_GATE_LOCK:-}" "${CONTROLLER_GATE2_LOCK:-}"; do
  [[ -n $lock && $lock == /* ]] || {
    echo 'gate-run: export CONTROLLER_GATE_LOCK and CONTROLLER_GATE2_LOCK from controller.local.env.example before running; both values must be absolute paths' >&2
    exit 2
  }
done
[[ $CONTROLLER_GATE_LOCK != "$CONTROLLER_GATE2_LOCK" ]] || { echo 'gate lock paths must be distinct' >&2; exit 2; }
command -v flock >/dev/null 2>&1 || { echo 'flock is required' >&2; exit 2; }
command -v run-gate >/dev/null 2>&1 || { echo 'run-gate is required on PATH' >&2; exit 2; }

slot_acquired=0
run_slot() {
  local lock=$1 label=$2 rc
  shift 2
  slot_acquired=0
  if ! exec 9>"$lock"; then
    echo "cannot open gate lock: $lock" >&2
    return 2
  fi
  if flock -n -E 75 9; then
    slot_acquired=1
    echo "gate-run: slot $label" >&2
    nice -n 10 run-gate "$@"
    return $?
  else
    rc=$?
  fi
  exec 9>&-
  [[ $rc == 75 ]] && return 75
  echo "cannot lock gate slot $label (flock exit $rc)" >&2
  return "$rc"
}

run_slot "$CONTROLLER_GATE_LOCK" gate.lock "$@"
rc=$?
if (( slot_acquired )); then exit "$rc"; fi
[[ $rc == 75 ]] || exit "$rc"
run_slot "$CONTROLLER_GATE2_LOCK" gate2.lock "$@"
rc=$?
if (( slot_acquired )); then exit "$rc"; fi
[[ $rc == 75 ]] || exit "$rc"

poll_lock() {
  local lock_fd=$1 lock=$2 label=$3 rc
  shift 3
  slot_acquired=0
  if flock -n -E 75 "$lock_fd"; then
    slot_acquired=1
    echo "gate-run: slot $label" >&2
    nice -n 10 run-gate "$@"
    return $?
  else
    rc=$?
  fi
  [[ $rc == 75 ]] && return 75
  echo "cannot lock gate slot $label (flock exit $rc; $lock)" >&2
  return "$rc"
}

echo 'gate-run: both slots busy, polling gate.lock and gate2.lock every 5s' >&2
if ! exec 9>"$CONTROLLER_GATE_LOCK"; then
  echo "cannot open gate lock: $CONTROLLER_GATE_LOCK" >&2
  exit 2
fi
if ! exec 8>"$CONTROLLER_GATE2_LOCK"; then
  echo "cannot open gate2 lock: $CONTROLLER_GATE2_LOCK" >&2
  exit 2
fi
waited=0
next_progress=300
while :; do
  poll_lock 9 "$CONTROLLER_GATE_LOCK" gate.lock "$@"
  rc=$?
  if (( slot_acquired )); then exit "$rc"; fi
  [[ $rc == 75 ]] || exit "$rc"

  poll_lock 8 "$CONTROLLER_GATE2_LOCK" gate2.lock "$@"
  rc=$?
  if (( slot_acquired )); then exit "$rc"; fi
  [[ $rc == 75 ]] || exit "$rc"

  if (( waited >= next_progress )); then
    echo "gate-run: still waiting after ${waited}s; gate.lock and gate2.lock are both busy" >&2
    next_progress=$((next_progress + 300))
  fi
  sleep 5 || { echo 'gate-run: wait interval failed' >&2; exit 2; }
  waited=$((waited + 5))
done
