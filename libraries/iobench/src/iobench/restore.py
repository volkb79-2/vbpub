"""Safety: restore journal, io.max watch, abort rules, single instance (IOB-SAF-01..09)."""
from __future__ import annotations

import json
import math
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from . import devio
from .errors import Aborted, Refusal, VerificationFailure
from .host import Host, HostError, is_scratch_name  # one naming rule, shared with the argv rule of host.check_argv

JOURNAL = "/run/iobench/restore.json"
LOCK = "/run/iobench/lock"
ABORT_FILE = "/run/iobench/abort"
CGROUP_ROOT = "/sys/fs/cgroup"
PSI_SAMPLES = 3
INFLIGHT_WAIT_S = 30
ALLOWED_PATHS = (
    re.compile(r"^/proc/sys/vm/dirty_bytes$"),
    re.compile(r"^/proc/sys/vm/dirty_background_bytes$"),
    re.compile(r"^/sys/class/block/[A-Za-z0-9_][A-Za-z0-9_.-]*/queue/(nr_requests|max_sectors_kb)$"),
)
# The kernel couples each bytes knob with a ratio knob: writing the bytes value zeroes the ratio, and a changed ratio zeroes the bytes
# value.  Restoring the bytes knob alone would leave the host with ratio 0 (heavy writeback throttling), so the ratio is journalled too.
COUPLED_RATIO = {
    "/proc/sys/vm/dirty_bytes": "/proc/sys/vm/dirty_ratio",
    "/proc/sys/vm/dirty_background_bytes": "/proc/sys/vm/dirty_background_ratio",
}


def knob_allowed(path: str) -> bool:
    return any(p.match(path) for p in ALLOWED_PATHS)


@dataclass
class RestoreReport:
    verified: bool = True
    slice_io_max_unchanged: bool = True
    journal_removed: bool = True
    skipped: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    io_max_before: dict[str, str] = field(default_factory=dict)
    io_max_after: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"verified": self.verified, "slice_io_max_unchanged": self.slice_io_max_unchanged,
                "journal_removed": self.journal_removed, "skipped": self.skipped, "warnings": self.warnings}


# -- io.max (read only) ---------------------------------------------------------------------
def snapshot_io_max(host: Host, root: str = CGROUP_ROOT, depth: int = 3) -> dict[str, str]:
    """The raw ``io.max`` of every cgroup directory (read only; iobench never writes it)."""
    out: dict[str, str] = {}
    try:
        if host.exists(root + "/io.max"):
            out[root] = host.read_text(root + "/io.max")
        if depth:
            for name in host.listdir(root):
                sub = f"{root}/{name}"
                st = host.stat(sub)
                if st is not None and st.is_dir:
                    out.update(snapshot_io_max(host, sub, depth - 1))
    except FileNotFoundError:
        pass  # a cgroup removed while walking (a container stopped) is not an io.max change
    return out


# -- pressure and abort -------------------------------------------------------------------------
def psi_some_avg10(host: Host, cgroup: str) -> float:
    text = host.read_text(f"{CGROUP_ROOT}/{cgroup}/io.pressure")
    some = next(line for line in text.splitlines() if line.startswith("some "))
    return float(dict(f.split("=") for f in some.split()[1:])["avg10"])


class AbortWatch:
    """Abort when the protected cgroup's io.pressure ``some avg10`` exceeds the threshold for 3 samples in a row, or on /run/iobench/abort."""

    def __init__(self, host: Host, cgroup: str | None, threshold: float | None) -> None:
        self.host, self.cgroup, self.threshold, self.strikes = host, cgroup, threshold, 0

    def check(self) -> None:
        if self.host.exists(ABORT_FILE):
            raise Aborted("operator abort file /run/iobench/abort")
        if self.cgroup is None or self.threshold is None:
            return
        if psi_some_avg10(self.host, self.cgroup) > self.threshold:
            self.strikes += 1
        else:
            self.strikes = 0
        if self.strikes >= PSI_SAMPLES:
            raise Aborted(f"io.pressure of {self.cgroup} above {self.threshold} for {PSI_SAMPLES} consecutive samples")


def device_inflight(host: Host, disk: str) -> int:
    reads, writes = host.read_text(f"/sys/class/block/{disk}/inflight").split()[:2]
    return int(reads) + int(writes)


def wait_inflight_zero(host: Host, disk: str, timeout_s: float = INFLIGHT_WAIT_S) -> bool:
    waited = 0.0
    while device_inflight(host, disk) > 0:
        if waited >= timeout_s:
            return False
        host.sleep(1.0)
        waited += 1.0
    return True


UNIT_STOP_WAIT_S = 30.0
UNIT_GONE_STATES = ("inactive", "failed")  # anything else (including output we do not recognise) is not "gone"


