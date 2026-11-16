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
# call in flight with its age, its command and the pid to steer, assistant
# turns, tool calls, latest usage total, commits ahead of the base ref,
# uncommitted files, the age of this run's own last commit, and the snapshot's
# own age.
#
# States: ACTIVE, STALLED (wrapper alive, no pi event for longer than the
# stall threshold), LONG-TOOL (one tool call in flight longer than its threshold
# WHILE the run is still showing progress evidence), HUNG-TOOL (the same age
# with NO progress evidence), WRAPPER-DEAD (pid gone while no .done), FINISHED,
# NO-LIVE.
#
# LONG-TOOL and HUNG-TOOL are separate words on purpose: a slow grep or a long
# pytest is a working job, and calling it HUNG-TOOL made the controller kill
# working runs. Progress evidence is the sidecar's `progress_epoch` (stream
# growth or CPU/IO growth of the wrapper's process subtree), so it is a fact
# read from the kernel and the stream, never a guess from wall-clock alone.
#
# Steering a stuck tool: kill the EXACT child pid printed as `steer=`, never the
# wrapper. Killing the wrapper discards the run's classification, its `.done`
# marker and its continuation history, and the tool it was waiting on is what
# actually needs to stop. A tool blocked by the agent's own experiment (a git
# command waiting on a FIFO the agent created) shows no progress EITHER, so the
# row prints the command and the pids and the controller decides which one to
# kill.
#
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


def command_text(children):
    """A short, bounded rendering of the wrapper's steering children."""
    if not isinstance(children, list):
        return None
    rows = []
    for entry in children:
        if not isinstance(entry, dict):
            continue
        pid = entry.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            continue
        command = entry.get("command")
        if isinstance(command, str) and command.strip():
            rows.append(f"{pid}({command[:60]})")
        else:
            rows.append(str(pid))
    return ",".join(rows) if rows else None


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
    progress_seconds = age(payload.get("progress_epoch"))
    tool_command = payload.get("tool_in_flight_command")
    steer = command_text(payload.get("steering_children"))
    if progress_seconds is None:
        progress_text = "-"
    else:
        progress_text = f"{progress_seconds}s"
    ahead = payload.get("commits_ahead")
    # `since-commit` names WHOSE commit the age belongs to. Before this run's
    # first commit the newest commit on the branch is the BASE commit, and an
    # unqualified age would answer "how long since this run committed" with
    # somebody else's commit age. The qualifier is the commits_ahead fact on the
    # same row: 0 means no commit of this run's own, null means git could not
    # answer (and an unreadable qualifier never licenses the number).
    if not isinstance(ahead, int):
        commit_text = "null"
    elif ahead == 0:
        commit_text = "no-commits-yet"
    else:
        commit_text = f"{show(age(payload.get('last_commit_epoch')))}s(ahead={ahead})"
    state = "ACTIVE"
    if wrapper_dead:
        state = "WRAPPER-DEAD"
    elif tool_seconds is not None and tool_seconds > hung_limit:
        # Age alone is not a hung tool: it is a hung tool only when the run has
        # ALSO stopped showing progress evidence within the same threshold. An
        # unreadable progress fact (null) cannot license the stronger word.
        if progress_seconds is not None and progress_seconds > hung_limit:
            state = "HUNG-TOOL"
        else:
            state = "LONG-TOOL"
    elif since_event is not None and since_event > stall_limit:
        state = "STALLED"
    tool_text = "-"
    if isinstance(tool, str) and tool:
        tool_text = f"{tool}/{show(tool_seconds)}s"
    if isinstance(tool_command, str) and tool_command:
        tool_text += f" cmd={tool_command[:80]}"
    if steer is not None:
        tool_text += f" steer={steer}"
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
        f"tokens-last-report={show(payload.get('latest_total_tokens_last_report'))} "
        f"since-commit={commit_text} "
        f"progress={progress_text} "
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
