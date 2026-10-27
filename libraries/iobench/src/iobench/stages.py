"""The Runner: shared machinery of every run (S0, fio supervision, confinement, budget, stage records)."""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from . import ceilings, devio, guard, plan, restore, result as res
from . import series as S
from .devio import FioJob, FioResult
from .errors import Aborted, IobenchError, Refusal, RunFailed, VerificationFailure
from .extents import Extents
from .host import TAIL_BYTES, Host, HostError
from .proposals import DEFAULT_TARGETS
from .restore import Safety

FIO_FLOOR = (3, 30)
SCRATCH = "/run/iobench"
TICK_S = 1.0
GIB = 1 << 30
MIB = 1 << 20


class IntegrityViolation(IobenchError):
    """IOB-PLA-14/15/16: the run itself broke an integrity rule; it fails and keeps the good record."""

    exit_code = 1


class StageSkipped(S.SeriesError):
    """A stage that cannot apply to this target (no discard, no fresh extent): recorded ``skipped: <why>``, never ``failed``."""

    def __init__(self, why: str, message: str = "") -> None:
        self.why = why
        super().__init__(message or why)


@dataclass
class Config:
    target: str
    tool_version: str
    work_dir: str | None = None
    profile: str = "standard"
    budget_minutes: float | None = None
    state_dir: str | None = None
    confirm_quiet_window: bool = False
    protect_cgroup: str | None = None
    abort_psi: float | None = None
    installer: bool = False
    production_pattern: str | None = None
    production_units: tuple[str, ...] = ()
    metering: str | None = None
    full_sweep: bool = False
    knob: str | None = None
    candidates: tuple[int, ...] = ()
    repeat: bool = False
    governor_histogram: str | None = None
    require_strong_identity: bool = False
    assume_r_est: float | None = None
    targets: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_TARGETS))

    def budget_s(self, default: int) -> int:
        return int(self.budget_minutes * 60) if self.budget_minutes is not None else default


def parse_fio_version(text: str) -> tuple[int, int]:
    m = re.search(r"fio-(\d+)\.(\d+)", text)
    if m is None:
        raise Refusal("fio_version", f"cannot read the fio version from {text.strip()!r}", "install fio >= 3.30")
    return int(m.group(1)), int(m.group(2))


