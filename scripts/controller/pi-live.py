#!/usr/bin/env python3
"""Write a run's liveness/progress snapshot for the controller's Monitor.

Started by an agent wrapper (currently pi-impl.sh) while the run is active and
killed when the wrapper exits. It re-reads only the bytes appended to the run's
event stream since the previous tick, so a multi-hundred-megabyte stream costs
one incremental parse per tick.

The snapshot holds only facts read from the stream or from git, as absolute
epochs and counts: a value that cannot be read is written as null rather than
invented, and the reader derives every age itself (so a stale snapshot still
describes what it last saw). The file is replaced atomically (temp file + rename
in the same directory), so a reader never sees a partial JSON object.

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
        self.total_tokens: int | None = None
        self.in_flight: dict[str, object] | None = None

    def read_new(self) -> None:
        try:
            with self.stream.open("rb") as source:
                if os.fstat(source.fileno()).st_size < self.offset:
                    # The stream was truncated and rewritten (a new first run):
                    # start over rather than wait to outgrow the old offset.
                    self.offset = 0
                    self.incomplete = b""
                    self.turns = 0
                    self.tool_calls = 0
                    self.total_tokens = None
                    self.in_flight = None
                source.seek(self.offset)
                chunk = source.read()
                self.offset = source.tell()
        except OSError:
            return
        if not chunk:
            return
        buffer = self.incomplete + chunk
        lines = buffer.split(b"\n")
        self.incomplete = lines.pop()
        for line in lines:
            self.consume(line)

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
                usage = message.get("usage")
                if isinstance(usage, dict) and isinstance(usage.get("totalTokens"), int):
                    self.total_tokens = usage["totalTokens"]
        elif event_type == "message_update":
            usage = event.get("usage")
            if isinstance(usage, dict) and isinstance(usage.get("totalTokens"), int):
                self.total_tokens = usage["totalTokens"]
        elif event_type == "tool_execution_start":
            self.tool_calls += 1
            self.in_flight = {
                "name": event.get("toolName"),
                "call_id": event.get("toolCallId"),
                "started_epoch": int(time.time()),
            }
        elif event_type == "tool_execution_end":
            call_id = event.get("toolCallId")
            if self.in_flight is None or self.in_flight.get("call_id") == call_id:
                self.in_flight = None


def payload(run: str, stream: Path, worktree: str, wrapper_pid: int, state: Snapshot) -> dict[str, object]:
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
    if isinstance(in_flight, dict):
        name = in_flight.get("name")
        tool_name = name if isinstance(name, str) else None
        started = in_flight.get("started_epoch")
        tool_started = started if isinstance(started, int) else None
    return {
        "run": run,
        "wrapper_pid": wrapper_pid,
        "updated_epoch": int(time.time()),
        "last_event_epoch": last_event,
        "tool_in_flight": tool_name,
        "tool_in_flight_started_epoch": tool_started,
        "assistant_turns": state.turns,
        "tool_calls": state.tool_calls,
        "latest_total_tokens": state.total_tokens,
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
    target = Path(arguments.out)
    while True:
        state.read_new()
        try:
            write_atomic(target, payload(
                arguments.run, Path(arguments.stream), arguments.worktree,
                arguments.wrapper_pid, state,
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
