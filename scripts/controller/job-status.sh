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
# growth, or CPU/IO growth over the same processes of the in-flight TOOL CALL's
# own subtree - never the wrapper's whole tree, which also holds the agent and
# the snapshotter and grows every tick), so it is a fact read from the kernel and
# the stream, never a guess from wall-clock alone.
#
# The sidecar's `progress_measured` flag is the licence for that evidence, and it
# is read, never assumed: a tick that could not re-read the tool's subtree
# carries a previous epoch forward (or nothing at all) and publishes it as
# unmeasured, and an epoch older than the in-flight call is published as null.
# Both are UNKNOWN here - the row prints `progress=-` and the weaker word - so a
# tool's age alone can never decide HUNG-TOOL, which is the shape the defect took
# when a stream from BEFORE the tool started was lifted up to the tool's start.
# A payload that predates the flag is unknown for the same reason.
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


def pid_start_ticks(pid):
    """The kernel's own pid-reuse discriminator (field 22 of /proc/<pid>/stat)."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as source:
            line = source.read().decode("utf-8", "replace")
    except OSError:
        return None
    _, _, rest = line.partition(") ")
    fields = rest.split()
    return fields[19] if len(fields) >= 20 and fields[19].isdigit() else None


def current_boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return None


def read_owner(name):
    """The run name's owner RECORD (lib.sh controller_run_claim writes it)."""
    record = runs_dir / f"{name}.lock"
    try:
        line = record.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return None
    fields = {}
    for token in line.split():
        key, _, value = token.partition("=")
        if key and value:
            fields[key] = value
    return fields or None


def owner_verdict(owner, payload_pid):
    """('alive'|'dead'|'absent', detail).

    The RECORD is what a reader must trust when it exists: a wrapper pid in a
    `.live` payload can be recycled (`kill -0` says yes, and the process is
    somebody else's), and a record written before a reboot cannot name a live
    process at all. This is the same rule job-check.sh applies, against the same
    record.

    With NO record, the honest answer is the weaker one: the payload's own pid is
    checked (`kill -0`) and the row says `owner-record=none`, because "the record
    is missing" is not "the wrapper is dead" - absence is not emptiness.
    """
    if not owner:
        if pid_alive(payload_pid):
            return ("absent", "alive")
        return ("dead", f"wrapper pid {payload_pid} gone (no owner record)")
    pid_text = owner.get("pid", "")
    if not pid_text.isdigit():
        return ("absent", None)
    pid = int(pid_text)
    boot = owner.get("boot_id")
    live_boot = current_boot_id()
    if boot and live_boot and boot != live_boot:
        return ("dead", f"wrapper pid {pid} is from a previous boot")
    if not pid_alive(pid):
        return ("dead", f"wrapper pid {pid} gone")
    recorded_ticks = owner.get("pid_start_ticks", "")
    if recorded_ticks.isdigit():
        live_ticks = pid_start_ticks(pid)
        if live_ticks and int(live_ticks) != int(recorded_ticks):
            # The number exists but it is another process now: a relaunch
            # refusal based on this record would name a stranger.
            return ("dead", f"wrapper pid {pid} recycled (recorded start {recorded_ticks}, now {live_ticks})")
    return ("alive", pid)


def agent_processes(worktree):
    """Live agent processes for this run's worktree, as (pid, command) rows.

    Only used when the wrapper is gone: an orphaned agent holds the work, and a
    controller that cannot name it can only watch the sidecar go stale. The
    match is structural - the process's own cwd IS this worktree (both wrappers
    `cd` there) and its argv[0] is `pi` or `codex` - never a name guess, and the
    scan is bounded to the process table's own entries.
    """
    if not worktree:
        return []
    rows = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return rows
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            continue
        if cwd != worktree:
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as source:
                raw = source.read().decode("utf-8", "replace")
        except OSError:
            continue
        parts = [part for part in raw.split("\0") if part]
        if not parts or os.path.basename(parts[0]) not in ("pi", "codex"):
            continue
        rows.append((pid, " ".join(parts)[:80]))
    return rows


