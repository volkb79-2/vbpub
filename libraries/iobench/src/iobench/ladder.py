"""Working-set ladder: rung records, cliff, classes, QD90, fresh vs rewrite, bases (IOB-CAL-02..10)."""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Sequence

from . import series as S
from .devio import FioJob, FioResult
from .extents import Extents
from .host import TAIL_BYTES

GIB = 1 << 30
MIB = 1 << 20
WARMUP_S = 5.0
CLIFF_RATIO = 0.75
REARM_RATIO = 3.0
FRESH_MAX = 8 * GIB
PCT_KEYS = {"p50": "50.000000", "p90": "90.000000", "p95": "95.000000", "p99": "99.000000", "p999": "99.900000"}


def fresh_label(kind: str) -> str:
    """The write measurement on a fresh extent is ``fresh`` on a device, ``fresh_fs_alloc`` in a file (IOB-TGT-08)."""
    return "fresh" if kind == "device" else "fresh_fs_alloc"


def align(value: float) -> int:
    return int(value) // MIB * MIB


def align_up(value: float) -> int:
    return -(-int(value) // MIB) * MIB


def latency_ladder(result: FioResult) -> dict[str, float]:
    return {k: result.stats.pct_us[v] for k, v in PCT_KEYS.items()}


def rung_record(result: FioResult, ws_bytes: int, *, warmup_s: float = WARMUP_S) -> dict[str, Any]:
    """IOB-CAL-03: one rung/cell through ``reduce_series`` (raises SeriesError on < 8 points)."""
    job = result.job
    points = S.discard_warmup(result.series, warmup_s)
    reading = S.reduce_series(points)
    return {"ws_bytes": ws_bytes, "dir": job.ddir, "qd": job.iodepth, "bs": job.bs, "steady_iops": reading.steady,
            "mean_iops": reading.mean, "state": reading.state, "observation_s": reading.observation_s,
            "knee_s": reading.knee_s, "burst_units": reading.burst_units, "rate_limited": reading.rate_limited,
            "class": "unclassified", "lat_us": latency_ladder(result), "series": S.downsample(points)}


def classify_rungs(rungs: Sequence[dict[str, Any]]) -> tuple[float | None, float, list[dict[str, Any]]]:
    """IOB-CAL-04: ``(cliff_bytes, cached_ref_iops, rungs with classes)``; rungs sorted by size."""
    ordered = sorted(rungs, key=lambda r: r["ws_bytes"])
    cached_ref = statistics.median(r["steady_iops"] for r in ordered[:3])
    threshold = CLIFF_RATIO * cached_ref
    cliff = None
    for i, r in enumerate(ordered):
        if r["steady_iops"] < threshold and all(x["steady_iops"] < threshold for x in ordered[i + 1:]):
            cliff = r["ws_bytes"]
            break
    out = []
    for r in ordered:
        cls = "no_cliff" if cliff is None else ("cached_tier" if r["ws_bytes"] < cliff else "post_cliff")
        out.append({**r, "class": cls})
    return cliff, cached_ref, out


def qd90(by_depth: dict[int, float]) -> int:
    """Smallest tested depth whose steady >= 0.9 x the maximum over the tested depths (IOB-CAL-08)."""
    best = max(by_depth.values())
    return min(d for d, v in by_depth.items() if v >= 0.9 * best)


def cached_tier_factor(rungs: Sequence[dict[str, Any]]) -> float:
    ordered = sorted(rungs, key=lambda r: r["ws_bytes"])
    return ordered[0]["steady_iops"] / ordered[-1]["steady_iops"]


def best_read_rung(rungs: Sequence[dict[str, Any]]) -> float | None:
    """The maximum steady read IOPS over the steady rungs (the only permitted use of a cached figure, IOB-DER-06).

    An unsteady rung has no trustworthy rate; ``None`` when no rung is steady."""
    steady = [r["steady_iops"] for r in rungs if r["state"] in ("steady", "flat")]
    return max(steady) if steady else None


def fresh_extent(usable: int, ws_max: int) -> tuple[int, int] | None:
    """The fresh extent above WS_max and below the reserved tail (IOB-CAL-07); ``None`` when there is no room."""
    start = align_up(ws_max)
    length = align(min(usable - start - TAIL_BYTES, FRESH_MAX))
    return (start, start + length) if length > 0 else None


def fresh_vs_rewrite(kind: str, rewrite_iops: float, fresh_iops: float, mount_discard: str | None) -> dict[str, Any]:
    return {"measured": True, "kind": kind, "label": fresh_label(kind), "mount_discard": mount_discard,
            "fresh_iops": fresh_iops, "rewrite_iops": rewrite_iops, "penalty": rewrite_iops / fresh_iops}


def fresh_in_basis(fvr: dict[str, Any]) -> bool:
    """Fresh enters the write cap basis on a device, or on a ``discard`` mount for a file (IOB-TGT-08)."""
    return bool(fvr.get("measured")) and (fvr["kind"] == "device" or fvr["mount_discard"] == "true")


def write_iops_basis(rewrite_iops: float, fvr: dict[str, Any] | None) -> float:
    """``min(rewrite, fresh)`` when both are measured and fresh counts (IOB-CAL-07)."""
    if fvr is not None and fresh_in_basis(fvr):
        return min(rewrite_iops, fvr["fresh_iops"])
    return rewrite_iops


def rearm_verdict(before_iops: float, after_iops: float, reference_moved: bool) -> dict[str, Any]:
    """IOB-CAL-10: did a discard re-arm the fresh-block penalty?  Info only."""
    ratio = before_iops / after_iops
    if reference_moved:
        verdict = "ceiling_moved"
    else:
        verdict = "rearmed" if ratio >= REARM_RATIO else "not_rearmed"
    return {"rearm_ratio": ratio, "verdict": verdict}


def observation_plan(profile: str) -> dict[str, int]:
    """IOB-CAL-05: seconds of the write, read and fresh observation runs."""
    if profile == "thorough":
        return {"write": 900, "read": 240, "fresh": 120}
    return {"write": 240, "read": 120, "fresh": 120}


def observation_limit(observation_s: float) -> str:
    return f"bucket_exclusion_s<={int(observation_s)}"


def confined(job: FioJob, extent: Extents) -> bool:
    """Every read job's offset+size lies within the recorded extent (argv-level, IOB-CAL-02)."""
    return extent.covers(job.offset, job.end)


@dataclass(frozen=True)
class Basis:
    """A cap basis with its own status (IOB-CAL-06)."""

    value: float | None
    status: str
    reasons: tuple[str, ...] = ()
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"value": self.value, "status": self.status}
        out["reason" if self.status == "absent" else "reasons"] = self.reason if self.status == "absent" else list(self.reasons)
        return out


def bps_basis(direction: str, seq_cell: dict[str, Any] | None, *, cache_ratio: float | None,
              read_reasons: Sequence[str], write_reasons: Sequence[str]) -> Basis:
    """BPS cap basis: the sequential 1 MiB QD32 cell, scaled by ``cache_ratio`` for proven reads (IOB-CAL-06).

    ``read_reasons`` (used when the read cache state is unproven) and ``write_reasons`` (empty = the write series is proven)
    are the real reasons of the basis, supplied by the caller: there is no default that could stand in for them.
    A flat-line (``rate_limited``) cell is not a proof: it is treated exactly like any other cell."""
    if seq_cell is None:
        return Basis(None, "absent", reason="bps_basis_missing")
    bps = seq_cell["steady_bps"]
    if direction == "read":
        if cache_ratio is None:
            return Basis(bps, "unproven", tuple(read_reasons))
        return Basis(bps * min(1.0, cache_ratio), "proven")
    return Basis(bps, "unproven" if write_reasons else "proven", tuple(write_reasons))
