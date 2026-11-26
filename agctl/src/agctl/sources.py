"""The non-git estate sources: gate leases, gate results, peer state prose and handovers.

Design references: DESIGN section 4a ("Plans and state", "Gates", "Handover").

Three rules this module exists to keep:

* **Never acquire a lock.** Gate admission is a kernel `flock` on a bare file
  (`remote-gate.sh:184-195`). A probe `flock` here would make a real gate admission or a
  launcher see "busy", so held/free is decided by looking the file's device and inode up in
  `/proc/locks`. When that cannot be decided (no readable `/proc/locks`, a file that is not
  there, a stat that fails) the answer is `unknown`, never "free".
* **Never open a lock file for writing**, and never write anything at all.
* **Say when a value is prose.** `STATE.md` is a human document; a package line in it is
  reported `unverified` with the file and the line number, because a sentence is not a state.

Gate results come from `$CONTROLLER_HOME/remote-gates/<run>/`: `meta.json` (host, project,
lane, commit), `result.json` (the recorder's verdict), and `lane.log` (run-gate's own verdict
line, which wins when present — it is the tool's own statement, not a wrapper's echo).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import bounded

PROC_LOCKS = "/proc/locks"
RESULT_JSON = "result.json"
META_JSON = "meta.json"
LANE_LOG = "lane.log"
STATE_FILE = "STATE.md"
HANDOVER_FILE = "HANDOFF.md"
# `run-gate.sh` writes `run-gate: lane '<lane>' verdict <VERDICT>; ...` and
# `remote-gate.sh:294` picks the LAST line that STARTS with this prefix.
VERDICT_PREFIX = "run-gate: lane '"
LANE_STATUS = "verdict"
# A package id line of `STATE.md`: `P-<topic>-<id>` -- the token the join key comes from.
PACKAGE_TOKEN_RE = re.compile(r"P-([A-Za-z0-9][A-Za-z0-9._-]*)")
# `STATE.md` is prose and can be long; a file bigger than this is not read whole.
STATE_LIMIT = 262144

HELD = "held"
FREE = "free"
UNKNOWN = "unknown"
# A `STATE.md` that was read whole. The other two states are `missing` (no file) and
# `unknown` (the file exists but could not be read whole).
PRESENT = "present"


@dataclass(frozen=True)
class LockEntry:
    """One line of `/proc/locks`: what kind of lock, by which pid, on which inode."""

    kind: str  # "FLOCK" | "POSIX" | ...
    pid: int
    device: tuple[int, int]  # major, minor
    inode: int

    @property
    def key(self) -> tuple[int, int, int]:
        return (self.device[0], self.device[1], self.inode)


@dataclass(frozen=True)
class LockTable:
    """The kernel's lock list, or the reason it could not be read."""

    held: tuple[LockEntry, ...]
    readable: bool
    detail: str

    def state_of(self, path: Path) -> tuple[str, int | None, str]:
        """`(held|free|unknown, holder pid, detail)` for one lock file.

        The match is on DEVICE and INODE, never the inode alone: an inode number is only
        unique within a filesystem, so an inode-only match would report a different file's
        lock as this file's holder.
        """

        if not self.readable:
            return UNKNOWN, None, self.detail
        try:
            status = path.stat()
        except OSError as error:
            return UNKNOWN, None, f"cannot stat {path}: {error}"
        key = (os.major(status.st_dev), os.minor(status.st_dev), status.st_ino)
        for entry in self.held:
            if entry.key == key:
                return HELD, entry.pid, f"{entry.kind} lock by pid {entry.pid}"
        return FREE, None, ""


def _decode_device(text: str) -> tuple[int, int] | None:
    """`fd:00` / `00:79` -> `(major, minor)`, both hex, as `/proc/locks` prints them."""

    parts = text.split(":")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0], 16), int(parts[1], 16)
    except ValueError:
        return None


