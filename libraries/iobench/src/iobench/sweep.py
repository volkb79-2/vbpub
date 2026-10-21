"""Drain rate, the interference sweep and the read cost (IOB-DRN-01, IOB-SWP-01..04, IOB-RDC-01).

Pure decision logic: the stages module runs the cells, this module decides what they mean.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any, Sequence

from . import plan

GIB = 1 << 30
DRAIN_CONSISTENCY = 1.3
NOISE_LIMIT = 4.0
ALLOWED_KNOBS = ("vm.dirty_bytes", "vm.dirty_background_bytes", "queue/nr_requests", "queue/max_sectors_kb")
SWEEP_CELL_S = 60
REF_LOAD_S = 60
QUIET_DIRTY_BYTES = 4 * 1024 * 1024
QUIET_FOR_S = 5
QUIET_MAX_S = 60
RDC_CELL_S = 10
RDC_BUFFERED_S = 20
PROBE_INTERVAL_S = 0.5
PROBE_BYTES = 200


# -- IOB-DRN-01 -----------------------------------------------------------------------
def drain_rate(cumulative_bytes: Sequence[float], dt: float = 1.0) -> float:
    """Median of the last 25 % of the 1 s samples of device write bytes/s."""
    rates = [(b - a) / dt for a, b in zip(cumulative_bytes, cumulative_bytes[1:])]
    tail = rates[len(rates) - max(3, math.ceil(len(rates) * 0.25)):]
    return statistics.median(tail)


def rates_consistent(r_drain: float, ladder_write_bps: float) -> dict[str, Any]:
    """R_drain within 1.3x of the ladder's ``min(rewrite, fresh)`` write rate, else a named inconsistency."""
    if r_drain * DRAIN_CONSISTENCY < ladder_write_bps:
        return {"consistent": False, "verdict": "drain_below_ladder", "reason": "drain_below_ladder"}
    if r_drain > DRAIN_CONSISTENCY * ladder_write_bps:
        return {"consistent": False, "verdict": "drain_above_ladder", "reason": "cache_residual_possible"}
    return {"consistent": True, "verdict": "consistent", "reason": None}


def quiet_gap_ended(dirty_history: Sequence[float], elapsed_s: float) -> bool:
    """The quiet gap ends when dirty+writeback < 4 MiB for 5 s or after 60 s."""
    if elapsed_s >= QUIET_MAX_S:
        return True
    recent = list(dirty_history[-QUIET_FOR_S:])
    return len(recent) >= QUIET_FOR_S and all(d < QUIET_DIRTY_BYTES for d in recent)


# -- IOB-SWP-01 ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SweepCell:
    name: str
    knobs: dict[str, int]  # the complete setting of this cell: dirty_bytes, nr_requests, max_sectors_kb
    varies: str | None  # the one knob this cell varies (None for REF)


def next_pow2_up(kb: int, max_hw: int) -> int:
    """The next power of two strictly above ``kb``, capped at the hardware limit."""
    return min(1 << kb.bit_length(), max_hw)


