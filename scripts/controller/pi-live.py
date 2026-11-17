#!/usr/bin/env python3
"""Write a run's liveness/progress snapshot for the controller's Monitor.

Started by an agent wrapper (currently pi-impl.sh) while the run is active and
killed when the wrapper exits. It re-reads only the bytes appended to the run's
event stream since the previous tick, so a multi-hundred-megabyte stream costs
one incremental parse per tick.

The snapshot holds only facts read from the stream, from git, or from the
kernel's own counters, as absolute epochs and counts: a value that cannot be read
is written as null rather than invented, and the reader derives every age itself
(so a stale snapshot still describes what it last saw). The file is replaced
atomically (temp file + rename in the same directory), so a reader never sees a
partial JSON object.

Two facts exist because a reader must not turn one into the other:

* `latest_total_tokens` is the last NON-ZERO context figure read from the stream
  (`null` when no measurement was ever read), and
  `latest_total_tokens_last_report` is the value of the most recent usage report
  (which may be 0). pi reports `totalTokens: 0` on streaming deltas
  (`text_start`/`text_delta`/`toolcall_start`/`toolcall_delta`) before any usage
  has been computed, and an error `message_end` can also carry 0; publishing
  that 0 as the context size would read as "the context is empty" on a run at
  200k. The last non-zero figure is therefore kept separately, and the zero
  report stays visible as its own fact instead of being discarded.
* `progress_epoch` is the last tick at which the run showed PROGRESS evidence:
  new bytes in the run's event stream, or a POSITIVE growth of the CPU-time and
  IO counters of the IN-FLIGHT TOOL CALL's own process subtree
  (`/proc/<pid>/stat` fields 14/15 and `/proc/<pid>/io`). It is the fact that
  separates a heavy-but-working tool (`LONG-TOOL`) from a tool that stopped
  making progress (`HUNG-TOOL`).

  The summed set is the tool's subtree and nothing else. A wrapper's tree holds
  three kinds of member, and two of them grow every tick on their own: this
  sidecar (its own `/proc` scans and `git` calls) and the agent (a model loop).
  Counting either of them made `progress_epoch` fresh while a tool was blocked,
  which is exactly the state `HUNG-TOOL` must be able to describe, so the
  subtree is located structurally: `os.getpid()` is this sidecar (the one pid
  known exactly, never guessed from a name), the direct child of the wrapper
  that is not it is the agent, and the agent's own descendants are the tool's
  processes. The agent node itself is excluded with the wrapper and the sidecar.

  A tick is progress only when the SAME set of processes (`pid` + the kernel's
  own start ticks, so a recycled pid is a different process) shows a strictly
  positive delta in CPU ticks or IO bytes. A decrease, a set change (a tool
  exited, another started) and an unreadable `/proc` are baselines, never
  progress; `unknown` stays null rather than being clamped to the tool's start.

  `progress_measured` says whether the progress fact on this snapshot rests on
  a MEASUREMENT of the current in-flight tool: the tool's subtree was read this
  tick, or evidence was produced this tick. A tick that could not read the
  subtree at all (the agent child or the wrapper is missing from the process
  table, an empty `/proc`) carries the previous epoch forward as UNMEASURED, and
  an epoch older than the in-flight call is published as `null` with the flag
  false - never lifted up to the tool's start. Lifting it is the wall-clock rule
  CTL-18 removed: the tool's own age would stand in for evidence nobody read,
  and `HUNG-TOOL` would be decided on that age alone.

The tool in flight also carries `tool_in_flight_command` (the tool's own
`args.command`, bounded) and `steering_children` (the tool call's own live
processes with their short command lines), so a reader can name the work and
steer the EXACT child pid instead of the wrapper.

The process-tree walk is capped by `PI_LIVE_TREE_NODE_LIMIT` (default
`TREE_NODE_LIMIT`, 512 nodes) so one tick never walks an unbounded table. The
cap is not a silent truncation: the snapshot publishes `progress_tree_nodes`
(how many processes were summed) and `progress_tree_truncated` (true when the
cap cut the walk short), and a row that hits the cap says so, so a reader can see
that the summed set is a prefix of the tree.

Usage: pi-live.py --stream PATH --worktree PATH --out PATH --run NAME
                  --wrapper-pid PID --interval SECONDS [--once]

Environment override: CONTROLLER_BASE_REF (default origin/main) is the ref
`commits_ahead` counts from.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def git(worktree: str, arguments: list[str]) -> str | None:
    try:
        process = subprocess.run(
            ["git", "-C", worktree, *arguments],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if process.returncode != 0:
        return None
    return process.stdout


# The process tree is walked from `/proc`; a fake root makes the walk testable
# without wall-clock waits or a real process tree (a test sets PI_LIVE_PROC_ROOT).
PROC_ROOT = os.environ.get("PI_LIVE_PROC_ROOT") or "/proc"
COMMAND_LIMIT = 200
# Documented cap on the process-tree walk; see PI_LIVE_TREE_NODE_LIMIT.
TREE_NODE_LIMIT = 512


def read_text(path: str) -> str | None:
    try:
        with open(path, "rb") as source:
            return source.read().decode("utf-8", "replace")
    except OSError:
        return None


def process_table() -> dict[int, tuple[int, int | None]]:
    """pid -> (parent pid, kernel start ticks); ONE scan per tick.

    The start ticks are the kernel's own pid-reuse discriminator: two readings
    of the same pid number with different start ticks are different processes.
    """
    table: dict[int, tuple[int, int | None]] = {}
    try:
        entries = os.listdir(PROC_ROOT)
    except OSError:
        return table
    for entry in entries:
        if not entry.isdigit():
            continue
        stat_line = read_text(f"{PROC_ROOT}/{entry}/stat")
        if stat_line is None:
            continue
        # After the parenthesised comm: field 3 is state, so ppid (field 4) is
        # index 1 and starttime (field 22) is index 19.
        _, _, rest = stat_line.partition(") ")
        fields = rest.split()
        if len(fields) >= 2 and fields[1].isdigit():
            ticks = int(fields[19]) if len(fields) >= 20 and fields[19].isdigit() else None
            table[int(entry)] = (int(fields[1]), ticks)
    return table


def children_map(table: dict[int, tuple[int, int | None]]) -> dict[int, list[int]]:
    children: dict[int, list[int]] = {}
    for pid, (parent, _) in table.items():
        children.setdefault(parent, []).append(pid)
    return children


def descendants(
    root_pid: int, children: dict[int, list[int]], limit: int
) -> tuple[list[int], bool]:
    """Every live descendant of root_pid (the root excluded), and whether the
    node cap cut the walk short. `False` means the whole subtree was read."""
    pending = sorted(children.get(root_pid, []))
    found: list[int] = []
    truncated = False
    while pending:
        if len(found) >= limit:
            truncated = True
            break
        pid = pending.pop(0)
        found.append(pid)
        pending.extend(sorted(children.get(pid, [])))
    return found, truncated


def tool_processes(
    wrapper_pid: int, table: dict[int, tuple[int, int | None]], limit: int,
    sidecar_pid: int | None = None,
) -> tuple[list[int], bool] | None:
    """The in-flight tool call's own processes, or None when they are unknown.

    A wrapper's tree holds three kinds of member and only one of them is
    evidence about a tool:

    * the wrapper itself, which writes the run's records and runs `git`;
    * the sidecar, which is THIS process: its own `/proc` scans grow every tick,
      and the `git` calls it forks are its descendants - so it is excluded
      together with its whole subtree;
    * the agent (pi), a direct child of the wrapper, which runs a model loop and
      grows every tick whether or not anything is being executed.

    So the summed set is structured, never guessed from a command name: the
    sidecar is `os.getpid()` (the one pid known exactly), the agent is the
    wrapper's direct child that is not that sidecar, and the tool's processes are
    the agent's own descendants. Nothing else is subtracted on a second
    condition - one decision, in one place.

    `None` means the subtree could not be located at all (the wrapper or its
    agent is not in the table), which a reader must see as unknown, not as
    silence.
    """
    if not isinstance(wrapper_pid, int) or wrapper_pid <= 0:
        return None
    if wrapper_pid not in table:
        return None
    self_pid = os.getpid() if sidecar_pid is None else int(sidecar_pid)
    children = children_map(table)
    agent_roots = [
        pid for pid, (parent, _) in table.items()
        if parent == wrapper_pid and pid != self_pid
    ]
    if not agent_roots:
        return None
    pids: list[int] = []
    truncated = False
    for root in sorted(agent_roots):
        found, cut = descendants(root, children, limit - len(pids))
        pids.extend(found)
        truncated = truncated or cut
        if truncated:
            break
    return pids[:limit], truncated


def short_command(pid: int) -> str | None:
    raw = read_text(f"{PROC_ROOT}/{pid}/cmdline")
    if raw is None:
        return None
    parts = [part for part in raw.split("\0") if part]
    if not parts:
        return None
    text = " ".join(parts)
    return text[:COMMAND_LIMIT]


class Sample:
    """One comparable counter reading: WHICH processes, and their totals."""

    __slots__ = ("identity", "cpu", "io")

    def __init__(
        self, identity: frozenset[tuple[int, int | None]], cpu: int, io: int
    ) -> None:
        self.identity = identity
        self.cpu = cpu
        self.io = io


def read_counters(
    pids: list[int], table: dict[int, tuple[int, int | None]]
) -> Sample | None:
    """(identity, cpu ticks, io bytes) summed over these pids.

    An empty set is a defined fact - no tool process is running - and sums to
    zero. A NON-empty set whose counters cannot be read is unknown (`None`),
    never zero: "could not read it" and "nothing happened" are different facts.
    """
    if not pids:
        return Sample(frozenset(), 0, 0)
    cpu = 0
    io = 0
    readable = False
    for pid in pids:
        stat_line = read_text(f"{PROC_ROOT}/{pid}/stat")
        if stat_line is not None:
            _, _, rest = stat_line.partition(") ")
            fields = rest.split()
            if len(fields) >= 15:
                try:
                    cpu += int(fields[11]) + int(fields[12])
                    readable = True
                except ValueError:
                    pass
        io_text = read_text(f"{PROC_ROOT}/{pid}/io")
        if io_text is not None:
            for line in io_text.splitlines():
                key, _, value = line.partition(":")
                if key in ("read_bytes", "write_bytes") and value.strip().isdigit():
                    io += int(value.strip())
                    readable = True
    if not readable:
        return None
    identity = frozenset((pid, table.get(pid, (0, None))[1]) for pid in pids)
    return Sample(identity, cpu, io)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def clock_now(clock=None) -> int:
    """The run's clock: a test-supplied one, else the wall clock.

    A test needs the SAME clock for the tool's start epoch and for the tick the
    progress fact is written at, or it would be asserting two unrelated numbers.
    """
    return int(clock()) if clock is not None else int(time.time())


class Snapshot:
    """Incremental reader of a pi JSON event stream."""

    def __init__(self, stream: Path, clock=None) -> None:
        self.stream = stream
        # The clock a test controls (see dump_payload --now); None = wall clock.
        self.clock = clock
        self.offset = 0
        self.incomplete = b""
        self.turns = 0
        self.tool_calls = 0
        self.latest_nonzero_tokens: int | None = None
        self.last_reported_tokens: int | None = None
        self.in_flight: dict[str, object] | None = None
        # Set while the bytes of a tick carry a `tool_execution_start`: that
        # growth is the tool's own START event, and a start is a boundary, never
        # progress evidence for the tool it begins (see Progress).
        self.new_tool_started = False
        # Set while the bytes of a tick carry a `tool_execution_update`: the
        # tool's OWN output, produced after its call started. That is real
        # evidence even on the tick that also announced the call.
        self.new_tool_output = False

    def read_new(self) -> int:
        """Read newly appended bytes; returns how many were consumed."""
        self.new_tool_started = False
        self.new_tool_output = False
        try:
            with self.stream.open("rb") as source:
                if os.fstat(source.fileno()).st_size < self.offset:
                    # The stream was truncated and rewritten (a new first run):
                    # start over rather than wait to outgrow the old offset.
                    self.offset = 0
                    self.incomplete = b""
                    self.turns = 0
                    self.tool_calls = 0
                    self.latest_nonzero_tokens = None
                    self.last_reported_tokens = None
                    self.in_flight = None
                source.seek(self.offset)
                chunk = source.read()
                self.offset = source.tell()
        except OSError:
            return 0
        if not chunk:
            return 0
        buffer = self.incomplete + chunk
        lines = buffer.split(b"\n")
        self.incomplete = lines.pop()
        for line in lines:
            self.consume(line)
        return len(chunk)

    def consume(self, line: bytes) -> None:
        if not line.strip():
            return
        try:
            event = json.loads(line)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        event_type = event.get("type")
        if event_type == "message_end":
            message = event.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                self.turns += 1
                self.record_usage(message.get("usage"))
        elif event_type == "message_update":
            self.record_usage(event.get("usage"))
        elif event_type == "tool_execution_start":
            self.tool_calls += 1
            self.new_tool_started = True
            arguments = event.get("args")
            command = None
            if isinstance(arguments, dict):
                value = arguments.get("command")
                if isinstance(value, str) and value.strip():
                    command = value[:COMMAND_LIMIT]
            self.in_flight = {
                "name": event.get("toolName"),
                "call_id": event.get("toolCallId"),
                "started_epoch": self.epoch_now(),
                "command": command,
                "output_epoch": None,
            }
        elif event_type == "tool_execution_update":
            # A tool's own partial output is progress evidence: the event is
            # appended to the stream while the tool is still running, and a
            # heavy-but-working tool produces them.
            call_id = event.get("toolCallId")
            if self.in_flight is not None and self.in_flight.get("call_id") == call_id:
                self.in_flight["output_epoch"] = self.epoch_now()
                self.new_tool_output = True
        elif event_type == "tool_execution_end":
            call_id = event.get("toolCallId")
            if self.in_flight is None or self.in_flight.get("call_id") == call_id:
                self.in_flight = None

    def epoch_now(self) -> int:
        return clock_now(self.clock)

    def record_usage(self, usage: object) -> None:
        """Keep the last report AND the last real measurement, separately.

        0 is a legitimate report (a fresh stream, or an error turn) but it is
        never a context size, so it updates the last-report fact only.
        """
        if not isinstance(usage, dict):
            return
        total = usage.get("totalTokens")
        if not isinstance(total, int):
            return
        self.last_reported_tokens = total
        if total > 0:
            self.latest_nonzero_tokens = total


class Progress:
    """Last tick at which the run showed progress evidence.

    Progress evidence is observable, never inferred, and the rule is defined
    rather than tuned against a threshold:

    * stream growth (new bytes in the run's event stream) is progress;
    * a counter reading is progress only when the SAME set of processes shows a
      STRICTLY POSITIVE delta in CPU ticks or IO bytes. The same set means the
      same pids with the same kernel start ticks, because a recycled pid number
      is not the process that was measured before.

    Three boundaries are NOT progress: a process set that changed (the tool
    exited, another started, a pid was recycled), a tool call that STARTS (the
    bytes that announce it are its boundary, not evidence it did anything yet,
    and a start resets the counter baseline with it), and a `/proc` that cannot
    be read.

    Everything else is a baseline update and leaves the epoch where it was: a
    counter decrease (a shorter-lived counter, a process that was reaped), a
    set change (the tool exited, another started, a pid was recycled) and an
    unreadable `/proc` (`None`, which is unknown). An empty set is a defined
    fact - no tool process is running - and sums to zero.

    `measured` is the reader's licence (see the module docstring): the current
    tool's subtree was read this tick, or evidence was produced this tick. A
    tick that read nothing leaves the epoch where it was but marks it
    unmeasured, so a reader never turns stale or absent evidence into a
    tool-age-only verdict.

    The summed set is the in-flight tool call's own subtree, never the wrapper's
    whole tree: the agent and this sidecar grow every tick on their own, and
    counting them made a tool that was blocked on the agent's FIFO read as
    working (see the module docstring).
    """

    def __init__(self, tool_in_flight: bool = False) -> None:
        self.sample: Sample | None = None
        self.epoch: int | None = None
        # The tick at which the CURRENT tool call's counters were first read.
        # It is the difference between "measured, and it never moved" (a real
        # absence of progress) and "could not measure it at all" (unknown): only
        # the first may become HUNG-TOOL, the second is the weaker word.
        self.baseline: int | None = None
        self.tree_nodes: int | None = None
        self.tree_truncated = False
        self.reason: str | None = None
        # Whether the progress fact rests on a measurement of the CURRENT tool
        # (see the module docstring). A carried, unread epoch is measured=False.
        self.measured = False
        self.tool_in_flight = tool_in_flight

    def set_tool_in_flight(self, in_flight: bool) -> None:
        """The tool call's own lifetime is the measurement's lifetime.

        A tool's start resets the baseline: the counters of a finished tool
        (often already read as zero after it was reaped) say nothing about the
        NEXT tool, and comparing across the two would make a fresh tool inherit
        the old one's reading. A tool's end also resets it, and the fact this
        resets to is `idle`: with no tool in flight there is no tool evidence to
        read, so the interval belongs to the stream rule alone.
        """
        if in_flight == self.tool_in_flight:
            return
        self.tool_in_flight = in_flight
        self.sample = None
        self.baseline = None
        # The epoch measured while the PREVIOUS tool ran is no evidence about
        # the next one.
        self.measured = False

    def observe(
        self, now: int, grew: bool, sample: Sample | None, nodes: int | None = None,
        truncated: bool = False, tool_started: bool = False,
        tool_output: bool = False,
    ) -> int | None:
        self.tree_nodes = nodes
        self.tree_truncated = truncated
        if sample is None and not grew:
            # This tick observed nothing (no tool subtree, no stream bytes). A
            # tick that observed nothing cannot be a baseline change either: the
            # previous measurement stands until something is actually read - and
            # it is carried as UNMEASURED, because nothing about the current
            # state was read.
            self.reason = "unread"
            self.measured = False
            return self.epoch
        changed = bool(grew)
        reason = "stream" if grew else "none"
        if tool_started:
            # A tool call's start is a boundary, not evidence: the stream grew
            # because the call was announced, and the counters that just became
            # readable belong to a process the previous tick never measured.
            # The tool's OWN output in the same bytes is evidence though: it was
            # produced after the call started.
            changed = bool(tool_output)
            reason = "tool-output" if tool_output else "tool-start"
        if not self.tool_in_flight:
            # No tool in flight: the tool-process counters are a baseline, and
            # saying they grew would be evidence about processes no tool owns.
            reason = "idle" if not grew else reason
        elif sample is not None:
            previous = self.sample
            if (
                previous is not None
                and previous.identity == sample.identity
                and (sample.cpu > previous.cpu or sample.io > previous.io)
            ):
                changed = True
                reason = "counters"
            self.sample = sample
            if self.baseline is None:
                self.baseline = now
        else:
            reason = "unknown" if not grew else reason
        if changed:
            self.epoch = now
        # The reader's licence: a measurement of the current state is the tool's
        # own subtree read this tick, or evidence produced this tick. A tool
        # call's boundary without its own output measures nothing about the
        # tool, so it only counts when its counters were read.
        self.measured = bool(changed) or sample is not None
        self.reason = reason
        return self.epoch


def payload(
    run: str,
    stream: Path,
    worktree: str,
    wrapper_pid: int,
    state: Snapshot,
    progress: "Progress | None" = None,
    located: tuple[list[int], bool] | None = None,
) -> dict[str, object]:
    progress_epoch = None if progress is None else progress.epoch
    progress_measured = False if progress is None else progress.measured
    progress_baseline = None if progress is None else progress.baseline
    progress_reason = None if progress is None else progress.reason
    tree_nodes = None if progress is None else progress.tree_nodes
    tree_truncated = None if progress is None else progress.tree_truncated
    last_event: int | None
    try:
        last_event = int(stream.stat().st_mtime)
    except OSError:
        last_event = None
    base = os.environ.get("CONTROLLER_BASE_REF") or "origin/main"
    ahead = git(worktree, ["rev-list", "--count", f"{base}..HEAD"])
    # --no-optional-locks: a 30 s poll must never take the index lock the agent's
    # own git add/commit needs.
    porcelain = git(worktree, ["--no-optional-locks", "status", "--porcelain"])
    commit_epoch_text = git(worktree, ["log", "-1", "--format=%ct", "HEAD"])
    commit_epoch: int | None = None
    if commit_epoch_text is not None and commit_epoch_text.strip().isdigit():
        commit_epoch = int(commit_epoch_text.strip())
    in_flight = state.in_flight
    tool_name: str | None = None
    tool_started: int | None = None
    tool_command: str | None = None
    tool_output: int | None = None
    if isinstance(in_flight, dict):
        name = in_flight.get("name")
        tool_name = name if isinstance(name, str) else None
        started = in_flight.get("started_epoch")
        tool_started = started if isinstance(started, int) else None
        command = in_flight.get("command")
        tool_command = command if isinstance(command, str) else None
        output_epoch = in_flight.get("output_epoch")
        tool_output = output_epoch if isinstance(output_epoch, int) else None
    # The wrapper and this sidecar are not steering targets: a controller kills
    # the tool's own child, never the wrapper (a wrapper kill discards the run's
    # classification and its `.done` marker). The set is the in-flight tool
    # call's own processes: the direct child of the wrapper that is not this
    # sidecar is the agent, and its descendants are the processes the tool itself
    # started (a `bash -lc` shell, its `pytest` workers, a compiler's children).
    steering_pids = [] if located is None else located[0]
    tree_rows = [{"pid": pid, "command": short_command(pid)} for pid in steering_pids]
    # A tool cannot have made progress before it started. An epoch EARLIER than
    # the in-flight tool's own start is evidence about something else - the run,
    # or the tool before this one - so the epoch is published as `null`, never
    # lifted up to the tool's start: lifting it makes the tool's own age stand
    # in for evidence nobody read, which is the wall-clock-only rule CTL-18
    # removed. The `measured` flag is NOT cleared by this: it describes whether
    # the current tool's counters were READ this tick, and a baseline from that
    # reading is a real measurement of a tool that has not moved yet.
    if (
        tool_started is not None
        and progress_epoch is not None
        and progress_epoch < tool_started
    ):
        progress_epoch = None
    return {
        "run": run,
        "wrapper_pid": wrapper_pid,
        "updated_epoch": int(time.time()),
        "last_event_epoch": last_event,
        "tool_in_flight": tool_name,
        "tool_in_flight_started_epoch": tool_started,
        "assistant_turns": state.turns,
        "tool_calls": state.tool_calls,
        "latest_total_tokens": state.latest_nonzero_tokens,
        "latest_total_tokens_last_report": state.last_reported_tokens,
        "tool_in_flight_command": tool_command,
        "tool_in_flight_output_epoch": tool_output,
        "progress_epoch": progress_epoch,
        "progress_measured": bool(progress_measured),
        "progress_reason": progress_reason,
        "progress_baseline_epoch": progress_baseline,
        "progress_tree_nodes": tree_nodes,
        "progress_tree_truncated": tree_truncated,
        "steering_children": tree_rows,
        "commits_ahead": int(ahead.strip()) if ahead is not None and ahead.strip().isdigit() else None,
        "uncommitted_files": None if porcelain is None else len(
            [entry for entry in porcelain.splitlines() if entry.strip()]
        ),
        "last_commit_epoch": commit_epoch,
    }


def write_atomic(target: Path, body: dict[str, object]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(target.parent), prefix=f".{target.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(body, output, sort_keys=True)
            output.write("\n")
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def tree_limit() -> int:
    """The documented node cap for one tick's tree walk."""
    override = os.environ.get("PI_LIVE_TREE_NODE_LIMIT") or ""
    if override.strip().isdigit() and int(override.strip()) > 0:
        return int(override.strip())
    return TREE_NODE_LIMIT


def sample_once(
    wrapper_pid: int, limit: int, sidecar_pid: int | None = None
) -> tuple[Sample | None, int | None, bool, tuple[list[int], bool] | None]:
    """One tick's tool-process reading: (counters, node count, truncated, pids).

    Every fact comes from ONE `/proc` scan: the table read here is the table the
    summed pids and their start ticks come from, so a process that exits between
    two separate scans cannot make the members of the set disagree with each
    other.
    """
    table = process_table()
    located = tool_processes(wrapper_pid, table, limit, sidecar_pid)
    if located is None:
        # The agent's subtree could not be located: counters are unknown, and a
        # node count is not reported because no set was summed.
        return None, None, False, None
    pids, truncated = located
    return read_counters(pids, table), len(pids), truncated, located


def tick_source(path: str | None):
    """The tick/clock source for one run.

    Production: the wall clock and `--interval`. A test supplies `--tick-stream
    PATH` instead: PATH holds one epoch per line (integer seconds), and each
    line IS the next tick's clock. A FIFO as the path supplies both directions
    of the sync the tests need - the test decides exactly when a tick happens
    and exactly what the clock says at it - so no check has to sleep for a wall
    clock or guess how loaded the host is. Closing the stream ends the run.
    """
    if path is not None:
        return open(path, "r", encoding="utf-8")
    return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Write a run's live/progress snapshot.")
    parser.add_argument("--stream", required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--wrapper-pid", required=True, type=int)
    parser.add_argument("--interval", required=True, type=float)
    parser.add_argument("--once", action="store_true")
    # A test-side sync point: one clock epoch per line, one tick per line. With
    # it there is no wall clock and no interval in the loop at all.
    parser.add_argument("--tick-stream", default=None)
    # A test-side record of each completed tick: one line per tick, so a test
    # knows a tick FINISHED before it changes the fake tree the next tick reads.
    parser.add_argument("--tick-log", default=None)
    # This sidecar's own pid; the default is the truth in production. A test
    # supplies a KNOWN pid so it can put a growing process there and assert that
    # the sidecar's own counters never count as a tool's progress.
    parser.add_argument("--sidecar-pid", default=None, type=int)
    arguments = parser.parse_args(argv)
    clock_holder: list[int] = []
    state = Snapshot(Path(arguments.stream), lambda: clock_holder[0])
    progress = Progress()
    target = Path(arguments.out)
    ticks = tick_source(arguments.tick_stream)

    def note_tick(now: int, nodes: int | None, truncated: bool, epoch: int | None,
                  reason: str | None, baseline: int | None, measured: bool) -> None:
        """One JSON line per FINISHED tick.

        A test's sync point: it knows tick N ended before it changes the fake
        tree tick N+1 reads, and it reads the tick's facts as JSON rather than
        as a text format of its own.
        """
        if arguments.tick_log is None:
            return
        try:
            with open(arguments.tick_log, "a", encoding="utf-8") as log:
                log.write(json.dumps({
                    "tick": now, "nodes": nodes, "truncated": bool(truncated),
                    "reason": reason, "progress": epoch,
                    "baseline": baseline, "measured": bool(measured),
                }, sort_keys=True) + "\n")
        except OSError:
            pass

    def tick(now: int) -> None:
        clock_holder[:] = [now]
        grew = state.read_new() > 0
        progress.set_tool_in_flight(state.in_flight is not None)
        sample, nodes, truncated, located = sample_once(
            arguments.wrapper_pid, tree_limit(), arguments.sidecar_pid
        )
        progress.observe(
            now, grew, sample, nodes, truncated,
            tool_started=state.new_tool_started,
            tool_output=state.new_tool_output,
        )
        body = payload(
            arguments.run, Path(arguments.stream), arguments.worktree,
            arguments.wrapper_pid, state, progress, located,
        )
        note_tick(now, nodes, truncated, progress.epoch, progress.reason,
                  progress.baseline, progress.measured)
        try:
            write_atomic(target, body)
        except OSError:
            pass

    if ticks is not None:
        with ticks:
            for line in ticks:
                if not line.strip().isdigit():
                    continue
                tick(int(line.strip()))
                if arguments.once:
                    break
        return 0
    while True:
        tick(int(time.time()))
        if arguments.once:
            return 0
        if not alive(arguments.wrapper_pid):
            return 0
        time.sleep(arguments.interval)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(0)