def parse_proc_locks(text: str) -> tuple[LockEntry, ...]:
    """Parse `/proc/locks`; a line this parser does not understand is skipped, not guessed.

    Field layout: `id: KIND ADVISORY ACCESS PID DEVICE:INODE START END` (8 fields). The
    device:inode field is the sixth, e.g. `fd:00:18141984` or `00:79:18`.
    """

    entries: list[LockEntry] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 6:
            continue
        kind = fields[1]
        try:
            pid = int(fields[4])
        except ValueError:
            continue
        # The device:inode field is split on the LAST colon-free inode: `DEV:INO`.
        device_text, _, inode_text = fields[5].rpartition(":")
        device = _decode_device(device_text)
        if device is None or not inode_text.isdigit():
            continue
        entries.append(LockEntry(kind=kind, pid=pid, device=device, inode=int(inode_text)))
    return tuple(entries)


def read_proc_locks(path: str = PROC_LOCKS) -> LockTable:
    """Read the kernel lock list once. Unreadable is `unknown`, never an empty held set."""

    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as error:
        return LockTable((), False, f"cannot read {path}: {error}")
    return LockTable(parse_proc_locks(text), True, "")


@dataclass(frozen=True)
class Slot:
    """One gate lock file: a host's admission ticket or one of its lane slots."""

    host: str
    name: str  # `<host>.admit` / `<host>.slot<N>`
    kind: str  # "admit" | "slot"
    state: str
    holder_pid: int | None
    detail: str
    path: str

    def text(self) -> str:
        holder = f" pid {self.holder_pid}" if self.holder_pid is not None else ""
        return f"{self.host} {self.name}{holder}"

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "host": self.host, "name": self.name, "kind": self.kind, "state": self.state, "path": self.path,
        }
        if self.holder_pid is not None:
            payload["holder_pid"] = self.holder_pid
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass(frozen=True)
class SlotScan:
    """The gate lock files, and why their directory could not be listed when it could not.

    A lock directory that exists but cannot be listed is ONE source's failure: it must not
    take down the runs, the gate results or the peer prose, so the reason travels beside an
    empty slot list instead of raising.
    """

    slots: tuple[Slot, ...]
    detail: str = ""


def split_lock_name(name: str) -> tuple[str, str] | None:
    """`(<host>, "admit"|"slot")` of a gate lock file name, else ``None``.

    The two shapes `remote-gate.sh` writes are `<host>.admit` and `<host>.slot<N>` (184-195).
    Anything else in that directory belongs to some other tool and is not a gate lease.
    """

    if name.endswith(".admit") and len(name) > len(".admit"):
        return name[: -len(".admit")], "admit"
    host, separator, number = name.rpartition(".slot")
    if separator and host and number.isdigit():
        return host, "slot"
    return None


def gate_slots(config, locks: LockTable) -> tuple[Slot, ...]:
    """Every gate lock file under `$CONTROLLER_HOME/remote-gate-locks`, with its state."""

    return scan_gate_slots(config, locks).slots


def scan_gate_slots(config, locks: LockTable) -> SlotScan:
    """The slots plus the reason the directory could not be listed (one unreadable source)."""

    directory = config.gate_lock_dir
    if not directory.is_dir():
        return SlotScan(())
    entries, reason = bounded.walk(directory)
    if reason:
        return SlotScan((), reason)
    slots: list[Slot] = []
    for path in entries:
        parsed = split_lock_name(path.name)
        if parsed is None or not path.is_file():
            continue
        host, kind = parsed
        state, pid, detail = locks.state_of(path)
        slots.append(Slot(host=host, name=path.name, kind=kind, state=state, holder_pid=pid, detail=detail, path=str(path)))
    return SlotScan(tuple(slots))


@dataclass(frozen=True)
class GateResult:
    """One remote-gate run directory: who ran what against which commit, and its verdict."""

    name: str
    host: str
    project: str  # the repo-relative project dir (`cmru`, `libraries/iobench`, ...)
    lane: str
    sha: str
    verdict: str
    verdict_source: str
    started: str
    mtime: float
    detail: str

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "run": self.name, "host": self.host, "project": self.project, "lane": self.lane,
            "sha": self.sha, "verdict": self.verdict, "verdict_source": self.verdict_source,
            "started": self.started, "mtime": self.mtime,
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload

    def text(self) -> str:
        sha = self.sha[:9] if self.sha else "sha-unknown"
        return f"{self.lane} {self.verdict} {self.host} {sha}"


