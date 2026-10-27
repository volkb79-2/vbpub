"""Orchestration of one `tick` (cheap read probes + passive snapshot) and one `bench` (the ~6-hourly write bench).

Order inside a run: kill switch -> backoff -> device detection -> load gate -> (signal handlers) -> probes -> journal.
A skipped run costs one gate read of procfs/sysfs and (deduplicated) one journal line, never a probe IO.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import __version__
from .bench import run_bench, tick_reads
from .budget import BudgetExceeded, DailyBudget, compute_budget
from . import covariates as cov
from .collectors import Collector, passive_summary, settings_epoch
from .config import Config
from .devices import Disk, detect, is_block_node
from .env import Env, StopRequested
from .gating import Backoff, Gate, RunGuard, kill_switch_reason
from .journal import Journal, load_state, read_records, save_state
from .rawread import RawReader, RawReadError, write_denied
from .recognizer import new_events, recognize
from .scratch import ScratchBusy, plan_region, remove_stale


@dataclass
class Runtime:
    cfg: Config
    env: Env
    journal: Journal
    state: dict
    collector: Collector
    disks: list
    notes: list

    def save(self) -> None:
        save_state(self.cfg.state_file(), self.state)


def build(cfg: Config, env: Env | None = None, load: bool = True) -> Runtime:
    env = env or Env()
    disks, notes = detect(cfg)
    journal = Journal(cfg.journal_dir(), env.wall, run_id=env.urandom(4).hex())
    state = load_state(cfg.state_file()) if load else {}
    return Runtime(cfg, env, journal, state, Collector(cfg, disks, env), disks, notes)


def disk_bytes(cfg: Config, disk: Disk) -> int | None:
    try:
        with open(os.path.join(disk.sys_path, "size"), "r") as fh:
            return int(fh.read().strip()) * 512
    except (OSError, ValueError):
        return None


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


def maybe_recognize(rt: Runtime, force: bool = False) -> list[dict]:
    now = rt.env.wall()
    if not force and now - rt.state.get("last_recognize", 0) < rt.cfg.recognize_every_s:
        return []
    records = list(read_records(rt.cfg.journal_dir(), since=now - rt.cfg.lookback_days * 86400))
    known = {r["evidence"]["key"] for r in records if r["type"] == "recommendation" and "key" in r.get("evidence", {})}
    emitted = []
    for ev in new_events(recognize(records, rt.cfg), known):
        emitted.append(rt.journal.append("recommendation", **ev))
    rt.state["last_recognize"] = now
    return emitted


def _begin(rt: Runtime, kind: str) -> str:
    settings = rt.collector.settings()
    epoch = settings_epoch(settings)
    rt.journal.append("run_start", kind=kind, version=__version__, mode="run", settings_epoch=epoch,
                      settings=settings, devices=[d.kname for d in rt.disks], notes=list(rt.notes),
                      covariates=cov.collect(rt.cfg, rt.disks, rt.env.wall))
    return epoch


def run_tick(rt: Runtime) -> int:
    cfg, env = rt.cfg, rt.env
    t_start = env.mono()
    ks = kill_switch_reason(cfg)
    if ks:
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
    epoch = _begin(rt, "tick")
    budget = DailyBudget(rt.state, cfg, env.wall)
    guard = RunGuard(cfg, rt.collector, env, strict_pressure=False)
    io = {"read_bytes": 0, "reads": 0}
    status, abort = "ok", None
    readers: list[RawReader] = []
    try:
        before = rt.collector.snapshot()
        readers = _open_readers(rt)
        for reader in readers:
            out = tick_reads(reader, cfg, env, guard, budget, rt.state)
            for k in io:
                io[k] += out["io"].get(k, 0)
            for lane in ("r_cold", "r_witness"):
                if lane in out:
                    rec = out[lane]
                    dev = os.path.basename(os.path.realpath(reader.path))
                    rt.journal.append("probe", lane=lane, device=dev, quiet=gate.quiet, direct=reader.direct_active,
                                      ctx=gate.ctx, epoch=epoch, **rec)
                    if rec.get("p99") is not None and rec["p99"] > cfg.canary_latency_ms * 1000:
                        rt.journal.append("harm", kind="probe_latency", evidence={
                            "lane": lane, "device": dev, "p99_us": rec["p99"], "max_us": rec["max"], "n": rec["n"],
                            "threshold_ms": cfg.canary_latency_ms, "quiet": gate.quiet, "ctx": gate.ctx})
        after = rt.collector.snapshot()
        rt.journal.append("passive", **passive_summary(before, after, cfg.others_top))
    except StopRequested as exc:
        status, abort = "aborted", exc.reason
    except BudgetExceeded as exc:
        status, abort = "budget", exc.what
    finally:
        for r in readers:
            r.close()
        restore()
    rt.state["last_tick"] = env.wall()
    maybe_recognize(rt)
    rt.journal.append("run_end", kind="tick", status=status, abort=abort, duration_s=round(env.mono() - t_start, 3), io=io)
    rt.save()
    return 0


def bench_due(rt: Runtime) -> bool:
    return rt.env.wall() >= rt.state.get("next_bench", 0.0)


def run_bench_cmd(rt: Runtime, force: bool = False) -> int:
    cfg, env = rt.cfg, rt.env
    t_start = env.mono()
    ks = kill_switch_reason(cfg)
    if ks:
        _log_skip(rt, "bench", [f"kill_switch:{ks}"])
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
    restore = env.install_signal_handlers()
    epoch = _begin(rt, "bench")
    budget = DailyBudget(rt.state, cfg, env.wall)
    guard = RunGuard(cfg, rt.collector, env, strict_pressure=True)
    readers = _open_readers(rt)
    try:
        result = run_bench(cfg, env, guard, budget, plan, readers, escalate)
    except ScratchBusy:
        result = {"status": "aborted", "abort": "scratch_busy", "lanes": {}, "region": plan.as_dict(), "io": {}}
    finally:
        for r in readers:
            r.close()
        restore()
    rt.journal.append("bench", status=result["status"], abort=result["abort"], region=result["region"],
                      lanes=result["lanes"], settings_epoch=epoch, ctx=gate.ctx)
    interval = cfg.bench_interval_h * 3600.0
    jitter = (env.rng.random() * 2 - 1) * cfg.bench_jitter * interval
    rt.state["last_bench"] = now
    rt.state["next_bench"] = now + interval + jitter
    if escalate and result["status"] == "ok":
        rt.state["last_escalation"] = now
    maybe_recognize(rt, force=True)
    rt.journal.append("run_end", kind="bench", status=result["status"], abort=result["abort"],
                      duration_s=round(env.mono() - t_start, 3), io=result["io"])
    rt.save()
    return 0


def cleanup(cfg: Config) -> list[str]:
    try:
        return remove_stale(cfg.scratch_dir)
    except ScratchBusy:
        return ["<busy: another run holds the lock, nothing removed>"]


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
        lines.append(f"       lanes: fresh sequential {cfg.block_bytes} B writes qd{cfg.qd_low} then qd{cfg.qd_high} (fdatasync every {cfg.sync_every_bytes} B), "
                     f"{cfg.sync4k_samples} x 4 KiB O_DSYNC overwrites <= {cfg.sync4k_iops_max:.0f}/s, "
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
        f"any single IO > {cfg.abort_latency_ms:g} ms stops further submissions",
    ]))
    lines.append("This tool changes no cap, model, queue setting or cgroup file.")
    return "\n".join(lines)
