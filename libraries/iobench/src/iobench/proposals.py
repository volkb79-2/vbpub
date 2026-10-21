"""The status model (IOB-STA-01..05) and proposal assembly (IOB-DER-01..10, IOB-CAL-06).

Every proposed value carries its own ``status``: ``proven``, ``unproven`` (with reasons from a closed
vocabulary) or ``absent`` (with one reason).  A value is never stronger than its weakest basis.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from . import derive

UNPROVEN_REASONS = (
    "cache_model_undetermined", "cache_residual_possible", "no_cliff_hit_like_mode", "device_smaller_than_cache_margin",
    "ceiling_moved", "budget_limited", "short_observation", "quick_profile", "worst_from_single_run", "metering_undetermined",
    "operator_override", "shape_fit_poor", "noise_floor_high", "drain_below_ladder", "formula_only", "target_missed",
    "cross_disk", "identity_weak", "cache_not_excluded", "fresh_not_measured")
ABSENT_REASONS = ("stage_skipped", "stage_failed", "basis_unsteady", "basis_missing", "bps_basis_missing", "no_calibration")
CACHE_STATES = ("cold_measured", "rate_cap", "no_cache_observed", "cold_hit_like", "undetermined")
METERING = ("byte", "iops", "mixed", "undetermined")
FITS = ("good", "poor", "insufficient", "plateau")
LIMIT_FIXED = ("quick-profile", "limited-by-budget", "short-observation", "single-run", "filesystem_layer", "fresh_semantics_filesystem")
LIMIT_PATTERNS = (re.compile(r"^stage_skipped:S[0-9]$"), re.compile(r"^bucket_exclusion_s<=[0-9]+$"))
BASES = ("measured", "confirmed-by-sweep", "ratio-inferred", "formula-only", "single-run")
SECTIONS = ("writeback", "caps", "iocost_model", "iocost_qos")
WARNINGS = ("scheduler_changed", "ram_changed", "kernel_changed", "root_source_changed", "workdir_fs_changed",
            "older_than_180_days", "recalibrate_recommended", "last_run_aborted")
DEFAULT_TARGETS = {"W_s": 2.0, "P_ms": 250, "F_read_ratio": 0.25, "U_dev_share": 0.70}


def limit_ok(limit: str) -> bool:
    return limit in LIMIT_FIXED or any(p.match(limit) for p in LIMIT_PATTERNS)


def dedupe(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def value(val: Any, rule: str, inputs: Mapping[str, Any], basis: str, *, reasons: Iterable[str] = (),
          flags: Iterable[str] = ()) -> dict[str, Any]:
    """A proposal value; ``reasons`` empty means ``proven``."""
    reasons = dedupe(reasons)
    unknown = [r for r in reasons if r not in UNPROVEN_REASONS]
    if unknown or basis not in BASES:
        raise ValueError(f"outside the closed vocabulary: {unknown or basis}")
    out = {"value": val, "status": "unproven" if reasons else "proven", "rule": rule, "inputs": dict(inputs),
           "basis": basis, "flags": list(flags)}
    if reasons:
        out["reasons"] = reasons
    return out


def absent(reason: str, rule: str, inputs: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A value that cannot be proposed; ``value: 0`` is only a placeholder, the status is the truth."""
    if reason not in ABSENT_REASONS:
        raise ValueError(f"outside the closed vocabulary: {reason}")
    return {"value": 0, "status": "absent", "reason": reason, "rule": rule, "inputs": dict(inputs or {}), "basis": "measured", "flags": []}


def worsen(prop: dict[str, Any], reasons: Iterable[str]) -> dict[str, Any]:
    """A value is never stronger than its weakest basis: add reasons to an existing value (absent stays absent)."""
    reasons = list(reasons)
    if prop["status"] == "absent" or not reasons:
        return prop
    merged = dedupe([*prop.get("reasons", []), *reasons])
    unknown = [r for r in merged if r not in UNPROVEN_REASONS]
    if unknown:
        raise ValueError(f"outside the closed vocabulary: {unknown}")
    return {**prop, "status": "unproven", "reasons": merged}


