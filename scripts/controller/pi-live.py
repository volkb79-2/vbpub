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
  new bytes in the run's event stream, or growth of the CPU-time and IO counters
  of the wrapper's own process subtree (`/proc/<pid>/stat` fields 14/15 and
  `/proc/<pid>/io`). It is the fact that separates a heavy-but-working tool
  (`LONG-TOOL`) from a tool that stopped making progress (`HUNG-TOOL`); a
  `/proc` that cannot be read leaves it null and never invents progress.

The tool in flight also carries `tool_in_flight_command` (the tool's own
`args.command`, bounded) and `steering_children` (the wrapper's live descendant
pids and their short command lines, bounded, excluding the wrapper and this
sidecar), so a reader can name the work and steer the EXACT child pid instead of
the wrapper.

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


COMMAND_LIMIT = 200
TREE_LIMIT = 8


def read_text(path: str) -> str | None:
    try:
        with open(path, "rb") as source:
            return source.read().decode("utf-8", "replace")
    except OSError:
        return None


def process_table() -> dict[int, int]:
    """pid -> parent pid, from the kernel's own view of the process table."""
    table: dict[int, int] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return table
    for entry in entries:
        if not entry.isdigit():
            continue
        stat_line = read_text(f"/proc/{entry}/stat")
        if stat_line is None:
            continue
        # Field 4 (ppid) is the second field after the parenthesised comm.
        _, _, rest = stat_line.partition(") ")
        fields = rest.split()
        if len(fields) >= 2 and fields[1].isdigit():
            table[int(entry)] = int(fields[1])
    return table


def process_tree(root_pid: int) -> list[int]:
    """The root pid and every live descendant of it, bounded to TREE_LIMIT."""
    if not isinstance(root_pid, int) or root_pid <= 0:
        return []
    table = process_table()
    if root_pid not in table:
        return []
    children: dict[int, list[int]] = {}
    for pid, parent in table.items():
        children.setdefault(parent, []).append(pid)
    pending = [root_pid]
    tree: list[int] = []
    while pending and len(tree) < TREE_LIMIT:
        pid = pending.pop(0)
        tree.append(pid)
        pending.extend(sorted(children.get(pid, [])))
    return tree


def short_command(pid: int) -> str | None:
    raw = read_text(f"/proc/{pid}/cmdline")
    if raw is None:
        return None
    parts = [part for part in raw.split("\0") if part]
    if not parts:
        return None
    text = " ".join(parts)
    return text[:COMMAND_LIMIT]


def resource_counters(root_pid: int) -> tuple[int, int, None] | None:
    """(cpu ticks, io bytes) summed over the subtree; None when unreadable."""
    tree = process_tree(root_pid)
    if not tree:
        return None
    cpu = 0
    io = 0
    readable = False
    for pid in tree:
        stat_line = read_text(f"/proc/{pid}/stat")
        if stat_line is not None:
            _, _, rest = stat_line.partition(") ")
            fields = rest.split()
            if len(fields) >= 15:
                try:
                    cpu += int(fields[11]) + int(fields[12])
                    readable = True
                except ValueError:
                    pass
        io_text = read_text(f"/proc/{pid}/io")
        if io_text is not None:
            for line in io_text.splitlines():
                key, _, value = line.partition(":")
                if key in ("read_bytes", "write_bytes") and value.strip().isdigit():
                    io += int(value.strip())
                    readable = True
    return (cpu, io) if readable else None


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class Snapshot:
    """Incremental reader of a pi JSON event stream."""

    def __init__(self, stream: Path) -> None:
        self.stream = stream
        self.offset = 0
        self.incomplete = b""
        self.turns = 0
        self.tool_calls = 0
        self.latest_nonzero_tokens: int | None = None
        self.last_reported_tokens: int | None = None
        self.in_flight: dict[str, object] | None = None

    def read_new(self) -> int:
        """Read newly appended bytes; returns how many were consumed."""
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
            arguments = event.get("args")
            command = None
            if isinstance(arguments, dict):
                value = arguments.get("command")
                if isinstance(value, str) and value.strip():
                    command = value[:COMMAND_LIMIT]
            self.in_flight = {
                "name": event.get("toolName"),
                "call_id": event.get("toolCallId"),
                "started_epoch": int(time.time()),
                "command": command,
                "output_epoch": None,
            }
        elif event_type == "tool_execution_update":
            # A tool's own partial output is progress evidence: the event is
            # appended to the stream while the tool is still running, and a
            # heavy-but-working tool produces them.
            call_id = event.get("toolCallId")
            if self.in_flight is not None and self.in_flight.get("call_id") == call_id:
                self.in_flight["output_epoch"] = int(time.time())
        elif event_type == "tool_execution_end":
            call_id = event.get("toolCallId")
            if self.in_flight is None or self.in_flight.get("call_id") == call_id:
                self.in_flight = None

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

    Progress evidence is observable, never inferred: bytes appended to the run's
    event stream, or growth of the CPU-time and IO counters summed over the
    wrapper's own process subtree. A tick that shows neither leaves the epoch
    where it was, which is exactly the fact a reader needs to tell a
    heavy-but-working tool from a stopped one. When the counters cannot be read
    at all, stream growth alone decides, and a stream that does not grow is
    reported as no new progress evidence rather than as progress.
    """

    def __init__(self) -> None:
        self.counters: tuple[int, int] | None = None
        self.epoch: int | None = None

    def observe(self, now: int, grew: bool, counters: tuple[int, int] | None) -> int | None:
        changed = bool(grew)
        if counters is not None:
            if self.counters is not None and counters != self.counters:
                changed = True
            self.counters = counters
        if changed:
            self.epoch = now
        return self.epoch


def payload(
    run: str,
    stream: Path,
    worktree: str,
    wrapper_pid: int,
    state: Snapshot,
    progress_epoch: int | None = None,
) -> dict[str, object]:
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
    # classification and its `.done` marker).
    steering_pids = [pid for pid in process_tree(wrapper_pid) if pid not in (wrapper_pid, os.getpid())]
    tree_rows = [{"pid": pid, "command": short_command(pid)} for pid in steering_pids]
    # A tool cannot have made progress before it started: the age a reader
    # derives is bounded by the tool's own age.
    if tool_started is not None:
        if progress_epoch is None or progress_epoch < tool_started:
            progress_epoch = tool_started
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


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Write a run's live/progress snapshot.")
    parser.add_argument("--stream", required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--wrapper-pid", required=True, type=int)
    parser.add_argument("--interval", required=True, type=float)
    parser.add_argument("--once", action="store_true")
    arguments = parser.parse_args(argv)
    state = Snapshot(Path(arguments.stream))
    progress = Progress()
    target = Path(arguments.out)
    while True:
        grew = state.read_new() > 0
        now = int(time.time())
        progress_epoch = progress.observe(now, grew, resource_counters(arguments.wrapper_pid))
        try:
            write_atomic(target, payload(
                arguments.run, Path(arguments.stream), arguments.worktree,
                arguments.wrapper_pid, state, progress_epoch,
            ))
        except OSError:
            pass
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
