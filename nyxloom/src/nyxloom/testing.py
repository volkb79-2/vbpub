"""Scriptable fake agent/CLI for the F0 behavioral test harness.

WHY THIS EXISTS (F0 2026-07-17): the unit suite (~610 tests) is green yet
these shipped: REVIEW_REJECTED stranded (no handler), a notification storm
every reconcile cycle for a persistent condition, a reviewer's MISNAMED
review file falsely rejecting a genuinely-approved task, and an illegal
transition leaving a spurious log event. Every one of those slipped past
unit tests exercising a single reconcile pass against a trivial fake ("echo
OK; exit 0"). This module is the fake CLI's SCRIPTING SURFACE: it lets a
test author, per (task_id, role), queue a sequence of behaviors -- commit N
files (or none), write a review file (at any filename, with any verdict),
exit BLOCKED, exit with an error, or hit a provider "limit" -- then drive
the REAL daemon reconcile loop (nyxloom.daemon.Daemon.run_pass) over many
cycles against it. See tests/test_behavioral.py for the seed lifecycle
tests built on top of this.

ARCHITECTURE NOTE (why scripting happens by file, not by editing the fake
CLI's argv per call): the wrapper (wrapper.py) spawns the CLI as a real,
detached subprocess and writes its OWN Receipt from the process's exit code
+ a log-tail classification (adapters.classify_log_tail: a `BLOCKED: ...`
line, or a rate/quota-limit phrase, in the last lines of stdout) -- it never
reads back anything the CLI itself may have written to receipt_path. So the
fake CLI's only levers over the daemon-visible outcome are: (1) its OWN
process exit code, (2) what it prints to stdout (captured into the attempt
log the wrapper classifies), and (3) real git commits it makes on disk (the
daemon's EmitAttemptExit cross-checks git truth for head_commit -- see
daemon.py's `_crosscheck_head_commit`, P21 2026-07-16 -- and
`_parse_review_verdict` reads a committed `*REVIEW*.md` file straight off
the task's feat/<task_id> branch via `git show`). This module's `FakeStep`
covers exactly those three levers; nothing here needs a wrapper.py or
adapters.py change, and it never touches daemon.py/reconcile.py/storage.py.

FILE-BASED SCRIPT (not in-process state) because the fake CLI runs in a
genuinely separate, double-forked OS process (wrapper.launch_detached) --
there is no shared Python object between the test and the fake CLI, only
whatever's on disk or in the (inherited, then process-forked) environment.
The script is a small JSON file (path named by the NYXLOOM_FAKE_SCRIPT env
var, set once for the whole test) keyed by task_id then role (a literal
string a test controls via its own route's `dispatch_extra` template --
see tests/test_behavioral.py's BEHAVIORAL_ROUTES_TOML, which tags the
implementer route `--role implementer` and the review route `--role
review`). Each (task_id, role) entry holds:
  - "queue": a FIFO list of one-shot steps, popped in order (each fake CLI
    invocation for that (task_id, role) consumes exactly one).
  - "default": a step reused indefinitely once the queue is empty (models a
    PERSISTENT condition, e.g. "this reviewer always rejects" --
    test_behavioral.py's bounded-notification-storm oracle needs exactly
    this shape).
A companion `.lock` file (flock, exclusive) guards the read-modify-write so
sequential test-driven cycles (the harness always waits for a receipt
before the next reconcile pass -- see test_behavioral.py's `_tick`) never
race, even though nothing here assumes true concurrent callers.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# test-side scripting API (imported by tests; runs in the pytest process)

@dataclass
class FakeStep:
    """One scripted invocation's behavior. See module docstring for how each
    field maps onto the ONLY three levers a detached fake CLI process has
    over the daemon-visible outcome (exit code, stdout, real git commits)."""

    # Directory the git commit (if any) is made in. None -> the fake CLI's
    # own CWD (correct for an IMPLEMENTER dispatch: the wrapper's cwd IS the
    # task's own feat/<task_id> worktree). A FRONTIER_REVIEW dispatch's cwd
    # is cfg.root (NOT the task worktree -- see daemon.py's LaunchReview
    # execute branch), so a review step that must commit onto feat/<task_id>
    # needs this set explicitly to str(cfg.root / cfg.worktree_root /
    # f"feat/{task_id}") by the test (it already knows cfg, same convention
    # DispatchImplementer/_ensure_worktree use).
    target_dir: str | None = None

    # Extra files to write + commit (relative path -> content). An EMPTY
    # dict (the default) means "commit nothing" -- the null-head_commit
    # case (daemon.py's _crosscheck_head_commit only upgrades head_commit
    # when the branch is actually ahead of default_branch).
    commit_files: dict[str, str] = field(default_factory=dict)

    # Convenience: also write+commit a review file alongside commit_files.
    # `review_file` is a path RELATIVE TO target_dir (e.g.
    # f"{cfg.reports_dir}/{task_id}-REVIEW.md" for the documented name, or
    # any other name -- including a MISNAMED one -- to exercise
    # daemon.py's broadened *REVIEW*.md fallback search, P33 2026-07-16).
    review_file: str | None = None
    review_verdict: str | None = None      # "APPROVED" | "REJECTED"
    review_reason: str = ""
    review_body: str | None = None         # override the generated body

    # If set, the fake prints `BLOCKED: <reason>` to stdout -- wrapper.py's
    # classify_log_tail then forces receipt.result BLOCKED regardless of
    # exit_code (blocked beats exit code beats limit).
    blocked_reason: str | None = None

    # If True, the fake prints a provider rate/quota-limit phrase --
    # classify_log_tail then forces receipt.result LIMIT.
    limit: bool = False

    extra_stdout: str = ""
    exit_code: int = 0

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"exit_code": self.exit_code}
        if self.target_dir:
            d["target_dir"] = self.target_dir
        if self.commit_files:
            d["commit_files"] = dict(self.commit_files)
        if self.review_file:
            d["review_file"] = self.review_file
            d["review_verdict"] = self.review_verdict or "APPROVED"
            if self.review_reason:
                d["review_reason"] = self.review_reason
            if self.review_body is not None:
                d["review_body"] = self.review_body
        if self.blocked_reason:
            d["blocked_reason"] = self.blocked_reason
        if self.limit:
            d["limit"] = True
        if self.extra_stdout:
            d["extra_stdout"] = self.extra_stdout
        return d


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"tasks": {}}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {"tasks": {}}
    data = json.loads(text)
    data.setdefault("tasks", {})
    return data


def _save(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def _with_lock(path: Path, fn):
    """Run fn(data) -> data under an exclusive flock on a companion .lock
    file, persisting the (possibly mutated) result. Not needed for true
    concurrency (the harness only ever drives one attempt at a time) --
    cheap insurance against any accidental overlap."""
    lock_path = path.with_suffix(".lock")
    lock_path.touch(exist_ok=True)
    with lock_path.open("r+") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            data = _load(path)
            result = fn(data)
            _save(path, data)
            return result
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


class FakeScript:
    """Test-side handle for the on-disk JSON script the fake CLI reads.
    Always reads-modifies-writes the file fresh (never caches in memory) --
    the fake CLI subprocess mutates the SAME file (popping consumed queue
    entries) between test-side calls."""

    def __init__(self, path: Path):
        self.path = path
        if not self.path.exists():
            _save(self.path, {"tasks": {}})

    def queue(self, task_id: str, role: str, *steps: FakeStep) -> "FakeScript":
        """Append one-shot steps to (task_id, role)'s FIFO queue."""
        def _do(data: dict[str, Any]):
            entry = data["tasks"].setdefault(task_id, {}).setdefault(role, {})
            entry.setdefault("queue", []).extend(s.to_dict() for s in steps)
        _with_lock(self.path, _do)
        return self

    def set_default(self, task_id: str, role: str, step: FakeStep) -> "FakeScript":
        """Set the step reused indefinitely once (task_id, role)'s queue is
        empty -- models a PERSISTENT condition (e.g. "this reviewer always
        rejects")."""
        def _do(data: dict[str, Any]):
            entry = data["tasks"].setdefault(task_id, {}).setdefault(role, {})
            entry["default"] = step.to_dict()
        _with_lock(self.path, _do)
        return self

    def remaining(self, task_id: str, role: str) -> int:
        data = _load(self.path)
        return len(data.get("tasks", {}).get(task_id, {}).get(role, {}).get("queue", []))


