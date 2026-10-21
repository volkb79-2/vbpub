"""Safety: restore journal, io.max watch, abort rules, single instance (IOB-SAF-01..09)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from . import devio
from .errors import Aborted, Refusal, VerificationFailure
from .host import Host, HostError

JOURNAL = "/run/iobench/restore.json"
LOCK = "/run/iobench/lock"
ABORT_FILE = "/run/iobench/abort"
CGROUP_ROOT = "/sys/fs/cgroup"
PSI_SAMPLES = 3
INFLIGHT_WAIT_S = 30
ALLOWED_PATHS = (
    re.compile(r"^/proc/sys/vm/dirty_bytes$"),
    re.compile(r"^/proc/sys/vm/dirty_background_bytes$"),
    re.compile(r"^/sys/class/block/[A-Za-z0-9_.-]+/queue/(nr_requests|max_sectors_kb)$"),
)


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
                "journal_removed": self.journal_removed, "skipped": self.skipped}


# -- io.max (read only) ---------------------------------------------------------------------
def snapshot_io_max(host: Host, root: str = CGROUP_ROOT, depth: int = 3) -> dict[str, str]:
    """The raw ``io.max`` of every cgroup directory (read only; iobench never writes it)."""
    out: dict[str, str] = {}
    if host.exists(root + "/io.max"):
        out[root] = host.read_text(root + "/io.max")
    if depth:
        for name in host.listdir(root):
            sub = f"{root}/{name}"
            st = host.stat(sub)
            if st is not None and st.is_dir:
                out.update(snapshot_io_max(host, sub, depth - 1))
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
        if self.cgroup is None:
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


# -- live-host preflight (SAF-05 / SAF-06) -----------------------------------------------------
LIMITATION = "Units not named with --production-unit are not detected."


def live_preflight(host: Host, *, confirm_quiet_window: bool, installer: bool, production_pattern: str | None,
                   production_units: Sequence[str], protect_cgroup: str | None, abort_psi: float | None) -> dict[str, Any]:
    """Refuse a live-host run with production work running unless confirmed; require the abort guards."""
    if installer:
        return {"containers_active": 0, "confirmed_quiet_window": confirm_quiet_window, "bypassed": True}
    names = [n for n in host.run(devio.docker_ps_argv()).stdout.split() if production_pattern is None or re.search(production_pattern, n)]
    units = [u for u in production_units if host.run(devio.unit_active_argv(u)).returncode == 0]
    if (names or units) and not confirm_quiet_window:
        raise Refusal("production_running", f"production containers are running ({len(names)}) or listed units are active ({len(units)}); {LIMITATION}",
                      "rerun with --confirm-quiet-window in an agreed window")
    if protect_cgroup is None or abort_psi is None:
        raise Refusal("abort_guard_required", "live-host runs need --protect-cgroup and --abort-psi (there are no defaults)",
                      "pass --protect-cgroup <cgroup> and --abort-psi <threshold> (for example wings.slice and 40)")
    return {"containers_active": len(names), "units_active": units, "confirmed_quiet_window": confirm_quiet_window}


# -- the journal ----------------------------------------------------------------------------------
class Safety:
    """Single instance, restore journal, io.max snapshots, signals."""

    def __init__(self, host: Host, run_id: str, started: str, *, journal: str = JOURNAL, lock: str = LOCK) -> None:
        self.host, self.run_id, self.started = host, run_id, started
        self.journal, self.lock = journal, lock
        self.entries: list[dict[str, str]] = []
        self.files: list[str] = []
        self.report = RestoreReport()
        self.locked = False

    # lifecycle
    def acquire(self) -> None:
        if not self.host.lock(self.lock):
            raise Refusal("already_running", "another iobench run holds the lock", "wait for it to finish (or `iobench restore` if it died)")
        self.locked = True

    def begin(self) -> None:
        self.host.install_signals()
        self.report.io_max_before = snapshot_io_max(self.host)

    def release(self) -> None:
        if self.locked:
            self.host.unlock()
            self.locked = False

    # journal writes (SAF-01: before the first state change)
    def _write_journal(self) -> None:
        self.host.makedirs(self.journal.rsplit("/", 1)[0])
        self.host.write_atomic(self.journal, json.dumps({"run": self.run_id, "started": self.started, "entries": self.entries,
                                                         "files": self.files}, sort_keys=True))

    def register_file(self, path: str) -> None:
        self.files.append(path)
        self._write_journal()

    def set_knob(self, path: str, new_value: str) -> None:
        if not knob_allowed(path):
            raise HostError(f"knob outside the allowlist: {path}")
        original = self.host.read_text(path).strip()
        self.entries.append({"path": path, "original": original, "applied": str(new_value)})
        self._write_journal()
        self.host.write_sysfs(path, str(new_value))

    # restore
    def restore(self) -> RestoreReport:
        mismatches = []
        for entry in reversed(self.entries):
            current = self.host.read_text(entry["path"]).strip()
            if current != entry["applied"]:
                self.report.skipped.append({"path": entry["path"], "reason": "changed_by_other"})
                self.report.warnings.append(f"{entry['path']} was changed by someone else; left alone")
                continue
            self.host.write_sysfs(entry["path"], entry["original"])
            back = self.host.read_text(entry["path"]).strip()
            if back != entry["original"]:
                mismatches.append(f"{entry['path']}: wrote {entry['original']!r}, read back {back!r}")
        for path in self.files:
            self.host.remove(path)
        self.entries, self.files = [], []
        if mismatches:
            self.report.verified = False
            self.report.journal_removed = False
            raise VerificationFailure("restore read-back mismatch: " + "; ".join(mismatches))
        self.host.remove(self.journal)
        return self.report

    def verify_io_max(self) -> None:
        self.report.io_max_after = snapshot_io_max(self.host)
        self.report.slice_io_max_unchanged = self.report.io_max_after == self.report.io_max_before
        if not self.report.slice_io_max_unchanged:
            changed = sorted(k for k in {*self.report.io_max_before, *self.report.io_max_after}
                             if self.report.io_max_before.get(k) != self.report.io_max_after.get(k))
            raise VerificationFailure("io.max changed during the run (iobench never writes it): " + ", ".join(changed))

    def recover_previous(self) -> RestoreReport | None:
        """Restore a journal left by a run that died (`iobench restore`, and the start of every run)."""
        if not self.host.exists(self.journal):
            return None
        doc = json.loads(self.host.read_text(self.journal))
        prior = Safety(self.host, doc["run"], doc["started"], journal=self.journal, lock=self.lock)
        prior.entries, prior.files = doc["entries"], doc["files"]
        return prior.restore()
