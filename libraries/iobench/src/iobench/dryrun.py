"""``--dry-run``: print each stage's allotment, its fio argv and its written-byte bound; write nothing (IOB-PRF-01)."""
from __future__ import annotations

import math
from typing import Any

from . import guard, ladder, plan, shape, sweep
from . import result as res
from .calibrate import HIST_S, MIN_FILL_CHUNK, OBS_QD, REF_S, RUNG_S, Calibrator
from .devio import FioJob
from .errors import Refusal
from .matrix import LOAD_S, Matrixer
from .stages import GIB, MIB, Config

COLD_QD32_S = 70


def rate_source(host: Any, cfg: Config) -> float:
    """R_est for a dry run: ``--assume-r-est``, else the existing calibration's S1 value; never invented."""
    if cfg.assume_r_est is not None:
        return cfg.assume_r_est
    state = res.state_dir_of(host, cfg.state_dir)
    path = res.paths(state)["calibration"]
    if host.exists(path):
        # a stored rate is a fact about ONE disk: another target's calibration, or one whose disk has changed, must not plan this run
        chk = res.check(host, state, calibration=True, now=host.now())
        if chk.state in ("stale", "corrupt", "aborted_only"):
            raise Refusal("dry_run_rate_unusable", f"the stored calibration cannot supply a write rate ({chk.state}{': ' + chk.reasons[0] if chk.reasons else ''})",
                          "pass --assume-r-est BYTES_PER_S or calibrate this disk again")
        doc = res.load(host, path, res.REQUIRED_CAL_TOP)["calibration"]
        if doc["target"]["path"] != cfg.target:
            raise Refusal("dry_run_rate_other_target", f"the stored calibration belongs to {doc['target']['path']}, not {cfg.target}",
                          "pass --assume-r-est BYTES_PER_S or calibrate this target first")
        return doc["R_est_bps"]
    raise Refusal("dry_run_rate", "a dry run cannot measure S1, so it has no write rate to plan with",
                  "pass --assume-r-est BYTES_PER_S or calibrate once first")


def entry(run: Any, job: FioJob) -> dict[str, Any]:
    bound = job.io_size if job.writes else None
    if job.writes and bound is None:
        bound = plan.volume_bound(run.r_est, job.runtime_s)
    return {"argv": job.argv(), "written_bytes_bound": bound}