def stop_unit(host: Host, unit: str, timeout_s: float = UNIT_STOP_WAIT_S) -> bool:
    """Stop a transient probe unit and wait until it is gone. Killing the ``systemd-run`` client leaves the unit running (it lingers until its
    RuntimeMaxSec and recreates its output file after cleanup), so an abort stops the unit itself. False when it is still active after ``timeout_s``."""
    try:
        host.run(devio.unit_stop_argv(unit), timeout=timeout_s)
    except Aborted:
        raise  # the operator's second signal ends the wait
    except Exception:  # noqa: BLE001 -- a stop that timed out or failed to start is judged by the state wait below
        pass
    waited = 0.0
    # `is-active` answers 3 for activating/deactivating too; ActiveState names the stages, so a unit still stopping is not read as gone
    while devio.probe(host, f"systemctl show {unit}", devio.unit_state_argv(unit)).stdout.strip() not in UNIT_GONE_STATES:
        if waited >= timeout_s:
            return False
        host.sleep(1.0)
        waited += 1.0
    return True


# -- live-host preflight (SAF-05 / SAF-06) -----------------------------------------------------
LIMITATION = "Units not named with --production-unit are not detected."


def _validate_guards(host: Host, protect_cgroup: str, abort_psi: float) -> None:
    """A guard that can never fire is worse than none: the threshold must be a real percentage and the cgroup's pressure readable."""
    if not (math.isfinite(abort_psi) and 0 < abort_psi <= 100):
        raise Refusal("abort_psi_invalid", f"--abort-psi {abort_psi} is not a finite percentage in (0, 100]; a NaN or out-of-range threshold never aborts",
                      "pass a threshold such as 40")
    try:
        psi_some_avg10(host, protect_cgroup)
    except (OSError, StopIteration, KeyError, ValueError) as exc:
        raise Refusal("protect_cgroup_unreadable", f"cannot read the io.pressure of {protect_cgroup} ({type(exc).__name__}); the abort guard would not work",
                      "name an existing cgroup with PSI enabled (for example game-server.slice)") from exc


def live_preflight(host: Host, *, confirm_quiet_window: bool, installer: bool, production_pattern: str | None,
                   production_units: Sequence[str], protect_cgroup: str | None, abort_psi: float | None) -> dict[str, Any]:
    """Refuse a live-host run with production work running unless confirmed; require the abort guards."""
    if installer:
        if (protect_cgroup is None) != (abort_psi is None):
            raise Refusal("abort_guard_incomplete", "--protect-cgroup and --abort-psi must be given together; half a guard never aborts",
                          "pass both (for example --protect-cgroup game-server.slice --abort-psi 40) or neither")
        if protect_cgroup is not None and abort_psi is not None:
            _validate_guards(host, protect_cgroup, abort_psi)
        return {"containers_active": 0, "confirmed_quiet_window": confirm_quiet_window, "bypassed": True}
    try:
        pattern = None if production_pattern is None else re.compile(production_pattern)
    except re.error as exc:
        raise Refusal("production_pattern_invalid", f"--production-pattern {production_pattern!r} is not a valid regular expression ({exc})",
                      "pass a Python regular expression, for example '^game-server'") from exc
    names = [n for n in devio.probe(host, "docker ps", devio.docker_ps_argv()).stdout.split() if pattern is None or pattern.search(n)]
    units = [u for u in production_units if unit_is_active(host, u)]
    if (names or units) and not confirm_quiet_window:
        raise Refusal("production_running", f"production containers are running ({len(names)}) or listed units are active ({len(units)}); {LIMITATION}",
                      "rerun with --confirm-quiet-window in an agreed window")
    if protect_cgroup is None or abort_psi is None:
        raise Refusal("abort_guard_required", "live-host runs need --protect-cgroup and --abort-psi (there are no defaults)",
                      "pass --protect-cgroup <cgroup> and --abort-psi <threshold> (for example game-server.slice and 40)")
    _validate_guards(host, protect_cgroup, abort_psi)
    return {"containers_active": len(names), "units_active": units, "confirmed_quiet_window": confirm_quiet_window}


def unit_is_active(host: Host, unit: str) -> bool:
    """``is-active`` exits 0 (active) or 3 (inactive); a unit that does not exist is a typo, not "inactive"."""
    load = devio.probe(host, f"systemctl show {unit}", devio.unit_loadstate_argv(unit)).stdout.strip()
    if load == "not-found":
        raise Refusal("unit_not_found", f"--production-unit {unit} does not exist (LoadState=not-found); a typo would be read as inactive",
                      "name an existing unit, for example game-server.service")
    return devio.probe(host, f"systemctl is-active {unit}", devio.unit_active_argv(unit), ok=(0, 3)).returncode == 0


