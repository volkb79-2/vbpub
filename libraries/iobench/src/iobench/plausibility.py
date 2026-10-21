"""Reference cells and the runtime plausibility checks (IOB-REF-01/02, IOB-PLA-01..18).

A check's ``fail`` degrades only the proposal groups it lists, through proposal statuses.  Data-integrity
checks (PLA-14, -15, -16) fail the whole run instead: they are raised as exceptions by the stages and surface
as ``run.status: failed`` plus ``*.last-failed.json``, never as a stored ``fail``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from . import ceilings, proposals
from .cache import MARGIN

MOVE_LIMIT = 0.15
LITTLE = (0.7, 1.3)
BYTES_TOLERANCE = 1.05
FILL_SEQ_FACTOR = 5.0
FRESH_FASTER = 1.5
OUTSIDE_HISTORY = 3.0
MEAN_REPORT_RATIO = 1.10  # reporting threshold only: the mean is never used (IOB-PLA-04)
STATUSES = ("pass", "fail", "info", "not_applicable")


def ref_moved(ref_before: float, ref_after: float) -> bool:
    """IOB-REF-01: the reference cell moved by more than 15 % across the comparison."""
    return abs(ref_after / ref_before - 1) > MOVE_LIMIT


def bracket(group: Sequence[Any], ref: Any) -> list[Any]:
    """``[ref, A, B, ..., ref]``: a comparison group bracketed by the reference cell."""
    return [ref, *group, ref]


def compare_group(values: Mapping[str, float], ref_before: float, ref_after: float) -> dict[str, Any]:
    """IOB-REF-02: a moved reference makes the comparison ``unproven: ceiling_moved``; both values stay in the evidence."""
    moved = ref_moved(ref_before, ref_after)
    return {"values": dict(values), "reference": {"before": ref_before, "after": ref_after}, "moved": moved,
            "reasons": ["ceiling_moved"] if moved else [], "finding": "reference_shift" if moved else None}


def shift_observation(ts: str, direction: str, ref_after_iops: float) -> dict[str, Any]:
    return ceilings.reference_shift(ts, direction, ref_after_iops * 4096)


@dataclass(frozen=True)
class Check:
    id: str
    status: str
    detail: str = ""
    degrades: tuple[str, ...] = ()
    effects: tuple[tuple[str, str, str], ...] = field(default=(), compare=False)  # (target, "absent"|"unproven", reason)
    value: Any = None

    def as_dict(self) -> dict[str, Any]:
        out = {"id": self.id, "status": self.status, "detail": self.detail, "degrades": list(self.degrades)}
        if self.value is not None:
            out["value"] = self.value
        return out


def pla_01(largest_rung_state: str) -> Check:
    if largest_rung_state == "unsteady":
        return Check("IOB-PLA-01", "fail", "the largest planned rung stayed unsteady after extension; no fallback to a smaller rung",
                     ("caps", "iocost_model", "iocost_qos"),
                     (("caps", "absent", "basis_unsteady"), ("iocost_model", "absent", "basis_unsteady"), ("iocost_qos", "absent", "basis_unsteady")))
    return Check("IOB-PLA-01", "pass", "the largest rung is steady")


def pla_02(largest_rung_bytes: int, cliff_bytes: float | None, limited_by_budget: bool) -> Check:
    if limited_by_budget:
        return Check("IOB-PLA-02", "info", "limited-by-budget: the working-set rule cannot be shown")
    if cliff_bytes is None:
        return Check("IOB-PLA-02", "info", "no cliff observed; the cache state is decided by the cold-region probe")
    if largest_rung_bytes >= MARGIN * cliff_bytes:
        return Check("IOB-PLA-02", "pass", "the largest rung is at least 1.2 x the cliff")
    return Check("IOB-PLA-02", "fail", "the largest rung is below 1.2 x the cliff: device_smaller_than_cache_margin", ())


def pla_03(cached_tier_factor: float) -> Check:
    return Check("IOB-PLA-03", "info", "small rung / largest rung", value=round(cached_tier_factor, 3))


def pla_04(overstating: Sequence[str]) -> Check:
    return Check("IOB-PLA-04", "info", "rungs whose mean overstates the steady value (the mean is never used): " + (", ".join(overstating) or "none"),
                 value=list(overstating))


def pla_05(flat_cells: Sequence[str]) -> Check:
    return Check("IOB-PLA-05", "info", "rate_limited (flat-line) series: " + (", ".join(flat_cells) or "none"), value=list(flat_cells))


def pla_06(iops: float, mean_clat_us: float, qd: int) -> Check:
    ratio = iops * mean_clat_us / 1e6 / qd
    if LITTLE[0] <= ratio <= LITTLE[1]:
        return Check("IOB-PLA-06", "pass", f"Little's law holds ({ratio:.2f})", value=round(ratio, 3))
    return Check("IOB-PLA-06", "fail", f"iops x mean clat / QD = {ratio:.2f} is outside [0.7, 1.3]: broken or faked fio; the rung is unusable",
                 ("caps",), (("caps", "absent", "stage_failed"),), value=round(ratio, 3))


def pla_07(rand_bps: float, seq_bps: float, moved: bool) -> Check:
    if rand_bps <= BYTES_TOLERANCE * seq_bps:
        return Check("IOB-PLA-07", "pass", "random-4K bytes/s do not exceed sequential bytes/s")
    if moved:
        return Check("IOB-PLA-07", "info", "random exceeds sequential but the reference moved: ceiling_moved", (), (("iocost_model", "unproven", "ceiling_moved"),))
    return Check("IOB-PLA-07", "fail", "random-4K bytes/s exceed sequential bytes/s by more than 5 % with an unmoved reference", ("caps", "iocost_model"),
                 (("caps:bps", "absent", "stage_failed"), ("iocost_model", "absent", "stage_failed")))


def pla_08(violations: Sequence[str]) -> Check:
    if violations:
        raise AssertionError("read outside the written extent: " + ", ".join(violations))  # a programming error fails the run
    return Check("IOB-PLA-08", "pass", "every read job is confined to the written extent (argv-level)")


def pla_09(fill_bps: float, seq_rewrite_bps: float, moved: bool) -> Check:
    ratio = max(fill_bps, seq_rewrite_bps) / min(fill_bps, seq_rewrite_bps)
    if ratio <= FILL_SEQ_FACTOR:
        return Check("IOB-PLA-09", "pass", f"fill and sequential rewrite differ {ratio:.1f}x")
    if moved:
        return Check("IOB-PLA-09", "info", f"fill and sequential rewrite differ {ratio:.1f}x but the reference moved: ceiling_moved",
                     (), (("caps:bps", "unproven", "ceiling_moved"),))
    return Check("IOB-PLA-09", "fail", f"fill and sequential rewrite differ {ratio:.1f}x with an unmoved reference", ("caps",),
                 (("caps:bps", "absent", "stage_failed"),))


def pla_10(cache_state: str, reasons: Sequence[str]) -> Check:
    return Check("IOB-PLA-10", "info", f"cache state {cache_state}" + (f" ({', '.join(reasons)})" if reasons else ""), value=cache_state)


def pla_11(fit_by_dir: Mapping[str, str]) -> Check:
    if "poor" in fit_by_dir.values():
        return Check("IOB-PLA-11", "fail", "the per-IO + per-byte linear fit is poor: shape_fit_poor", ("iocost_model", "iocost_qos"))
    return Check("IOB-PLA-11", "pass", "the linear fit is good or not assessed")


def pla_12(noise_ratio: float) -> Check:
    if noise_ratio > 4:
        return Check("IOB-PLA-12", "fail", f"sweep noise {noise_ratio:.1f} exceeds 4: noise_floor_high", ("writeback",), (("writeback", "unproven", "noise_floor_high"),))
    return Check("IOB-PLA-12", "pass", f"sweep noise {noise_ratio:.1f}")


def pla_13(fresh_iops: float, rewrite_iops: float, moved: bool) -> Check:
    if fresh_iops <= FRESH_FASTER * rewrite_iops:
        return Check("IOB-PLA-13", "pass", "fresh writes are not faster than rewrites")
    if moved:
        return Check("IOB-PLA-13", "info", "fresh faster than rewrite but the reference moved: ceiling_moved", (), (("caps:WIOPS", "unproven", "ceiling_moved"),))
    return Check("IOB-PLA-13", "fail", "fresh writes are more than 1.5 x faster than rewrites with an unmoved reference (impossible without a cache effect)",
                 ("caps",), (("caps:WIOPS", "absent", "stage_failed"),))


def pla_17(steady_bps: float, history_median: float | None) -> Check:
    if history_median is None:
        return Check("IOB-PLA-17", "not_applicable", "no ceiling history")
    ratio = max(steady_bps / history_median, history_median / steady_bps)
    if ratio > OUTSIDE_HISTORY:
        return Check("IOB-PLA-17", "info", f"steady differs from the ceiling median by {ratio:.1f}x (warning)", value=round(ratio, 2))
    return Check("IOB-PLA-17", "pass", "steady is within 3x of the ceiling median")


def pla_18(fresh_write_bps: float | None, rewrite_bps: float | None, moved: bool, rearm_ratio: float | None) -> Check:
    if fresh_write_bps is None or rewrite_bps is None:
        return Check("IOB-PLA-18", "not_applicable", "no fresh sequential 1 MiB cell")
    if moved:
        return Check("IOB-PLA-18", "info", "not_comparable: ceiling_moved", value={"fresh_write_bps": fresh_write_bps, "rearm_ratio": rearm_ratio})
    return Check("IOB-PLA-18", "info", "fresh sequential 1 MiB bytes/s beside the rewrite figure",
                 value={"fresh_write_bps": fresh_write_bps, "rewrite_bps": rewrite_bps, "rearm_ratio": rearm_ratio})


def apply_effects(props: dict[str, Any], checks: Sequence[Check]) -> dict[str, Any]:
    """Degrade proposals per the ``effects`` of the failed/informational checks (only what each check lists)."""
    out = {section: dict(group) for section, group in props.items() if section != "notes"}
    out["notes"] = props.get("notes", [])
    for check in checks:
        for target, kind, reason in check.effects:
            section, _, key = target.partition(":")
            group = out.get(section)
            if group is None:
                continue
            if section == "caps":
                inner = dict(group["dev_aggregate"])
                for name, item in inner.items():
                    if key == "" or key == name or (key == "bps" and name.endswith("BPS")):
                        inner[name] = proposals.absent(reason, "R5", item["inputs"]) if kind == "absent" else proposals.worsen(item, [reason])
                out["caps"] = {"dev_aggregate": inner}
            else:
                out[section] = {name: (proposals.absent(reason, item["rule"], item["inputs"]) if kind == "absent" else proposals.worsen(item, [reason]))
                                for name, item in group.items()}
    return out
