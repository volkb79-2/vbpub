"""Profiles, budgets, stage allotments and write-volume bounds (IOB-SLO, IOB-PRF, IOB-SWP, IOB-MAT)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from .host import TAIL_BYTES

GIB = 1 << 30
MIB = 1 << 20
PROFILE_BUDGET_S = {"quick": 720, "standard": 3600, "thorough": 4500, "matrix": 900, "knob": 480}
FILL_BUDGET_FRACTION = 0.12
VOLUME_FACTOR = 1.2
SLOW_DISK_BPS = 5_000_000

CELL_S = 30
RUNG_RUNTIME = 30
REF_S = 15
SWEEP_CELL_S = 60
SWEEP_GAP_TYPICAL_S = 20
SWEEP_GAP_WORST_S = 60
KNOB_CELL_S = 45
KNOB_GAP_TYPICAL_S = 10
KNOB_GAP_WORST_S = 60
S3_S = 90
RDC_S = 50
S0_S = 30
S1_S = 30
SWEEP_CELLS = 6
FULL_SWEEP_CELLS = 8


def ceil30(seconds: float) -> int:
    return int(math.ceil(seconds / 30.0) * 30)


def usable(size_bytes: int) -> int:
    return size_bytes - TAIL_BYTES


def volume_bound(r_est_bps: float, stage_seconds: float) -> int:
    """No stage writes more than 1.2 x R_est x its seconds (IOB-SLO-02)."""
    return int(VOLUME_FACTOR * r_est_bps * stage_seconds)


def ws_max(size_bytes: int, r_est_bps: float, budget_s: float, *, fill_seconds: float | None = None) -> int:
    """WS_max = min(0.85 x usable, R_est x fill_budget_s) (IOB-CAL-01)."""
    fill_s = FILL_BUDGET_FRACTION * budget_s if fill_seconds is None else fill_seconds
    return int(min(0.85 * usable(size_bytes), r_est_bps * fill_s))


def rung_plan(ws_max_bytes: int, *, quick: bool = False) -> list[int]:
    """1 GiB, doubling while < WS_max, then WS_max itself (quick: 1 GiB, 16 GiB, WS_max)."""
    if quick:
        sizes = [g * GIB for g in (1, 16) if g * GIB < ws_max_bytes]
        return sizes + [ws_max_bytes]
    sizes = []
    size = GIB
    while size < ws_max_bytes:
        sizes.append(size)
        size *= 2
    return sizes + [ws_max_bytes]


def densification_rungs(last_good: int, ws_max_bytes: int, count: int = 2) -> list[int]:
    """``count`` log-equispaced rungs strictly between ``last_good`` and ``ws_max_bytes``."""
    ratio = (ws_max_bytes / last_good) ** (1.0 / (count + 1))
    return [int(last_good * ratio ** k) for k in range(1, count + 1)]


@dataclass(frozen=True)
class Stage:
    name: str
    allotted_s: int
    required: bool = False
    note: str = ""


@dataclass(frozen=True)
class Allotment:
    profile: str
    budget_s: int
    stages: tuple[Stage, ...]
    ws_max: int
    rungs: tuple[int, ...]

    @property
    def total(self) -> int:
        return sum(s.allotted_s for s in self.stages)

    def seconds(self, name: str) -> int:
        return next(s.allotted_s for s in self.stages if s.name == name)

    def as_dict(self) -> dict:
        return {"profile": self.profile, "budget_s": self.budget_s, "total_s": self.total, "ws_max_bytes": self.ws_max,
                "stages": [{"name": s.name, "allotted_s": s.allotted_s, "required": s.required, "note": s.note}
                           for s in self.stages]}


def plan_calibration(profile: str, size_bytes: int, r_est_bps: float, budget_s: int | None = None) -> Allotment:
    """The stage allotment table of SPEC section 15, derived from the geometry."""
    budget = PROFILE_BUDGET_S[profile] if budget_s is None else budget_s
    quick = profile == "quick"
    thorough = profile == "thorough"
    ws = ws_max(size_bytes, r_est_bps, budget, fill_seconds=60 if quick else None)
    base = rung_plan(ws, quick=quick)
    n_rungs = len(base) if quick else len(base) + 2
    fill_s = ceil30(1.5 * ws / r_est_bps)
    stages = [Stage("S0", S0_S, True), Stage("S1", S1_S, True), Stage("fill", fill_s, True)]
    if not quick:
        stages.append(Stage("E-cold", 140, False, "QD32 70 s + QD1 histogram 60 s + setup"))
    stages.append(Stage("ladder", n_rungs * 2 * RUNG_RUNTIME, True))
    if not quick:
        stages += [Stage("ladder-qd", 120 + (60 if thorough else 0), False, "largest rung QD1/QD8" + (" + QD64" if thorough else "")),
                   Stage("E2", 120), Stage("fresh-vs-rewrite", 90), Stage("fresh-observation", 120),
                   Stage("rearm", 100)]
    stages.append(Stage("reference", 60 if quick else 150))
    if not quick:
        stages.append(Stage("observation", 1140 if thorough else 360))
    stages.append(Stage("S6", 160 if quick else 720))
    stages.append(Stage("S7", 120 if quick else 160))
    return Allotment(profile, budget, tuple(stages), ws, tuple(base))


def sweep_cells(full: bool = False) -> int:
    return FULL_SWEEP_CELLS if full else SWEEP_CELLS


def sweep_seconds(cells: int, *, worst: bool = False) -> int:
    return cells * (SWEEP_CELL_S + (SWEEP_GAP_WORST_S if worst else SWEEP_GAP_TYPICAL_S))


def knob_seconds(cells: int, *, worst: bool = False) -> int:
    return cells * (KNOB_CELL_S + (KNOB_GAP_WORST_S if worst else KNOB_GAP_TYPICAL_S))


def matrix_seconds(*, worst: bool = False, full_sweep: bool = False) -> int:
    return S0_S + S3_S + sweep_seconds(sweep_cells(full_sweep), worst=worst) + RDC_S


def knob_cells(candidates: int, repeat: bool) -> int:
    return candidates * (2 if repeat else 1)


def knob_plan_seconds(candidates: int, repeat: bool, *, worst: bool = False) -> int:
    return knob_seconds(knob_cells(candidates, repeat), worst=worst)


@dataclass
class Budget:
    """Hard wall-clock budget; stages that do not fit their minimum are skipped."""

    now: Callable[[], float]
    total_s: float
    started: float = field(default=0.0)

    def __post_init__(self) -> None:
        self.started = self.now()

    def elapsed(self) -> float:
        return self.now() - self.started

    def remaining(self) -> float:
        return self.total_s - self.elapsed()

    def fits(self, seconds: float) -> bool:
        return self.remaining() >= seconds
