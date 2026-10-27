"""The persisted state (`state.json`): one schema, one validator, used by every consumer.

The runner validates the whole file ONCE after loading it (and journals what it had to drop); `status` validates it the same way before
printing; the budget, backoff and schedule code re-check the slots they own, so a NaN, an inf, a string, a bool, a negative or an absurd
number can never bypass a daily cap, freeze a schedule or a rate limit, or crash a run. A field that fails is dropped to its default and
named; nothing is guessed.
"""

from __future__ import annotations

import math
import re

MAX_EPOCH = 1e11  # epoch seconds: well past any plausible wall clock, well below float trouble
MAX_COUNT = 1e15  # bytes / ops / counters
FUTURE_SLACK_S = 8 * 86400.0  # no persisted timestamp may lie further ahead of the clock than the longest schedule (7 d backoff + jitter)
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def finite(v, lo: float = 0.0, hi: float = MAX_EPOCH) -> bool:
    """A real, finite number (bool excluded) within [lo, hi]. An int of any size is compared exactly (never converted to float, so a
    400-digit integer cannot raise OverflowError); a float must be finite."""

    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    if isinstance(v, float) and not math.isfinite(v):
        return False
    return lo <= v <= hi


def count(v, hi: float = MAX_COUNT) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= hi


BUDGET_COUNTERS = ("read_bytes", "write_bytes", "read_ops", "write_ops", "sync_ops")


def valid_budget(b) -> bool:
    return (isinstance(b, dict) and isinstance(b.get("day"), str) and bool(DAY_RE.match(b["day"]))
            and all(count(b.get(k)) for k in BUDGET_COUNTERS))


def _epoch_checker(hi: float):
    """Epoch fields are bounded by the clock when one is given: a timestamp decades ahead would freeze a schedule or a dedup forever."""

    return lambda v: finite(v, 0.0, hi)


def _valid_backoff(bo, ok=finite) -> bool:
    return isinstance(bo, dict) and all(
        isinstance(k, str) and isinstance(v, dict) and count(v.get("n"), 1e6) and ok(v.get("until", 0.0)) for k, v in bo.items())


def _valid_bench_fail(f, ok=finite) -> bool:
    return isinstance(f, dict) and count(f.get("n"), 1e6) and (f.get("disabled_at") is None or ok(f["disabled_at"]))


def _valid_last_full(lf, ok=finite) -> bool:
    return isinstance(lf, dict) and isinstance(lf.get("epoch"), str) and ok(lf.get("t"))


def _valid_skip_logged(s, ok=finite) -> bool:
    return isinstance(s, dict) and all(
        isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("sig"), str) and ok(v.get("t")) for k, v in s.items())


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


EPOCH_KEYS = ("last_tick", "last_bench", "next_bench", "last_escalation", "last_prune", "skip_passive_t")


def schema(now: float | None = None) -> dict:
    """key -> (validator, description). Order = the order problems are reported in. With `now`, every epoch field (top level and
    nested) must also be at most FUTURE_SLACK_S ahead of the clock."""

    hi = MAX_EPOCH if now is None else min(MAX_EPOCH, float(now) + FUTURE_SLACK_S)
    ok = _epoch_checker(hi)
    epoch = "finite epoch seconds" + ("" if now is None else ", not in the future")
    s = {k: (ok, epoch) for k in EPOCH_KEYS}
    s.update({
        "last_full": (lambda v: _valid_last_full(v, ok), "{epoch: str, t: " + epoch + "}"),
        "budget": (valid_budget, "{day: YYYY-MM-DD, " + ", ".join(BUDGET_COUNTERS) + ": non-negative ints}"),
        "bench_fail": (lambda v: _valid_bench_fail(v, ok), "{n: int >= 0, disabled_at: null | " + epoch + "}"),
        "backoff": (lambda v: _valid_backoff(v, ok), "{kind: {n: int >= 0, until: " + epoch + "}}"),
        "skip_logged": (lambda v: _valid_skip_logged(v, ok), "{kind: {sig: str, t: " + epoch + "}}"),
        "devices": (_valid_devices, "{disk: {n_blocks, stride >= 1, start, counter: ints, witness: [ints]}}"),
    })
    return s


SCHEMA = schema()  # the clock-free schema (for inspection); callers with a clock use `schema(now)` through validate_state


def validate_state(raw, now: float | None = None) -> tuple[dict, list[str]]:
    """-> (usable state, problems). A field that is absent is fine; one that is present and invalid is DROPPED and named ("key: reason").
    Keys this version does not know are PRESERVED untouched (a newer version's fields survive a downgrade and are not nagged about);
    they are opaque data, never read by name."""

    problems: list[str] = []
    if not isinstance(raw, dict):
        return {}, ["state: not an object"]
    out: dict = {}
    s = schema(now)
    for key, (check, want) in s.items():
        if key not in raw:
            continue
        if check(raw[key]):
            out[key] = raw[key]
        else:
            problems.append(f"{key}: expected {want}")
    for key, value in raw.items():
        if key not in s and isinstance(key, str):
            out[key] = value
    return out, problems


def budget_damaged(problems: list[str]) -> bool:
    """True when a budget slot was PRESENT and invalid: the caller must then fail closed (count the day as spent), never start from zero."""

    return any(p.startswith("budget:") for p in problems)