def next_pow2_down(kb: int) -> int:
    """The next power of two strictly below ``kb`` (a power of two steps down by one), floor 4 KB."""
    below = 1 << (kb.bit_length() - 1)
    return max(4, below // 2 if below == kb else below)


def sweep_plan(base: dict[str, int], max_hw: int, *, full: bool = False) -> list[SweepCell]:
    """REF, dirty/2, nr x2, max_sectors up, max_sectors down, REF (+ dirty x2, nr /2 with ``--full-sweep``)."""
    def cell(name: str, knob: str, value: int) -> SweepCell:
        return SweepCell(name, {**base, knob: value}, knob)

    ref = SweepCell("REF", dict(base), None)
    cells = [ref, cell("dirty_bytes/2", "dirty_bytes", base["dirty_bytes"] // 2),
             cell("nr_requests*2", "nr_requests", base["nr_requests"] * 2),
             cell("max_sectors_kb+", "max_sectors_kb", next_pow2_up(base["max_sectors_kb"], max_hw)),
             cell("max_sectors_kb-", "max_sectors_kb", next_pow2_down(base["max_sectors_kb"])),
             SweepCell("REF2", dict(base), None)]
    if full:
        cells[-1:-1] = [cell("dirty_bytes*2", "dirty_bytes", base["dirty_bytes"] * 2),
                        cell("nr_requests/2", "nr_requests", max(base["nr_requests"] // 2, 4))]
    return cells


def sweep_seconds(cells: int, *, worst: bool = False) -> int:
    return plan.sweep_seconds(cells, worst=worst)


# -- IOB-SWP-02: the probe script ----------------------------------------------------------
PROBE_SOURCE = '''import os, sys, time

def run(directory, out, duration, interval, now=time.monotonic, sleep=time.sleep, opener=open):
    """Append 200 B and fdatasync every `interval` s; one latency (us) per line in `out`."""
    path = os.path.join(directory, ".iobench-probe")
    lats = []
    start = now()
    nxt = start
    with opener(path, "ab", buffering=0) as fh:
        while now() - start < duration:
            t0 = now()
            fh.write(b"x" * 200)
            os.fdatasync(fh.fileno())
            lats.append((now() - t0) * 1e6)
            nxt += interval
            delay = nxt - now()
            if delay > 0:
                sleep(delay)
    with opener(out, "w") as fh:
        fh.write("".join("%d\\n" % round(v) for v in lats))
    os.unlink(path)

if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4]))
'''


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q / 100.0 * len(ordered)) - 1)]


def cell_record(cell: SweepCell, probe_lats_us: Sequence[float], load_iops: float, load_fsync_p99_us: float) -> dict[str, Any]:
    return {"name": cell.name, "knobs": cell.knobs, "varies": cell.varies, "probe_p99_us": percentile(probe_lats_us, 99),
            "probe_samples": len(probe_lats_us), "load_iops": load_iops, "load_fsync_p99_us": load_fsync_p99_us}


# -- IOB-SWP-03: decision rule ---------------------------------------------------------------
def noise_ratio(ref_a: dict[str, Any], ref_b: dict[str, Any]) -> float:
    """Noise floor = max/min of the two REF probe p99 values."""
    hi, lo = max(ref_a["probe_p99_us"], ref_b["probe_p99_us"]), min(ref_a["probe_p99_us"], ref_b["probe_p99_us"])
    return hi / lo


def beats_ref(ref: dict[str, Any], cell: dict[str, Any], noise: float) -> bool:
    """Better probe p99 than REF by more than the noise ratio, without regressing the gate-like load."""
    better = ref["probe_p99_us"] / cell["probe_p99_us"] > noise
    load_ok = (cell["load_iops"] * noise >= ref["load_iops"]) and (cell["load_fsync_p99_us"] <= ref["load_fsync_p99_us"] * noise)
    return better and load_ok


def decide(ref_a: dict[str, Any], ref_b: dict[str, Any], cell: dict[str, Any], repeat: dict[str, Any] | None) -> dict[str, Any]:
    """A neighbour replaces REF only if it beats it in its cell AND in a repeat."""
    noise = noise_ratio(ref_a, ref_b)
    ref = ref_a
    first = beats_ref(ref, cell, noise)
    second = repeat is not None and beats_ref(ref, repeat, noise)
    return {"cell": cell["name"], "noise": noise, "replaces_ref": first and second, "needs_repeat": first and repeat is None,
            "wins_in_cell": first, "wins_in_repeat": second}


def choose(cells: Sequence[dict[str, Any]], repeats: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Per varied knob, the winning neighbour (largest margin) or REF; and the noise floor."""
    ref_a, ref_b = cells[0], cells[-1]
    noise = noise_ratio(ref_a, ref_b)
    winners: dict[str, dict[str, Any]] = {}
    decisions = []
    for cell in cells[1:-1]:
        d = decide(ref_a, ref_b, cell, repeats.get(cell["name"]))
        decisions.append(d)
        if d["replaces_ref"]:
            best = winners.get(cell["varies"])
            if best is None or cell["probe_p99_us"] < best["probe_p99_us"]:
                winners[cell["varies"]] = cell
    return {"noise_floor_ratio": noise, "noise_high": noise > NOISE_LIMIT, "winners": winners, "decisions": decisions,
            "needs_repeat": [d["cell"] for d in decisions if d["needs_repeat"]]}


# -- IOB-RDC-01 -------------------------------------------------------------------------------
def rdc_candidates(candidates: Sequence[int], max_hw: int) -> list[int]:
    return sorted({*candidates, max_hw})


def rdc_seconds(cells: int) -> int:
    return cells * RDC_CELL_S + RDC_BUFFERED_S


def read_ratio(candidate_bps: float, max_hw_bps: float) -> float:
    return candidate_bps / max_hw_bps
