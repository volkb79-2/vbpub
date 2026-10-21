"""Matrix iteration (IOB-MAT-01..04, IOB-DRN-01, IOB-SWP-01..04, IOB-RDC-01): the fast path on a current calibration."""
from __future__ import annotations

import posixpath
from typing import Any

from . import ceilings, derive, devio, ladder, plan, plausibility, proposals, sweep
from . import result as res
from . import series as S
from .errors import Aborted, IobenchError, Refusal
from .extents import Extents
from .host import HostError
from .stages import GIB, MIB, Config, Outcome, Runner

LOAD_S = 60
PROBE_DURATION_S = 60
PROBE_SLACK_S = 30
PROBE_SLICE = "iobench-probe.slice"
WRITER_FILE = GIB
FSYNC_FILE = 256 * MIB
FSYNC_EVERY = 16
RDC_QD = 4
KNOB_FILES = {"dirty_bytes": "/proc/sys/vm/dirty_bytes", "dirty_background_bytes": "/proc/sys/vm/dirty_background_bytes"}


def disk_knob(disk: str, knob: str) -> str:
    return f"/sys/class/block/{disk}/queue/{knob}"


class Matrixer(Runner):
    def __init__(self, host: Any, cfg: Config) -> None:
        super().__init__(host, cfg, command="matrix")
        self.cal: dict[str, Any] = {}
        self.calibration_doc: dict[str, Any] = {}
        self.warnings: list[str] = []
        self.evidence: dict[str, Any] = {"drain": {}, "sweep": {}, "read_cost": {}}
        self.sweep_cells: list[dict[str, Any]] = []
        self.repeats: dict[str, dict[str, Any]] = {}
        self.decision: dict[str, Any] | None = None
        self.r_drain: float | None = None
        self.consistency: dict[str, Any] | None = None
        self.read_ratio: dict[str, float] = {}
        self.cands: list[int] = []
        self.knobs: dict[str, int] = {}
        self.base: dict[str, int] = {}
        self.t_eff = 0
        self.max_hw = 0
        self.ws = 0
        self.r_est = 0.0

    # -- MAT-01 / MAT-04 ------------------------------------------------------------------------------
    def require_calibration(self) -> None:
        chk = res.check(self.host, self.state_dir, calibration=True, now=self.host.now())
        if chk.state in ("absent", "stale", "corrupt", "aborted_only"):
            raise Refusal("no_calibration", f"no current calibration ({chk.state}{': ' + chk.reasons[0] if chk.reasons else ''})",
                          "run `iobench calibrate` first (it writes calibration.json)")
        self.warnings = [w for w in chk.warnings if w in ("recalibrate_recommended",)]
        self.calibration_doc = res.load(self.host, res.paths(self.state_dir)["calibration"], res.REQUIRED_CAL_TOP)
        self.cal = self.calibration_doc["calibration"]

    # -- helpers --------------------------------------------------------------------------------------
    def meminfo_kb(self, field_name: str) -> int:
        line = next(ln for ln in self.host.read_text("/proc/meminfo").splitlines() if ln.startswith(field_name + ":"))
        return int(line.split()[1])

    def dirty_now(self) -> int:
        return (self.meminfo_kb("Dirty") + self.meminfo_kb("Writeback")) * 1024

    def written_bytes(self) -> int:
        line = next(ln.split() for ln in self.host.read_text("/proc/diskstats").splitlines() if ln.split()[2] == self.workdir.disk_name)
        return int(line[9]) * 512

    def read_knob(self, knob: str) -> int:
        path = KNOB_FILES.get(knob) or disk_knob(self.workdir.disk_name, knob)
        return int(self.host.read_text(path).strip())

    def apply(self, knobs: dict[str, int]) -> bool:
        """Set the cell's knobs through the restore journal; ``False`` if the kernel rejects one."""
        wanted = {"dirty_bytes": knobs["dirty_bytes"], "dirty_background_bytes": knobs["dirty_bytes"] // 4,
                  "nr_requests": knobs["nr_requests"], "max_sectors_kb": knobs["max_sectors_kb"]}
        try:
            for knob, value in wanted.items():
                if self.read_knob(knob) != value:
                    self.safety.set_knob(KNOB_FILES.get(knob) or disk_knob(self.workdir.disk_name, knob), str(value))
        except OSError:
            return False
        return True

    def load_jobs(self, tag: str, seconds: int) -> tuple[devio.FioJob, devio.FioJob]:
        wd = self.cfg.work_dir
        bound = plan.volume_bound(self.r_est, seconds)
        names = [posixpath.join(wd, f"iobench-{self.run_id}-{tag}-{k}") for k in ("w", "f")]
        for n in names:
            self.safety.register_file(n)
        writer = self.job(f"{tag}-w", "write", MIB, RDC_QD, seconds, 0, WRITER_FILE, filename=names[0], io_size=min(bound, WRITER_FILE * seconds),
                          direct=False, log_avg_ms=1000)
        fsyncj = self.job(f"{tag}-f", "randwrite", 4096, 1, seconds, 0, FSYNC_FILE, filename=names[1], io_size=min(bound, FSYNC_FILE * 4),
                          direct=False, fsync=FSYNC_EVERY, log_avg_ms=1000)
        return writer, fsyncj

    # -- S3 ------------------------------------------------------------------------------------------------
    def s3(self) -> None:
        writer, fsyncj = self.load_jobs("s3", LOAD_S)
        t0 = self.host.now()
        handles = [self.spawn(writer), self.spawn(fsyncj)]
        samples = [self.written_bytes()]
        self.supervise(handles, lambda: samples.append(self.written_bytes()))
        self.finish(writer, handles[0], t0)
        self.finish(fsyncj, handles[1], t0)
        self.r_drain = sweep.drain_rate(samples)
        ladder_bps = (self.cal["cap_basis"]["bps"]["write"].get("value")
                      or (self.cal["cap_basis"]["write"].get("value") or 0) * 4096)
        self.consistency = sweep.rates_consistent(self.r_drain, ladder_bps) if ladder_bps else None
        self.evidence["drain"] = {"r_drain_bps": self.r_drain, "ladder_write_bps": ladder_bps, "seconds": LOAD_S, "consistency": self.consistency}

    def plan_base(self) -> None:
        """REF = the formula triple of R2, R3 (candidate) and R1; neighbours are single-knob changes (IOB-SWP-01)."""
        cur = {"dirty_bytes": self.read_knob("dirty_bytes"), "nr_requests": self.read_knob("nr_requests"),
               "max_sectors_kb": self.read_knob("max_sectors_kb")}
        self.max_hw = self.read_knob("max_hw_sectors_kb")
        ratio = int(self.host.read_text("/proc/sys/vm/dirty_ratio"))
        self.t_eff = derive.t_eff(ratio, self.meminfo_kb("MemAvailable") * 1024)
        qd90 = self.cal["ladder"]["qd90"]["write"]
        self.r2_value = derive.r2(qd90_write=qd90, current_nr_requests=cur["nr_requests"]) if qd90 is not None else None
        nr = cur["nr_requests"] if self.r2_value in (None, "kernel") else self.r2_value
        cand = derive.r3_candidate(self.r_drain, self.max_hw)
        sectors = cur["max_sectors_kb"] if cand >= self.max_hw else cand
        value, flags = derive.r1(W_s=self.cfg.targets["W_s"], R_drain_bps=self.r_drain, inflight_bytes=nr * sectors * 1024, T_eff_bytes=self.t_eff)
        dirty = (cur["dirty_bytes"] or self.t_eff) if value == "kernel" else value
        self.base = {"dirty_bytes": dirty, "nr_requests": nr, "max_sectors_kb": sectors}
        self.r1_flags, self.inflight = flags, nr * sectors * 1024
        self.r3_cand = cand

    # -- S4 sweep ---------------------------------------------------------------------------------------------
    def quiet_gap(self) -> None:
        hist: list[float] = []
        waited = 0
        while not sweep.quiet_gap_ended(hist, waited):
            hist.append(self.dirty_now())
            self.host.sleep(1.0)
            self._tick()
            waited += 1

    def run_cell(self, cell: sweep.SweepCell, tag: str, seconds: int = sweep.SWEEP_CELL_S) -> dict[str, Any]:
        if not self.apply(cell.knobs):
            return {"name": cell.name, "knobs": cell.knobs, "varies": cell.varies, "skipped": "knob_rejected"}
        self.quiet_gap()
        writer, fsyncj = self.load_jobs(tag, seconds)
        out_path = posixpath.join(self.cfg.work_dir, f".iobench-probe-{self.run_id}-{tag}")
        self.safety.register_file(out_path)
        self.safety.register_file(posixpath.join(self.cfg.work_dir, ".iobench-probe"))
        unit = devio.probe_unit_argv(f"iobench-probe-{self.run_id}-{tag}", PROBE_SLICE, seconds + PROBE_SLACK_S, sweep.PROBE_SOURCE,
                                     [self.cfg.work_dir, out_path, str(seconds), str(sweep.PROBE_INTERVAL_S)])
        t0 = self.host.now()
        handles = [self.spawn(writer), self.spawn(fsyncj), self.host.spawn(unit)]
        self.supervise(handles)
        self.finish(writer, handles[0], t0)
        out = self.finish(fsyncj, handles[1], t0)
        handles[2].wait()
        lats = [float(x) for x in self.host.read_text(out_path).split()]
        return sweep.cell_record(cell, lats, out.stats.iops, out.stats.pct_us["99.000000"])

    def s4(self) -> None:
        plan_cells = sweep.sweep_plan(self.base, self.max_hw, full=self.cfg.full_sweep)
        records = [self.run_cell(c, f"s4-{k}") for k, c in enumerate(plan_cells)]
        measured = [r for r in records if "skipped" not in r]
        self.sweep_cells = records
        if len(measured) < 3 or measured[0]["name"] != "REF" or measured[-1]["name"] != "REF2":
            raise S.SeriesError("the sweep lost a REF cell")
        decision = sweep.choose(measured, {})
        for name in decision["needs_repeat"][:2]:
            if self.fits(sweep.SWEEP_CELL_S + 60):
                cell = next(c for c in plan_cells if c.name == name)
                self.repeats[name] = self.run_cell(cell, f"s4-rep-{name}")
        self.decision = sweep.choose(measured, self.repeats)
        self.evidence["sweep"] = {"noise_floor_ratio": self.decision["noise_floor_ratio"], "cells": records, "repeats": self.repeats,
                                  "winners": {k: v["name"] for k, v in self.decision["winners"].items()}, "base": self.base}

    # -- S5 --------------------------------------------------------------------------------------------------------
    def read_job(self, name: str, seconds: int, *, direct: bool) -> devio.FioResult:
        return self.fio(self.job(name, "read", MIB, RDC_QD, seconds, 0, self.ws, direct=direct, log_avg_ms=1000))

    def s5(self) -> None:
        up = sweep.next_pow2_up(self.r3_cand, self.max_hw)
        self.cands = sweep.rdc_candidates([self.r3_cand, up], self.max_hw)
        bps: dict[int, float] = {}
        for kb in self.cands:
            self.apply({**self.base, "max_sectors_kb": kb})
            bps[kb] = self.read_job(f"rdc-{kb}", sweep.RDC_CELL_S, direct=True).stats.bw_bps
        native = bps[self.max_hw]
        self.read_ratio = {str(kb): sweep.read_ratio(v, native) for kb, v in bps.items()}
        buffered = {}
        for kb in (64, 256):
            self.apply({**self.base, "max_sectors_kb": kb})
            buffered[kb] = self.read_job(f"rdc-buf-{kb}", sweep.RDC_BUFFERED_S // 2, direct=False).stats.bw_bps
        self.evidence["read_cost"] = {"native_bps": native, "candidates": [{"max_sectors_kb": kb, "bps": v, "read_ratio": self.read_ratio[str(kb)]}
                                                                           for kb, v in bps.items()],
                                      "buffered": {"64": buffered[64], "256": buffered[256], "ratio": buffered[64] / buffered[256]}}

    # -- knob comparison (--knob NAME --candidates a,b[,c] [--repeat]) -----------------------------------------------
    def knob_compare(self) -> None:
        name = self.cfg.knob
        if name not in ("dirty_bytes", "nr_requests", "max_sectors_kb"):
            raise Refusal("knob_unknown", f"unknown knob {name!r}", "use dirty_bytes, nr_requests or max_sectors_kb")
        current = {"dirty_bytes": self.read_knob("dirty_bytes"), "nr_requests": self.read_knob("nr_requests"),
                   "max_sectors_kb": self.read_knob("max_sectors_kb")}
        self.max_hw = self.read_knob("max_hw_sectors_kb")
        ratio = int(self.host.read_text("/proc/sys/vm/dirty_ratio"))
        current["dirty_bytes"] = current["dirty_bytes"] or derive.t_eff(ratio, self.meminfo_kb("MemAvailable") * 1024)
        order = list(self.cfg.candidates) * (2 if self.cfg.repeat else 1)
        cells = []
        for k, value in enumerate(order):
            cell = sweep.SweepCell(f"{name}={value}", {**current, name: value}, name)
            cells.append(self.run_cell(cell, f"knob-{k}", plan.KNOB_CELL_S))
        measured = [c for c in cells if "skipped" not in c]
        decision = None
        if self.cfg.repeat and len(measured) == len(cells):
            n = len(self.cfg.candidates)
            first, again = cells[0], cells[n]
            decision = [sweep.decide(first, again, cells[i], cells[i + n]) for i in range(1, n)]
        self.evidence["sweep"] = {"knob": name, "candidates": list(self.cfg.candidates), "cells": cells, "decision": decision}

    # -- writeback proposals (R1..R4) --------------------------------------------------------------------------------
    def dependency(self, stage: str) -> str:
        return "stage_skipped" if self.stages.get(stage, "skipped").startswith("skipped") else "stage_failed"

    def writeback(self) -> dict[str, Any]:
        cfg = self.cfg
        p_us = cfg.targets["P_ms"] * 1000.0
        sweep_ok = self.stage_ok("S4")
        winners = self.decision["winners"] if sweep_ok else {}
        common = (["cross_disk"] if self.workdir.cross_disk else []) + (["noise_floor_high"] if sweep_ok and self.decision["noise_high"] else []) \
            + ([] if sweep_ok else ["formula_only"])
        qd90 = self.cal["ladder"]["qd90"]["write"]
        out: dict[str, Any] = {}
        cur_nr = self.read_knob("nr_requests")
        if qd90 is None:
            out["nr_requests"] = proposals.absent("basis_missing", "R2")
        else:
            inputs = {"qd90_write": qd90, "current_nr_requests": cur_nr}
            win = winners.get("nr_requests")
            out["nr_requests"] = proposals.value(win["knobs"]["nr_requests"] if win else derive.r2(**inputs), "R2", inputs,
                                                 "measured" if win else ("confirmed-by-sweep" if sweep_ok else "formula-only"), reasons=common)
        if not self.stage_ok("S3"):
            reason = self.dependency("S3")
            out.update({k: proposals.absent(reason, r) for k, r in (("dirty_bytes", "R1"), ("dirty_background_bytes", "R1b"), ("max_sectors_kb", "R3"))})
            return {k: out[k] for k in ("dirty_bytes", "dirty_background_bytes", "nr_requests", "max_sectors_kb")}
        drain = {"drain_below_ladder": ["drain_below_ladder"], "drain_above_ladder": ["cache_residual_possible"]}.get(
            (self.consistency or {}).get("verdict"), [])
        # the final triple: R2, R3 (with its read floor), then R1 from the resulting in-flight bytes
        nr = out["nr_requests"]["value"] if out["nr_requests"]["status"] != "absent" else cur_nr
        nr_eff = cur_nr if nr == "kernel" else nr
        if not self.stage_ok("S5"):
            sectors, sflags, s_inputs, s_out = None, [], {}, proposals.absent(self.dependency("S5"), "R3")
        else:
            probe = {str(c["knobs"]["max_sectors_kb"]): c["probe_p99_us"] for c in self.sweep_cells if "skipped" not in c} if sweep_ok else {}
            s_inputs = {"R_drain_bps": self.r_drain, "max_hw_kb": self.max_hw, "F": cfg.targets["F_read_ratio"], "P_us": p_us,
                        "read_ratio": self.read_ratio, "probe_p99_us": probe}
            sectors, sflags = derive.r3(**s_inputs)
            win = winners.get("max_sectors_kb")
            if win is not None and win["knobs"]["max_sectors_kb"] != sectors:
                sectors, sflags = win["knobs"]["max_sectors_kb"], [*sflags, "sweep_winner"]
                if self.read_ratio.get(str(sectors), 1.0) < cfg.targets["F_read_ratio"]:
                    sflags.append("read_floor_violated")
            s_out = None
        sectors_eff = self.read_knob("max_sectors_kb") if sectors == "kernel" or sectors is None else sectors
        d_inputs = {"W_s": cfg.targets["W_s"], "R_drain_bps": self.r_drain, "inflight_bytes": nr_eff * sectors_eff * 1024, "T_eff_bytes": self.t_eff}
        dirty, dflags = derive.r1(**d_inputs)
        dwin = winners.get("dirty_bytes")
        if dwin is not None:
            dirty, dflags = dwin["knobs"]["dirty_bytes"], [*dflags, "sweep_winner"]
        chosen = {"dirty_bytes": self.t_eff if dirty == "kernel" else dirty, "nr_requests": nr_eff, "max_sectors_kb": sectors_eff}
        r4 = derive.r4(chosen, [c for c in self.sweep_cells if "skipped" not in c], p_us) if sweep_ok else None
        missed = ["target_missed"] if r4 is not None and not r4["ok"] else []
        out["nr_requests"] = proposals.worsen(out["nr_requests"], missed)
        basis_d = "measured" if dwin else ("confirmed-by-sweep" if sweep_ok else "formula-only")
        out["dirty_bytes"] = proposals.value(dirty, "R1", d_inputs, basis_d, reasons=[*common, *drain, *missed], flags=dflags)
        out["dirty_background_bytes"] = proposals.value(derive.r1b(dirty_bytes=dirty), "R1b", {"dirty_bytes": dirty}, "ratio-inferred",
                                                        reasons=[*common, *drain, *missed])
        if s_out is not None:
            out["max_sectors_kb"] = s_out
        else:
            basis_s = "measured" if "sweep_winner" in sflags else ("confirmed-by-sweep" if sweep_ok else "measured")
            out["max_sectors_kb"] = proposals.value(sectors, "R3", s_inputs, basis_s, reasons=[*common, *drain, *missed], flags=sflags)
        if r4 is not None:
            self.evidence["sweep"]["r4"] = r4
        return {k: out[k] for k in ("dirty_bytes", "dirty_background_bytes", "nr_requests", "max_sectors_kb")}

    # -- documents ---------------------------------------------------------------------------------------------------
    def tool(self) -> dict[str, str]:
        return {"name": "iobench", "version": self.cfg.tool_version, "method": self.calibration_doc["tool"]["method"]}

    def run_record(self, status: str, message: str) -> dict[str, Any]:
        run = {"id": self.run_id, "started": ceilings.iso(self.started), "finished": ceilings.iso(self.host.now()), "profile": "matrix",
               "status": status, "budget_s": self.budget.total_s if self.budget else None,
               "calibration_id": self.cal["id"] if self.cal else None, "stages": dict(self.stages)}
        if message:
            run["message"] = message
        if self.warnings:
            run["warnings"] = list(self.warnings)
        return run

    def assemble(self, report: Any) -> dict[str, Any]:
        carried = {k: self.cal["proposals"][k] for k in ("caps", "iocost_model", "iocost_qos", "notes")}
        checks = list(self.calibration_doc["quality"]["checks"])
        if self.decision is not None:
            checks.append(plausibility.pla_12(self.decision["noise_floor_ratio"]).as_dict())
        limits = proposals.dedupe([*self.calibration_doc["quality"]["limits"], *self.limits])
        targets = {**self.cfg.targets, "source": "default" if self.cfg.targets == proposals.DEFAULT_TARGETS else "operator"}
        wb = self.writeback() if self.cfg.knob is None else {n: proposals.absent("stage_skipped", r) for n, r in (
            ("dirty_bytes", "R1"), ("dirty_background_bytes", "R1b"), ("nr_requests", "R2"), ("max_sectors_kb", "R3"))}
        evidence = {**self.evidence, "preflight": {**self.preflight, "slice_io_max_after": report.io_max_after}}
        return {"schema": res.SCHEMA, "schema_minor": res.SCHEMA_MINOR, "tool": self.tool(), "run": self.run_record("complete", ""),
                "identity": self.identity, "targets": targets, "evidence": evidence, "proposals": {"writeback": wb, **carried},
                "quality": {"cache_proof": self.calibration_doc["quality"]["cache_proof"], "limits": limits, "checks": checks},
                "restore": report.as_dict()}

    def conclude(self, status: str, message: str, code: int) -> Outcome:
        p = res.paths(self.state_dir)
        report = self.safety.report
        if not self.identity:
            return Outcome(status, code, message, None, None)
        if status != "complete":
            doc = {"schema": res.SCHEMA, "schema_minor": res.SCHEMA_MINOR, "tool": self.tool(), "run": self.run_record(status, message),
                   "identity": self.identity, "quality": {"cache_proof": "none", "limits": proposals.dedupe(self.limits), "checks": []},
                   "restore": report.as_dict()}
            res.write_failed(self.host, p["result"], doc)
            return Outcome(status, code, message, doc, None)
        result = self.assemble(report)
        res.write_json(self.host, p["result"], result)
        return Outcome(status, code, message, result, None)

    # -- the run -------------------------------------------------------------------------------------------------------
    def body(self) -> None:
        self.require_calibration()
        self.s0(need_workdir=True)
        self.r_est = self.cal["R_est_bps"]
        self.ws = self.cal["extent_written"][0][1]
        self.read_extent = Extents(self.cal["extent_written"])
        self.start_budget(self.cfg.budget_s(plan.PROFILE_BUDGET_S["knob" if self.cfg.knob else "matrix"]))
        if self.cfg.knob is not None:
            self.stage("S4", self.knob_compare, required=True)
            return
        self.stage("S3", self.s3, min_s=plan.S3_S)
        if self.stage_ok("S3"):
            self.plan_base()
            self.stage("S4", self.s4, min_s=plan.sweep_seconds(plan.sweep_cells(self.cfg.full_sweep)))
            self.stage("S5", self.s5, min_s=sweep.rdc_seconds(3))
        else:
            self.skip("S4", "S3")
            self.skip("S5", "S3")
