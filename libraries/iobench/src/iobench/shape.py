"""Cost shape and metering (IOB-SHP-01..04): the S6 grid, the per-IO + per-byte fit, the two-test metering rule."""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Sequence

from . import ladder
from . import series as S
from .devio import FioResult

SIZES = (4096, 16384, 65536, 262144, 1048576)
QUICK_SIZES = (4096, 65536)
CELL_S = 30
QUICK_CELL_S = 20
ANCHOR_REPEAT_S = 15
FIT_R2 = 0.9
FIT_RESIDUAL = 0.25
BYTE_CV_FLAT = 0.15
BYTE_CV_VARY = 0.40
PATTERN_TOL = 0.20
DRIFT_LIMIT = 0.15


@dataclass(frozen=True)
class Cell:
    dir: str  # read | write
    pattern: str  # seq | rand
    bs: int
    block_state: str  # rewrite | fresh
    seconds: int = CELL_S
    anchor: bool = False
    repeat: bool = False

    @property
    def id(self) -> str:
        return f"{self.dir}-{self.pattern}-{self.bs}-{self.block_state}" + ("-repeat" if self.repeat else "")

    @property
    def rw(self) -> str:
        return ("rand" if self.pattern == "rand" else "") + self.dir

    @property
    def qd(self) -> int:
        return 32


def is_anchor(pattern: str, bs: int) -> bool:
    return (pattern, bs) in (("rand", 4096), ("seq", 1048576))


def _shuffle(items: list, seed: int) -> list:
    """Deterministic Fisher-Yates over an LCG (the core never calls ``random``)."""
    out = list(items)
    state = seed & 0x7FFFFFFF
    for i in range(len(out) - 1, 0, -1):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        j = state % (i + 1)
        out[i], out[j] = out[j], out[i]
    return out


def grid_plan(seed: int = 1, *, quick: bool = False) -> list[Cell]:
    """The S6 cells in execution order: anchors first, a fixed seeded shuffle, drift repeats mid and end."""
    sizes = QUICK_SIZES if quick else SIZES
    seconds = QUICK_CELL_S if quick else CELL_S
    body = [Cell(d, p, bs, "rewrite", seconds, is_anchor(p, bs) and not quick)
            for d in ("read", "write") for p in ("seq", "rand") for bs in sizes]
    if not quick:
        body += [Cell("write", "rand", 4096, "fresh", seconds, False), Cell("write", "seq", 1048576, "fresh", seconds, False)]
    anchors = [c for c in body if c.anchor]
    rest = _shuffle([c for c in body if not c.anchor], seed)
    if quick:
        return rest
    repeats = [Cell(c.dir, c.pattern, c.bs, c.block_state, ANCHOR_REPEAT_S, True, True) for c in anchors if c.dir == "write"]
    half = len(rest) // 2
    return anchors + rest[:half] + repeats + rest[half:] + repeats


def cell_record(result: FioResult, cell: Cell) -> dict[str, Any]:
    reading = S.reduce_series(S.discard_warmup(result.series, ladder.WARMUP_S))
    lat = ladder.latency_ladder(result)
    return {"id": cell.id, "dir": cell.dir, "pattern": cell.pattern, "bs": cell.bs, "block_state": cell.block_state,
            "offered_bps": None, "steady_iops": reading.steady, "steady_bps": reading.steady * cell.bs,
            "lat_us": {"p50": lat["p50"], "p90": lat["p90"], "p99": lat["p99"]}, "cv": reading.tail_cv,
            "flat_line": reading.rate_limited is True, "state": reading.state, "repeat": cell.repeat}


