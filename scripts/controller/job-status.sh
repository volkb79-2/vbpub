#!/usr/bin/env bash
# Read-only liveness/progress report for controller runs. No fetch, no
# process control: it reads the run sidecars a launcher writes and checks the
# pid they name with `kill -0`, then prints one line per requested run.
# Usage: job-status.sh [run ...]
#
# With no arguments, every run that has a live sidecar is reported. A run with
# a .done marker is reported as finished with its recorded outcome; a run with
# no sidecar is NO-LIVE, not healthy.
#
# Per line: run, state, wrapper pid, seconds since the last pi event, the tool
# call in flight and its age, assistant turns, tool calls, latest usage total,
# commits ahead of the base ref, uncommitted files, seconds since the last
# commit, and the snapshot's own age.
#
# States: ACTIVE, STALLED (wrapper alive, no pi event for longer than the
# stall threshold), HUNG-TOOL (one tool call in flight longer than its
# threshold), WRAPPER-DEAD (pid gone while no .done), FINISHED, NO-LIVE.
# Thresholds: JOB_STATUS_STALL_SECONDS (default 600) and
# JOB_STATUS_HUNG_TOOL_SECONDS (default 1800), integer seconds.
set -uo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
# shellcheck source=lib.sh
. "$SCRIPT_DIR/lib.sh"
controller_init

stall_seconds=${JOB_STATUS_STALL_SECONDS:-600}
hung_tool_seconds=${JOB_STATUS_HUNG_TOOL_SECONDS:-1800}
for threshold in "$stall_seconds" "$hung_tool_seconds"; do
  [[ $threshold =~ ^[0-9]+$ ]] || {
    echo 'JOB_STATUS_STALL_SECONDS and JOB_STATUS_HUNG_TOOL_SECONDS must be integer seconds' >&2
    exit 2
  }
done
base_ref=${CONTROLLER_BASE_REF:-origin/main}

runs=("$@")
if (( ${#runs[@]} == 0 )); then
  shopt -s nullglob
  for live in "$CONTROLLER_RUNS_DIR"/*.live; do
    base=${live##*/}
    runs+=("${base%.live}")
  done
  shopt -u nullglob
  if (( ${#runs[@]} == 0 )); then
    echo 'no controller runs have a live sidecar'
    exit 0
  fi
fi
for run in "${runs[@]}"; do
  [[ $run =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ && $run != *..* ]] || {
    echo "invalid run name: $run" >&2
    exit 2
  }
done

python3 - "$CONTROLLER_RUNS_DIR" "$stall_seconds" "$hung_tool_seconds" "$base_ref" "${runs[@]}" <<'PY'
import json
import os
import sys
import time
from pathlib import Path

runs_dir = Path(sys.argv[1])
stall_limit = int(sys.argv[2])
hung_limit = int(sys.argv[3])
names = sys.argv[5:]
now = int(time.time())


def pid_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def age(epoch):
    if not isinstance(epoch, int):
        return None
    return max(0, now - epoch)


def show(value):
    return '-' if value is None else value


def finished_line(name):
    marker = (runs_dir / f"{name}.done").read_text(encoding="utf-8", errors="replace").splitlines()
    status = marker[0] if marker else "EXIT=? OUTCOME=?"
    return f"{name}: FINISHED {status}"


def active_line(name, payload):
    wrapper_pid = payload.get("wrapper_pid")
    wrapper_dead = not pid_alive(wrapper_pid)
    since_event = age(payload.get("last_event_epoch"))
    tool = payload.get("tool_in_flight")
    tool_seconds = age(payload.get("tool_in_flight_started_epoch"))
    state = "ACTIVE"
    if wrapper_dead:
        state = "WRAPPER-DEAD"
    elif tool_seconds is not None and tool_seconds > hung_limit:
        state = "HUNG-TOOL"
    elif since_event is not None and since_event > stall_limit:
        state = "STALLED"
    tool_text = "-"
    if isinstance(tool, str) and tool:
        tool_text = f"{tool}/{show(tool_seconds)}s"
    pid_text = f"{show(wrapper_pid)}"
    if wrapper_dead:
        pid_text += " DEAD"
    return (
        f"{name}: {state} pid={pid_text} last-event={show(since_event)}s "
        f"tool={tool_text} turns={show(payload.get('assistant_turns'))} "
        f"calls={show(payload.get('tool_calls'))} "
        f"tokens={show(payload.get('latest_total_tokens'))} "
        f"ahead={show(payload.get('commits_ahead'))} "
        f"uncommitted={show(payload.get('uncommitted_files'))} "
        f"since-commit={show(age(payload.get('last_commit_epoch')))}s "
        f"snapshot-age={show(age(payload.get('updated_epoch')))}s"
    )


for name in names:
    if (runs_dir / f"{name}.done").is_file():
        print(finished_line(name))
        continue
    live_path = runs_dir / f"{name}.live"
    if not live_path.is_file():
        print(f"{name}: NO-LIVE (no {live_path.name} sidecar)")
        continue
    try:
        payload = json.loads(live_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"{name}: NO-LIVE ({live_path.name} is not readable JSON)")
        continue
    if not isinstance(payload, dict):
        print(f"{name}: NO-LIVE ({live_path.name} is not a JSON object)")
        continue
    print(active_line(name, payload))
PY