def orphan_fio(host: Host) -> list[str]:
    """Pids of an ``fio`` that an earlier iobench run left running (its job names start with ``run-``).  Called with the lock held, so
    any such process is an orphan: restoring knobs under it would change the settings of a run that is still writing."""
    found = []
    for name in host.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            argv = host.read_text(f"/proc/{name}/cmdline").split("\0")
        except (OSError, UnicodeDecodeError):
            continue  # the process ended while we looked, or its arguments are not UTF-8 (any process on the host: not an fio of ours)
        if posixpath.basename(argv[0]) == "fio" and any(a.startswith("--name=run-") for a in argv):
            found.append(name)
    return found


def stale_abort_file(host: Host) -> None:
    """S0: an abort file left over from an earlier run would abort this one only after it had started writing."""
    if host.exists(ABORT_FILE):
        raise Refusal("stale_abort_file", f"{ABORT_FILE} exists (left from an earlier abort)", f"inspect it, remove {ABORT_FILE}, and rerun")


# -- the journal ----------------------------------------------------------------------------------
class Safety:
    """Single instance, restore journal, io.max snapshots, signals.

    Only the lock holder writes, restores or removes the journal: a refused or lock-contended invocation owns nothing and must
    leave the live run's journal alone (SAF-02, SAF-08).
    """

    def __init__(self, host: Host, run_id: str, started: str, *, journal: str = JOURNAL, lock: str = LOCK) -> None:
        self.host, self.run_id, self.started = host, run_id, started
        self.journal, self.lock = journal, lock
        self.entries: list[dict[str, str]] = []
        self.files: list[str] = []
        self.coupled: dict[str, str] = {}
        self.report = RestoreReport()
        self.locked = False
        self.journal_written = False
        self.begun = False

    # lifecycle
    def acquire(self) -> None:
        if not self.host.lock(self.lock):
            raise Refusal("already_running", "another iobench run holds the lock", "wait for it to finish (or `iobench restore` if it died)")
        self.locked = True

    def begin(self) -> None:
        self.host.install_signals()
        self.report.io_max_before = snapshot_io_max(self.host)
        self.begun = True

    def release(self) -> None:
        if self.locked:
            self.host.unlock()
            self.locked = False

    # journal writes (SAF-01: before the first state change)
    def _write_journal(self) -> None:
        if not self.locked:
            raise HostError("the restore journal is written only by the lock holder")
        self.host.makedirs(self.journal.rsplit("/", 1)[0])
        self.host.write_atomic(self.journal, json.dumps({"run": self.run_id, "started": self.started, "entries": self.entries,
                                                         "files": self.files, "coupled": self.coupled}, sort_keys=True))
        self.journal_written = True

    def register_file(self, path: str) -> None:
        if path in self.files:
            return  # the probe file is registered by every sweep cell: one entry, one unlink
        self.files.append(path)
        self._write_journal()

    def set_knob(self, path: str, new_value: str) -> bool:
        """Journal, write and read back one knob; ``False`` if the kernel rejected the write (nothing changed).

        The journalled ``applied`` value is what the kernel actually reports after the write: ``nr_requests`` and
        ``max_sectors_kb`` are clamped or rounded, and comparing the restore against the requested value would
        mistake a normalised value for "changed by someone else".  A journal I/O error propagates.
        """
        if not knob_allowed(path):
            raise HostError(f"knob outside the allowlist: {path}")
        original = self.host.read_text(path).strip()
        partner = COUPLED_RATIO.get(path)
        if partner is not None and partner not in self.coupled:
            self.coupled[partner] = self.host.read_text(partner).strip()
        self.entries.append({"path": path, "original": original, "applied": str(new_value)})
        self._write_journal()
        try:
            self.host.write_sysfs(path, str(new_value))
        except OSError:
            self.entries.pop()
            self._write_journal()
            return False
        effective = self.host.read_text(path).strip()
        if effective != str(new_value):
            self.entries[-1]["applied"] = effective
            self._write_journal()
        return effective == str(new_value)

    # restore
    def restore(self) -> RestoreReport:
        """Put every journalled knob back, verify by read-back, and remove the journal this session wrote.

        Every entry is attempted even when one fails; the failures are reported together (exit 9).  For a coupled
        pair the member that was originally non-zero is the one written last, so the other member returns to 0
        on its own, and both are verified.
        """
        if not self.journal_written:
            return self.report  # this session owns no journal: touching /run/iobench/restore.json would clobber another run's
        problems: list[str] = []
        restored: set[str] = set()
        first_original: dict[str, str] = {}
        for entry in self.entries:
            first_original.setdefault(entry["path"], entry["original"])
        for entry in reversed(self.entries):
            path = entry["path"]
            try:
                current = self.host.read_text(path).strip()
                if current != entry["applied"]:
                    self.report.skipped.append({"path": path, "reason": "changed_by_other"})
                    self.report.warnings.append(f"{path} was changed by someone else; left alone")
                    continue
                self.host.write_sysfs(path, entry["original"])
                back = self.host.read_text(path).strip()
                if back != entry["original"]:
                    problems.append(f"{path}: wrote {entry['original']!r}, read back {back!r}")
                restored.add(path)
            except (OSError, HostError) as exc:
                problems.append(f"{path}: {type(exc).__name__}: {exc}")
        for bytes_path, ratio_path in COUPLED_RATIO.items():
            if bytes_path not in restored or ratio_path not in self.coupled:
                continue
            bytes_original, ratio_original = first_original[bytes_path], self.coupled[ratio_path]
            try:
                if bytes_original == "0" and ratio_original != "0":
                    self.host.write_sysfs(ratio_path, ratio_original)  # a changed ratio zeroes the bytes knob again
                for path, want in ((bytes_path, bytes_original), (ratio_path, ratio_original)):
                    back = self.host.read_text(path).strip()
                    if back != want:
                        problems.append(f"{path}: expected {want!r} after restoring the pair, read back {back!r}")
            except (OSError, HostError) as exc:
                problems.append(f"{ratio_path}: {type(exc).__name__}: {exc}")
        for path in self.files:
            try:
                self.host.remove(path)
            except (OSError, HostError) as exc:
                problems.append(f"{path}: could not remove ({exc})")
        self.entries, self.files, self.coupled = [], [], {}
        if problems:
            self.report.verified = False
            self.report.journal_removed = False
            self.journal_written = False  # the journal stays on disk for `iobench restore`; this session no longer owns it
            raise VerificationFailure("restore failed or read-back mismatch: " + "; ".join(problems))
        self.host.remove(self.journal)
        self.journal_written = False
        return self.report

    def verify_io_max(self) -> None:
        """Compare the io.max of every cgroup present both before and after (a container starting or stopping is not a change)."""
        if not self.begun:
            return  # no "before" snapshot exists; comparing against {} would invent a difference and mask the real failure
        self.report.io_max_after = snapshot_io_max(self.host)
        before, after = self.report.io_max_before, self.report.io_max_after
        changed = sorted(k for k in before.keys() & after.keys() if before[k] != after[k])
        for k in sorted(before.keys() ^ after.keys()):
            self.report.warnings.append(f"cgroup {k} {'appeared' if k in after else 'disappeared'} during the run (io.max not compared)")
        self.report.slice_io_max_unchanged = not changed
        if changed:
            raise VerificationFailure("io.max changed during the run (iobench never writes it): " + ", ".join(changed))

    def recover_previous(self) -> RestoreReport | None:
        """Restore a journal left by a run that died (`iobench restore`, and the start of every run); the caller holds the lock."""
        if not self.locked:
            raise HostError("a journal is recovered only by the lock holder")
        if not self.host.exists(self.journal):
            return None
        doc = parse_journal(self.host.read_text(self.journal))
        prior = Safety(self.host, doc["run"], doc["started"], journal=self.journal, lock=self.lock)
        prior.entries, prior.files, prior.coupled = doc["entries"], doc["files"], doc["coupled"]
        prior.locked = prior.journal_written = True  # the journal is the dead run's, and this session holds the lock
        try:
            return prior.restore()
        finally:
            prior.locked = False


