"""Containment predicate for ``@pytest.mark.requires_contained_cgroup`` (TP-13).

The contract is ``docs/DESIGN-CONTAINMENT-MARKER.md``.  Process and cgroup
roots are injectable so the complete decision can be exercised against fake
filesystems; the plugin supplies the real roots.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

DEFAULT_PROC_ROOT = Path("/proc")
DEFAULT_CGROUP_ROOT = Path("/sys/fs/cgroup")
SETTLE_POLL_SECONDS = 0.05
_DIGITS = re.compile(r"[0-9]+$")
REMEDY = (
    "run under a capped container with a private PID namespace "
    "(docker run --init --memory … --pids-limit …)"
)


@dataclass(frozen=True)
class Process:
    pid: int
    ppid: int
    state: str
    command: str
    start_time: int


@dataclass(frozen=True)
class Refusal:
    reason: str
    summary: str
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContainmentResult:
    contained: bool
    refusals: tuple[Refusal, ...]

    @property
    def message(self) -> str:
        if self.contained:
            return ""
        lines = ["estate-test-policy: requires_contained_cgroup refused:"]
        for refusal in self.refusals:
            lines.append(f"  {refusal.reason}: {refusal.summary}")
            lines.extend(f"    {detail}" for detail in refusal.details)
        lines.append(f"  remedy: {REMEDY}")
        return "\n".join(lines)


class _ProcessReadError(Exception):
    """A process entry existed but could not be interpreted as a stat record."""


def _read_record(path: Path, expected_pid: int | None = None) -> Process | None:
    try:
        stat = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _ProcessReadError(f"cannot read {path}: {exc}") from exc

    open_paren = stat.find("(")
    close_paren = stat.rfind(")")
    if open_paren < 0 or close_paren <= open_paren:
        raise _ProcessReadError(f"malformed process stat file {path}")
    fields = stat[close_paren + 1 :].split()
    if len(fields) < 20:
        raise _ProcessReadError(f"malformed process stat file {path}")
    try:
        pid = int(stat[:open_paren].strip())
        if expected_pid is not None and pid != expected_pid:
            raise ValueError(f"stat pid {pid} does not match directory pid {expected_pid}")
        ppid = int(fields[1])
        start_time = int(fields[19])
    except ValueError as exc:
        raise _ProcessReadError(f"malformed process identity in {path}") from exc
    return Process(pid, ppid, fields[0], stat[open_paren + 1 : close_paren], start_time)


def _process_tree(proc_root: Path) -> tuple[dict[int, Process], list[str]]:
    records: dict[int, Process] = {}
    errors: list[str] = []
    try:
        entries = list(Path(proc_root).iterdir())
    except OSError as exc:
        return records, [f"cannot enumerate {proc_root}: {exc}"]
    for entry in entries:
        if not _DIGITS.fullmatch(entry.name):
            continue
        try:
            record = _read_record(entry / "stat", expected_pid=int(entry.name))
        except _ProcessReadError as exc:
            errors.append(str(exc))
            continue
        if record is not None:
            records[record.pid] = record
    return records, errors


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _cgroup_leaf(proc_root: Path, cgroup_root: Path) -> tuple[Path | None, list[Refusal]]:
    cgroup_path = proc_root / "self" / "cgroup"
    try:
        lines = _read_text(cgroup_path).splitlines()
    except OSError as exc:
        return None, [Refusal("cgroup-v2", f"cannot read {cgroup_path}: {exc}")]
    if len(lines) != 1 or not lines[0].startswith("0::/"):
        return None, [Refusal("cgroup-v2", "expected exactly one cgroup-v2 line `0::/…`")]

    path = lines[0][3:]
    parts = PurePosixPath(path).parts
    if ".." in parts:
        return None, [Refusal("cgroup-v2", f"cgroup path {path!r} contains a `..` component")]
    leaf = cgroup_root.joinpath(*parts[1:])
    try:
        if not leaf.is_dir():
            return None, [Refusal("cgroup-v2", f"cgroup directory does not exist: {leaf}")]
        controllers = leaf / "cgroup.controllers"
        _read_text(controllers)
    except OSError as exc:
        return None, [Refusal("cgroup-v2", f"cannot read cgroup-v2 capability file: {exc}")]
    return leaf, []


def _cgroup_limits(leaf: Path, cgroup_root: Path) -> list[Refusal]:
    levels = []
    current = leaf
    while True:
        levels.append(current)
        if current == cgroup_root:
            break
        current = current.parent

    failures = []
    for filename in ("memory.max", "pids.max"):
        finite: list[int] = []
        errors: list[str] = []
        for level in levels:
            limit_path = level / filename
            try:
                value = _read_text(limit_path).strip()
            except FileNotFoundError:
                continue
            except OSError as exc:
                errors.append(f"{limit_path}: {exc}")
                continue
            if value == "max":
                continue
            try:
                parsed = int(value)
                if parsed < 0:
                    raise ValueError("negative limit")
            except ValueError:
                errors.append(f"{limit_path}: expected a non-negative integer or `max`, got {value!r}")
                continue
            finite.append(parsed)
        if errors:
            failures.append(Refusal(filename, "cannot evaluate the effective limit", tuple(errors)))
        elif not finite:
            failures.append(Refusal(filename, "no finite effective limit"))
    return failures


_MOUNT_ESCAPE = re.compile(r"\\([0-7]{3})")


def _unescape_mount(value: str) -> str:
    return _MOUNT_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)


def _proc_mount(proc_root: Path) -> list[Refusal]:
    mountinfo = proc_root / "self" / "mountinfo"
    try:
        lines = _read_text(mountinfo).splitlines()
    except OSError as exc:
        return [Refusal("proc-hidepid", f"cannot read {mountinfo}: {exc}")]

    proc_mounts: list[tuple[list[str], list[str]]] = []
    parse_errors = []
    for line in lines:
        if not line.strip():
            continue
        try:
            left, right = line.split(" - ", 1)
            left_fields = left.split()
            right_fields = right.split()
            if len(left_fields) < 6 or len(right_fields) < 3:
                raise ValueError("too few fields")
        except ValueError as exc:
            parse_errors.append(f"malformed mountinfo line: {exc}")
            continue
        if right_fields[0] == "proc" and _unescape_mount(left_fields[4]) == "/proc":
            proc_mounts.append((left_fields, right_fields))

    if not proc_mounts:
        return [Refusal("proc-hidepid", "no proc filesystem is mounted at /proc", tuple(parse_errors))]
    invalid = []
    for left_fields, right_fields in proc_mounts:
        options = left_fields[5].split(",") + left_fields[6:] + right_fields[2:]
        tokens = [token for option_field in options for token in option_field.split(",") if token]
        for token in tokens:
            if token == "hidepid" or token.startswith("hidepid="):
                value = token.partition("=")[2].lower()
                if value not in {"0", "off"}:
                    invalid.append(f"hidepid={value or '<missing>'}")
            elif token == "subset" or token.startswith("subset="):
                value = token.partition("=")[2].lower()
                if value == "pid":
                    invalid.append("subset=pid")
    if parse_errors or invalid:
        return [Refusal("proc-hidepid", "the /proc mount hides processes", tuple(parse_errors + invalid))]
    return []


def _classify_namespace(
    proc_root: Path,
    records: dict[int, Process],
    scan_errors: list[str],
) -> tuple[list[Refusal], bool]:
    try:
        me = _read_record(proc_root / "self" / "stat")
    except _ProcessReadError as exc:
        return [Refusal("pid-namespace-entry", str(exc))], False
    if me is None:
        return [Refusal("pid-namespace-entry", f"cannot read {proc_root / 'self' / 'stat'}")], False
    records.setdefault(me.pid, me)

    failures = []
    top = me.pid
    seen = set()
    while True:
        record = records.get(top)
        if record is None:
            failures.append(Refusal("pid-namespace-entry", f"cannot resolve process-tree ancestor pid {top}"))
            return failures, False
        if record.ppid in {0, 1}:
            break
        if record.ppid in seen or record.ppid == top:
            failures.append(Refusal("pid-namespace-entry", f"process-tree ancestor cycle at pid {top}"))
            return failures, False
        seen.add(top)
        top = record.ppid

    top_parent = records[top].ppid
    if me.pid != 1 and top_parent == 0:
        failures.append(
            Refusal(
                "pid-namespace-entry",
                f"pid {me.pid} entered through a ppid-0 exec session, not from PID 1",
            )
        )

    subtree = set()
    pending = [top]
    while pending:
        parent = pending.pop()
        subtree.add(parent)
        pending.extend(pid for pid, record in records.items() if record.ppid == parent)
    foreign = {
        pid: record for pid, record in records.items() if pid != 1 and pid not in subtree
    }
    has_entry = any(record.pid != 1 and record.ppid == 0 for record in records.values())

    if foreign or scan_errors:
        details = []
        if scan_errors:
            details.append("process table could not be read completely")
            details.extend(scan_errors)
        if foreign:
            orphan = not has_entry and all(record.ppid == 1 for record in foreign.values())
            if orphan:
                details.append(
                    f"orphan, likely leaked by an earlier test: {_process_list(foreign)}"
                )
            else:
                by_parent: dict[int, list[Process]] = {}
                for record in foreign.values():
                    by_parent.setdefault(record.ppid, []).append(record)
                for parent, grouped in sorted(by_parent.items()):
                    if parent == 0:
                        label = "ppid 0 entry (exec session)"
                    elif parent == 1:
                        label = "PID 1 child"
                    else:
                        label = f"child of pid {parent}"
                    details.append(f"{label}: {_process_list({record.pid: record for record in grouped})}")
        failures.append(
            Refusal(
                "pid-namespace-shared",
                f"{len(foreign)} process(es) outside this test's tree (first 5 per group)",
                tuple(details),
            )
        )
    return failures, bool(foreign or scan_errors)


def _process_list(records: dict[int, Process]) -> str:
    entries = []
    for record in sorted(records.values(), key=lambda item: item.pid)[:5]:
        command = " ".join(record.command.split())
        entries.append(f"pid {record.pid} {command}")
    return "; ".join(entries)


def _namespace(
    proc_root: Path,
    settle_seconds: float,
    clock: Callable[[], float],
    sleeper: Callable[[float], None],
) -> list[Refusal]:
    records, scan_errors = _process_tree(proc_root)
    failures, unsettled = _classify_namespace(proc_root, records, scan_errors)
    if not unsettled or settle_seconds <= 0:
        return failures

    deadline = clock() + settle_seconds
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            return failures
        sleeper(min(SETTLE_POLL_SECONDS, remaining))
        records, scan_errors = _process_tree(proc_root)
        failures, unsettled = _classify_namespace(proc_root, records, scan_errors)
        if not unsettled:
            return failures


def check(
    *,
    proc_root: Path | None = None,
    cgroup_root: Path | None = None,
    settle_seconds: float = 5.0,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> ContainmentResult:
    """Evaluate every containment fact and aggregate every refusal."""
    proc_root = DEFAULT_PROC_ROOT if proc_root is None else Path(proc_root)
    cgroup_root = DEFAULT_CGROUP_ROOT if cgroup_root is None else Path(cgroup_root)
    leaf, cgroup_failures = _cgroup_leaf(proc_root, cgroup_root)
    limit_failures = _cgroup_limits(leaf, cgroup_root) if leaf is not None else []
    failures = cgroup_failures + limit_failures + _proc_mount(proc_root)
    failures.extend(_namespace(proc_root, settle_seconds, clock, sleeper))
    return ContainmentResult(not failures, tuple(failures))