def json_object(path: Path) -> dict[str, object] | None:
    text = bounded.whole(path, bounded.SMALL_LIMIT)
    if text is None:
        return None
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        return None
    return document if isinstance(document, dict) else None


def parse_lane_verdict(line: str | None, lane: str) -> str:
    """The verdict of a `run-gate: lane '<lane>' verdict <VERDICT>; ...` line.

    The lane must match exactly: a log line about a DIFFERENT lane in the same run directory
    is not a verdict for this lane. A line that repeats the prefix inside test output cannot
    match, because only lines that START with the prefix reach here.
    """

    if line is None:
        return ""
    prefix = f"{VERDICT_PREFIX}{lane}' {LANE_STATUS} "
    if not line.startswith(prefix):
        return ""
    rest = line[len(prefix):]
    return rest.split(";")[0].strip()


def read_results(config) -> tuple[GateResult, ...]:
    """Every `remote-gates/<run>` directory, newest first by its own mtime."""

    return scan_results(config).results


def scan_results(config) -> GateScan:
    """The gate results, plus the reason their directory could not be listed."""

    directory = config.remote_gate_out
    if not directory.is_dir():
        return GateScan(())
    entries, reason = bounded.walk(directory)
    if reason:
        return GateScan((), reason)
    results: list[GateResult] = []
    for path in entries:
        if not path.is_dir():
            continue
        meta = json_object(path / META_JSON)
        result = json_object(path / RESULT_JSON)
        detail = ""
        if meta is None:
            detail = f"{META_JSON} is missing or unreadable"
        meta = meta or {}
        lane = meta.get("lane")
        lane = lane if isinstance(lane, str) else ""
        recorded = result.get("verdict") if result is not None else None
        recorded = recorded if isinstance(recorded, str) else ""
        line = bounded.last_line_with_prefix(path / LANE_LOG, VERDICT_PREFIX) if lane else None
        verdict = parse_lane_verdict(line, lane) if lane else ""
        verdict_source = f"{LANE_LOG}: run-gate verdict line" if verdict else ""
        if not verdict and recorded:
            verdict, verdict_source = recorded, RESULT_JSON
        if not verdict:
            verdict_source = ""
        mtime = mtime_of(path) or 0.0
        host = meta.get("host")
        project = meta.get("project_dir")
        sha = meta.get("sha")
        started = meta.get("started_utc")
        results.append(
            GateResult(
                name=path.name,
                host=host if isinstance(host, str) else UNKNOWN,
                project=project if isinstance(project, str) else UNKNOWN,
                lane=lane or UNKNOWN,
                sha=sha if isinstance(sha, str) else "",
                verdict=verdict or UNKNOWN,
                verdict_source=verdict_source,
                started=started if isinstance(started, str) else "",
                mtime=mtime,
                detail=detail,
            )
        )
    results.sort(key=lambda item: (-item.mtime, item.name))
    return GateScan(tuple(results))


@dataclass(frozen=True)
class GateScan:
    """The gate results, and why their directory could not be listed when it could not."""

    results: tuple[GateResult, ...]
    detail: str = ""


@dataclass(frozen=True)
class StateLine:
    """A `P-<id>` line of a peer's `STATE.md`: prose, so always `unverified`."""

    peer: str
    path: str
    line: int
    package: str  # the package id the token names, e.g. "plat-22c" or "core-08"
    topic: str  # its first stage, e.g. "plat" / "core" -- the only join a branch slug allows
    designator: str  # the id token, e.g. "22c" / "08"; "" when the token has no id stage
    token: str
    text: str  # the line, bounded: prose is quoted, never echoed whole


