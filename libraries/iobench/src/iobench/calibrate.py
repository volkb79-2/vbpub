"""Calibration (IOB-CAL-01..10, IOB-CCH-01..07, IOB-SLO, IOB-SHP, IOB-LAT): the slow once-per-host phase."""
from __future__ import annotations

import math
import statistics
from dataclasses import replace
from typing import Any

from .derive import latency_knee
from . import cache, ceilings, devio, guard, ladder, plan, plausibility, proposals, shape
from . import result as res
from . import series as S
from .devio import FioResult
from .errors import Aborted, IobenchError, Refusal, RunFailed
from .extents import Extents
from .host import HostError
from .stages import GIB, MIB, Config, IntegrityViolation, Outcome, Runner, StageSkipped

METHOD = "ladder-v1+cachefit-v1+shape-v1+latcurve-v1+drain-v1+sweep-v1+readcost-v1"
COLD_MAX = 32 * GIB
COLD_SHARE = 0.10
E_COLD_QD32_S = 70
HIST_S = 60
REF_S = 15
REF_WARMUP_S = 3.0
EXTEND_S = 300
OBS_QD = 32  # the cap basis is a QD32 quantity: the observation that time-proves it runs at the same depth (review B-B3)
MIN_FILL_CHUNK = 64 * MIB
FILL_REPLAN_FACTOR = 0.5
FILL_CHECK_S = 10
REARM_RANGE = 128 * MIB
S7_POINTS = (10, 30, 60, 90)
S7_QUICK_POINTS = (10, 30, 90)
S7_S = 20
REACHED = 0.9
MIN_RUNGS = 3
RUNG_S = 30


class TouchMap:
    """Bytes written or read per 1/8 of the written extent, by stage (IOB-CCH-01)."""

    def __init__(self) -> None:
        self.maps: dict[str, list[float]] = {}

    def add(self, key: str, start: int, end: int, nbytes: float, extent: int) -> None:
        eighth = extent / 8.0
        row = self.maps.setdefault(key, [0.0] * 8)
        for i in range(8):
            lo, hi = max(start, i * eighth), min(end, (i + 1) * eighth)
            if hi > lo:
                row[i] += nbytes * (hi - lo) / (end - start)

    def as_dict(self) -> dict[str, list[int]]:
        return {k: [int(v) for v in row] for k, row in self.maps.items()}


def sparse(counts: list[int]) -> dict[str, int]:
    return {str(i): c for i, c in enumerate(counts) if c}


def _basis(value: float | None, reasons: list[str], absent: str | None = None) -> dict[str, Any]:
    if absent is not None:
        return {"value": None, "status": "absent", "reason": absent}
    return {"value": value, "status": "unproven" if reasons else "proven", "reasons": proposals.dedupe(reasons)}


