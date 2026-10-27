"""The probes themselves: the ~6-hourly mini write bench (scratch file) and the read lanes (raw disk, read-only).

Lanes produced (all journalled by the runner):
  w_fresh_qd1 / w_fresh_qd4   sequential 64 KiB writes into the NEW scratch file (fresh allocation), each phase with its
                              own periodic fdatasync; durable rate = bytes / (write wall time + sync time)
  w_over_4k                   4 KiB O_DIRECT|O_DSYNC overwrites at random slots of the written area, qd 1, paced
  r_seq                       one bounded sequential read at a random non-zero offset of a raw disk
  r_cold / r_witness          4 KiB raw reads (see tick_reads)
Every IO is charged to the budget and a rate bucket BEFORE submission; the guard is consulted between IOs.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

from .budget import BudgetExceeded, DailyBudget, TokenBucket
from .config import Config
from .env import Env, StopRequested
from .gating import RunGuard
from .rawread import AlignedBuffer, RawReader, coprime_stride, block_at, summarize_latencies
from .scratch import RegionPlan, Scratch


class LaneAbort(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _fill(buf: AlignedBuffer, env: Env, n: int) -> None:
    data = env.urandom(n)
    if not any(data[:64]):  # a random block that is all-zero in its head: vanishingly unlikely; make sure
        data = b"\x01" + data[1:]
    buf.mm[:n] = data


def _lane_base(planned: int) -> dict:
    return {"planned_bytes": planned, "bytes": 0, "valid": False, "aborted": None}


def seq_write_phase(sc: Scratch, cfg: Config, env: Env, guard: RunGuard, budget: DailyBudget,
                    start: int, length: int, qd: int) -> dict:
    lane = _lane_base(length)
    lane.update({"qd": qd, "block": cfg.block_bytes, "t_write": 0.0, "t_sync": 0.0, "syncs": 0})
    block = cfg.block_bytes
    bucket = TokenBucket(cfg.write_bps_max, env.mono, env.sleep, burst=block * qd)
    deadline = env.mono() + cfg.phase_wall_s
    lat_us: list[int] = []
    sync_ms: list[float] = []
    lock = threading.Lock()
    local = threading.local()
    abort = threading.Event()
    reason: list[str] = []

    def do_chunk(offset: int) -> None:
        if abort.is_set():
            return
        try:
            guard.check()
            bucket.acquire(block)
            buf = getattr(local, "buf", None)
            if buf is None:
                buf = local.buf = AlignedBuffer(block)
            _fill(buf, env, block)
            t0 = env.mono()
            os.pwrite(sc.fd, memoryview(buf.mm)[:block], offset)
            dt = env.mono() - t0
            with lock:
                lat_us.append(int(dt * 1e6))
                lane["bytes"] += block
            guard.note_latency(dt)
        except StopRequested as exc:
            reason.append(exc.reason)
            abort.set()

    per_batch = max(1, cfg.sync_every_bytes // block)
    chunks = length // block
    pos = 0
    pool = ThreadPoolExecutor(max_workers=qd) if qd > 1 else None
    try:
        while pos < chunks and not abort.is_set():
            if env.mono() > deadline:
                reason.append("phase_wall_clock")
                break
            n = min(per_batch, chunks - pos)
            try:
                budget.charge("write", n * block, ops=n)
                budget.charge("sync", 0, ops=1)
            except BudgetExceeded as exc:
                reason.append(f"budget:{exc.what}")
                break
            offsets = [start + (pos + i) * block for i in range(n)]
            t0 = env.mono()
            if pool is None:
                for off in offsets:
                    do_chunk(off)
                    if abort.is_set():
                        break
            else:
                list(pool.map(do_chunk, offsets))
            lane["t_write"] += env.mono() - t0
            t0 = env.mono()
            os.fdatasync(sc.fd)
            ds = env.mono() - t0
            lane["t_sync"] += ds
            lane["syncs"] += 1
            sync_ms.append(round(ds * 1000.0, 3))
            pos += n
    finally:
        if pool is not None:
            pool.shutdown(wait=True)
    if reason:
        lane["aborted"] = reason[0]
    total = lane["t_write"] + lane["t_sync"]
    if lane["bytes"] and total > 0:
        lane["rate_write_bps"] = lane["bytes"] / lane["t_write"] if lane["t_write"] > 0 else None
        lane["rate_durable_bps"] = lane["bytes"] / total
        lane["at_cap"] = lane["rate_durable_bps"] >= 0.9 * cfg.write_bps_max
        lane["valid"] = lane["bytes"] >= max(block * 2, length // 4)
    lane["lat"] = summarize_latencies(lat_us)
    lane["sync_ms"] = sync_ms[:32]
    lane["sync_ms_max"] = max(sync_ms) if sync_ms else None
    return lane


def over_4k(sc: Scratch, cfg: Config, env: Env, guard: RunGuard, budget: DailyBudget, written: int) -> dict:
    lane = _lane_base(cfg.sync4k_samples * 4096)
    lane["n_planned"] = cfg.sync4k_samples
    slots = written // 4096
    lat_us: list[int] = []
    buf = AlignedBuffer(4096)
    bucket = TokenBucket(cfg.sync4k_iops_max, env.mono, env.sleep, burst=1)
    try:
        for _ in range(cfg.sync4k_samples):
            guard.check()
            budget.charge("write", 4096, ops=1)
            budget.charge("sync", 0, ops=1)
            bucket.acquire(1)
            _fill(buf, env, 4096)
            off = env.rng.randrange(slots) * 4096
            t0 = env.mono()
            os.pwrite(sc.fd_dsync, memoryview(buf.mm)[:4096], off)
            dt = env.mono() - t0
            lat_us.append(int(dt * 1e6))
            lane["bytes"] += 4096
            guard.note_latency(dt)
    except StopRequested as exc:
        lane["aborted"] = exc.reason
    except BudgetExceeded as exc:
        lane["aborted"] = f"budget:{exc.what}"
    finally:
        buf.close()
    lane["lat"] = summarize_latencies(lat_us)
    lane["n"] = len(lat_us)
    lane["valid"] = len(lat_us) >= max(8, cfg.sync4k_samples // 4)
    return lane


def seq_read(reader: RawReader, cfg: Config, env: Env, guard: RunGuard, budget: DailyBudget) -> dict:
    """One bounded sequential read at a random aligned offset whose first block is non-zero (not a thin hole)."""

    length = min(cfg.seq_read_bytes, (reader.size // cfg.seq_read_block) * cfg.seq_read_block)
    lane = _lane_base(length)
    lane.update({"block": cfg.seq_read_block, "device_bytes": reader.size, "direct": reader.direct_active})
    if length < cfg.seq_read_block * 4:
        lane["aborted"] = "device_too_small"
        return lane
    probe = AlignedBuffer(reader.block)
    buf = AlignedBuffer(cfg.seq_read_block)
    bucket = TokenBucket(cfg.read_bps_max, env.mono, env.sleep, burst=cfg.seq_read_block)
    try:
        slots = (reader.size - length) // cfg.seq_read_block
        offset = None
        for _ in range(8):
            cand = env.rng.randrange(slots + 1) * cfg.seq_read_block
            budget.charge("read", reader.block)
            if reader.read_at(probe, cand, reader.block).nonzero:
                offset = cand
                break
        if offset is None:
            lane["aborted"] = "no_nonzero_offset_found"
            return lane
        lane["offset_block"] = offset // cfg.seq_read_block
        t0 = env.mono()
        deadline = t0 + cfg.phase_wall_s
        nz = 0
        blocks = length // cfg.seq_read_block
        for i in range(blocks):
            guard.check()
            if env.mono() > deadline:
                lane["aborted"] = "phase_wall_clock"
                break
            budget.charge("read", cfg.seq_read_block)
            bucket.acquire(cfg.seq_read_block)
            r = reader.read_at(buf, offset + i * cfg.seq_read_block, cfg.seq_read_block)
            lane["bytes"] += r.nbytes
            nz += 1 if r.nonzero else 0
            guard.note_latency(r.seconds)
        elapsed = env.mono() - t0
        if lane["bytes"] and elapsed > 0:
            lane["rate_bps"] = lane["bytes"] / elapsed
            lane["at_cap"] = lane["rate_bps"] >= 0.9 * cfg.read_bps_max
            lane["nonzero_fraction"] = nz / max(1, lane["bytes"] // cfg.seq_read_block)
            lane["valid"] = lane["bytes"] >= length // 2 and lane["nonzero_fraction"] >= 0.5
    except StopRequested as exc:
        lane["aborted"] = exc.reason
    except BudgetExceeded as exc:
        lane["aborted"] = f"budget:{exc.what}"
    finally:
        probe.close()
        buf.close()
    return lane


def run_bench(cfg: Config, env: Env, guard: RunGuard, budget: DailyBudget, plan: RegionPlan,
              readers: list[RawReader], escalate: bool) -> dict:
    """Run every lane that can run. Returns {'status', 'abort', 'lanes', 'region', 'io'}; always cleans the scratch file."""

    lanes: dict[str, dict] = {}
    region = plan.as_dict()
    region["escalated"] = escalate
    abort: str | None = None
    try:
        if plan.mode == "file":
            nbytes = plan.escalation_bytes if escalate else plan.probe_bytes
            n1 = (nbytes // 4 // cfg.block_bytes) * cfg.block_bytes
            n2 = ((nbytes - n1) // cfg.block_bytes) * cfg.block_bytes
            with Scratch(cfg, env, nbytes) as sc:
                region["direct"] = sc.direct
                region["stale_removed"] = sc.removed_stale
                lanes["w_fresh_qd1"] = seq_write_phase(sc, cfg, env, guard, budget, 0, n1, cfg.qd_low)
                if lanes["w_fresh_qd1"]["aborted"]:
                    raise LaneAbort(lanes["w_fresh_qd1"]["aborted"])
                lanes["w_fresh_qd4"] = seq_write_phase(sc, cfg, env, guard, budget, n1, n2, cfg.qd_high)
                if lanes["w_fresh_qd4"]["aborted"]:
                    raise LaneAbort(lanes["w_fresh_qd4"]["aborted"])
                written = lanes["w_fresh_qd1"]["bytes"] + lanes["w_fresh_qd4"]["bytes"]
                if written >= 4096 * 16:
                    lanes["w_over_4k"] = over_4k(sc, cfg, env, guard, budget, written)
                    if lanes["w_over_4k"]["aborted"]:
                        raise LaneAbort(lanes["w_over_4k"]["aborted"])
        else:
            region["disabled"] = plan.reason
        for reader in readers:
            lane = seq_read(reader, cfg, env, guard, budget)
            lane["device"] = os.path.basename(os.path.realpath(reader.path))
            lanes["r_seq" if len(readers) == 1 else f"r_seq@{lane['device']}"] = lane
            if lane["aborted"]:
                raise LaneAbort(lane["aborted"])
    except LaneAbort as exc:
        abort = exc.reason
    except StopRequested as exc:
        abort = exc.reason
    except BudgetExceeded as exc:
        abort = f"budget:{exc.what}"
    io = {
        "write_bytes": sum(l.get("bytes", 0) for k, l in lanes.items() if k.startswith("w_")),
        "read_bytes": sum(l.get("bytes", 0) for k, l in lanes.items() if k.startswith("r_")),
    }
    return {"status": "aborted" if abort else "ok", "abort": abort, "lanes": lanes, "region": region, "io": io}


# ---------------------------------------------------------------------------------------------------------
# tick: 4 KiB raw reads
# ---------------------------------------------------------------------------------------------------------


def tick_reads(reader: RawReader, cfg: Config, env: Env, guard: RunGuard, budget: DailyBudget, state: dict) -> dict:
    """r_cold: walk the whole device (coprime stride, persisted counter, random start); r_witness: re-read known non-zero blocks.

    Returns {'r_cold': {...}, 'r_witness': {...}, 'io': {...}}. Zero blocks (unallocated or genuinely zero) are counted
    but excluded from latency statistics, because a thin/sparse backend answers them from a fast path.
    """

    key = os.path.basename(os.path.realpath(reader.path))
    dev_state = state.setdefault("devices", {}).setdefault(key, {})
    block = reader.block
    n = reader.n_blocks()
    out = {"io": {"read_bytes": 0, "reads": 0}}
    if n < 16:
        out["error"] = "device_too_small"
        return out
    # a changed geometry (resize) restarts the walk: the old stride is not coprime with the new size
    if dev_state.get("n_blocks") != n:
        dev_state.update({"n_blocks": n, "stride": coprime_stride(n), "start": env.rng.randrange(n), "counter": 0, "witness": []})
    bucket = TokenBucket(cfg.read_iops_max, env.mono, env.sleep, burst=1)
    buf = AlignedBuffer(block)
    cold_lat: list[int] = []
    zero = 0
    errors = 0
    attempts = 0
    witness_lat: list[int] = []
    found: list[int] = []
    try:
        while len(cold_lat) < cfg.read_samples_per_tick and attempts < cfg.read_max_attempts:
            guard.check()
            budget.charge("read", block)
            bucket.acquire(1)
            blk = block_at(dev_state["counter"], n, dev_state["stride"], dev_state["start"])
            dev_state["counter"] += 1
            attempts += 1
            try:
                r = reader.read_at(buf, blk * block, block)
            except OSError:
                errors += 1
                continue
            out["io"]["read_bytes"] += block
            out["io"]["reads"] += 1
            guard.note_latency(r.seconds)
            if r.nonzero:
                cold_lat.append(int(r.seconds * 1e6))
                found.append(blk)
            else:
                zero += 1
        witnesses = dev_state.setdefault("witness", [])
        witnesses[:] = [w for w in witnesses if 0 <= w < n]
        for blk in list(witnesses[: cfg.witness_reads]):
            guard.check()
            budget.charge("read", block)
            bucket.acquire(1)
            try:
                r = reader.read_at(buf, blk * block, block)
            except OSError:
                errors += 1
                continue
            out["io"]["read_bytes"] += block
            out["io"]["reads"] += 1
            guard.note_latency(r.seconds)
            if r.nonzero:
                witness_lat.append(int(r.seconds * 1e6))
            else:  # it became a zero block (trim/overwrite): retire it
                witnesses.remove(blk)
        # rotate the witness list: keep the oldest ones first, add new finds until the cap
        for blk in found:
            if len(witnesses) >= cfg.witness_keep:
                break
            if blk not in witnesses:
                witnesses.append(blk)
        if len(witnesses) > cfg.witness_reads:
            witnesses.append(witnesses.pop(0))  # round-robin which witnesses are read next time
    finally:
        buf.close()
    out["r_cold"] = dict(summarize_latencies(cold_lat), zero=zero, attempts=attempts, errors=errors)
    out["r_witness"] = dict(summarize_latencies(witness_lat), zero=0, attempts=len(witness_lat), errors=0)
    return out