def parse_journal(text: str) -> dict[str, Any]:
    """Validate a journal read from disk: it is root-writable input and drives sysfs writes and unlinks as root."""
    def bad(why: str) -> Refusal:
        return Refusal("journal_corrupt", f"{JOURNAL} is not a valid restore journal ({why})",
                       f"inspect {JOURNAL}; restore the knobs by hand if needed, then remove it")
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise bad("not JSON") from exc
    if not isinstance(doc, dict) or not all(k in doc for k in ("run", "started", "entries", "files", "coupled")):
        raise bad("missing keys")
    if not (isinstance(doc["entries"], list) and isinstance(doc["files"], list) and isinstance(doc["coupled"], dict)):
        raise bad("wrong types")
    for entry in doc["entries"]:
        if not (isinstance(entry, dict) and all(isinstance(entry.get(k), str) for k in ("path", "original", "applied"))):
            raise bad("malformed entry")
        if not knob_allowed(entry["path"]):
            raise bad(f"knob outside the allowlist: {entry['path']}")
    for path in doc["files"]:
        if not (isinstance(path, str) and posixpath.isabs(path) and ".." not in path.split("/") and is_scratch_name(path)):
            raise bad(f"file outside the iobench naming: {path!r}")
    for ratio_path, value in doc["coupled"].items():
        if ratio_path not in COUPLED_RATIO.values() or not isinstance(value, str):
            raise bad(f"unknown coupled knob: {ratio_path}")
    return doc