# ---------------------------------------------------------------------------
# fake CLI entry point (runs in the detached, double-forked subprocess)

def _pop_step(script_path: Path, task_id: str, role: str) -> dict[str, Any] | None:
    def _do(data: dict[str, Any]) -> dict[str, Any] | None:
        entry = data.get("tasks", {}).get(task_id, {}).get(role)
        if entry is None:
            return None
        queue = entry.get("queue") or []
        if queue:
            step = queue.pop(0)
            return step
        return entry.get("default")
    return _with_lock(script_path, _do)


def _run_step(task_id: str, step: dict[str, Any]) -> int:
    target_dir = step.get("target_dir") or os.getcwd()
    Path(target_dir).mkdir(parents=True, exist_ok=True)

    files: dict[str, str] = dict(step.get("commit_files") or {})
    review_file = step.get("review_file")
    if review_file:
        verdict = step.get("review_verdict", "APPROVED")
        reason = step.get("review_reason", "")
        line = f"VERDICT: {verdict}" + (f" — {reason}" if reason else "")
        body = step.get("review_body")
        if body is None:
            body = f"# Review for {task_id}\n\n{line}\n"
        files[review_file] = body

    committed = False
    if files:
        for rel, content in files.items():
            p = Path(target_dir) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", target_dir, "add", "-A"],
                        check=True, capture_output=True)
        res = subprocess.run(
            ["git", "-C", target_dir, "-c", "user.email=fake@nyxloom.test",
             "-c", "user.name=fake-agent", "commit", "-qm",
             f"fake: step for {task_id}"],
            capture_output=True, text=True,
        )
        committed = res.returncode == 0

    extra = step.get("extra_stdout")
    if extra:
        print(extra)

    blocked_reason = step.get("blocked_reason")
    if blocked_reason:
        # adapters.classify_log_tail requires a LINE-START match.
        print(f"BLOCKED: {blocked_reason}")

    if step.get("limit"):
        print("rate limit exceeded")

    print(f"fake-cli: step complete for {task_id} (committed={committed})")
    return int(step.get("exit_code", 0))


def fake_cli_main(argv: list[str]) -> int:
    """Entry point invoked as `fake --role <r> --task <t> <prompt>` (see
    tests/test_behavioral.py's BEHAVIORAL_ROUTES_TOML dispatch_extra
    templates). `argv` excludes argv[0] (the program name)."""
    role: str | None = None
    task_id: str | None = None
    rest = list(argv)
    while len(rest) >= 2 and rest[0] in ("--role", "--task"):
        key, val = rest[0], rest[1]
        rest = rest[2:]
        if key == "--role":
            role = val
        else:
            task_id = val

    script_env = os.environ.get("NYXLOOM_FAKE_SCRIPT")
    if not script_env or not role or not task_id:
        print(
            f"fake-cli: missing script/role/task "
            f"(script={script_env!r} role={role!r} task={task_id!r})",
            file=sys.stderr,
        )
        return 1

    step = _pop_step(Path(script_env), task_id, role)
    if step is None:
        print(f"fake-cli: no scripted step for (task={task_id!r}, role={role!r})",
              file=sys.stderr)
        return 1

    return _run_step(task_id, step)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(fake_cli_main(sys.argv[1:]))