def anchor_drift(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Spread of each repeated anchor; ``ceiling_moved`` if any exceeds 15 % (IOB-SHP-01)."""
    groups: dict[tuple, list[float]] = {}
    for r in records:
        if is_anchor(r["pattern"], r["bs"]) and r["block_state"] == "rewrite":
            groups.setdefault((r["dir"], r["pattern"], r["bs"]), []).append(r["steady_iops"])
    spread = {"-".join(map(str, k)): max(v) / min(v) - 1 for k, v in groups.items() if len(v) > 1}
    return {"spread": spread, "moved": any(v > DRIFT_LIMIT for v in spread.values())}


def certified(cell: dict[str, Any]) -> bool:
    """Only a steady or flat cell may enter a fit or a metering test: an unsteady cell has no trustworthy rate (IOB-DER-08)."""
    return cell["state"] in ("steady", "flat")


# -- fit ----------------------------------------------------------------------------
def fit_cells(cells: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """``1/steady_iops = c_io + bs x c_byte`` over the sizes of one dir x pattern x block_state."""
    pts = sorted((c["bs"], 1.0 / c["steady_iops"]) for c in cells)
    if len(pts) < 3:
        return {"fit": "insufficient", "c_io_us": None, "c_byte_ns": None, "r2": None, "residual_max": None, "n": len(pts)}
    xs, ys = [float(p[0]) for p in pts], [p[1] for p in pts]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    icpt = my - slope * mx
    pred = [icpt + slope * x for x in xs]
    ss_tot = sum((y - my) ** 2 for y in ys)
    r2 = 1.0 if ss_tot == 0 else 1 - sum((y - p) ** 2 for y, p in zip(ys, pred)) / ss_tot
    resid = max(abs(p - y) / y for y, p in zip(ys, pred))
    return {"fit": "good" if r2 >= FIT_R2 and resid <= FIT_RESIDUAL else "poor", "c_io_us": icpt * 1e6,
            "c_byte_ns": slope * 1e9, "r2": r2, "residual_max": resid, "n": len(pts)}


def _cv(values: Sequence[float]) -> float:
    return statistics.pstdev(values) / statistics.fmean(values)


def _pattern_ok(rand: dict[int, float], seq: dict[int, float]) -> bool:
    sizes = [bs for bs in rand if bs in seq]
    return len(sizes) >= 4 and sum(abs(seq[bs] / rand[bs] - 1) <= PATTERN_TOL for bs in sizes) >= 4


def metering_for(cells: Sequence[dict[str, Any]], direction: str, fits: dict[str, Any], *, moved: bool = False) -> dict[str, Any]:
    """The two-test metering rule for one direction (IOB-SHP-03)."""
    rewrite = [c for c in cells if c["dir"] == direction and c["block_state"] == "rewrite" and not c.get("repeat") and certified(c)]
    by = {p: {c["bs"]: c for c in rewrite if c["pattern"] == p} for p in ("rand", "seq")}
    if moved:
        return {"metering": "undetermined", "reasons": ["ceiling_moved"], "tests": {}}
    rand = by["rand"]
    if len(rand) < 4 or len(by["seq"]) < 4:
        return {"metering": "undetermined", "reasons": [], "tests": {"sizes": len(rand)}}
    bps = [c["steady_bps"] for c in rand.values()]
    iops = [c["steady_iops"] for c in rand.values()]
    t_byte = _cv(bps) <= BYTE_CV_FLAT and _cv(iops) >= BYTE_CV_VARY
    t_iops = _cv(iops) <= BYTE_CV_FLAT and _cv(bps) >= BYTE_CV_VARY
    p_byte = _pattern_ok({bs: c["steady_bps"] for bs, c in rand.items()}, {bs: c["steady_bps"] for bs, c in by["seq"].items()})
    p_iops = _pattern_ok({bs: c["steady_iops"] for bs, c in rand.items()}, {bs: c["steady_iops"] for bs, c in by["seq"].items()})
    tests = {"t_size_byte": t_byte, "t_size_iops": t_iops, "t_pattern_byte": p_byte, "t_pattern_iops": p_iops}
    fit = fits.get(f"{direction}-rand-rewrite") or {}
    if t_byte and p_byte:
        kind = "byte"
    elif t_iops and p_iops:
        kind = "iops"
    elif fit.get("fit") == "good" and fit["c_io_us"] > 0 and fit["c_byte_ns"] > 0 and p_iops:
        kind = "mixed"
    else:
        kind = "undetermined"
    return {"metering": kind, "reasons": [], "tests": tests}


def shape_summary(cells: Sequence[dict[str, Any]], *, override: str | None = None) -> dict[str, Any]:
    """Fits per dir x pattern x block_state, drift and metering per direction (+ the typed override)."""
    drift = anchor_drift(cells)
    fits = {}
    for d in ("read", "write"):
        for p in ("seq", "rand"):
            group = [c for c in cells if c["dir"] == d and c["pattern"] == p and c["block_state"] == "rewrite" and not c.get("repeat") and certified(c)]
            if group:
                fits[f"{d}-{p}-rewrite"] = fit_cells(group)
    by_dir = {d: metering_for(cells, d, fits, moved=drift["moved"]) for d in ("read", "write")}
    fit_by_dir = {d: fits.get(f"{d}-rand-rewrite", {}).get("fit", "insufficient") for d in ("read", "write")}
    if override is not None:
        by_dir = {d: {**m, "metering": override, "override": override} for d, m in by_dir.items()}
    return {"cells": list(cells), "fit": fits, "drift": drift, "metering_by_dir": {d: m["metering"] for d, m in by_dir.items()},
            "metering": by_dir["write"]["metering"], "metering_detail": by_dir, "override": override,
            "linear_model_fit_by_dir": fit_by_dir, "linear_model_fit": fit_by_dir["write"]}
