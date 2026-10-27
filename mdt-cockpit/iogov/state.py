"""The persisted state (`state.json`): one schema, one validator, used by every consumer.

The runner validates the whole file ONCE after loading it (and journals what it had to drop); `status` validates it the same way before
printing; the budget, backoff and schedule code re-check the slots they own, so a NaN, an inf, a string, a bool, a negative or an absurd
number can never bypass a daily cap, freeze a schedule or a rate limit, or crash a run. A field that fails is dropped to its default and
named; nothing is guessed.
"""

from __future__ import annotations

import math

MAX_EPOCH = 1e11  # epoch seconds: well past any plausible wall clock, well below float trouble
MAX_COUNT = 1e15  # bytes / ops / counters


def finite(v, lo: float = 0.0, hi: float = MAX_EPOCH) -> bool:
    """A real, finite number (bool excluded) within [lo, hi]."""

    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi


def count(v, hi: float = MAX_COUNT) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= hi


BUDGET_COUNTERS = ("read_bytes", "write_bytes", "read_ops", "write_ops", "sync_ops")


def valid_budget(b) -> bool:
    return (isinstance(b, dict) and isinstance(b.get("day"), str) and len(b["day"]) == 10
            and all(count(b.get(k)) for k in BUDGET_COUNTERS))


def _valid_backoff(bo) -> bool:
    return isinstance(bo, dict) and all(
        isinstance(k, str) and isinstance(v, dict) and count(v.get("n"), 1e6) and finite(v.get("until", 0.0)) for k, v in bo.items())


def _valid_bench_fail(f) -> bool:
    return isinstance(f, dict) and count(f.get("n"), 1e6) and (f.get("disabled_at") is None or finite(f["disabled_at"]))


def _valid_last_full(lf) -> bool:
    return isinstance(lf, dict) and isinstance(lf.get("epoch"), str) and finite(lf.get("t"))


def _valid_skip_logged(s) -> bool:
    return isinstance(s, dict) and all(
        isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("sig"), str) and finite(v.get("t")) for k, v in s.items())


def _valid_devices(d) -> bool:
    if not isinstance(d, dict):
        return False
    for k, v in d.items():
        if not (isinstance(k, str) and isinstance(v, dict)):
            return False
        if not (count(v.get("n_blocks")) and count(v.get("stride")) and v.get("stride", 0) >= 1 and count(v.get("start")) and count(v.get("counter"))):
            return False
        w = v.get("witness", [])
        if not (isinstance(w, list) and len(w) <= 4096 and all(count(x) for x in w)):
            return False
    return True


# key -> (validator, description). Order = the order problems are reported in.
SCHEMA = {
    "last_tick": (finite, "finite epoch seconds"),
    "last_bench": (finite, "finite epoch seconds"),
    "next_bench": (finite, "finite epoch seconds"),
    "last_escalation": (finite, "finite epoch seconds"),
    "last_prune": (finite, "finite epoch seconds"),
    "skip_passive_t": (finite, "finite epoch seconds"),
    "last_full": (_valid_last_full, "{epoch: str, t: finite}"),
    "budget": (valid_budget, "{day: YYYY-MM-DD, " + ", ".join(BUDGET_COUNTERS) + ": non-negative ints}"),
    "bench_fail": (_valid_bench_fail, "{n: int >= 0, disabled_at: null | finite}"),
    "backoff": (_valid_backoff, "{kind: {n: int >= 0, until: finite}}"),
    "skip_logged": (_valid_skip_logged, "{kind: {sig: str, t: finite}}"),
    "devices": (_valid_devices, "{disk: {n_blocks, stride >= 1, start, counter: ints, witness: [ints]}}"),
}


def validate_state(raw) -> tuple[dict, list[str]]:
    """-> (usable state, problems). A field that is absent is fine; one that is present and invalid is DROPPED and named ("key: reason");
    unknown keys are dropped too (nothing in this file is trusted by name alone)."""

    problems: list[str] = []
    if not isinstance(raw, dict):
        return {}, ["state: not an object"]
    out: dict = {}
    for key, (check, want) in SCHEMA.items():
        if key not in raw:
            continue
        if check(raw[key]):
            out[key] = raw[key]
        else:
            problems.append(f"{key}: expected {want}")
    for key in raw:
        if key not in SCHEMA:
            problems.append(f"{key}: unknown key")
    return out, problems