def process_children(pid):
    """The live pids whose parent is pid, from one /proc scan."""
    found = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return found
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as source:
                line = source.read().decode("utf-8", "replace")
        except OSError:
            continue
        _, _, rest = line.partition(") ")
        fields = rest.split()
        if len(fields) >= 2 and fields[1].isdigit() and int(fields[1]) == pid:
            found.append(int(entry))
    return found


def tool_of_agent(agent_pid):
    """The in-flight tool call's processes under an orphaned agent.

    Same structure the sidecar uses: the agent's own descendants are what the
    tool started (a `bash -lc` shell and its `sleep`), so a controller that must
    stop one can name it, not guess it.
    """
    pids = []
    pending = process_children(agent_pid)
    depth = 0
    while pending and depth < 8:
        depth += 1
        next_level = []
        for pid in pending:
            pids.append(pid)
            next_level.extend(process_children(pid))
        pending = next_level
    return pids


def stream_epoch(name):
    try:
        return int((runs_dir / f"{name}.jsonl").stat().st_mtime)
    except OSError:
        return None


def thread_from_stream(name):
    """The run's thread/session id, from the run stream's FIRST session record.

    A wrapper writes `<run>.thread` while it works, but a wrapper killed before
    that leaves the file missing and job-check cannot print a resume command.
    The stream itself already carries the id - pi puts it on the first `session`
    event, Codex on `thread.started` - so the fact is READ from the record the
    run already produced, never invented and never silently ignored.
    """
    try:
        with open(runs_dir / f"{name}.jsonl", encoding="utf-8", errors="replace") as source:
            for line in source:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("type") == "session" and isinstance(event.get("id"), str):
                    return event["id"]
                if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
                    return event["thread_id"]
    except OSError:
        return None
    return None


def worktree_of(name):
    try:
        return (runs_dir / f"{name}.wt").read_text(encoding="utf-8").strip()
    except OSError:
        return None


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


def suffix_age_source(source):
    # Only the fallback needs saying: a sidecar tick is the normal case, and a
    # reader that assumes "sidecar" for it is right.
    return "" if source == "sidecar" else f"({source})"