class Runner:
    """One run of one command; owns the Safety session, the budget and the stage record."""

    def __init__(self, host: Host, cfg: Config, *, command: str) -> None:
        self.host, self.cfg, self.command = host, cfg, command
        self.started = host.now()
        stamp = ceilings.iso(self.started)
        self.run_id = f"run-{stamp.replace('-', '').replace(':', '')}-{host.token()}"
        self.safety = Safety(host, self.run_id, stamp)
        self.state_dir = res.state_dir_of(host, cfg.state_dir)
        self.stages: dict[str, str] = {}
        self.limits: list[str] = []
        self.jobs: list[FioJob] = []
        self.target: guard.Target | None = None
        self.identity: dict[str, Any] = {}
        self.preflight: dict[str, Any] = {}
        self.watch = restore.AbortWatch(host, cfg.protect_cgroup, cfg.abort_psi)
        self.allowed_writes = Extents()
        self.read_extent = Extents()
        self.disk_name = ""
        self.budget: plan.Budget | None = None
        self.scratch = f"{SCRATCH}/{self.run_id}"

    # -- budget and stage records ---------------------------------------------------------
    def start_budget(self, total_s: int) -> None:
        self.budget = plan.Budget(self.host.now, total_s, self.started)

    def fits(self, seconds: float) -> bool:
        return self.budget.fits(seconds)

    def mark(self, stage: str, status: str) -> None:
        self.stages[stage] = status

    def skip(self, stage: str, why: str) -> None:
        self.stages[stage] = f"skipped: {why}"
        if re.fullmatch(r"S[0-9]", stage):
            self.limits.append(f"stage_skipped:{stage}")
        if why == "budget":
            self.limits.append("limited-by-budget")

    def stage_ok(self, stage: str) -> bool:
        return self.stages.get(stage) == "ok"

    # -- fio ------------------------------------------------------------------------------------
    def job(self, name: str, rw: str, bs: int, qd: int, runtime_s: int, offset: int, size: int, *, filename: str | None = None,
            io_size: int | None = None, metric: str = "iops", hist: bool = False, rate_iops: int | None = None, log_avg_ms: int = 2000,
            direct: bool = True, fsync: int | None = None) -> FioJob:
        return FioJob(f"{self.run_id}-{name}", filename or self.target.path, rw, bs, qd, runtime_s, offset, size, io_size,
                      direct=direct, log_avg_ms=log_avg_ms, metric=metric, hist=hist, rate_iops=rate_iops, fsync=fsync,
                      prefix=f"{self.scratch}/{name}", exclusive=self.target.kind == "device" and (filename is None or filename == self.target.path))

    def _tick(self) -> None:
        sig = self.host.signal_pending()
        if sig is not None:
            raise Aborted(f"signal {sig}")
        self.watch.check()

    def supervise(self, handles: Sequence[Any], on_tick: Any = None, units: Sequence[str] = ()) -> None:
        """Poll until every handle exits, checking signals and the abort rules each second; kill on any exception.

        ``units`` are transient systemd units the handles are clients of: killing a ``systemd-run`` client does not stop its unit, so an abort
        stops each unit and waits for it (otherwise it lingers and recreates its output file after cleanup, review A N-r3-1)."""
        try:
            while any(h.poll() is None for h in handles):
                self.host.sleep(TICK_S)
                self._tick()
                if on_tick is not None:
                    on_tick()
        except BaseException:
            for h in handles:
                h.kill()
            self.stop_units(units)
            if not restore.wait_inflight_zero(self.host, self.disk_name):
                self.safety.report.warnings.append("inflight_not_drained: the device still had requests in flight 30 s after fio was killed")
            raise

    def stop_units(self, units: Sequence[str]) -> None:
        for unit in units:
            if not restore.stop_unit(self.host, unit):
                self.safety.report.warnings.append(f"unit_not_stopped: {unit} was still active {restore.UNIT_STOP_WAIT_S:.0f} s after the abort")

    def _check_job(self, job: FioJob) -> None:
        if job.filename != self.target.path:
            return
        end_ok = job.end <= self.target.usable_bytes
        if job.writes:
            if not (end_ok and self.allowed_writes.covers(job.offset, job.end)):
                raise IntegrityViolation(f"IOB-PLA-16: writer {job.name} [{job.offset}, {job.end}) is outside the planned extents")
            guard.verify_target(self.host, self.target)
        elif not (end_ok and self.read_extent.covers(job.offset, job.end)):
            raise IntegrityViolation(f"IOB-PLA-08: read {job.name} [{job.offset}, {job.end}) is outside the written extent")

    def spawn_all(self, jobs: Sequence[Any]) -> list[Any]:
        """Start several jobs together; if a later one cannot start, the earlier ones are killed (never an unsupervised fio)."""
        handles: list[Any] = []
        try:
            for start in jobs:
                handles.append(start())
        except BaseException:
            for h in handles:
                h.kill()
            raise
        return handles

    def spawn(self, job: FioJob) -> Any:
        self._check_job(job)
        self.jobs.append(job)
        if job.writes and job.filename == self.target.path:
            return self.host.write_range(job.argv(), self.target.ref, job.offset, job.size)
        return self.host.spawn(job.argv())

    def finish(self, job: FioJob, handle: Any, started: float) -> FioResult:
        out = devio.collect(self.host, job, handle.wait(), started)
        for path in (job.log_path, job.hist_path):
            self.host.remove(path)
        if out.returncode != 0:
            raise RunFailed(f"fio job {job.name} failed ({out.returncode}): {out.error}")
        return out

    def fio(self, job: FioJob) -> FioResult:
        started = self.host.now()
        handle = self.spawn(job)
        self.supervise([handle])
        return self.finish(job, handle, started)

    # -- S0 -----------------------------------------------------------------------------------------
    def s0(self, *, need_workdir: bool) -> None:
        """Preflight: fio, lock, leftover journal, live-host rules, the target, the work dir, identity."""
        cfg = self.cfg
        guard.require_host_namespaces(self.host)
        version = parse_fio_version(devio.probe(self.host, "fio --version", devio.fio_version_argv()).stdout)
        if version < FIO_FLOOR:
            raise Refusal("fio_version", f"fio {version[0]}.{version[1]} is older than 3.30 (clat_ns percentiles)", "install fio >= 3.30")
        self.safety.acquire()
        orphans = restore.orphan_fio(self.host)
        if orphans:
            raise Refusal("orphan_fio", f"an fio from an earlier iobench run is still running (pid {', '.join(orphans)}); it may still be writing to the target",
                          "wait for it to end or stop it, then rerun (the leftover journal is restored on the next start)")
        self.safety.recover_previous()
        restore.stale_abort_file(self.host)
        self.preflight = restore.live_preflight(self.host, confirm_quiet_window=cfg.confirm_quiet_window, installer=cfg.installer,
                                                production_pattern=cfg.production_pattern, production_units=cfg.production_units,
                                                protect_cgroup=cfg.protect_cgroup, abort_psi=cfg.abort_psi)
        self.target = guard.load_target(self.host, cfg.target)
        if self.target.kind == "device":
            devio.require_fio_option(self.host, "exclusive_open")
        self.disk_name = self.target.disk_name or guard.device_chain(self.host, guard.fs_info(self.host, posixpath.dirname(cfg.target)).devno).disk_name
        workdir = None
        if need_workdir:
            if cfg.work_dir is None:
                raise Refusal("workdir_required", "this command needs --work-dir", "pass --work-dir DIR on an existing filesystem")
            workdir = guard.check_workdir(self.host, cfg.work_dir, planned_volume=GIB, installer=cfg.installer,
                                          confirm_quiet_window=cfg.confirm_quiet_window, target_disk=self.disk_name)
            self.workdir = workdir
        self.identity = res.s0_identity(self.host, self.target, cfg.work_dir)
        self.safety.begin()
        self.host.makedirs(self.scratch)
        self.preflight.update({"slice_io_max_before": self.safety.report.io_max_before})
        self.mark("S0", "ok")


    # -- stage wrapper ---------------------------------------------------------------------------------
    def stage(self, name: str, fn: Any, *, min_s: float = 0.0, required: bool = False) -> bool:
        """Run one stage: skip if it cannot fit its minimum, record ``failed`` on an unusable series."""
        if not required and not self.fits(min_s):
            self.skip(name, "budget")
            return False
        try:
            fn()
        except S.IntegrityError:
            raise
        except StageSkipped as exc:
            if required:
                raise
            self.skip(name, exc.why)
            return False
        except S.TooFewPoints:
            if required:
                raise
            self.mark(name, "failed: insufficient_points")
            return False
        except S.SeriesError:
            if required:
                raise
            self.mark(name, "failed: unusable_series")
            return False
        self.mark(name, "ok")
        return True

    # -- execution envelope --------------------------------------------------------------------------------
    def execute(self) -> Outcome:
        status, message, code = "complete", "", 0
        try:
            self.body()
        except Aborted as exc:
            status, message, code = "aborted", str(exc), exc.exit_code
        except Refusal:
            self.cleanup("failed", "", 2)
            raise
        except (IobenchError, HostError, S.SeriesError) as exc:
            status, message, code = "failed", str(exc), getattr(exc, "exit_code", 1)
        except BaseException:
            self.cleanup("failed", "", 1)
            raise
        else:
            status, message, code = self.identity_at_end()
        status, message, code = self.cleanup(status, message, code)
        return self.conclude(status, message, code)

    def identity_at_end(self) -> tuple[str, str, int]:
        """IOB-PLA-15: the target identity must still match its marker when the run ends, else the run failed."""
        try:
            guard.verify_target(self.host, self.target)
        except (IobenchError, HostError) as exc:
            return "failed", f"IOB-PLA-15: the target identity changed during the run ({exc})", 1
        return "complete", "", 0

    def cleanup(self, status: str, message: str, exit_code: int) -> tuple[str, str, int]:
        """Restore every knob, verify io.max, release the lock (IOB-SAF-02/-03/-04)."""
        if not self.safety.locked:
            return status, message, exit_code  # refused before the lock: this invocation owns no journal and restores nothing
        try:
            self.safety.restore()
            self.safety.verify_io_max()
        except VerificationFailure as exc:
            return "failed", str(exc), exc.exit_code
        finally:
            self.safety.release()
        return status, message, exit_code


@dataclass
class Outcome:
    status: str
    exit_code: int
    message: str
    result: dict[str, Any] | None
    calibration: dict[str, Any] | None
    extra: dict[str, Any] = field(default_factory=dict)
