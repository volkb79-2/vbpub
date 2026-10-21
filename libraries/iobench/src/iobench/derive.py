"""Derivation rules R1..R6 (IOB-DER-01..10).  Every rule is a pure function of its recorded inputs."""
from __future__ import annotations

import math
from typing import Any, Mapping

MIB = 1024 * 1024
DIRTY_FLOOR = 4 * MIB
DIRTY_CEIL_SHARE = 0.25
T_REQ_S = 0.010  # [I] a request should occupy the device <= 10 ms (OI-3)
NR_MIN, NR_MAX = 8, 64
SECTORS_MIN_KB = 32
QOS_MIN = 100
QOS_MARGIN = 110
WPCT, RPCT = 90, 95
WLAT_STEP_MS, WLAT_MIN_MS, WLAT_MAX_MS = 5, 10, 50
RLAT_STEP_MS, RLAT_MIN_MS, RLAT_MAX_MS = 1, 2, 20
LIGHT_POINTS = (10, 30)
PLAIN_BPS_IOPS_DIV = 4096


def pow2_floor(n: float) -> int:
    return 1 << (int(n).bit_length() - 1) if n >= 1 else 0


def pow2_ceil(n: float) -> int:
    return 1 << (max(1, math.ceil(n)) - 1).bit_length()


def t_eff(dirty_ratio_pct: float, available_bytes: float) -> int:
    """Kernel effective dirty threshold from the ORIGINAL vm.dirty_ratio (never from an applied vm.dirty_bytes)."""
    return int(dirty_ratio_pct / 100.0 * available_bytes)


# -- R1 ---------------------------------------------------------------------------------
def r1(*, W_s: float, R_drain_bps: float, inflight_bytes: float, T_eff_bytes: float) -> tuple[Any, list[str]]:
    """``dirty_bytes``: largest power-of-two MiB <= W x R_drain - inflight, clamped; ``kernel`` at/above T_eff."""
    budget = W_s * R_drain_bps - inflight_bytes
    if inflight_bytes >= W_s * R_drain_bps:
        return DIRTY_FLOOR, ["inflight_exceeds_budget"]
    if budget >= T_eff_bytes:
        return "kernel", []
    value = pow2_floor(budget / MIB) * MIB
    return min(max(value, DIRTY_FLOOR), int(DIRTY_CEIL_SHARE * T_eff_bytes)), []


def r1b(*, dirty_bytes: Any) -> Any:
    return "kernel" if dirty_bytes == "kernel" else int(dirty_bytes) // 4


# -- R2 ---------------------------------------------------------------------------------
def r2(*, qd90_write: int, current_nr_requests: int) -> Any:
    value = min(max(pow2_ceil(2 * qd90_write), NR_MIN), NR_MAX)
    return "kernel" if value >= current_nr_requests else value


# -- R3 / R4 ----------------------------------------------------------------------------
def r3_candidate(R_drain_bps: float, max_hw_kb: int) -> int:
    """Largest power-of-two KB <= R_drain x 10 ms, clamped to [32, max_hw]."""
    return min(max(pow2_floor(R_drain_bps * T_REQ_S / 1024), SECTORS_MIN_KB), max_hw_kb)


def r3(*, R_drain_bps: float, max_hw_kb: int, F: float, P_us: float, read_ratio: Mapping[str, float],
       probe_p99_us: Mapping[str, float]) -> tuple[Any, list[str]]:
    """``max_sectors_kb`` with the read floor ``F`` unless the sweep shows the raised candidate breaks ``P``."""
    cand = r3_candidate(R_drain_bps, max_hw_kb)
    if cand >= max_hw_kb:
        return "kernel", []
    if read_ratio[str(cand)] >= F:  # S5 always measures the candidate; a missing ratio is a bug, not a pass
        return cand, []
    meets = sorted(int(k) for k, v in read_ratio.items() if v >= F and int(k) > cand)
    if not meets:
        return cand, ["read_floor_violated"]
    raised = meets[0]
    shown = probe_p99_us.get(str(raised))  # only a measured p99 can show a violation
    if shown is not None and shown > P_us:
        return cand, ["read_floor_violated"]
    return ("kernel" if raised >= max_hw_kb else raised), []


def r4(chosen: Mapping[str, int], cells: list[Mapping[str, Any]], P_us: float) -> dict[str, Any]:
    """The probe p99 of the sweep cell closest to the chosen triple must be <= P (else ``target_missed``)."""
    def distance(cell: Mapping[str, Any]) -> float:
        return sum(abs(math.log2(cell["knobs"][k] / chosen[k])) for k in ("dirty_bytes", "nr_requests", "max_sectors_kb"))

    closest = min((c for c in cells), key=distance)
    return {"ok": closest["probe_p99_us"] <= P_us, "closest_cell": closest["name"], "probe_p99_us": closest["probe_p99_us"]}