def iter_values(group: Mapping[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    """Leaf proposal values of a section (``caps`` nests one level: ``dev_aggregate``)."""
    for key, item in group.items():
        if "status" in item:
            yield key, item
        else:
            yield from iter_values(item)


STATUSES = ("proven", "unproven", "absent")


def section_summary(group: Mapping[str, Any], *, identity_weak: bool = False) -> dict[str, Any]:
    """``{proven: n, unproven: [{key, reasons}], absent: [{key, reason}]}`` for ``check``.

    The status is whitelisted: anything that is not one of the three known strings is a programming error or a damaged file
    and raises ``ValueError`` (it is never counted as ``proven``).  A section without any value is not "nothing wrong"; the
    caller decides what an empty section means.
    """
    out: dict[str, Any] = {"proven": 0, "unproven": [], "absent": []}
    for key, item in iter_values(group):
        status = item["status"]
        if status not in STATUSES:
            raise ValueError(f"unknown proposal status {status!r} for {key}")
        if status == "absent":
            out["absent"].append({"key": key, "reason": item["reason"]})
        elif status == "unproven" or identity_weak:
            out["unproven"].append({"key": key, "reasons": dedupe([*item.get("reasons", []), *(["identity_weak"] if identity_weak else [])])})
        else:
            out["proven"] += 1
    return out


def annotate_apply(group: Mapping[str, Any]) -> dict[str, Any]:
    """A copy of a proposals section with ``apply`` (what the wizard may do) added to every value (IOB-STA-04)."""
    out: dict[str, Any] = {}
    for key, item in group.items():
        out[key] = {**item, "apply": apply_requirement(item)} if "status" in item else annotate_apply(item)
    return out


def apply_requirement(item: Mapping[str, Any]) -> str:
    """IOB-STA-04: what the wizard may do with a value."""
    return {"proven": "ok", "unproven": "requires_accept", "absent": "not_possible"}[item["status"]]


# -- caps (R5) -----------------------------------------------------------------------------
CAP_KEYS = {"RIOPS": ("read", "iops", "rand4k"), "WIOPS": ("write", "iops", "rand4k"),
            "RBPS": ("read", "bps", "seq1m"), "WBPS": ("write", "bps", "seq1m")}


def worst_over_median(history: Mapping[str, Any], direction: str, workload: str) -> tuple[float, list[str]]:
    entry = history.get(direction, {}).get(workload)
    if entry is None:
        return 1.0, ["worst_from_single_run"]
    return entry["worst"] / entry["median"], []


def build_caps(cal: Mapping[str, Any], targets: Mapping[str, Any], history: Mapping[str, Any]) -> dict[str, Any]:
    basis = cal["cap_basis"]
    u = targets["U_dev_share"]
    caps = {}
    for key, (direction, unit, workload) in CAP_KEYS.items():
        b = basis[direction] if unit == "iops" else basis["bps"][direction]
        if b["status"] == "absent":
            caps[key] = absent(b["reason"], "R5")
            continue
        womed, extra = worst_over_median(history, direction, workload)
        inputs = {"U": u, "worst_over_median": womed, "basis_iops" if unit == "iops" else "basis_bps": b["value"]}
        caps[key] = value(derive.r5(**inputs), "R5", inputs, "measured", reasons=[*b.get("reasons", []), *extra])
    return {"dev_aggregate": caps}


# -- model (R6) -----------------------------------------------------------------------------
def build_model(cal: Mapping[str, Any], history: Mapping[str, Any]) -> dict[str, Any]:
    shape = cal["shape"]
    out: dict[str, Any] = {}
    for direction in ("read", "write"):
        p = direction[0]
        names = (f"{p}bps", f"{p}seqiops", f"{p}randiops")
        fit = shape["linear_model_fit_by_dir"][direction]
        bps_basis = cal["cap_basis"]["bps"][direction]
        iops_basis = cal["cap_basis"][direction]
        if shape.get("skipped"):
            why = "stage_failed" if str(shape["stage_status"]).startswith("failed") else "stage_skipped"
            out.update({n: absent(why, "R6-model", {"key": n}) for n in names})
            continue
        if fit != "good":
            out.update({n: absent("stage_failed", "R6-model", {"key": n, "linear_model_fit": fit}) for n in names})
            continue
        if bps_basis["status"] == "absent":
            out.update({n: absent(bps_basis["reason"], "R6-model", {"key": n}) for n in names})
            continue
        hist_bps = history.get(direction, {}).get("seq1m", {}).get("worst")
        hist_iops = history.get(direction, {}).get("rand4k", {}).get("worst")
        reasons = list(bps_basis.get("reasons", []))
        basis_tag = "measured"
        if hist_bps is None:
            reasons.append("worst_from_single_run")
            basis_tag = "single-run"
        # S6 reads ran on the cache-basis working set: their fitted costs inherit the read cache state (review B-B6)
        cache_ratio = 1.0
        if direction == "read":
            cache = cal["cache"]["read"]
            if cache["status"] == "proven" and cache["R_iops"]:
                cache_ratio = min(1.0, cache["R_iops"] / cal["ladder"]["measured_largest"]["read"])
            else:
                reasons.append("cache_residual_possible")
        metering = shape["metering_by_dir"][direction]
        if metering == "undetermined":
            reasons.append("metering_undetermined")
        if shape.get("override"):
            reasons.append("operator_override")
        fits = shape["fit"]
        seq, rand = fits.get(f"{direction}-seq-rewrite", {}), fits.get(f"{direction}-rand-rewrite", {})
        if metering not in ("byte", "iops") and not all((x or 0) > 0 for x in (seq.get("c_io_us"), rand.get("c_io_us"), seq.get("c_byte_ns"))):
            # the fit gave no positive per-IO / per-byte cost, so there is nothing to derive from it: never a guess
            out.update({n: absent("stage_failed", "R6-model", {"key": n, "metering": metering}) for n in names})
            continue
        # DER-05: the WORST plateau; without history that is the run's lowest steady window (a draining bucket makes the first cell the highest)
        windows = cal["windows"]["write_seq1m_bps"] if direction == "write" else []
        worst_bps = min([bps_basis["value"], *windows, *([] if hist_bps is None else [hist_bps])])
        worst_iops = None
        if metering == "iops":
            if iops_basis["status"] == "absent":
                out.update({n: absent(iops_basis["reason"], "R6-model", {"key": n}) for n in names})
                continue
            worst_iops = iops_basis["value"] if hist_iops is None else min(hist_iops / 4096.0, iops_basis["value"])
            reasons += [r for r in iops_basis.get("reasons", [])]
        for n in names:
            inputs = {"key": n, "direction": direction, "metering": metering, "worst_bps": worst_bps, "worst_iops": worst_iops,
                      "cache_ratio": cache_ratio, "c_io_seq_us": seq.get("c_io_us"), "c_io_rand_us": rand.get("c_io_us"), "c_byte_ns": seq.get("c_byte_ns")}
            out[n] = value(derive.recompute("R6-model", inputs), "R6-model", inputs, basis_tag, reasons=reasons)
    return out


# -- QoS (R6) -------------------------------------------------------------------------------
def build_qos(cal: Mapping[str, Any], targets: Mapping[str, Any], history: Mapping[str, Any]) -> dict[str, Any]:
    c = cal["cache"]["read"]
    ladder = cal["ladder"]
    reasons = list(c["read_value"]["reasons"])
    read_value = c["read_value"]["value"]
    hist_w = history.get("write", {}).get("seq1m")
    if hist_w is None:
        windows = cal["windows"]["write_seq1m_bps"]
        best_w, worst_w = (max(windows), min(windows)) if windows else (None, None)
        reasons.append("worst_from_single_run")
    else:
        best_w, worst_w = hist_w["best"], hist_w["worst"]
    out: dict[str, Any] = {"min": value(derive.QOS_MIN, "R6-min", {}, "measured")}
    if read_value is None or best_w is None or ladder["best_read_rung_iops"] is None:
        out["max"] = absent("basis_missing", "R6-qos-max")
    else:
        inputs = {"best_read_rung": ladder["best_read_rung_iops"], "R": read_value, "best_w": best_w, "worst_w": worst_w}
        shape_poor = ["shape_fit_poor"] if "poor" in cal["shape"]["linear_model_fit_by_dir"].values() else []
        # the cap it depends on may be unproven for reasons the cache state does not carry (a truncated ladder, a moved reference)
        inherited = [r for r in cal["cap_basis"]["read"].get("reasons", []) if r in ("budget_limited", "ceiling_moved")]
        out["max"] = value(derive.recompute("R6-qos-max", inputs), "R6-qos-max", inputs,
                           "measured" if hist_w else "single-run", reasons=[*reasons, *inherited, *shape_poor])
    out["wpct"] = value(derive.WPCT, "R6-wpct", {}, "measured")
    out["rpct"] = value(derive.RPCT, "R6-rpct", {}, "measured")
    curve = cal["latency_curve"]
    for key, direction, pct_key, rule, fn in (("wlat", "write", "p90", "R6-wlat", derive.wlat_us), ("rlat", "read", "p95", "R6-rlat", derive.rlat_us)):
        if cal.get("stages", {}).get("S7") != "ok":
            out[key] = absent("stage_skipped", rule)
            continue
        light = derive.light_point(curve, direction, pct_key)
        if light is None:
            out[key] = absent("basis_missing", rule)
            continue
        inputs = {f"light_{pct_key}_us": light}
        # the load points are fractions of the direction's ceiling: a latency is not stronger than the ceiling it was measured under
        out[key] = value(fn(light), rule, inputs, "measured", reasons=cal["cap_basis"][direction].get("reasons", []))
    return out


def build_notes(cal: Mapping[str, Any]) -> list[dict[str, Any]]:
    c = cal["cache"]["read"]
    notes = [{"note": "read_cache_headroom", "value": c.get("read_cache_headroom")}]
    fvr = cal.get("fresh_vs_rewrite") or {}
    if fvr.get("measured") and fvr["penalty"] >= 3:
        notes.append({"note": "fresh_write_penalty", "penalty": fvr["penalty"], "label": fvr["label"]})
    return notes


def build_calibration_proposals(cal: Mapping[str, Any], targets: Mapping[str, Any], history: Mapping[str, Any]) -> dict[str, Any]:
    """caps / iocost_model / iocost_qos / notes of a calibration (writeback comes from the matrix)."""
    return {"caps": build_caps(cal, targets, history), "iocost_model": build_model(cal, history),
            "iocost_qos": build_qos(cal, targets, history), "notes": build_notes(cal)}