def calibrate_plan(host: Any, cfg: Config) -> dict[str, Any]:
    run = Calibrator(host, cfg)
    run.target = guard.load_target(host, cfg.target)
    run.disk_name = run.target.disk_name or ""
    run.r_est = rate_source(host, cfg)
    alloc = run.make_plan()
    run.ws = run.ws_planned
    ws = run.ws
    first = run.job("s1", "write", MIB, 16, 20, 0, GIB, metric="bw", log_avg_ms=1000)
    chunk = max(MIN_FILL_CHUNK, ladder.align(run.r_est * 10))
    fills = [run.job(f"fill-{k}", "write", MIB, 16, math.ceil(3 * min(chunk, ws - k * chunk) / run.r_est) + 10, k * chunk,
                     min(chunk, ws - k * chunk), io_size=min(chunk, ws - k * chunk), metric="bw") for k in range(math.ceil(ws / chunk))]
    rungs = []
    for r in run.rung_sizes:
        rungs += [run.job(f"rung-r32-{r // MIB}", "randread", 4096, 32, RUNG_S, 0, r),
                  run.job(f"rung-w32-{r // MIB}", "randwrite", 4096, 32, RUNG_S, 0, r, io_size=run.bound(RUNG_S))]
    per_stage: dict[str, list[FioJob]] = {"S1": [first], "fill": fills, "ladder": rungs}
    if not run.quick:
        half = ladder.align(min(32 * GIB, 0.1 * ws) / 2)
        per_stage["E-cold"] = [run.job("ecold", "randread", 4096, 32, COLD_QD32_S, 0, half),
                               run.job("ecold-qd1", "randread", 4096, 1, HIST_S, half, half, hist=True, log_avg_ms=1000)]
        per_stage["E2"] = [run.job("hitref", "randread", 4096, 1, HIST_S, 0, min(GIB, ws), hist=True, log_avg_ms=1000),
                           run.job("e2-qd1", "randread", 4096, 1, HIST_S, 0, ws, hist=True, log_avg_ms=1000)]
        per_stage["ladder-qd"] = [run.job(f"rung-{d[0]}{qd}-d", "randread" if d == "read" else "randwrite", 4096, qd, RUNG_S, 0, ws,
                                          io_size=run.bound(RUNG_S) if d == "write" else None)
                                  for d in ("read", "write") for qd in (1, 8) + ((64,) if cfg.profile == "thorough" else ())]
        if run.fresh:
            lo, hi = run.fresh
            per_stage["fresh-vs-rewrite"] = [run.job("fresh", "randwrite", 4096, 32, RUNG_S, lo, hi - lo, io_size=run.bound(RUNG_S)),
                                             run.job("rewrite", "randwrite", 4096, 32, RUNG_S, 0, ws, io_size=run.bound(RUNG_S))]
            per_stage["fresh-observation"] = [run.job("obs-fresh", "randwrite", 4096, OBS_QD, ladder.observation_plan(cfg.profile)["fresh"], lo, hi - lo,
                                                      io_size=run.bound(ladder.observation_plan(cfg.profile)["fresh"]), log_avg_ms=1000)]
        obs = ladder.observation_plan(cfg.profile)
        per_stage["observation"] = [run.job("obs-write", "randwrite", 4096, OBS_QD, obs["write"], 0, ws, io_size=run.bound(obs["write"]), log_avg_ms=1000),
                                    run.job("obs-read", "randread", 4096, OBS_QD, obs["read"], 0, ws, log_avg_ms=1000)]
    per_stage["reference"] = [run.job(f"ref-{d}", rw, 4096, 8, REF_S, 0, min(GIB, ws), log_avg_ms=1000, io_size=run.bound(REF_S) if rw == "randwrite" else None)
                              for d, rw in (("read", "randread"), ("write", "randwrite"))]
    per_stage["S6"] = [run.job(f"s6-{n}", c.rw, c.bs, c.qd, c.seconds, 0, ws, io_size=run.bound(c.seconds) if c.dir == "write" else None)
                       for n, c in enumerate(shape.grid_plan(quick=run.quick)) if c.block_state == "rewrite"]
    stages = []
    for st in alloc.stages:
        jobs = per_stage.get(st.name, [])
        stages.append({"name": st.name, "allotted_s": st.allotted_s, "required": st.required, "jobs": [entry(run, j) for j in jobs],
                       "note": st.note or ("offered rates are percentages of the measured cap basis" if st.name == "S7" else "")})
    return {"command": "calibrate", "profile": cfg.profile, "budget_s": alloc.budget_s, "total_s": alloc.total, "r_est_bps": run.r_est,
            "ws_max_bytes": alloc.ws_max, "target": run.target.path, "stages": stages}


def matrix_plan(host: Any, cfg: Config) -> dict[str, Any]:
    run = Matrixer(host, cfg)
    run.require_calibration()
    run.target = guard.load_target(host, cfg.target)
    run.r_est = run.cal["R_est_bps"]
    run.ws = run.cal["extent_written"][0][1]
    wd = cfg.work_dir or "<work-dir>"
    bound = plan.volume_bound(run.r_est, LOAD_S)
    w = FioJob("s3-w", f"{wd}/iobench-load-w", "write", MIB, 4, LOAD_S, 0, GIB, min(bound, GIB * LOAD_S), direct=False, log_avg_ms=1000, prefix="/run/iobench/s3-w")
    f = FioJob("s3-f", f"{wd}/iobench-load-f", "randwrite", 4096, 1, LOAD_S, 0, 256 * MIB, min(bound, 1 * GIB), direct=False, fsync=16, log_avg_ms=1000,
               prefix="/run/iobench/s3-f")
    cells = plan.sweep_cells(cfg.full_sweep)
    stages = [{"name": "S0", "allotted_s": plan.S0_S, "jobs": []},
              {"name": "S3", "allotted_s": plan.S3_S, "jobs": [entry(run, w), entry(run, f)]},
              {"name": "S4", "allotted_s": plan.sweep_seconds(cells), "jobs": [entry(run, w.with_(name=f"s4-{k}-w", runtime_s=sweep.SWEEP_CELL_S)) for k in range(cells)]},
              {"name": "S5", "allotted_s": sweep.rdc_seconds(3), "jobs": [entry(run, FioJob(f"rdc-{kb}", run.target.path, "read", MIB, 4, sweep.RDC_CELL_S, 0, run.ws, None,
                                                                                           log_avg_ms=1000, prefix="/run/iobench/rdc")) for kb in (64, 128, 1280)]}]
    return {"command": "matrix", "budget_s": plan.matrix_seconds(), "total_s": sum(s["allotted_s"] for s in stages), "stages": stages}
