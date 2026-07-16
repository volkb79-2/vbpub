"""topos compare — informational baseline delta over two P88 summary results (P64).

D-007: baseline regression is explicitly NOT release-critical (see
``docs/DECISIONS-INBOX.md`` D-007 and ``docs/ROADMAP.md``). This module is a
pure consumer of two already-computed ``topos query --shape summary`` JSON
results (a *current* and a *baseline*): it never reads a recording, re-selects
entities, or re-aggregates frames. Feed it the ``Result.to_jsonable()`` dict
(or an equivalently-shaped JSON-loaded dict) from each side; it returns
deterministic per-(key, metric) deltas.

Zero baseline, absent/redacted values, mismatched semantics, incomplete
coverage and mid-window counter resets each produce an explicit typed
*outcome* rather than a division, coercion or silent pass — see the
``OUTCOME_*`` constants below.

Threshold gating reuses P61's exit-code convention (0 pass / 1 breach /
2 malformed spec): :func:`evaluate_compare_rules` never silently passes a
refused comparison, and :func:`combine_exit_codes` composes a P64 result with
a P61 result (or several P64 results) without losing or reordering either
gate's outcome.

Module-level exports (the public API):
    compare_summaries — pure comparison entry point
    format_compare / compare_to_jsonable — deterministic JSON serialization
    parse_compare_rule — "KEY:METRIC:delta<=VALUE" / ":pct<=VALUE" parser
    evaluate_compare_rules — pass/breach evaluation, never a silent pass
    combine_exit_codes — deterministic 0/1/2 combination across P61/P64 gates
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

_ROUND_DIGITS = 6

# The single comparable scalar stat per value semantic — mirrors the P88
# engine's own default summary-ranking stat (query.engine._DEFAULT_SORT_STAT)
# so this module never invents a second convention for "the" figure to diff.
_PRIMARY_STAT: dict[str, str | None] = {
    "gauge": "p95",
    "rate": "p95",
    "counter_delta": "total",
    "event_count": "events",
    "integral": "integral",
    "state_duration": None,  # no single scalar; unsupported for delta math
}

# Semantics whose summary stats carry a reset-aware "resets" counter.
_RESET_AWARE_SEMANTICS = frozenset({"rate", "counter_delta", "event_count", "integral"})


class CompareError(ValueError):
    """Malformed compare input — a usage error (CLI exit 2), never a breach."""


# ---------------------------------------------------------------------------
# Typed outcomes (never a division, coercion or silent pass).
# ---------------------------------------------------------------------------

OUTCOME_OK = "ok"
OUTCOME_ZERO_ZERO = "zero_zero"
OUTCOME_ZERO_BASELINE = "zero_baseline"
OUTCOME_MISSING = "missing"
OUTCOME_MISSING_CURRENT = "missing_current"
OUTCOME_MISSING_BASELINE = "missing_baseline"
OUTCOME_REDACTED = "redacted"
OUTCOME_SEMANTIC_MISMATCH = "semantic_mismatch"
OUTCOME_UNSUPPORTED_SEMANTIC = "unsupported_semantic"
OUTCOME_INCOMPLETE_COVERAGE = "incomplete_coverage"
OUTCOME_RESET_BOUNDARY = "reset_boundary"


@dataclass(frozen=True)
class Delta:
    """One (key, metric) comparison outcome.

    ``delta``/``pct`` are only populated for ``ok``, ``zero_zero`` and
    ``zero_baseline`` (where the absolute delta is always well-defined);
    every other outcome is a refusal and carries neither.
    """

    key: str
    metric: str
    outcome: str
    current: float | None
    baseline: float | None
    delta: float | None
    pct: float | None
    reason: str | None


def _round(v: float | None) -> float | None:
    if v is None:
        return None
    return round(v, _ROUND_DIGITS)


def _is_redacted(cell: Any) -> bool:
    """Detect the P81 redaction marker dialect: ``{"redacted": True, ...}``."""
    return isinstance(cell, dict) and cell.get("redacted") is True


def _numeric_stat(cell: dict[str, Any], stat: str) -> float | None:
    val = cell.get(stat)
    if val is None or isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    return float(val)


def _require_summary_meta(result: Any, label: str) -> dict[str, Any]:
    if not isinstance(result, dict) or "meta" not in result or "rows" not in result:
        raise CompareError(f"{label} is not a P88 query result (missing 'meta'/'rows')")
    meta = result["meta"]
    if not isinstance(meta, dict):
        raise CompareError(f"{label}.meta is not a mapping")
    if meta.get("shape") != "summary":
        raise CompareError(
            f"{label} has shape {meta.get('shape')!r}; baseline comparison requires "
            f"two shape='summary' P88 query results"
        )
    return meta


def _rows_by_key(result: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
    rows = result.get("rows")
    if not isinstance(rows, list):
        raise CompareError(f"{label}.rows is not a list")
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or "key" not in row or "metrics" not in row:
            raise CompareError(f"{label} has a row missing 'key'/'metrics'")
        out[row["key"]] = row["metrics"]
    return out


def _coverage_reason(current_meta: dict[str, Any], baseline_meta: dict[str, Any]) -> str | None:
    """Return a refusal reason unless BOTH windows report complete coverage."""
    cur_cov = current_meta.get("coverage")
    base_cov = baseline_meta.get("coverage")
    cur_complete = cur_cov.get("complete") if isinstance(cur_cov, dict) else None
    base_complete = base_cov.get("complete") if isinstance(base_cov, dict) else None
    if cur_complete is True and base_complete is True:
        return None
    return (
        f"incomplete coverage: current.coverage.complete={cur_complete!r}, "
        f"baseline.coverage.complete={base_complete!r} (gaps or eviction present)"
    )


def compare_summaries(
    current: dict[str, Any],
    baseline: dict[str, Any],
    *,
    metrics: tuple[str, ...] | None = None,
) -> list[Delta]:
    """Compare two P88 ``shape="summary"`` query results.

    Pure — consumes the two already-computed JSONable results verbatim; never
    reads a recording, re-selects entities, or re-aggregates frames (the
    comparison operates only on the ``rows``/``metrics`` cells already present
    in each result). Returns one :class:`Delta` per (key, metric) pair present
    in either result's rows, sorted by (key, metric) for byte-deterministic
    output across repeated runs on the same input.

    Raises :class:`CompareError` (a usage error) when either input is not a
    ``summary``-shape P88 result, or when the two results were produced with
    incompatible projection/visibility — comparing them would not be
    apples-to-apples regardless of any single cell's values.
    """
    cur_meta = _require_summary_meta(current, "current")
    base_meta = _require_summary_meta(baseline, "baseline")
    for field in ("projection", "visibility"):
        if cur_meta.get(field) != base_meta.get(field):
            raise CompareError(
                f"current and baseline {field} differ "
                f"({cur_meta.get(field)!r} vs {base_meta.get(field)!r}); "
                f"re-run both queries with matching {field}"
            )

    cur_rows = _rows_by_key(current, "current")
    base_rows = _rows_by_key(baseline, "baseline")

    if metrics is not None:
        metric_names = sorted(set(metrics))
    else:
        seen: set[str] = set()
        for row in cur_rows.values():
            seen.update(row)
        for row in base_rows.values():
            seen.update(row)
        metric_names = sorted(seen)

    keys = sorted(set(cur_rows) | set(base_rows))
    coverage_reason = _coverage_reason(cur_meta, base_meta)

    deltas: list[Delta] = []
    for key in keys:
        cur_cells = cur_rows.get(key, {})
        base_cells = base_rows.get(key, {})
        for metric in metric_names:
            deltas.append(
                _compare_cell(key, metric, cur_cells.get(metric), base_cells.get(metric), coverage_reason)
            )
    return deltas


def _compare_cell(
    key: str,
    metric: str,
    cur_cell: dict[str, Any] | None,
    base_cell: dict[str, Any] | None,
    coverage_reason: str | None,
) -> Delta:
    def refuse(outcome: str, reason: str) -> Delta:
        return Delta(key, metric, outcome, None, None, None, None, reason)

    if cur_cell is None and base_cell is None:
        return refuse(OUTCOME_MISSING, "metric absent from both current and baseline")
    if _is_redacted(cur_cell) or _is_redacted(base_cell):
        return refuse(OUTCOME_REDACTED, "value redacted in current and/or baseline")
    if cur_cell is None:
        return refuse(OUTCOME_MISSING_CURRENT, "metric absent from current")
    if base_cell is None:
        return refuse(OUTCOME_MISSING_BASELINE, "metric absent from baseline")

    cur_sem = cur_cell.get("semantic")
    base_sem = base_cell.get("semantic")
    if cur_sem != base_sem:
        return refuse(
            OUTCOME_SEMANTIC_MISMATCH,
            f"current semantic {cur_sem!r} != baseline semantic {base_sem!r}",
        )

    stat = _PRIMARY_STAT.get(cur_sem)
    if stat is None:
        return refuse(OUTCOME_UNSUPPORTED_SEMANTIC, f"{cur_sem!r} has no comparable scalar stat")

    cur_val = _numeric_stat(cur_cell, stat)
    base_val = _numeric_stat(base_cell, stat)
    if cur_val is None:
        return refuse(OUTCOME_MISSING_CURRENT, f"current {stat} is null (e.g. zero-sample window)")
    if base_val is None:
        return refuse(OUTCOME_MISSING_BASELINE, f"baseline {stat} is null (e.g. zero-sample window)")

    if cur_sem in _RESET_AWARE_SEMANTICS:
        cur_resets = cur_cell.get("resets")
        base_resets = base_cell.get("resets")
        if (isinstance(cur_resets, int) and cur_resets > 0) or (
            isinstance(base_resets, int) and base_resets > 0
        ):
            return refuse(
                OUTCOME_RESET_BOUNDARY,
                f"counter reset within window (current resets={cur_resets!r}, "
                f"baseline resets={base_resets!r})",
            )

    if coverage_reason is not None:
        return refuse(OUTCOME_INCOMPLETE_COVERAGE, coverage_reason)

    if base_val == 0.0:
        if cur_val == 0.0:
            return Delta(
                key, metric, OUTCOME_ZERO_ZERO, _round(cur_val), _round(base_val), 0.0, None,
                "baseline and current both zero; percentage is undefined",
            )
        return Delta(
            key, metric, OUTCOME_ZERO_BASELINE, _round(cur_val), _round(base_val),
            _round(cur_val - base_val), None,
            "baseline is zero; percentage is undefined",
        )

    delta = cur_val - base_val
    pct = (delta / base_val) * 100.0
    return Delta(key, metric, OUTCOME_OK, _round(cur_val), _round(base_val), _round(delta), _round(pct), None)


# ---------------------------------------------------------------------------
# Deterministic JSON serialization
# ---------------------------------------------------------------------------

def delta_to_jsonable(d: Delta) -> dict[str, Any]:
    out: dict[str, Any] = {
        "key": d.key,
        "metric": d.metric,
        "outcome": d.outcome,
        "current": d.current,
        "baseline": d.baseline,
        "delta": d.delta,
        "pct": d.pct,
    }
    if d.reason is not None:
        out["reason"] = d.reason
    return out


def compare_to_jsonable(
    deltas: list[Delta],
    assertions: list[CompareAssertionResult] | None = None,
) -> dict[str, Any]:
    """Convert the full comparison to a deterministic JSON dict."""
    d: dict[str, Any] = {"deltas": [delta_to_jsonable(x) for x in deltas]}
    if assertions is not None:
        d["assertions"] = [compare_assertion_result_to_jsonable(r) for r in assertions]
    return d


def format_compare(
    deltas: list[Delta],
    assertions: list[CompareAssertionResult] | None = None,
    *,
    pretty: bool = False,
) -> str:
    """Return a deterministic JSON string for the full comparison."""
    payload = compare_to_jsonable(deltas, assertions=assertions)
    if pretty:
        return json.dumps(payload, sort_keys=True, indent=2)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Optional threshold gating — reuses P61's 0/1/2 exit-code convention.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CompareRule:
    """One parsed ``KEY:METRIC:delta<=VALUE`` / ``:pct<=VALUE`` (or ``>=``) spec."""

    key: str
    metric: str
    field: str  # "delta" | "pct"
    op: str  # "<=" | ">="
    value: float


_RULE_PATTERN = re.compile(
    r"^"
    r"(?P<key>[^:]*)"
    r":(?P<metric>[^:]+)"
    r":(?P<field>delta|pct)"
    r"(?P<op><=|>=)"
    r"(?P<value>[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"$"
)


def parse_compare_rule(spec: str) -> CompareRule:
    """Parse a single compare threshold spec.

    Raises :class:`CompareError` on malformed input, unknown field, or a
    non-finite value.
    """
    m = _RULE_PATTERN.match(spec.strip())
    if m is None:
        raise CompareError(
            f"invalid compare rule {spec!r} — expected KEY:METRIC:delta<=VALUE, "
            f"KEY:METRIC:pct<=VALUE, or the >= variant"
        )
    value_str = m.group("value")
    value = float(value_str)
    if math.isnan(value) or math.isinf(value):
        raise CompareError(f"invalid compare rule value {value_str!r} — must be finite")
    return CompareRule(
        key=m.group("key"), metric=m.group("metric"), field=m.group("field"),
        op=m.group("op"), value=value,
    )


@dataclass(frozen=True)
class CompareAssertionResult:
    """Outcome of evaluating one :class:`CompareRule` against a comparison."""

    key: str
    metric: str
    field: str
    op: str
    threshold: float
    actual: float | None
    passed: bool
    reason: str | None = None


def evaluate_compare_rules(deltas: list[Delta], rules: list[CompareRule]) -> list[CompareAssertionResult]:
    """Evaluate threshold rules against an already-computed delta list.

    Pure — no file or argparse I/O. A rule whose (key, metric) is not present
    in *deltas*, or whose comparison was refused (any outcome other than
    ``ok``/``zero_zero``/``zero_baseline``, or a ``pct`` rule against an
    undefined percentage), is a **breach**: refused/undefined data never
    silently passes a configured gate.
    """
    by_key_metric: dict[tuple[str, str], Delta] = {(d.key, d.metric): d for d in deltas}
    results: list[CompareAssertionResult] = []

    for r in rules:
        d = by_key_metric.get((r.key, r.metric))
        if d is None:
            results.append(CompareAssertionResult(
                key=r.key, metric=r.metric, field=r.field, op=r.op, threshold=r.value,
                actual=None, passed=False, reason="key/metric not present in comparison",
            ))
            continue

        actual = d.delta if r.field == "delta" else d.pct
        if actual is None:
            reason = d.reason or f"{r.field} is undefined for outcome {d.outcome!r}"
            results.append(CompareAssertionResult(
                key=r.key, metric=r.metric, field=r.field, op=r.op, threshold=r.value,
                actual=None, passed=False, reason=reason,
            ))
            continue

        passed = actual <= r.value if r.op == "<=" else actual >= r.value
        results.append(CompareAssertionResult(
            key=r.key, metric=r.metric, field=r.field, op=r.op, threshold=r.value,
            actual=actual, passed=passed,
            reason=None if passed else f"breached: {actual} {r.op} {r.value}",
        ))

    results.sort(key=lambda r: (r.key, r.metric, r.field, r.op))
    return results


def compare_assertion_result_to_jsonable(r: CompareAssertionResult) -> dict[str, Any]:
    d: dict[str, Any] = {
        "key": r.key,
        "metric": r.metric,
        "field": r.field,
        "op": r.op,
        "threshold": _round(r.threshold),
        "actual": _round(r.actual) if r.actual is not None else None,
        "passed": r.passed,
    }
    if r.reason is not None:
        d["reason"] = r.reason
    return d


def compare_exit_code(results: list[CompareAssertionResult]) -> int:
    """``0`` when every rule passes (or none were given); ``1`` on any breach."""
    return 1 if any(not r.passed for r in results) else 0


def combine_exit_codes(*codes: int) -> int:
    """Deterministically combine P61/P64-style 0/1/2 exit codes.

    ``2`` (usage error) outranks ``1`` (breach) outranks ``0`` (pass),
    regardless of the order codes are supplied in — composing a P61 report
    assertion result with a P64 baseline-breach result (or several of either)
    never loses or reorders either gate's outcome.
    """
    if any(c == 2 for c in codes):
        return 2
    if any(c == 1 for c in codes):
        return 1
    return 0
