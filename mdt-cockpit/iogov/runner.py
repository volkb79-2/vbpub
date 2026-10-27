"""Orchestration of one `tick` (cheap read probes + passive snapshot) and one `bench` (the write bench).

Order inside a run: identity + directory checks -> run lock -> load state/aggregates -> kill switch -> backoff -> device detection
-> load gate -> RESERVE the budget and persist next-due -> probes -> journal -> release/refund -> close elapsed windows -> save.
A skipped run costs one gate read of procfs/sysfs and (deduplicated) one journal line, never a probe IO. Everything that must
survive a crash (budget reservation, next bench time, failure counters) is persisted BEFORE the IO it protects.
"""

from __future__ import annotations

import contextlib
import os
import sys
from dataclasses import dataclass, field

from . import __version__
from . import covariates as cov
from . import safeio
from .bench import run_bench, tick_reads
from .budget import BudgetExceeded, DailyBudget, READ_BLOCK, compute_budget
from .collectors import Collector, passive_summary, settings_epoch
from .config import Config
from .devices import Disk, detect, is_block_node
from .env import Env, StopRequested
from .gating import Backoff, Gate, RunGuard, kill_switch_reason
from .journal import Journal, prune as prune_journal, read_records
from .rawread import RawReader, RawReadError, write_denied
from .recognizer import Engine
from .scratch import ScratchBusy, plan_region, remove_stale

DAY = 86400.0


@dataclass
class Runtime:
    cfg: Config
    env: Env
    journal: Journal
    collector: Collector
    disks: list
    notes: list
    allow_root: bool = False
    state: dict = field(default_factory=dict)
    engine: Engine | None = None

    # -- paths ---------------------------------------------------------------------------------------------
    def path(self, name: str) -> str:
        return os.path.join(self.cfg.state_dir, name)

    # -- persistence (single writer: the holder of run.lock; state.lock makes each write atomic w.r.t. readers) --
    def save(self) -> None:
        with safeio.FileLock(self.path("state.lock")):
            safeio.save_json(self.path("state.json"), self.state, lambda n: self.env.urandom(n).hex())

    def save_engine(self) -> None:
        if self.engine is not None:
            with safeio.FileLock(self.path("state.lock")):
                safeio.save_json(self.path("agg.json"), self.engine.to_dict(), lambda n: self.env.urandom(n).hex())

    def emit(self, type: str, **fields) -> dict:
        rec = self.journal.append(type, **fields)
        if self.engine is not None and type in ("probe", "bench", "passive", "note"):
            self.engine.feed(rec)
        return rec


def build(cfg: Config, env: Env | None = None, load: bool = True, allow_root: bool = False) -> Runtime:
    env = env or Env()
    disks, notes = detect(cfg)
    journal = Journal(cfg.journal_dir(), env.wall, run_id=env.urandom(4).hex())
    return Runtime(cfg, env, journal, Collector(cfg, disks, env), disks, notes, allow_root or cfg.allow_root)


def disk_bytes(cfg: Config, disk: Disk) -> int | None:
    try:
        with open(os.path.join(disk.sys_path, "size"), "r") as fh:
            return int(fh.read().strip()) * 512
    except (OSError, ValueError):
        return None


def preflight(rt: Runtime) -> str | None:
    """Identity and directory hygiene. Returns an error message (the run must not proceed) or None."""

    try:
        safeio.check_identity(rt.allow_root)
        safeio.ensure_dir(rt.cfg.state_dir)
        safeio.ensure_dir(rt.cfg.journal_dir())
        return None
    except (safeio.UnsafePath, OSError) as exc:
        return f"iogov: refusing to run: {exc}"


@contextlib.contextmanager
def exclusive(rt: Runtime):
    """The run lock: tick and bench never overlap (a second one exits quietly). State and aggregates are loaded AFTER the lock."""

    lock = safeio.FileLock(rt.path("run.lock"))
    if not lock.acquire():
        yield False
        return
    try:
        rt.state = safeio.load_json(rt.path("state.json"))
        rt.engine = _load_engine(rt)
        yield True
    finally:
        lock.release()


def _read_resets(rt: Runtime) -> list[float]:
    out = []
    try:
        fd = safeio.open_file(rt.path("resets.log"), os.O_RDONLY)
    except FileNotFoundError:
        return out
    with os.fdopen(fd, "r") as fh:
        for line in fh:
            try:
                out.append(float(line.strip()))
            except ValueError:
                pass
    return out