# -- R5 ---------------------------------------------------------------------------------
def r5(*, U: float, worst_over_median: float, basis_iops: float | None = None, basis_bps: float | None = None) -> int:
    """``K = U x basis x min(1, worst/median)`` for an IOPS key (``basis_iops``) or a BPS key (``basis_bps``)."""
    basis = basis_iops if basis_bps is None else basis_bps
    return round(U * basis * min(1.0, worst_over_median))


# -- R6 model ---------------------------------------------------------------------------
def r6_keys(*, direction: str, metering: str, worst_bps: float, worst_iops: float | None, c_io_seq_us: float | None,
            c_io_rand_us: float | None, c_byte_ns: float | None) -> dict[str, float]:
    """The three keys of one direction (``r``/``w`` prefix) per metering class."""
    p = direction[0]
    if metering == "byte":
        iops = int(worst_bps // PLAIN_BPS_IOPS_DIV)
        return {f"{p}bps": int(worst_bps), f"{p}seqiops": iops, f"{p}randiops": iops}
    if metering == "iops":
        return {f"{p}bps": int(worst_iops * 1048576), f"{p}seqiops": int(worst_iops), f"{p}randiops": int(worst_iops)}
    return {f"{p}bps": int(min(1e9 / c_byte_ns, worst_bps)), f"{p}seqiops": int(1e6 / c_io_seq_us),
            f"{p}randiops": int(1e6 / c_io_rand_us)}


# -- R6 QoS -----------------------------------------------------------------------------
def ceil_to(value: float, step: float) -> float:
    return math.ceil(value / step - 1e-9) * step


def light_point(curve: list[Mapping[str, Any]], direction: str, key: str) -> float | None:
    """The larger of the 10 % and 30 % points' latency (``key`` = p90/p95); ``None`` if neither was reached."""
    vals = [pt["lat_us"][key] for pt in curve if pt["dir"] == direction and pt["load_pct"] in LIGHT_POINTS and not pt.get("not_reached")]
    return max(vals) if vals else None


def qos_max(*, best_read_rung: float, R: float, best_w: float, worst_w: float) -> tuple[int, dict[str, float]]:
    ratio_r, ratio_w = best_read_rung / R, best_w / worst_w
    return int(ceil_to(QOS_MARGIN * max(ratio_r, ratio_w), 5)), {"ratio_r": ratio_r, "ratio_w": ratio_w}


def wlat_us(light_p90_us: float) -> int:
    ms = ceil_to(2 * light_p90_us / 1000.0, WLAT_STEP_MS)
    return int(min(max(ms, WLAT_MIN_MS), WLAT_MAX_MS) * 1000)


def rlat_us(light_p95_us: float) -> int:
    ms = ceil_to(2 * light_p95_us / 1000.0, RLAT_STEP_MS)
    return int(min(max(ms, RLAT_MIN_MS), RLAT_MAX_MS) * 1000)


def latency_knee(curve: list[Mapping[str, Any]], direction: str) -> float | None:
    """First load point whose p99 >= 2 x the 10 % point's p99 (IOB-LAT-02)."""
    pts = sorted((p for p in curve if p["dir"] == direction and not p.get("not_reached")), key=lambda p: p["load_pct"])
    base = next((p for p in pts if p["load_pct"] == 10), None)
    if base is None:
        return None
    return next((p["load_pct"] for p in pts if p["lat_us"]["p99"] >= 2 * base["lat_us"]["p99"]), None)


RULES = {
    "R1": lambda i: r1(**i)[0], "R1b": lambda i: r1b(**i), "R2": lambda i: r2(**i),
    "R3": lambda i: r3(**i)[0], "R5": lambda i: r5(**i),
    "R6-qos-max": lambda i: qos_max(**i)[0], "R6-wlat": lambda i: wlat_us(i["light_p90_us"]),
    "R6-rlat": lambda i: rlat_us(i["light_p95_us"]), "R6-min": lambda i: QOS_MIN,
    "R6-wpct": lambda i: WPCT, "R6-rpct": lambda i: RPCT,
    "R6-model": lambda i: r6_keys(**{k: v for k, v in i.items() if k != "key"})[i["key"]],
}


def recompute(rule: str, inputs: Mapping[str, Any]) -> Any:
    """Recompute a proposal value from its recorded inputs (IOB-DER-07)."""
    return RULES[rule](dict(inputs))
