#!/usr/bin/env bash
# Run a gate in one of the configured local controller slots (CONTROLLER_GATE_SLOTS, 1..8, default 2).
# Usage: gate-run.sh <run-gate arguments...> (run from the project directory)
#   slot 1 = CONTROLLER_GATE_LOCK, slot 2 = CONTROLLER_GATE2_LOCK, slot N >= 3 = <dir>/gate<N>.lock where <dir> is
#   CONTROLLER_GATE_LOCK_DIR if set, else the directory that holds CONTROLLER_GATE_LOCK. CONTROLLER_GATE2_LOCK is only required
#   when CONTROLLER_GATE_SLOTS is 2 or more. The default stays 2: the local slots guard the controller devcontainer, which has
#   a memory cap and was OOM-killed once when too much ran at the same time.
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"
controller_init
# Operator decision 2026-11-17: local gates run WITHOUT profiling data, so they behave the same before and after the
# host's resource-observer cutover (the observer needs the rebuilt container). The caller can still set RUN_GATE_PROFILE=on.
# A lane that declares resource_observer = "cgprofile" cannot run here at all: it goes to a remote gate worker (remote-gate.sh).
export RUN_GATE_PROFILE=${RUN_GATE_PROFILE:-off}

[[ $# -gt 0 ]] || { echo "usage: $0 <run-gate arguments...>" >&2; exit 2; }
slots=${CONTROLLER_GATE_SLOTS:-2}
[[ $slots =~ ^[1-8]$ ]] || {
  echo 'gate-run: CONTROLLER_GATE_SLOTS must be an integer from 1 to 8 (unset it for the default 2)' >&2
  exit 2
}
if (( slots == 1 )); then
  required_locks=("${CONTROLLER_GATE_LOCK:-}")
  refusal='gate-run: CONTROLLER_GATE_LOCK must be configured as an absolute path (CONTROLLER_GATE_SLOTS=1 uses one slot); copy the name from controller.local.env.example and export it before running'
else
  required_locks=("${CONTROLLER_GATE_LOCK:-}" "${CONTROLLER_GATE2_LOCK:-}")
  refusal='gate-run: CONTROLLER_GATE_LOCK and CONTROLLER_GATE2_LOCK must both be configured as absolute paths; copy both names from controller.local.env.example and export them before running'
fi
for lock in "${required_locks[@]}"; do
  [[ -n $lock && $lock == /* ]] || { echo "$refusal" >&2; exit 2; }
done
(( slots < 3 )) || [[ -z ${CONTROLLER_GATE_LOCK_DIR:-} || $CONTROLLER_GATE_LOCK_DIR == /* ]] || {
  echo 'gate-run: CONTROLLER_GATE_LOCK_DIR must be an absolute directory path (it holds gate3.lock, gate4.lock, ...)' >&2
  exit 2
}

# Slot tables, indexed by slot number: lock path, label in the messages, file descriptor that holds the lock while it is taken.
# Slots 1 and 2 keep the descriptors 9 and 8 they always had.
slot_lock=([1]=$CONTROLLER_GATE_LOCK)
slot_label=([1]=gate.lock)
slot_fd=([1]=9 [2]=8 [3]=10 [4]=11 [5]=12 [6]=13 [7]=14 [8]=15)
for (( slot = 2; slot <= slots; slot++ )); do
  slot_label[slot]=gate$slot.lock
  if (( slot == 2 )); then slot_lock[slot]=$CONTROLLER_GATE2_LOCK; else slot_lock[slot]=$(controller_gate_derived_lock "$slot"); fi
done
for (( slot = 1; slot <= slots; slot++ )); do
  for (( other = slot + 1; other <= slots; other++ )); do
    [[ ${slot_lock[slot]} != "${slot_lock[other]}" ]] || {
      echo 'gate lock paths must be distinct' >&2
      (( slots == 2 )) || echo "gate-run: slots $slot and $other both use ${slot_lock[slot]}" >&2
      exit 2
    }
  done
done
command -v flock >/dev/null 2>&1 || { echo 'flock is required' >&2; exit 2; }
command -v run-gate >/dev/null 2>&1 || { echo 'run-gate is required on PATH' >&2; exit 2; }

# CTL-57: a slot is not an admission. After it holds a slot and before it starts run-gate, wait while the host's memory
# PSI `full` avg10 is >= 3 (the programme's stop signal; no knob; an unreadable value counts as busy), one log line per
# minute, bounded by CONTROLLER_GATE_PSI_WAIT_S (default 1800). The wait is counted in its own sleeps, not read from a
# clock. Giving up is exit 75 "not admitted, nothing ran": never a lane verdict. Same rule as `dev` host-admission/1.
psi_file=${CONTROLLER_GATE_PSI_FILE:-/proc/pressure/memory}
psi_wait_limit=${CONTROLLER_GATE_PSI_WAIT_S:-1800}
[[ $psi_wait_limit =~ ^[0-9]+$ ]] || {
  echo 'gate-run: CONTROLLER_GATE_PSI_WAIT_S must be a whole number of seconds (unset it for the default 1800)' >&2
  exit 2
}
psi_why=
memory_busy() {   # 0 busy (psi_why names it), 1 quiet
  local full
  full=$(awk '/^full/ { sub("avg10=", "", $2); print $2; exit }' "$psi_file" 2>/dev/null) || full=
  case $full in
    ''|*[!0-9.]*|.*|*.|*.*.*) psi_why="memory PSI unreadable ($psi_file)"; return 0 ;;
  esac
  psi_why="memory PSI full avg10=$full >= 3"
  awk -v m="$full" 'BEGIN { exit !(m + 0 >= 3) }'
}
admit_memory() {
  local waited=0 next_log=0
  memory_busy || return 0
  while :; do
    if (( waited >= psi_wait_limit )); then
      echo "gate-run: not admitted ($psi_why for ${waited}s); nothing ran" >&2
      exit 75
    fi
    if (( waited >= next_log )); then
      echo "gate-run: waiting for host admission: $psi_why, ${waited}s of ${psi_wait_limit}s" >&2
      next_log=$((next_log + 60))
    fi
    sleep 10 || { echo 'gate-run: wait interval failed' >&2; exit 2; }
    waited=$((waited + 10))
    memory_busy || return 0
  done
}

slot_acquired=0
run_slot() {
  local rc lock=${slot_lock[$1]} label=${slot_label[$1]} fd=${slot_fd[$1]}
  shift
  slot_acquired=0
  # The descriptor number comes from the fixed table above, never from the caller's environment.
  if ! eval "exec ${fd}>\"\$lock\""; then
    echo "cannot open gate lock: $lock" >&2
    return 2
  fi
  if flock -n -E 75 "$fd"; then
    slot_acquired=1
    echo "gate-run: slot $label" >&2
    admit_memory
    nice -n 10 run-gate "$@"
    return $?
  else
    rc=$?
  fi
  eval "exec ${fd}>&-"
  [[ $rc == 75 ]] && return 75
  echo "cannot lock gate slot $label (flock exit $rc)" >&2
  return "$rc"
}

for (( slot = 1; slot <= slots; slot++ )); do
  run_slot "$slot" "$@"
  rc=$?
  if (( slot_acquired )); then exit "$rc"; fi
  [[ $rc == 75 ]] || exit "$rc"
done

poll_lock() {
  local rc lock=${slot_lock[$1]} label=${slot_label[$1]} fd=${slot_fd[$1]}
  shift
  slot_acquired=0
  if flock -n -E 75 "$fd"; then
    slot_acquired=1
    echo "gate-run: slot $label" >&2
    admit_memory
    nice -n 10 run-gate "$@"
    return $?
  else
    rc=$?
  fi
  [[ $rc == 75 ]] && return 75
  echo "cannot lock gate slot $label (flock exit $rc; $lock)" >&2
  return "$rc"
}

# "gate.lock and gate2.lock" for two slots, "gate.lock, gate2.lock and gate3.lock" for three, and so on.
labels_text=${slot_label[1]}
for (( slot = 2; slot <= slots; slot++ )); do
  if (( slot == slots )); then labels_text+=" and ${slot_label[slot]}"; else labels_text+=", ${slot_label[slot]}"; fi
done
case $slots in
  1) wait_text="the only slot is busy, polling $labels_text every 5s"; busy_text='is busy' ;;
  2) wait_text="both slots busy, polling $labels_text every 5s"; busy_text='are both busy' ;;
  *) wait_text="all $slots slots busy, polling $labels_text every 5s"; busy_text='are all busy' ;;
esac
echo "gate-run: $wait_text" >&2
for (( slot = 1; slot <= slots; slot++ )); do
  # The message names the slot as it always did: gate, gate2, gate3, ...
  if (( slot == 1 )); then open_name=gate; else open_name=gate$slot; fi
  if ! eval "exec ${slot_fd[slot]}>\"\${slot_lock[slot]}\""; then
    echo "cannot open $open_name lock: ${slot_lock[slot]}" >&2
    exit 2
  fi
done
waited=0
next_progress=300
while :; do
  for (( slot = 1; slot <= slots; slot++ )); do
    poll_lock "$slot" "$@"
    rc=$?
    if (( slot_acquired )); then exit "$rc"; fi
    [[ $rc == 75 ]] || exit "$rc"
  done

  if (( waited >= next_progress )); then
    echo "gate-run: still waiting after ${waited}s; $labels_text $busy_text" >&2
    next_progress=$((next_progress + 300))
  fi
  sleep 5 || { echo 'gate-run: wait interval failed' >&2; exit 2; }
  waited=$((waited + 5))
done