class Calibrator(Runner):
    def __init__(self, host: Any, cfg: Config) -> None:
        super().__init__(host, cfg, command="calibrate")
        self.profile, self.quick = cfg.profile, cfg.profile == "quick"
        self.touch = TouchMap()
        self.reads: list[dict[str, Any]] = []
        self.writes: list[dict[str, Any]] = []
        self.depth: dict[str, dict[int, float]] = {"read": {}, "write": {}}
        self.depth_unsteady: set[str] = set()  # directions with an unsteady depth rung: QD90 would rest on a drifting figure
        self.qd1_p99: dict[str, float] = {}
        self.evidence: dict[str, Any] = {"histograms": {}}
        self.refs: dict[str, dict[str, float]] = {}
        self.ref_tag: str | None = None
        self.moved: dict[str, bool] = {}
        self.moved_dirs: dict[str, dict[str, bool]] = {}  # a write comparison is judged by the WRITE reference (a read reference follows the read cache)
        self.checks: list[plausibility.Check] = []
        self.r_est = 0.0
        self.ws = 0
        self.ws_planned = 0
        self.margin = 0
        self.fresh: tuple[int, int] | None = None
        self.fresh_ok = False
        self.fresh_note: str | None = None
        self.budget_limited = False
        self.fill_info: dict[str, Any] = {}
        self.cells: list[dict[str, Any]] = []
        self.cold_raw: dict[str, Any] | None = None
        self.hit_ref_us: float | None = None
        self.e2_raw: dict[str, Any] | None = None
        self.fvr: dict[str, Any] | None = None
        self.rearm_info: dict[str, Any] | None = None
        self.obs: dict[str, S.Reading] = {}
        self.curve: list[dict[str, Any]] = []
        self.little: list[plausibility.Check] = []
        self.last_started = 0.0
        self.history_rotated: str | None = None
        self.calibration_id = f"cal-{self.run_id[4:].rsplit('-', 1)[0]}-{host.token()}{host.token()}"

    # -- helpers ---------------------------------------------------------------------------------------
    def reading(self, result: FioResult, warmup: float = ladder.WARMUP_S) -> S.Reading:
        return S.reduce_series(S.discard_warmup(result.series, warmup))

    def bound(self, seconds: float) -> int:
        return plan.volume_bound(self.r_est, seconds)

    def run_job(self, name: str, rw: str, bs: int, qd: int, runtime: int, offset: int, size: int, *, key: str = "other",
                **kw: Any) -> FioResult:
        io_size = self.bound(runtime) if rw in ("write", "randwrite") and "io_size" not in kw else kw.pop("io_size", None)
        out = self.fio(self.job(name, rw, bs, qd, runtime, offset, size, io_size=io_size, **kw))
        if self.ws and out.stats.io_bytes:
            self.touch.add(key, out.job.offset, out.job.end, out.stats.io_bytes, self.ws)
        return out

    def discard(self, extent: tuple[int, int]) -> bool:
        length = extent[1] - extent[0]
        build = devio.blkdiscard_argv if self.target.kind == "device" else devio.punch_hole_argv
        guard.verify_target(self.host, self.target)
        return self.host.discard_range(build(self.target.path, extent[0], length), self.target.ref, extent[0], length).returncode == 0

    def reference(self, tag: str) -> None:
        """Reference cells (IOB-REF-01): random 4 KiB QD8 over the first GiB of the written extent, 15 s (3 s discarded)."""
        row = {}
        for direction, rw in (("read", "randread"), ("write", "randwrite")):
            out = self.run_job(f"ref-{tag}-{direction}", rw, 4096, 8, REF_S, 0, min(GIB, self.ws), key="reference", log_avg_ms=1000)
            row[direction] = self.reading(out, REF_WARMUP_S).steady
        self.refs[tag] = row

    def close_group(self, group: str, tag: str) -> bool:
        """Take the closing reference of a comparison group and record whether the ceiling moved."""
        self.reference(tag)
        before, after = self.refs[self.ref_tag], self.refs[tag]
        self.moved_dirs[group] = {d: plausibility.ref_moved(before[d], after[d]) for d in ("read", "write")}
        self.moved[group] = any(self.moved_dirs[group].values())
        self.ref_tag = tag
        return self.moved[group]

    # -- S1 and the plan --------------------------------------------------------------------------------
    def s1(self) -> None:
        if self.target.usable_bytes < GIB:
            raise Refusal("target_too_small", f"{self.target.path} has {self.target.usable_bytes} usable bytes; S1 writes the first GiB",
                          "use a target of at least 1 GiB plus the 1 MiB reserved tail")
        self.allowed_writes.add(0, GIB)
        out = self.fio(self.job("s1", "write", MIB, 16, 20, 0, GIB, metric="bw", log_avg_ms=1000))
        self.r_est = statistics.median([v for _, v in out.series][-10:])
        if self.r_est <= 0:
            raise RunFailed("S1 measured no write throughput")
        self.evidence["rate_probe"] = {"r_est_bps": self.r_est, "samples": len(out.series), "seconds": 20}

    def make_plan(self) -> plan.Allotment:
        budget = self.cfg.budget_s(plan.PROFILE_BUDGET_S[self.profile])
        self.start_budget(budget)
        alloc = plan.plan_calibration(self.profile, self.target.size_bytes, self.r_est, budget)
        ws, fresh_len = alloc.ws_max, 0
        if self.target.kind == "file":
            fs = guard.fs_info(self.host, self.target.path.rsplit("/", 1)[0])
            self.margin = guard.file_margin(fs.total)
            fill_s = 60 if self.quick else plan.FILL_BUDGET_FRACTION * budget
            # the space the file could consume is what was free before it was preallocated
            ws, fresh_len = guard.file_ws_max(self.target.size_bytes, fs.free + self.target.size_bytes, self.margin, self.r_est, fill_s)
            start = ladder.align_up(ws)
            self.fresh = (start, start + ladder.align(fresh_len)) if fresh_len >= MIB and not self.quick else None
        elif not self.quick:
            self.fresh = ladder.fresh_extent(self.target.usable_bytes, ws)
        self.ws_planned = ladder.align(ws)
        self.allowed_writes.add(0, self.ws_planned)
        if self.fresh:
            self.allowed_writes.add(*self.fresh)
        self.rung_sizes = plan.rung_plan(self.ws_planned, quick=self.quick)
        self.budget_limited = self.target.kind == "device" and alloc.ws_max < int(0.85 * self.target.usable_bytes)
        return alloc

    # -- the fill ------------------------------------------------------------------------------------------
    def fill(self, alloc: plan.Allotment) -> None:
        """Sequential fill of ``[0, WS_max)`` in 10 s chunks; the extent is what fio reports written (IOB-CAL-02)."""
        pos, ws, k = 0, self.ws_planned, 0
        chunk = max(MIN_FILL_CHUNK, ladder.align(self.r_est * FILL_CHECK_S))
        started = self.host.now()
        deadline = started + alloc.seconds("fill")
        fill_budget_s = 60 if self.quick else plan.FILL_BUDGET_FRACTION * alloc.budget_s
        rates: list[float] = []
        replanned, aborted = False, None
        while pos < ws and self.host.now() < deadline:
            n = min(chunk, ws - pos)
            runtime = math.ceil(3 * n / self.r_est) + 10
            out = self.fio(self.job(f"fill-{k}", "write", MIB, 16, runtime, pos, n, io_size=n, metric="bw"))
            done = min(n, out.stats.io_bytes)
            rates += [v for _, v in out.series]
            pos += done
            k += 1
            if done == 0 or (done < n and out.stats.runtime_s < runtime - 1):
                break  # fio exited early: the extent is what it reported written
            if self.target.kind == "file" and guard.fs_info(self.host, self.target.path.rsplit("/", 1)[0]).free < self.margin:
                aborted = "free_space"
                break
            tail = rates[-10:]
            if not replanned and len(tail) >= 10 and statistics.median(tail) < FILL_REPLAN_FACTOR * self.r_est:
                replanned, self.r_est = True, statistics.median(tail)
                ws = min(ws, ladder.align(pos + self.r_est * max(0.0, fill_budget_s - (self.host.now() - started))))
                chunk = max(MIN_FILL_CHUNK, ladder.align(self.r_est * FILL_CHECK_S))
        if pos == 0:
            raise RunFailed("the fill wrote no bytes")
        self.ws = pos
        self.read_extent.add(0, pos)
        self.rung_sizes = plan.rung_plan(pos, quick=self.quick)  # rungs above the actual extent are never planned
        self.touch.maps["fill"] = [pos / 8.0] * 8
        self.fill_info = {"fill_bps": pos / max(self.host.now() - started, 1e-9), "fill_replanned": replanned, "fill_aborted": aborted,
                          "bytes": pos, "planned_bytes": self.ws_planned}
        if pos < self.ws_planned or replanned or aborted or self.budget_limited:
            self.budget_limited = True
            self.limits.append("limited-by-budget")

    # -- E-cold (IOB-CCH-01) ----------------------------------------------------------------------------
    def e_cold(self) -> None:
        cold_len = ladder.align(min(COLD_MAX, COLD_SHARE * self.ws))
        half = ladder.align(cold_len / 2)
        if half < MIB:
            raise S.SeriesError("the cold region is smaller than 1 MiB")
        qd32 = self.run_job("ecold", "randread", 4096, 32, E_COLD_QD32_S, 0, half, key="e_cold")
        qd1 = self.run_job("ecold-qd1", "randread", 4096, 1, HIST_S, half, cold_len - half, key="e_cold", hist=True, log_avg_ms=1000)
        counts = cache.aggregate_hist(qd1.hist)
        self.cold_raw = {"reading": self.reading(qd32), "modes": cache.modes(counts), "region_bytes": cold_len}
        self.evidence["histograms"]["e_cold_qd1"] = sparse(counts)

    # -- the ladder ----------------------------------------------------------------------------------------
    def rung(self, ws: int, direction: str, qd: int, runtime: int = RUNG_S, tag: str = "") -> dict[str, Any]:
        rw = "randread" if direction == "read" else "randwrite"
        label = f"rung-{direction[0]}{qd}-{ws // MIB}{tag}"
        out = self.run_job(label, rw, 4096, qd, runtime, 0, ws, key="ladder")
        rec = ladder.rung_record(out, ws)
        if rec["state"] == "unsteady" and qd == 32 and self.fits(EXTEND_S + 60):
            out = self.run_job(label + "-ext", rw, 4096, qd, EXTEND_S, 0, ws, key="ladder")
            rec = {**ladder.rung_record(out, ws), "extended": True}
        self.little.append(plausibility.pla_06(out.stats.iops, out.stats.clat_mean_us, qd))
        self.last_started = out.started
        return rec

    def hit_ref(self) -> None:
        out = self.run_job("hitref", "randread", 4096, 1, HIST_S, 0, min(GIB, self.ws), key="ladder", hist=True, log_avg_ms=1000)
        counts = cache.aggregate_hist(out.hist)
        self.hit_ref_us = cache.hist_percentile_us(counts, 50)
        self.evidence["histograms"]["hit_ref"] = sparse(counts)

    def e2(self, ws: int, qd32_started: float) -> None:
        """E2 follows the largest rung's QD32 read run immediately (both start timestamps are recorded, IOB-CCH-03)."""
        out = self.run_job("e2-qd1", "randread", 4096, 1, HIST_S, 0, ws, key="ladder", hist=True, log_avg_ms=1000)
        counts = cache.aggregate_hist(out.hist)
        self.e2_raw = {"modes": cache.modes(counts), "qd32_start": qd32_started, "qd1_start": out.started}
        self.evidence["histograms"]["e2_qd1"] = sparse(counts)

    def depth_runs(self, ws: int) -> None:
        for direction in ("read", "write"):
            for qd in (1, 8) + ((64,) if self.profile == "thorough" else ()):
                rec = self.rung(ws, direction, qd, RUNG_S, "-d")
                self.depth[direction][qd] = rec["steady_iops"]
                if rec["state"] == "unsteady":
                    self.depth_unsteady.add(direction)
                if qd == 1:
                    self.qd1_p99[direction] = rec["lat_us"]["p99"]

    def run_ladder(self) -> None:
        queue = list(self.rung_sizes)
        largest = queue[-1]
        densified = False
        self.reference("r0")
        self.ref_tag = "r0"
        while queue:
            ws = queue.pop(0)
            if len(self.reads) >= MIN_RUNGS and not self.fits(2 * RUNG_S + 30):
                self.budget_limited = True
                self.limits.append("limited-by-budget")
                break
            if not self.fits(2 * RUNG_S + 30):
                raise RunFailed("the budget allowed fewer than three ladder rungs")
            rec = self.rung(ws, "read", 32)
            self.reads.append(rec)
            if ws == largest:
                self.depth["read"][32] = rec["steady_iops"]
                if rec["state"] == "unsteady":
                    self.depth_unsteady.add("read")
                if not self.quick:
                    # E2 is an optional estimator (CCH-03): a missing or unusable histogram is recorded, never a failed calibration
                    started = self.last_started
                    self.stage("E2", lambda: self.e2(ws, started), min_s=HIST_S)
                    self.stage("ladder-qd", lambda: self.depth_runs(ws), min_s=4 * RUNG_S)
            self.writes.append(self.rung(ws, "write", 32))
            if len(self.writes) == 1 and not self.quick:
                # the smallest rung has been read AND written: a QD1 run on it is the cache-HIT reference (hit_ref_us)
                self.stage("hit_ref", self.hit_ref, min_s=HIST_S)
            if ws == largest:
                self.depth["write"][32] = self.writes[-1]["steady_iops"]
                if self.writes[-1]["state"] == "unsteady":
                    self.depth_unsteady.add("write")
            if not densified and len(self.reads) > MIN_RUNGS:
                ref = statistics.median(r["steady_iops"] for r in self.reads[:MIN_RUNGS])
                if rec["steady_iops"] < ladder.CLIFF_RATIO * ref:
                    densified = True
                    # the median of the first three rungs is one of them, so at least one rung is at or above the threshold
                    good = [r["ws_bytes"] for r in self.reads if r["steady_iops"] >= ladder.CLIFF_RATIO * ref]
                    have = {r["ws_bytes"] for r in self.reads} | set(queue)
                    queue = sorted({*queue, *(d for d in plan.densification_rungs(max(good), largest) if d not in have)})
        self.close_group("ladder", "r1")

    # -- fresh vs rewrite, rearm, observation ---------------------------------------------------------------
    def fresh_cell(self, name: str, extent: tuple[int, int] | None = None) -> FioResult:
        lo, hi = extent or self.fresh
        return self.run_job(name, "randwrite", 4096, 32, RUNG_S, lo, hi - lo, key="fresh")

    def fresh_vs_rewrite(self) -> None:
        if self.fresh is None:
            self.fresh_note = "no_fresh_extent"
            raise StageSkipped("no_fresh_extent", "no fresh extent")
        if not self.discard(self.fresh):
            self.fresh_note = "discard_unsupported"
            raise StageSkipped("discard_unsupported", "the target refused a discard / hole punch")
        self.fresh_ok = True
        fresh = self.reading(self.fresh_cell("fresh"))
        rewrite = self.reading(self.run_job("rewrite", "randwrite", 4096, 32, RUNG_S, 0, self.ws, key="ladder"))
        self.discard(self.fresh)
        mount_discard = guard.fs_info(self.host, self.target.path.rsplit("/", 1)[0]).mount_discard if self.target.kind == "file" else None
        self.fvr = ladder.fresh_vs_rewrite(self.target.kind, rewrite.steady, fresh.steady, mount_discard)
        self.close_group("fresh_vs_rewrite", "r2")
        self.checks.append(plausibility.pla_13(fresh.steady, rewrite.steady, self.moved_dirs["fresh_vs_rewrite"]["write"]))

    def rearm(self) -> None:
        if not self.fresh_ok:
            raise S.SeriesError("no fresh extent to re-arm")
        n = ladder.align(min(REARM_RANGE, self.r_est * 10))
        rng = (self.fresh[0], self.fresh[0] + n)
        self.run_job("rearm-prep", "write", MIB, 16, math.ceil(3 * n / self.r_est) + 10, rng[0], n, key="fresh", metric="bw", io_size=n)
        before = self.reading(self.fresh_cell("rearm-before", rng)).steady
        self.discard(rng)
        after = self.reading(self.fresh_cell("rearm-after", rng)).steady
        self.discard(self.fresh)
        self.close_group("rearm", "r3")
        self.rearm_info = ladder.rearm_verdict(before, after, self.moved_dirs["rearm"]["write"])

    def observation(self) -> None:
        plan_s = ladder.observation_plan(self.profile)
        w = self.run_job("obs-write", "randwrite", 4096, OBS_QD, plan_s["write"], 0, self.ws, key="ladder", log_avg_ms=1000)
        self.obs["write"] = self.reading(w, 0.0)
        r = self.run_job("obs-read", "randread", 4096, OBS_QD, plan_s["read"], 0, self.ws, key="ladder", log_avg_ms=1000)
        self.obs["read"] = self.reading(r, 0.0)
        if self.fresh_ok:
            self.discard(self.fresh)
            f = self.fresh_cell_obs(plan_s["fresh"])
            self.obs["fresh"] = self.reading(f, 0.0)
            self.discard(self.fresh)

    def fresh_cell_obs(self, seconds: int) -> FioResult:
        lo, hi = self.fresh
        return self.run_job("obs-fresh", "randwrite", 4096, OBS_QD, seconds, lo, hi - lo, key="fresh", log_avg_ms=1000)

    # -- S6 ------------------------------------------------------------------------------------------------
    def s6(self) -> None:
        if not self.quick:
            # the "before" reference of the shape group is taken NOW: the one from the end of rearm is several minutes of heavy writes old
            # and a draining bucket would make a healthy disk look like a moved ceiling (review B-S3)
            self.reference("r3s")
            self.ref_tag = "r3s"
        for n, cell in enumerate(shape.grid_plan(quick=self.quick)):
            if not self.fits(cell.seconds + 5):
                self.budget_limited = True
                self.limits.append("limited-by-budget")
                break  # never decide on a partial grid beyond what the budget allows; the fit sees fewer cells
            if cell.block_state == "fresh":
                if not self.fresh_ok:
                    continue
                self.discard(self.fresh)
                lo, hi = self.fresh
            else:
                lo, hi = 0, self.ws
            out = self.run_job(f"s6-{n}", cell.rw, cell.bs, cell.qd, cell.seconds, lo, hi - lo, key="ladder",
                               log_avg_ms=1000 if cell.seconds < RUNG_S else 2000)
            self.cells.append(shape.cell_record(out, cell))
            if cell.block_state == "fresh":
                self.discard(self.fresh)
        if not self.quick:
            self.close_group("shape", "r4")

    # -- S7 ------------------------------------------------------------------------------------------------
    def s7(self) -> None:
        for direction in ("read", "write"):
            ceiling = self.bases[direction]["iops"]
            if ceiling is None:
                continue
            for pct in (S7_QUICK_POINTS if self.quick else S7_POINTS):
                if not self.fits(S7_S + 5):
                    self.limits.append("limited-by-budget")
                    return
                offered = max(1, int(ceiling * pct / 100))
                out = self.run_job(f"s7-{direction[0]}{pct}", "randread" if direction == "read" else "randwrite", 4096, 32, S7_S,
                                   0, self.ws, key="ladder", rate_iops=offered)
                lat = devio_lat(out)
                self.curve.append({"dir": direction, "load_pct": pct, "offered_iops": offered, "achieved_iops": out.stats.iops,
                                   "bps": out.stats.bw_bps, "lat_us": lat, "not_reached": out.stats.iops < REACHED * offered})

    # -- analysis ----------------------------------------------------------------------------------------
    def analyze(self) -> None:
        cliff, cached_ref, classes = ladder.classify_rungs(self.reads)
        writes = sorted(self.writes, key=lambda r: r["ws_bytes"])
        for w in writes:
            w["class"] = "write"
        largest = classes[-1]
        cold = None
        if self.cold_raw is not None:
            cold = cache.cold_probe(self.cold_raw["reading"], self.cold_raw["modes"], self.hit_ref_us, self.cold_raw["region_bytes"])
        e2 = None
        if self.e2_raw is not None:
            e2 = cache.e2_estimate(largest["steady_iops"], self.e2_raw["modes"], self.e2_raw["qd32_start"], self.e2_raw["qd1_start"])
        before = [r["steady_iops"] for r in classes if r["class"] == "cached_tier"]
        post = [(r["ws_bytes"], r["steady_iops"]) for r in classes if r["class"] == "post_cliff" and r["state"] in ("steady", "flat")]
        e1 = cache.e1_fit(post, statistics.median(before) if before else None)
        state = cache.classify_cache(cold, e2, cliff_bytes=cliff, largest_rung=largest["ws_bytes"], quick=self.quick,
                                    cached_ref=cached_ref, cache_excludable=self.target.kind == "device")
        if self.target.kind == "file" and state.state == "cold_hit_like" and cliff is None:
            # a file target is bounded by --size and free space: its cliff is unreachable (IOB-TGT-10), so the honest reason is the margin
            state = replace(state, reasons=("device_smaller_than_cache_margin",))
        value = cache.read_value(state, largest["steady_iops"], e1)
        best = ladder.best_read_rung(classes)
        self.a = {"cliff": cliff, "cached_ref": cached_ref, "classes": classes, "writes": writes, "largest": largest, "cold": cold, "e2": e2,
                  "e1": e1, "state": state, "read_value": value, "best": best,
                  "headroom": best / state.r if state.status == "proven" and best is not None else None}
        extras = (["budget_limited"] if self.budget_limited else []) + (["ceiling_moved"] if self.moved.get("ladder") else [])
        unsteady = "unsteady" if "unsteady" in (largest["state"], writes[-1]["state"]) else "steady"
        self.checks += [plausibility.pla_01(unsteady),
                        plausibility.pla_02(largest["ws_bytes"], cliff, self.budget_limited),
                        plausibility.pla_03(ladder.cached_tier_factor(classes)),
                        plausibility.pla_04([f"{r['dir']}-{r['ws_bytes'] // MIB}M" for r in classes + writes
                                             if r["mean_iops"] > plausibility.MEAN_REPORT_RATIO * r["steady_iops"]]),
                        plausibility.pla_05([f"{r['dir']}-{r['ws_bytes'] // MIB}M" for r in classes + writes if r["rate_limited"] is True]),
                        plausibility.pla_10(state.state, list(state.reasons))]
        failing = [c for c in self.little if c.status == "fail"]
        self.checks.append(replace(failing[0], detail="; ".join(c.detail for c in failing)) if failing else self.little[0])
        # read basis
        if largest["state"] == "unsteady":
            read = _basis(None, [], "basis_unsteady")
        else:
            read = _basis(value.value, [*value.reasons, *extras])
        # write basis (IOB-CAL-07, IOB-CCH-07): the cap and its time proof come from the SAME series
        wl = writes[-1]
        if wl["state"] == "unsteady":
            write = _basis(None, [], "basis_unsteady")
            self.proof = {}
        else:
            rewrite = self.fvr["rewrite_iops"] if self.fvr else wl["steady_iops"]
            iops = ladder.write_iops_basis(rewrite, self.fvr)
            fresh_counts = bool(self.fvr) and ladder.fresh_in_basis(self.fvr)
            reasons: list[str] = list(extras)
            if self.fvr and self.moved_dirs.get("fresh_vs_rewrite", {}).get("write"):
                reasons.append("ceiling_moved")  # the reference of the fresh-vs-rewrite pair moved: that comparison is unproven (REF-02)
            self.proof = {}
            inputs: dict[str, Any] = {"rewrite_iops": rewrite, "fresh_iops": self.fvr["fresh_iops"] if fresh_counts else None}
            if self.quick:
                reasons.append("quick_profile")
            elif "write" not in self.obs:
                reasons.append("budget_limited")
            else:
                # the 30 s QD32 runs can sit wholly inside a burst that the 240 s observation outlives: the observation's own steady
                # rate bounds the cap, so the proven quantity and the capped quantity are one series (review B-B3)
                self.proof["rewrite"] = cache.write_time_proof(self.obs["write"], 4096)
                bounds = [self.obs["write"].steady]
                inputs["obs_write_steady"] = self.obs["write"].steady
                if fresh_counts:
                    if "fresh" in self.obs:
                        self.proof["fresh"] = cache.write_time_proof(self.obs["fresh"], 4096)
                        bounds.append(self.obs["fresh"].steady)
                        inputs["obs_fresh_steady"] = self.obs["fresh"].steady
                    else:
                        reasons.append("short_observation")  # the fresh half was never time-proven (the observation failed or was cut)
                iops = min(iops, *bounds)
                if not all(p.proven for p in self.proof.values()):
                    reasons.append("short_observation")
            if not self.quick and self.fvr is None:
                reasons.append(self.fresh_gap_reason())
            write = _basis(iops, reasons)
            write["inputs"] = inputs
        self.write_proven = write["status"] == "proven"
        self.bases = {"read": {"iops": read["value"], **read}, "write": {"iops": write["value"], **write}}

    # -- run-level plausibility checks that need S6 and the history (IOB-PLA-07, -09, -17, -18) -------------------------
    def cell_bps(self, direction: str, pattern: str, bs: int, block_state: str = "rewrite") -> float | None:
        cell = next((c for c in self.cells if (c["dir"], c["pattern"], c["bs"], c["block_state"]) == (direction, pattern, bs, block_state)
                     and not c["repeat"] and shape.certified(c)), None)
        return None if cell is None else cell["steady_bps"]

    def bytes_checks(self, history: dict[str, Any]) -> list[plausibility.Check]:
        """PLA-07 (random-4K bytes/s vs sequential, per direction), PLA-09 (fill vs sequential rewrite), PLA-17 (steady vs the ceiling
        history), PLA-18 (fresh sequential bytes/s).  Each is evaluated from the run's own measurements; none is a unit-only function."""
        # a bytes comparison of one direction is judged by that direction's reference; without the closing reference of the S6 group
        # (quick profile, or S6 cut by the budget) "unmoved" cannot be asserted, so the reference-dependent checks are not applicable
        moved_by = self.moved_dirs.get("shape")
        p7 = []
        for d in ("read", "write"):
            rand, seq = self.cell_bps(d, "rand", 4096), self.cell_bps(d, "seq", MIB)
            if rand is not None and seq is not None and moved_by is not None:
                p7.append((d, plausibility.pla_07(rand, seq, moved_by[d])))
        p9 = []
        seq_w = self.cell_bps("write", "seq", MIB)
        if seq_w is not None and self.fill_info.get("fill_bps") and moved_by is not None:
            p9 = [("write", plausibility.pla_09(self.fill_info["fill_bps"], seq_w, moved_by["write"]))]
        p17 = []
        for d in ("read", "write"):
            if d in self.obs:
                median = history.get(d, {}).get("rand4k", {}).get("median")
                p17.append((d, plausibility.pla_17(self.obs[d].steady * 4096, median)))
        fresh_w = self.cell_bps("write", "seq", MIB, "fresh")
        rearm = self.rearm_info["rearm_ratio"] if self.rearm_info else None
        p18 = []
        if fresh_w is None or seq_w is None:
            p18 = [("write", plausibility.pla_18(fresh_w, seq_w, False, rearm))]  # not_applicable: the check names what is missing
        elif moved_by is not None:
            p18 = [("write", plausibility.pla_18(fresh_w, seq_w, moved_by["write"], rearm))]
        return [worst_check(cid, found, f"{cid} was not evaluated: " + why) for cid, found, why in (
            ("IOB-PLA-07", p7, "random-4K and sequential 1 MiB cells are missing"), ("IOB-PLA-09", p9, "no fill rate or sequential rewrite cell"),
            ("IOB-PLA-17", p17, "no observation run"), ("IOB-PLA-18", p18, "no fresh sequential cell or no closing reference"))]

    def fresh_gap_reason(self) -> str:
        """Why the fresh-block penalty is absent from the write cap on a standard run (an unproven reason, never silence)."""
        return "budget_limited" if self.stages.get("fresh-vs-rewrite") == "skipped: budget" else "fresh_not_measured"

    def fresh_unmeasured_note(self) -> str:
        """The real cause recorded in ``fresh_vs_rewrite.reason`` when it was not measured."""
        if self.quick:
            return "quick_profile"
        if self.fresh_note:
            return self.fresh_note
        status = self.stages.get("fresh-vs-rewrite", "skipped")
        return "budget" if status == "skipped: budget" else ("stage_failed" if status.startswith("failed") else "stage_skipped")

    def bps_bases(self) -> dict[str, Any]:
        def cell(direction: str) -> dict[str, Any] | None:
            return next((c for c in self.cells if c["dir"] == direction and c["pattern"] == "seq" and c["bs"] == MIB
                         and c["block_state"] == "rewrite" and not c["repeat"] and c["state"] in ("steady", "flat")), None)

        ratio = None
        if self.a["state"].status == "proven":
            ratio = self.a["state"].r / self.a["largest"]["steady_iops"]
        # the reasons are the real ones of the basis the figure rests on (quick profile, undetermined cache, budget, time proof ...)
        write_reasons = tuple(self.bases["write"].get("reasons", ())) or (() if self.write_proven else ("short_observation",))
        out = {}
        for direction in ("read", "write"):
            b = ladder.bps_basis(direction, cell(direction), cache_ratio=ratio, read_reasons=tuple(self.a["state"].reasons),
                                 write_reasons=write_reasons)
            d = b.as_dict()
            extra = (["budget_limited"] if self.budget_limited else []) + (["ceiling_moved"] if self.moved.get("shape") else [])
            out[direction] = _basis(b.value, [*b.reasons, *extra]) if b.status != "absent" else d
        return out

    # -- documents -----------------------------------------------------------------------------------------
    def tool(self) -> dict[str, str]:
        return {"name": "iobench", "version": self.cfg.tool_version, "method": METHOD}

    def run_record(self, status: str, message: str, calibration_id: str | None) -> dict[str, Any]:
        run = {"id": self.run_id, "started": ceilings.iso(self.started), "finished": ceilings.iso(self.host.now()), "profile": self.profile,
               "status": status, "budget_s": self.budget.total_s if self.budget else None, "calibration_id": calibration_id,
               "stages": dict(self.stages)}
        if message:
            run["message"] = message
        return run

    def shape_doc(self) -> dict[str, Any]:
        if self.cells:
            return shape.shape_summary(self.cells, override=self.cfg.metering)
        und = {"read": "undetermined", "write": "undetermined"}
        return {"skipped": True, "stage_status": self.stages.get("S6", "skipped"), "cells": [], "fit": {}, "drift": {"spread": {}, "moved": False}, "metering_by_dir": und,
                "metering": "undetermined", "metering_detail": {}, "override": self.cfg.metering,
                "linear_model_fit_by_dir": {"read": "insufficient", "write": "insufficient"}, "linear_model_fit": "insufficient"}

    def qd90_doc(self) -> dict[str, Any]:
        return {d: (ladder.qd90(self.depth[d]) if {1, 8, 32} <= set(self.depth[d]) and d not in self.depth_unsteady else None) for d in ("read", "write")}

    def assemble(self, report: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        a = self.a
        digest = ceilings.identity_digest(self.identity)
        hist_path = res.paths(self.state_dir)["ceilings"]
        rows, _ = ceilings.load(self.host, hist_path, digest)
        history = ceilings.summary(rows, self.host.now())
        bps = self.bps_bases()
        shape_doc = self.shape_doc()
        self.checks += [plausibility.pla_11({d: shape_doc["linear_model_fit_by_dir"][d] for d in ("read", "write")})] if self.cells else []
        self.checks += self.bytes_checks(history)
        cap_basis = {d: {k: v for k, v in self.bases[d].items() if k != "iops"} for d in ("read", "write")}
        cap_basis["bps"] = bps
        windows = [c["steady_bps"] for c in self.cells if c["dir"] == "write" and c["pattern"] == "seq" and c["bs"] == MIB
                   and c["block_state"] == "rewrite" and c["state"] in ("steady", "flat")]
        state = a["state"]
        cal = {
            "id": self.calibration_id,
            "target": {"kind": self.target.kind, "path": self.target.path, "size_bytes": self.target.size_bytes,
                       "usable_bytes": self.target.usable_bytes, "reserved_tail_bytes": 1048576},
            "touch_map": self.touch.as_dict(), "extent_written": self.read_extent.as_list(),
            "fresh_extent": list(self.fresh) if self.fresh_ok else None, "fill_bps": self.fill_info["fill_bps"], "R_est_bps": self.r_est,
            "fill": self.fill_info,
            "ladder": {"rungs": a["classes"] + a["writes"], "cliff_bytes": a["cliff"], "cached_ref_iops": a["cached_ref"],
                       "qd90": self.qd90_doc(), "idle_qd1_p99_us": dict(self.qd1_p99), "cached_tier_factor": ladder.cached_tier_factor(a["classes"]),
                       "best_read_rung_iops": a["best"], "measured_largest": {"read": a["largest"]["steady_iops"], "write": a["writes"][-1]["steady_iops"]}},
            "cache": {"read": {**state.as_dict(), "e_cold": a["cold"].as_dict() if a["cold"] else None,
                               "E2": a["e2"].as_dict() if a["e2"] else None, "E1": a["e1"].as_dict(), "hit_ref_us": self.hit_ref_us,
                               "read_cache_headroom": a["headroom"],
                               "read_value": {"value": a["read_value"].value, "status": a["read_value"].status, "reasons": list(a["read_value"].reasons)}},
                      "write": {"time_proof": self.proof["rewrite"].as_dict() if "rewrite" in self.proof else None,
                                "fresh_proof": self.proof["fresh"].as_dict() if "fresh" in self.proof else None}},
            "cap_basis": cap_basis,
            "fresh_vs_rewrite": self.fvr or {"measured": False, "reason": self.fresh_unmeasured_note()},
            "rearm": self.rearm_info,
            "reference": {"cells": self.refs, "moved": self.moved, "moved_by_dir": self.moved_dirs},
            "shape": shape_doc,
            "latency_curve": self.curve,
            "latency_knee": {d: latency_knee(self.curve, d) for d in ("read", "write")},
            "windows": {"write_seq1m_bps": windows},
            "plateau_bps": {f"{d}/rand4k": self.obs[d].steady * 4096 for d in ("read", "write") if d in self.obs},
            "stages": dict(self.stages),
        }
        props = proposals.build_calibration_proposals(cal, self.cfg.targets, history)
        props = plausibility.apply_effects(props, self.checks)
        cal["proposals"] = props
        cal["iocost_keys"] = {k: (v["value"] if v["status"] != "absent" else None) for k, v in props["iocost_model"].items()}
        limits = self.final_limits(history)
        run_checks = [c.as_dict() for c in self.checks]
        quality = {"cache_proof": self.cache_proof(), "limits": limits, "checks": run_checks}
        tgt = dict(proposals.DEFAULT_TARGETS)
        targets = {**self.cfg.targets, "source": "default" if self.cfg.targets == tgt else "operator"}
        writeback = {n: proposals.absent("stage_skipped", r) for n, r in (("dirty_bytes", "R1"), ("dirty_background_bytes", "R1b"),
                                                                         ("nr_requests", "R2"), ("max_sectors_kb", "R3"))}
        evidence = {**self.evidence, "drain": {}, "sweep": {}, "read_cost": {}, "history_rotated": self.history_rotated,
                    "preflight": {**self.preflight, "slice_io_max_after": report.io_max_after}}
        result = {"schema": res.SCHEMA, "schema_minor": res.SCHEMA_MINOR, "tool": self.tool(),
                  "run": self.run_record("complete", "", self.calibration_id), "identity": self.identity, "targets": targets,
                  "evidence": evidence, "proposals": {"writeback": writeback, **props}, "quality": quality, "restore": report.as_dict()}
        calibration = {"schema": res.SCHEMA, "schema_minor": res.SCHEMA_MINOR, "tool": self.tool(),
                       "run": self.run_record("complete", "", self.calibration_id), "identity": self.identity,
                       "quality": quality, "calibration": cal}
        return calibration, result

    def final_limits(self, history: dict[str, Any]) -> list[str]:
        limits = list(self.limits)
        if self.quick:
            limits.append("quick-profile")
        if any(not p.proven for p in self.proof.values()):
            limits.append("short-observation")
        if history["history"] == "single-run":
            limits.append("single-run")
        if self.target.kind == "file":
            limits += ["filesystem_layer", "fresh_semantics_filesystem"]
        if self.obs:
            limits.append(ladder.observation_limit(max(r.observation_s for r in self.obs.values())))
        limits = proposals.dedupe(limits)
        assert all(proposals.limit_ok(x) for x in limits), limits
        return limits

    def cache_proof(self) -> str:
        if self.budget_limited:
            return "limited-by-budget"
        return "working-set" if self.a["state"].status == "proven" else "none"

    def failed_doc(self, status: str, message: str, report: Any) -> dict[str, Any]:
        return {"schema": res.SCHEMA, "schema_minor": res.SCHEMA_MINOR, "tool": self.tool(), "run": self.run_record(status, message, None),
                "identity": self.identity, "quality": {"cache_proof": "none", "limits": proposals.dedupe(self.limits), "checks": []},
                "restore": report.as_dict()}

    def conclude(self, status: str, message: str, code: int) -> Outcome:
        report = self.safety.report
        p = res.paths(self.state_dir)
        if not self.identity:
            return Outcome(status, code, message, None, None)
        if status != "complete":
            doc = self.failed_doc(status, message, report)
            res.write_failed(self.host, p["result"], doc)
            res.write_failed(self.host, p["calibration"], doc)
            return Outcome(status, code, message, doc, doc)
        digest = ceilings.identity_digest(self.identity)
        self.history_rotated = ceilings.rotate_foreign(self.host, p["ceilings"], digest, ceilings.iso(self.host.now()))
        calibration, result = self.assemble(report)
        res.write_json(self.host, p["calibration"], calibration)
        res.write_json(self.host, p["result"], result)
        ts = ceilings.iso(self.host.now())
        plateaus = [{"dir": d, "workload": "rand4k", "plateau_bps": self.obs[d].steady * 4096, "observation_s": self.obs[d].observation_s,
                     "state": self.obs[d].state} for d in ("read", "write") if d in self.obs]
        for obs in ceilings.run_observations(plateaus, ts):
            ceilings.append(self.host, p["ceilings"], obs, digest)
        for group, moved in self.moved.items():
            if moved:
                for d in ("read", "write"):
                    ceilings.append(self.host, p["ceilings"], plausibility.shift_observation(ts, d, self.refs[self.ref_tag][d]), digest)
        return Outcome(status, code, message, result, calibration)

    # -- the run ---------------------------------------------------------------------------------------------
    def body(self) -> None:
        self.s0(need_workdir=False)
        self.stage("S1", self.s1, required=True)
        alloc = self.make_plan()
        self.stage("fill", lambda: self.fill(alloc), required=True)
        if not self.quick:
            self.stage("E-cold", self.e_cold, min_s=alloc.seconds("E-cold"))
        self.stage("ladder", self.run_ladder, required=True)
        self.mark("reference", "ok")
        if not self.quick:
            self.stage("fresh-vs-rewrite", self.fresh_vs_rewrite, min_s=alloc.seconds("fresh-vs-rewrite"))
            if self.fresh_ok:
                self.stage("rearm", self.rearm, min_s=alloc.seconds("rearm"))
            else:
                self.skip("rearm", self.fresh_note or "no_fresh_extent")
            self.stage("observation", self.observation, min_s=alloc.seconds("observation") + alloc.seconds("fresh-observation"))
        self.analyze()
        self.stage("S6", self.s6, min_s=alloc.seconds("S6"))
        self.stage("S7", self.s7, min_s=alloc.seconds("S7"))




_RANK = {"fail": 3, "info": 2, "pass": 1, "not_applicable": 0}


def worst_check(check_id: str, found: list[tuple[str, plausibility.Check]], missing: str) -> plausibility.Check:
    """One stored check per id: the worst per-direction verdict, naming the direction(s) in the detail."""
    if not found:
        return plausibility.Check(check_id, "not_applicable", missing)
    best = max((c for _, c in found), key=lambda c: _RANK[c.status])
    detail = "; ".join(f"{d}: {c.detail}" for d, c in found)
    return replace(best, detail=detail)


def devio_lat(out: FioResult) -> dict[str, float]:
    lat = ladder.latency_ladder(out)
    return {"p50": lat["p50"], "p90": lat["p90"], "p95": lat["p95"], "p99": lat["p99"]}