def active_line(name, payload, owner):
    wrapper_pid = payload.get("wrapper_pid")
    # Liveness is the OWNER RECORD's verdict, never `kill -0` on the payload's
    # pid: a recycled pid answers "yes" for somebody else, and a record written
    # before a reboot cannot name a live process at all.
    verdict, detail = owner_verdict(owner, wrapper_pid)
    wrapper_dead = verdict == "dead"
    ancestors = []
    if wrapper_dead:
        # The wrapper may be gone while the agent it started still works. "Dead
        # wrapper, live agent" is a different fact from "nothing is running",
        # and the controller needs it: nothing else can name a process to stop.
        ancestors = agent_processes(worktree_of(name))
    # The pid the reader names is the RECORDED owner when there is one: the
    # payload's own pid may be a recycled stranger, and naming that stranger is
    # exactly the wrong process to steer.
    recorded_pid = owner.get("pid") if owner else None
    if isinstance(recorded_pid, str) and recorded_pid.isdigit():
        wrapper_text = recorded_pid
    elif isinstance(wrapper_pid, int) and wrapper_pid > 0:
        wrapper_text = str(wrapper_pid)
    else:
        wrapper_text = detail if detail else "unknown"
    since_event = age(payload.get("last_event_epoch"))
    since_event_source = "sidecar"
    if wrapper_dead:
        # When the wrapper died its sidecar is stale by definition, so the
        # newest event is the run stream's own mtime - not the last tick the
        # dead sidecar had a chance to write. The row says which one it read.
        stream_age = age(stream_epoch(name))
        if stream_age is not None:
            since_event = stream_age
            since_event_source = "stream"
    tool = payload.get("tool_in_flight")
    tool_seconds = age(payload.get("tool_in_flight_started_epoch"))
    progress_seconds = age(payload.get("progress_epoch"))
    # An epoch is evidence; a BASELINE is a measurement of a tool whose counters
    # were read and never moved. Only the second may license the stronger word,
    # and only when the sidecar actually read the counters: a `/proc` it could
    # not read leaves both null and the row prints `progress=-`.
    progress_baseline_seconds = age(payload.get("progress_baseline_epoch"))
    # The sidecar's own licence, and it is read, never assumed: `progress_measured`
    # says whether that fact rests on a measurement of the CURRENT tool. One flag
    # covers every unmeasured state the producer has - a tick that could not read
    # the subtree at all (`progress_reason=unread`, epoch carried forward as a
    # leftover) and an epoch older than the in-flight call (published as null with
    # the same flag) - because the producer clears it in both cases. A payload
    # that predates the field is unknown for the same reason: absence of a
    # measurement may never license HUNG-TOOL.
    progress_flag = payload.get("progress_measured")
    progress_reason = payload.get("progress_reason")
    progress_reason_text = progress_reason if isinstance(progress_reason, str) and progress_reason else "-"
    progress_unknown = progress_flag is not True
    progress_age = (
        progress_seconds if progress_seconds is not None else progress_baseline_seconds
    )
    if progress_unknown or progress_age is None:
        progress_age = None
        progress_text = "-"
    else:
        progress_text = f"{progress_age}s"
    tool_command = payload.get("tool_in_flight_command")
    steer = command_text(payload.get("steering_children"))
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
        # unknown progress fact (no reading, or a tick that could not measure)
        # cannot license the stronger word.
        if progress_age is not None and progress_age > hung_limit:
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
    pid_text = wrapper_text
    # The distinction a controller must not lose: a dead WRAPPER with a live
    # AGENT is one state, and "nothing is running" is another. The word is
    # printed, not implied by the presence of an `agent=` list.
    if wrapper_dead and ancestors:
        pid_text += " wrapper-dead-agent-alive"
    if verdict == "absent":
        # Say which fact the liveness claim rests on: no owner record was read.
        pid_text += " owner-record=none"
    elif detail and verdict == "dead" and "recycled" in detail:
        # The number is in use, but by a DIFFERENT process: say so on the row,
        # because "pid" alone would make a reader steer a stranger.
        pid_text += " recycled"
    if wrapper_dead:
        pid_text += " DEAD"
        if ancestors:
            # The word the controller needs: the wrapper is gone but the work
            # continues. `agent=` names the orphaned agent, `tool=` names what
            # that agent is running, so either can be stopped by pid.
            pid_text += " agent=" + ",".join(f"{pid}({command})" for pid, command in ancestors)
            tool_pids = []
            for pid, _ in ancestors:
                tool_pids.extend(tool_of_agent(pid))
            if tool_pids:
                pid_text += " tool=" + ",".join(str(pid) for pid in tool_pids)
        else:
            pid_text += " agent=none"
    return (
        f"{name}: {state} pid={pid_text} "
        f"last-event={show(since_event)}s{suffix_age_source(since_event_source)} "
        f"tool={tool_text} turns={show(payload.get('assistant_turns'))} "
        f"calls={show(payload.get('tool_calls'))} "
        f"tokens={show(payload.get('latest_total_tokens'))} "
        f"ahead={show(payload.get('commits_ahead'))} "
        f"uncommitted={show(payload.get('uncommitted_files'))} "
        f"tokens-last-report={show(payload.get('latest_total_tokens_last_report'))} "
        f"since-commit={commit_text} "
        f"progress={progress_text} progress-why={progress_reason_text} "
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
    print(active_line(name, payload, read_owner(name)))
PY
