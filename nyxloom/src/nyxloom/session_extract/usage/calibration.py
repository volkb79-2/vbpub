"""Bounded calibration against explicit operator readings, never rejections."""

from __future__ import annotations

from datetime import datetime, timezone
from itertools import pairwise
from typing import Any

from .model import Corpus, Node, UsageEvent

_MAX_RESIDUAL_PCT_POINTS = 5.0


def _walk(node: Node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def calibrate_anchors(
    corpus: Corpus,
    anchors: list[dict[str, Any]],
    threshold_events: list[UsageEvent] | list[dict[str, Any]],
    weights: object | None = None,
) -> dict[str, Any]:
    """Fit local weighted units to UI percentages for identifiable reset spans.

    Rejection records may contribute an observed reset boundary, but never a
    percentage or capacity target.
    """
    calls = [call for session in corpus.sessions for node in _walk(session.root) for call in node.calls]
    by_kind: dict[str, list[dict[str, Any]]] = {}
    reasons: list[str] = []
    for anchor in anchors:
        by_kind.setdefault(str(anchor.get("kind", "")), []).append(anchor)
    reset_boundaries: dict[str, set[datetime]] = {}
    for anchor in anchors:
        # The caller loads anchors through the versioned validator before
        # calibration; malformed state must fail before reaching this layer.
        reset_boundaries.setdefault(str(anchor["kind"]), set()).add(_parse(anchor["reset_at"]))
    observed_events: list[UsageEvent | dict[str, Any]] = list(threshold_events)
    if not observed_events:
        observed_events = [
            event for session in corpus.sessions for node in _walk(session.root)
            for event in node.events if event.kind == "api_rejection"
        ]
    for event in observed_events:
        if isinstance(event, dict):
            kind = event.get("limit_kind") or event.get("kind")
            reset_text = event.get("reset_at")
        else:
            kind = event.limit_kind
            reset_text = event.reset_at
        if kind == "seven_day":
            kind = "weekly"
        if kind not in {"five_hour", "weekly"} or reset_text is None:
            continue
        try:
            reset = reset_text if isinstance(reset_text, datetime) else _parse(reset_text)
        except (TypeError, ValueError):
            continue
        if isinstance(reset_text, datetime):
            if reset.tzinfo is None or reset.utcoffset() is None:
                continue
            reset = reset.astimezone(timezone.utc)
        reset_boundaries.setdefault(kind, set()).add(reset)
    groups: list[dict[str, Any]] = []
    for kind, selected in sorted(by_kind.items()):
        selected.sort(key=lambda item: (item["observed_at"], item["anchor_id"]))
        for reset_text in sorted({item["reset_at"] for item in selected}):
            target_anchors = [item for item in selected if item["reset_at"] == reset_text]
            reset_at = _parse(reset_text)
            observations: list[tuple[dict[str, Any], datetime, float]] = []
            for anchor in target_anchors:
                observed_at = _parse(anchor["observed_at"])
                previous_resets = [
                    reset for reset in reset_boundaries.get(kind, set())
                    if reset <= observed_at and reset < reset_at
                ]
                if not previous_resets:
                    continue
                start = max(previous_resets)
                interval_calls = [call for call in calls if start <= call.timestamp < observed_at]
                if any(call.weighted_cost is None for call in interval_calls):
                    reasons.append(f"{kind} anchor {anchor['anchor_id']}: weighted call values are incomplete")
                    continue
                local_units = sum(call.weighted_cost or 0.0 for call in interval_calls)
                observations.append((anchor, start, local_units))
            if len(observations) < 2:
                reasons.append(f"{kind} reset {reset_text}: fewer than two consistent UI readings")
                continue
            starts = {start for _, start, _ in observations}
            if len(starts) != 1:
                reasons.append(f"{kind} reset {reset_text}: observed window start is ambiguous")
                continue
            observations.sort(key=lambda entry: entry[0]["observed_at"])
            values = [(units, float(anchor["percent"])) for anchor, _, units in observations]
            if any(
                later_units > earlier_units and later_pct < earlier_pct
                for (earlier_units, earlier_pct), (later_units, later_pct) in pairwise(values)
            ):
                reasons.append(f"{kind} reset {reset_text}: percentage readings contradict local usage order")
                continue
            denominator = sum(units * units for units, _ in values)
            if denominator <= 0:
                reasons.append(f"{kind} reset {reset_text}: local weighted usage cannot identify capacity")
                continue
            scale = sum(units * percent for units, percent in values) / denominator
            if scale <= 0:
                reasons.append(f"{kind} reset {reset_text}: percentage readings cannot identify a positive capacity")
                continue
            # Percentages are bounded by 100, so the fitted scale cannot imply
            # a capacity below the largest observed local amount.
            scale = min(scale, min((100.0 / units) for units, _ in values if units > 0))
            capacity = 100.0 / scale
            residuals = [scale * units - percent for units, percent in values]
            if max(abs(value) for value in residuals) > _MAX_RESIDUAL_PCT_POINTS:
                reasons.append(f"{kind} reset {reset_text}: residual exceeds {_MAX_RESIDUAL_PCT_POINTS:g} percentage points")
                continue
            coverage = {
                anchor["coverage_assumption"] for anchor, _, _ in observations
            }
            all_account = coverage == {"all-account-usage-observed"}
            groups.append({
                "kind": kind,
                "window_start": _iso(next(iter(starts))),
                "window_reset_at": _iso(reset_at),
                "anchor_count": len(observations),
                "anchor_ids": [anchor["anchor_id"] for anchor, _, _ in observations],
                "fit_status": "fit-account-coverage" if all_account else "fit-local-lower-bound",
                "capacity_units": capacity,
                "capacity_interpretation": "conditional estimate under all-account coverage" if all_account else "lower bound; unobserved account activity can only increase capacity",
                "residuals_pct_points": residuals,
                "anchor_fits": [
                    {
                        "anchor_id": anchor["anchor_id"],
                        "observed_at": anchor["observed_at"],
                        "observed_pct": float(anchor["percent"]),
                        "local_weighted_units": units,
                        "fitted_pct": scale * units,
                        "residual_pct_points": residual,
                    }
                    for (anchor, _, units), residual in zip(
                        observations, residuals, strict=True,
                    )
                ],
                "max_abs_residual_pct_points": max(abs(value) for value in residuals),
                "coverage_assumptions": sorted(coverage),
                # Calibration records a denominator; without a requested
                # target instant it does not extrapolate a percentage.
                "estimated_limit_pct": None,
            })
    return {
        "schema_version": "usage-calibration-v1",
        "groups": groups,
        "capacity_fit_status": "fit" if groups else "unknown",
        "fit_residual_bound_pct_points": _MAX_RESIDUAL_PCT_POINTS,
        "reasons": sorted(set(reasons)),
        "threshold_event_count": len(observed_events),
        "threshold_events_are_anchors": False,
        "weight_profile": (
            {
                "name": getattr(weights, "default_weight_profile", None),
                "values": getattr(weights, "weight_profiles", {}).get(
                    getattr(weights, "default_weight_profile", None),
                ),
                "description": getattr(weights, "weight_profile_descriptions", {}).get(
                    getattr(weights, "default_weight_profile", None),
                ),
            }
            if weights is not None else None
        ),
        "weight_provenance": (
            {
                "source_url": getattr(weights, "provenance", {}).get("pricing_url"),
                "retrieved_on": getattr(weights, "provenance", {}).get("retrieved_on"),
                "weights_sha256": getattr(weights, "source_sha256", None),
            }
            if weights is not None else None
        ),
        "measure_basis": {
            "capacity_units": "experimental weighted units per displayed account percentage",
            "estimated_limit_pct": "unknown without a separately requested identifiable target",
            "estimates": True,
        },
        "coverage_note": "Local weighted usage is a lower bound when web, other devices, or other shared account surfaces consume the same limit. API prices and rejection events do not identify capacity.",
    }