def _load_engine(rt: Runtime) -> Engine:
    data = safeio.load_json(rt.path("agg.json"))
    if data.get("v"):
        eng = Engine(rt.cfg, data)
    else:  # first run (or lost aggregates): rebuild from the journal by streaming it once, bounded memory
        eng = Engine(rt.cfg)
        for rec in read_records(rt.cfg.journal_dir()):
            eng.feed(rec)
        eng.drain()  # events of that history are already in the journal
    for t in _read_resets(rt):
        eng.add_reset(t)
    return eng


def _finish_engine(rt: Runtime) -> None:
    eng = rt.engine
    eng.close_up_to(rt.env.wall())
    for ev in eng.drain():
        rt.journal.append("recommendation", **ev)
    rt.save_engine()


def _log_skip(rt: Runtime, kind: str, reasons: list[str], ctx: dict | None = None) -> None:
    """Journal a skip at most once per skip_log_interval_s per (kind, reason class): a gated host must not fill the journal."""

    sig = "|".join(sorted(r.split(":")[0] for r in reasons))
    logged = rt.state.setdefault("skip_logged", {}).get(kind, {})
    now = rt.env.wall()
    if logged.get("sig") == sig and now - logged.get("t", 0) < rt.cfg.skip_log_interval_s:
        return
    rt.state["skip_logged"][kind] = {"sig": sig, "t": now}
    rt.journal.append("skip", kind=kind, reasons=reasons, ctx=ctx or {})


def _open_readers(rt: Runtime) -> list[RawReader]:
    readers = []
    for disk in rt.disks:
        if not rt.cfg.allow_regular_files_as_devices and not is_block_node(disk.node):
            rt.notes.append(f"{disk.node}: not a block device node, skipped")
            continue
        if rt.cfg.require_write_denied and not write_denied(disk.node):
            rt.notes.append(f"{disk.node}: this identity could write the disk; refusing raw reads (require_write_denied)")
            continue
        try:
            readers.append(RawReader(disk.node, rt.env, allow_regular=rt.cfg.allow_regular_files_as_devices,
                                     allowed=(disk.node,)).open())
        except (OSError, RawReadError) as exc:
            rt.notes.append(f"{disk.node}: cannot open read-only: {exc}")
    return readers


def _cleanup_stale(rt: Runtime) -> None:
    """Leftover scratch files are removed even when a kill switch is set (the kill switch stops probing, not tidying)."""

    try:
        remove_stale(rt.cfg.scratch_dir)
    except (ScratchBusy, safeio.UnsafePath, OSError):
        pass


def _begin(rt: Runtime, kind: str) -> str:
    settings = rt.collector.settings()
    epoch = settings_epoch(settings)
    now = rt.env.wall()
    last = rt.state.get("last_full", {})
    full = last.get("epoch") != epoch or now - last.get("t", 0) >= 3600 or last.get("t", 0) > now
    fields: dict = {"full": full}
    if full:  # settings and covariates are recorded when they change and at least hourly, not on every 5-minute tick
        fields.update(settings=settings, devices=[d.kname for d in rt.disks], notes=list(rt.notes),
                      covariates=cov.collect(rt.cfg, rt.disks, rt.env.wall))
        rt.state["last_full"] = {"epoch": epoch, "t": now}
    rt.journal.append("run_start", kind=kind, version=__version__, mode="run", settings_epoch=epoch, **fields)
    return epoch


def _daily_prune(rt: Runtime) -> None:
    now = rt.env.wall()
    if now - rt.state.get("last_prune", 0) < DAY and rt.state.get("last_prune", 0) <= now:
        return
    rt.state["last_prune"] = now
    removed = prune_journal(rt.cfg.journal_dir(), now, rt.cfg.journal_max_age_days, rt.cfg.journal_max_bytes)
    if removed:
        rt.journal.append("note", text=f"journal retention removed {len(removed)} monthly file(s): {', '.join(removed)}", kind="retention")