@dataclass(frozen=True)
class PeerState:
    """One peer directory: its prose file, its handover file and their ages."""

    peer: str
    state_path: str
    state_mtime: float | None
    state_state: str  # "present" | "missing" | "unknown"
    state_detail: str
    handover_path: str
    handover_mtime: float | None
    lines: tuple[StateLine, ...]


def mtime_of(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def read_peers(config) -> tuple[PeerState, ...]:
    """Read `STATE.md` package lines and handover ages of every configured state directory.

    A state directory that is absent is not an unreadable source; it is simply not a peer
    here. What a reader must never do is turn "the directory is not there" into "this peer
    has no work".
    """

    return scan_peers(config).peers


def scan_peers(config) -> PeerScan:
    """The peers, plus the reason a state directory could not be listed.

    A directory under the state home is a peer only when it holds a `STATE.md` or a
    `HANDOFF.md`: `.controller-state/` also holds `archive`, `hooks`, `runs`, `handovers`,
    `e021` and `scratch-*` directories, and printing those as sub-controllers with `unknown`
    state says the estate is broken when it is not.
    """

    peers: list[PeerState] = []
    seen: set[str] = set()
    unreadable: list[str] = []
    for directory in config.state_dirs:
        if not directory.is_dir():
            continue
        entries, reason = bounded.walk(directory)
        if reason:
            unreadable.append(reason)
            continue
        for entry in entries:
            if not entry.is_dir() or not _is_peer(entry):
                continue
            name = entry.name
            if name in seen:
                continue
            seen.add(name)
            peers.append(_read_peer(name, entry))
    return PeerScan(tuple(peers), tuple(unreadable))


def _is_peer(directory: Path) -> bool:
    """A peer directory is one that holds a state file or a handover (a declared controller)."""

    return (directory / STATE_FILE).exists() or (directory / HANDOVER_FILE).exists()


@dataclass(frozen=True)
class PeerScan:
    """The peers, and the reasons a state directory could not be listed."""

    peers: tuple[PeerState, ...]
    unreadable: tuple[str, ...] = ()


def _read_peer(name: str, directory: Path) -> PeerState:
    state_path = directory / STATE_FILE
    handover_path = directory / HANDOVER_FILE
    text = bounded.whole(state_path, STATE_LIMIT)
    if text is None:
        state_state, state_detail = (
            ("missing", "") if not state_path.exists() else ("unknown", "not readable whole (absent or larger than the bounded read)")
        )
        lines: tuple[StateLine, ...] = ()
    else:
        state_state, state_detail, lines = "present", "", _package_lines(name, str(state_path), text)
    handover_mtime = mtime_of(handover_path)
    return PeerState(
        peer=name,
        state_path=str(state_path),
        state_mtime=mtime_of(state_path),
        state_state=state_state,
        state_detail=state_detail,
        handover_path=str(handover_path),
        handover_mtime=handover_mtime,
        lines=lines,
    )


def _package_lines(peer: str, path: str, text: str) -> tuple[StateLine, ...]:
    found: list[StateLine] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in PACKAGE_TOKEN_RE.finditer(line):
            token = match.group(0)
            package = token[len("P-"):]
            tokens = [part for part in package.split("-") if part]
            topic = tokens[0] if tokens else package
            # The designator is the LAST stage (`core-08`, `plat-22c`, `hostobs-11`), and it is
            # reported as its own cell: a row joined on its topic must not present another
            # package's designator as its own state.
            designator = tokens[-1] if len(tokens) > 1 else ""
            found.append(
                StateLine(
                    peer=peer,
                    path=path,
                    line=number,
                    package=package,
                    topic=topic,
                    designator=designator,
                    token=token,
                    text=line.strip()[:240],
                )
            )
            break
    return tuple(found)


def read_own_handover(config) -> tuple[str, float | None, str]:
    """The repository's own handover: `(path, mtime, detail)` (the pre-migration input)."""

    if config.repo_state_dir is None:
        return "", None, "no CONTROLLER_REPO is configured"
    path = config.repo_state_dir / HANDOVER_FILE
    mtime = mtime_of(path)
    if mtime is None:
        return str(path), None, f"{path} is not there"
    return str(path), mtime, ""
