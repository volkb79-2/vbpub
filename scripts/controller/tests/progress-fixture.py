#!/usr/bin/env python3
"""Fake `/proc` for the progress-rule checks, and a way to change it between ticks.

The checks that judge the progress rule must not depend on how loaded the host
is, on a real process tree, or on a wall clock. So a check drives pi-live.py
with `--tick-stream` (one epoch per line, one tick per line) against a fake
`/proc` root it owns, and changes that root between ticks. Every assertion then
reads a fact this check constructed.

Usage:
  progress-fixture.py tree DIR [WRAPPER_PID]        # write the default tree
  progress-fixture.py idle DIR [WRAPPER_PID]        # a tool call with no child
  progress-fixture.py set DIR PID cpu=N io=N start=N # change one process
  progress-fixture.py add DIR PID PPID CMD...        # add a process
  progress-fixture.py rm DIR PID                     # remove a process
  progress-fixture.py truncation DIR DEPTH           # a tree deeper than the cap

The tree is a wrapper (the pid the check passes as --wrapper-pid), the agent as
its direct child, and the in-flight tool call's processes under the agent:

  WRAPPER            the wrapper shell                     (never summed)
  WRAPPER+1          the agent (pi)                        (never summed)
  WRAPPER+2          the tool: `bash -lc sleep 600`         (summed)
  WRAPPER+3          the tool's own child: `sleep 600`      (summed)

Both `WRAPPER` and `WRAPPER+1` are members that grow on their own in a real
run (the wrapper's bookkeeping and the agent's model loop), which is why the
checks drive THEIR counters and assert that the progress fact does not move.
"""

import os
import shutil
import sys


def pid_paths(root: str, pid: int) -> tuple[str, str, str]:
    directory = os.path.join(root, str(pid))
    return (
        os.path.join(directory, "stat"),
        os.path.join(directory, "io"),
        os.path.join(directory, "cmdline"),
    )


def write_process(root: str, pid: int, ppid: int, cpu: int, io: int, cmd: str,
                  start_ticks: int | None = None) -> None:
    directory = os.path.join(root, str(pid))
    os.makedirs(directory, exist_ok=True)
    stat_path, io_path, cmdline_path = pid_paths(root, pid)
    # After the parenthesised comm: state is field 3, so `rest` index 1 is ppid,
    # utime and stime are indices 11/12 (fields 14/15) and starttime is index 19
    # (field 22) - exactly the fields the sidecar reads.
    fields = (
        ["S", str(ppid)]          # index 0: state (field 3), index 1: ppid (4)
        + ["0"] * 9               # indices 2-10: fields 5-13
        + [str(cpu), "0"]         # indices 11/12: utime/stime (fields 14/15)
        + ["0"] * 6               # indices 13-18: fields 16-21
        + [str(pid * 10 if start_ticks is None else start_ticks)]  # 19: field 22
    )
    with open(stat_path, "w", encoding="utf-8") as handle:
        handle.write(f"{pid} (fake) " + " ".join(fields) + "\n")
    with open(io_path, "w", encoding="utf-8") as handle:
        handle.write(f"read_bytes: {io}\nwrite_bytes: 0\n")
    with open(cmdline_path, "w", encoding="utf-8") as handle:
        handle.write(cmd.replace(" ", "\0") + "\0")


def read_process(root: str, pid: int) -> dict[str, object]:
    stat_path, io_path, cmdline_path = pid_paths(root, pid)
    with open(stat_path, encoding="utf-8") as handle:
        line = handle.read()
    _, _, rest = line.partition(") ")
    fields = rest.split()
    io: dict[str, int] = {}
    with open(io_path, encoding="utf-8") as handle:
        for entry in handle:
            key, _, value = entry.partition(":")
            if value.strip().isdigit():
                io[key.strip()] = int(value.strip())
    with open(cmdline_path, encoding="utf-8") as handle:
        cmd = handle.read().replace("\0", " ").strip()
    return {
        "ppid": int(fields[1]),
        "cpu": int(fields[11]) + int(fields[12]),
        "start_ticks": int(fields[19]),
        "io": io.get("read_bytes", 0),
        "cmd": cmd,
    }


def default_tree(root: str, wrapper: int) -> None:
    shutil.rmtree(root, ignore_errors=True)
    os.makedirs(root, exist_ok=True)
    write_process(root, wrapper, 1, cpu=5, io=0, cmd="bash pi-impl.sh")
    write_process(root, wrapper + 1, wrapper, cpu=1, io=0, cmd="pi -p --mode json")
    write_process(root, wrapper + 2, wrapper + 1, cpu=0, io=0, cmd="bash -lc sleep 600")
    write_process(root, wrapper + 3, wrapper + 2, cpu=0, io=0, cmd="sleep 600")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    command, root = argv[0], argv[1]
    if command == "tree":
        default_tree(root, int(argv[2]) if len(argv) > 2 else 100)
        return 0
    if command == "idle":
        # The real transient state this check needs: the agent's shell is alive
        # but the tool call has produced no child process yet (`sleep 600` was
        # just started, or the tool is blocked before it forks anything).
        default_tree(root, int(argv[2]) if len(argv) > 2 else 100)
        for pid in (102, 103):
            shutil.rmtree(os.path.join(root, str(pid)), ignore_errors=True)
        return 0
    if command == "set":
        pid = int(argv[2])
        previous = read_process(root, pid)
        cpu, io, start_ticks = previous["cpu"], previous["io"], previous["start_ticks"]
        for token in argv[3:]:
            key, _, value = token.partition("=")
            if key == "cpu":
                cpu = int(value)
            elif key == "io":
                io = int(value)
            elif key == "start":
                start_ticks = int(value)
        write_process(root, pid, previous["ppid"], cpu, io, previous["cmd"],
                      start_ticks=start_ticks)
        return 0
    if command == "add":
        write_process(root, int(argv[2]), int(argv[3]), cpu=0, io=0,
                      cmd=" ".join(argv[4:]) or "worker")
        return 0
    if command == "rm":
        shutil.rmtree(os.path.join(root, argv[2]), ignore_errors=True)
        return 0
    if command == "truncation":
        default_tree(root, int(argv[2]) if len(argv) > 2 else 100)
        depth = int(argv[3]) if len(argv) > 3 else 20
        parent = (int(argv[2]) if len(argv) > 2 else 100) + 3
        for step in range(depth):
            pid = 1000 + step
            write_process(root, pid, parent, cpu=0, io=0, cmd=f"worker {pid}")
            parent = pid
        return 0
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