def _refuse(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 2


def _guarded(fn):
    """A path-safety violation anywhere in a run (symlinked state file, wrong owner, swapped name) is a refusal, not a crash."""

    def wrapper(rt, *a, **k):
        try:
            return fn(rt, *a, **k)
        except safeio.UnsafePath as exc:
            return _refuse(f"iogov: refusing to continue: {exc}")

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


@_guarded
def run_tick(rt: Runtime) -> int:
    cfg, env = rt.cfg, rt.env
    err = preflight(rt)
    if err:
        return _refuse(err)
    t_start = env.mono()
    with exclusive(rt) as got:
        if not got:
            return 0
        ks = kill_switch_reason(cfg)
        if ks:
            _cleanup_stale(rt)
            _log_skip(rt, "tick", [f"kill_switch:{ks}"])
            rt.save()
            return 0
        backoff = Backoff(rt.state, cfg, env.wall, "tick")
        if backoff.blocked() > 0:
            return 0
        if not rt.disks:
            _log_skip(rt, "tick", ["no_devices"] + rt.notes[:3])
            rt.save()
            return 0
        gate = Gate(cfg, rt.collector, env).check("tick")
        if not gate.ok:
            _log_skip(rt, "tick", gate.reasons, gate.ctx)
            backoff.hit()
            rt.save()
            return 0
        backoff.clear()
        restore = env.install_signal_handlers()
        status, abort = "ok", None
        io = {"read_bytes": 0, "reads": 0}
        readers: list[RawReader] = []
        budget = DailyBudget(rt.state, cfg, env.wall, persist=rt.save)
        try:
            epoch = _begin(rt, "tick")
            guard = RunGuard(cfg, rt.collector, env, strict_pressure=False)
            before = rt.collector.snapshot()
            readers = _open_readers(rt)
            budget.reserve("read", len(readers) * (cfg.read_max_attempts + cfg.witness_reads) * READ_BLOCK)
            for reader in readers:
                out = tick_reads(reader, cfg, env, guard, budget, rt.state)
                for k in io:
                    io[k] += out["io"].get(k, 0)
                for lane in ("r_cold", "r_witness"):
                    if lane in out:
                        rec = out[lane]
                        dev = os.path.basename(os.path.realpath(reader.path))
                        rt.emit("probe", lane=lane, device=dev, quiet=gate.quiet, direct=reader.direct_active,
                                ctx=gate.ctx, epoch=epoch, **rec)
                        if rec.get("p99") is not None and rec["p99"] > cfg.canary_latency_ms * 1000:
                            rt.journal.append("harm", kind="probe_latency", evidence={
                                "lane": lane, "device": dev, "p99_us": rec["p99"], "max_us": rec["max"], "n": rec["n"],
                                "threshold_ms": cfg.canary_latency_ms, "quiet": gate.quiet, "ctx": gate.ctx})
            after = rt.collector.snapshot()
            rt.emit("passive", **passive_summary(before, after, cfg.others_top))
        except StopRequested as exc:
            status, abort = "aborted", exc.reason
        except BudgetExceeded as exc:
            status, abort = "budget", exc.what
        except Exception as exc:  # any unexpected failure: journalled, backed off, and the budget/state still saved below
            status, abort = "error", f"{type(exc).__name__}: {exc}"[:200]
            backoff.hit()
        finally:
            for r in readers:
                r.close()
            restore()
            budget.finish()
        rt.state["last_tick"] = env.wall()
        _daily_prune(rt)
        _finish_engine(rt)
        rt.journal.append("run_end", kind="tick", status=status, abort=abort, duration_s=round(env.mono() - t_start, 3), io=io)
        rt.save()
    return 0


def bench_due(rt: Runtime) -> bool:
    nxt = rt.state.get("next_bench", 0.0)
    now = rt.env.wall()
    if nxt - now > 7 * DAY + 3600:  # the wall clock went backwards: a due time must never be further away than the longest backoff
        return True
    return now >= nxt


def _bench_disabled(rt: Runtime) -> bool:
    fail = rt.state.get("bench_fail", {})
    at = fail.get("disabled_at")
    if at is None:
        return False
    if any(r > at for r in rt.engine.resets):  # `annotate --baseline-reset` re-enables the bench lane
        fail.update({"n": 0, "disabled_at": None})
        return False
    return True


@_guarded
def run_bench_cmd(rt: Runtime, force: bool = False) -> int:
    cfg, env = rt.cfg, rt.env
    err = preflight(rt)
    if err:
        return _refuse(err)
    t_start = env.mono()
    with exclusive(rt) as got:
        if not got:
            return 0
        ks = kill_switch_reason(cfg)
        if ks:
            _cleanup_stale(rt)
            _log_skip(rt, "bench", [f"kill_switch:{ks}"])
            rt.save()
            return 0
        if _bench_disabled(rt):
            _log_skip(rt, "bench", [f"bench_disabled:{rt.state['bench_fail']['n']}_consecutive_failures"])
            rt.save()
            return 0
        if not force and not bench_due(rt):
            return 0
        backoff = Backoff(rt.state, cfg, env.wall, "bench")
        if not force and backoff.blocked() > 0:
            return 0
        if not rt.disks:
            _log_skip(rt, "bench", ["no_devices"] + rt.notes[:3])
            rt.save()
            return 0
        gate = Gate(cfg, rt.collector, env).check("bench")
        if not gate.ok:
            _log_skip(rt, "bench", gate.reasons, gate.ctx)
            backoff.hit()
            rt.save()
            return 0
        backoff.clear()
        sizes = [b for b in (disk_bytes(cfg, d) for d in rt.disks) if b]
        plan = plan_region(cfg, min(sizes) if sizes else None)
        now = env.wall()
        escalate = (
            plan.mode == "file"
            and rt.state.get("last_bench") is not None
            and now - rt.state.get("last_escalation", 0.0) >= cfg.escalation_every_h * 3600.0
        )
        interval = cfg.bench_interval_h * 3600.0
        jitter = (env.rng.random() * 2 - 1) * cfg.bench_jitter * interval
        # ---- persist BEFORE any IO: the next due time and the budget reservation, so a crash cannot cause a retry storm
        rt.state["last_bench"] = now
        rt.state["next_bench"] = now + interval + jitter
        budget = DailyBudget(rt.state, cfg, env.wall, persist=rt.save)
        nbytes = plan.escalation_bytes if escalate else plan.probe_bytes
        budget.reserve("write", (nbytes + cfg.sync4k_samples * 4096) if plan.mode == "file" else 0)
        restore = env.install_signal_handlers()
        result = {"status": "aborted", "abort": "not_run", "lanes": {}, "region": plan.as_dict(), "io": {}, "crashed": True}
        readers: list[RawReader] = []
        try:
            epoch = _begin(rt, "bench")
            guard = RunGuard(cfg, rt.collector, env, strict_pressure=True)
            readers = _open_readers(rt)
            budget.reserve("read", len(readers) * (cfg.seq_read_bytes + 8 * READ_BLOCK))
            try:
                result = run_bench(cfg, env, guard, budget, plan, readers, escalate)
            except ScratchBusy:
                result = {"status": "aborted", "abort": "scratch_busy", "lanes": {}, "region": plan.as_dict(), "io": {}, "crashed": False}
            except safeio.UnsafePath as exc:
                result = {"status": "aborted", "abort": f"unsafe_path:{exc}"[:200], "lanes": {}, "region": plan.as_dict(), "io": {}, "crashed": False}
            except Exception as exc:
                result = {"status": "aborted", "abort": f"error:{type(exc).__name__}:{exc}"[:200], "lanes": {}, "region": plan.as_dict(),
                          "io": {}, "crashed": True}
            rt.emit("bench", status=result["status"], abort=result["abort"], region=result["region"], lanes=result["lanes"],
                    settings_epoch=epoch, ctx=gate.ctx)
        finally:
            for r in readers:
                r.close()
            restore()
            budget.finish()
            _bench_bookkeeping(rt, result, escalate, now, interval)
            rt.save()
        _finish_engine(rt)
        rt.journal.append("run_end", kind="bench", status=result["status"], abort=result["abort"],
                          duration_s=round(env.mono() - t_start, 3), io=result["io"])
        rt.save()
    return 0


def _bench_bookkeeping(rt: Runtime, result: dict, escalate: bool, now: float, interval: float) -> None:
    fail = rt.state.setdefault("bench_fail", {"n": 0, "disabled_at": None})
    if result.get("crashed"):
        fail["n"] += 1
        delay = min(7 * DAY, interval * 2 ** fail["n"])  # exponential backoff of the NEXT attempt, persisted
        rt.state["next_bench"] = max(rt.state.get("next_bench", 0.0), now + delay)
        if fail["n"] >= rt.cfg.bench_max_failures:
            fail["disabled_at"] = now
    else:
        fail["n"] = 0
        if escalate and result["status"] == "ok":
            rt.state["last_escalation"] = now


def cleanup(cfg: Config) -> list[str]:
    try:
        return remove_stale(cfg.scratch_dir)
    except ScratchBusy:
        return ["<busy: another run holds the lock, nothing removed>"]


def annotate(rt: Runtime, text: str, kind: str) -> dict | int:
    err = preflight(rt)
    if err:
        return _refuse(err)
    rec = rt.journal.append("note", text=text, kind=kind)
    if kind == "baseline_reset":  # the runner consumes this log (notes never expire; no journal scan is needed)
        fd = safeio.open_file(rt.path("resets.log"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, f"{rec['t']}\n".encode())
        finally:
            os.close(fd)
    return rec


def prune_cmd(rt: Runtime, dry_run: bool) -> list[str]:
    return prune_journal(rt.cfg.journal_dir(), rt.env.wall(), rt.cfg.journal_max_age_days, rt.cfg.journal_max_bytes, dry_run)


def plan_text(rt: Runtime) -> str:
    """Everything a run WOULD do, from config and metadata only. Opens no device, sleeps never, writes nothing."""

    cfg = rt.cfg
    sizes = [(d.kname, disk_bytes(cfg, d)) for d in rt.disks]
    known = [s for _, s in sizes if s]
    region = plan_region(cfg, min(known) if known else None)
    budget = compute_budget(cfg, region.probe_bytes if region.mode == "file" else None, region.mode)
    lines = ["iogov plan (dry run: no device opened, no file created, nothing written)", ""]
    ks = kill_switch_reason(cfg)
    lines.append(f"kill switch: {'ACTIVE ' + ks if ks else 'not set'}   (flag file: {cfg.kill_switch_file})")
    lines.append(f"identity: uid {os.geteuid()}" + ("  (ROOT: needs allow_root and a root-owned 0700 scratch dir)" if os.geteuid() == 0 else ""))
    for c in cfg.clamped:
        lines.append(f"config clamped: {c}")
    lines.append("devices:")
    for d in rt.disks:
        size = dict(sizes).get(d.kname)
        lines.append(f"  {d.kname}: node {d.node}  size {size if size else '?'} B  block-node={is_block_node(d.node)}")
    if not rt.disks:
        lines.append("  (none)")
    for n in rt.notes:
        lines.append(f"  note: {n}")
    lines.append(f"tick   every {cfg.tick_interval_s:.0f}s: {cfg.read_samples_per_tick} cold 4 KiB raw reads (<= {cfg.read_max_attempts} attempts) "
                 f"+ {cfg.witness_reads} witness reads per disk, <= {cfg.read_iops_max:.0f} IOPS, queue depth 1, read-only")
    if region.mode == "file":
        lines.append(f"bench  every {cfg.bench_interval_h:g}h (+-{cfg.bench_jitter * 100:.0f}% jitter) in {cfg.scratch_dir}: "
                     f"region {region.region_bytes} B (probe {region.probe_bytes} B, weekly escalation {region.escalation_bytes} B), fs {region.fstype}")
        lines.append(f"       lanes: fresh sequential {cfg.block_bytes} B writes qd{cfg.qd_low} then qd{cfg.qd_high} (fdatasync every <= {cfg.sync_every_bytes} B, "
                     f"adaptive to ~250 ms), {cfg.sync4k_samples} x 4 KiB overwrite+fdatasync <= {cfg.sync4k_iops_max:.0f}/s, "
                     f"{cfg.seq_read_bytes} B sequential raw read; file removed afterwards")
    else:
        lines.append(f"bench  write lanes DISABLED: {region.reason}; read lanes only")
    lines.append("budget:")
    for k, v in budget.items():
        lines.append(f"  {k}: {v}")
    lines.append("gates: " + ", ".join([
        f"tick skips if io pressure some avg10 > {cfg.tick_pressure_some_max}",
        f"bench needs some avg10 <= {cfg.bench_pressure_some_max}, full <= {cfg.bench_pressure_full_max}, "
        f"governed writes <= {int(cfg.bench_governed_write_bps_max)} B/s, nothing in flight",
        f"any single IO > {cfg.abort_latency_ms:g} ms or flush > {cfg.flush_limit_s:g} s stops further submissions",
    ]))
    lines.append("This tool changes no cap, model, queue setting or cgroup file.")
    return "\n".join(lines)
