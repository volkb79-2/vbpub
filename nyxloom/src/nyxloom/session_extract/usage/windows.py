"""Observed account reset intervals and clearly named rolling analysis ranges."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from itertools import pairwise
from typing import Any

from .model import Call, Corpus, Node, Window


def _walk(node: Node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _calls(corpus: Corpus) -> list[Call]:
    return [call for session in corpus.sessions for node in _walk(session.root) for call in node.calls]


def _metrics(calls: list[Call], start: datetime, end: datetime) -> dict[str, Any]:
    selected = [call for call in calls if start <= call.timestamp < end]
    weighted = [call.weighted_cost for call in selected]
    usd = [call.usd_cost for call in selected]
    return {
        "calls": len(selected),
        "raw_token_units": sum(call.raw_token_units for call in selected),
        "weighted_units": sum(value for value in weighted if value is not None) if all(value is not None for value in weighted) else None,
        "api_equivalent_usd": sum(value for value in usd if value is not None) if all(value is not None for value in usd) else None,
    }


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("reset timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _intervals(
    kind: str, reset_times: set[datetime], calls: list[Call], query: Window,
) -> list[dict[str, Any]]:
    ordered = sorted(reset_times)
    duration = timedelta(hours=5) if kind == "five_hour" else timedelta(days=7)
    intervals = []
    for start, end in pairwise(ordered):
        if end - start != duration:
            continue
        metrics_start = max(start, query.since)
        metrics_end = min(end, query.until)
        if metrics_start >= metrics_end:
            continue
        intervals.append({
            "kind": kind,
            "model": "observed-reset",
            "start": _iso(start),
            "end": _iso(end),
            "metrics_window": {
                "start": _iso(metrics_start),
                "end": _iso(metrics_end),
                "semantics": "[since, until)",
            },
            **_metrics(calls, metrics_start, metrics_end),
        })
    return intervals


def build_usage_windows(
    corpus: Corpus,
    anchors: list[dict[str, Any]],
    query: Window,
    weights: object | None = None,
) -> dict[str, Any]:
    """Describe observed five-hour/weekly intervals plus a rolling analysis view.

    The optional weights argument is accepted because callers load the same
    profile for all report verbs; values are already attached to corpus calls.
    """
    calls = _calls(corpus)
    boundary_map: dict[tuple[str, datetime], dict[str, Any]] = {}

    def add_boundary(
        kind: str, reset: datetime, source: str, observed_at: datetime, evidence_id: str,
    ) -> None:
        key = (kind, reset)
        boundary = boundary_map.setdefault(key, {
            "kind": kind,
            "reset_at": _iso(reset),
            "evidence": [],
        })
        evidence = {
            "source": source,
            "observed_at": _iso(observed_at),
            "evidence_id": evidence_id,
        }
        if evidence not in boundary["evidence"]:
            boundary["evidence"].append(evidence)

    for session in corpus.sessions:
        for node in _walk(session.root):
            for event in node.events:
                if (
                    event.kind == "api_rejection" and event.limit_kind in {"five_hour", "weekly"}
                    and event.reset_at is not None
                ):
                    add_boundary(
                        event.limit_kind, event.reset_at, "transcript_rejection",
                        event.timestamp, event.event_id,
                    )
    for anchor in anchors:
        try:
            reset = _parse_time(anchor["reset_at"])
            observed = _parse_time(anchor["observed_at"])
        except (KeyError, AttributeError, ValueError):
            continue
        if anchor.get("kind") in {"five_hour", "weekly"}:
            add_boundary(
                anchor["kind"], reset, "operator_ui_anchor", observed,
                str(anchor.get("anchor_id", "unknown")),
            )
    for boundary in boundary_map.values():
        boundary["evidence"] = [
            evidence for evidence in boundary["evidence"]
            if query.since <= _parse_time(evidence["observed_at"]) < query.until
        ]
    boundary_map = {
        key: boundary for key, boundary in boundary_map.items()
        if boundary["evidence"]
    }
    observed_boundaries = sorted(
        boundary_map.values(), key=lambda item: (item["reset_at"], item["kind"]),
    )
    for boundary in observed_boundaries:
        boundary["evidence"].sort(
            key=lambda item: (item["observed_at"], item["source"], item["evidence_id"]),
        )
    resets_by_kind = {
        kind: {instant for found_kind, instant in boundary_map if found_kind == kind}
        for kind in ("five_hour", "weekly")
    }
    intervals = [
        *_intervals("five_hour", resets_by_kind["five_hour"], calls, query),
        *_intervals("weekly", resets_by_kind["weekly"], calls, query),
    ]
    intervals.sort(key=lambda item: (item["start"], item["kind"]))
    rolling_start = max(query.until - timedelta(hours=5), query.since)
    rolling = {
        "kind": "rolling_analysis_not_account_session",
        "start": _iso(rolling_start),
        "end": _iso(query.until),
        **_metrics(calls, rolling_start, query.until),
    }
    intervals_by_kind = {
        kind: [item for item in intervals if item["kind"] == kind]
        for kind in ("five_hour", "weekly")
    }
    boundary_status = {}
    for kind, title in (("five_hour", "observed_reset_session"), ("weekly", "account_assigned_weekly_reset")):
        count = len(resets_by_kind[kind])
        completed = len(intervals_by_kind[kind])
        if completed:
            interval_status = "observed"
        elif count == 1:
            interval_status = "start_unknown"
        elif count > 1:
            interval_status = "spacing_unverified"
        else:
            interval_status = "unknown"
        boundary_status[kind] = {
            "model": title,
            "reset_times_observed": count,
            "complete_interval_count": completed,
            "reset_time_status": "observed" if count else "unknown",
            "interval_status": interval_status,
        }
    events = [
        {
            "event_id": event.event_id,
            "kind": event.limit_kind or "rate_limit",
            "timestamp": _iso(event.timestamp),
            "reset_at": _iso(event.reset_at) if event.reset_at else None,
            "percentage": None,
            "interpretation": "threshold_event_only; not a percentage or capacity anchor",
        }
        for session in corpus.sessions for node in _walk(session.root) for event in node.events
        if event.kind == "api_rejection" and query.since <= event.timestamp < query.until
    ]
    events.sort(key=lambda item: (item["timestamp"], item["event_id"]))
    return {
        "schema_version": "usage-windows-v1",
        "query_window": {
            "start": _iso(query.since),
            "end": _iso(query.until),
            "semantics": "[since, until)",
        },
        "observed_intervals": intervals,
        "observed_reset_boundaries": observed_boundaries,
        "reset_boundary_status": boundary_status,
        "session_anchor_status": "observed" if intervals_by_kind["five_hour"] else "unknown",
        "weekly_reset_status": "observed" if intervals_by_kind["weekly"] else "unknown",
        "rolling_analysis": rolling,
        "threshold_events": events,
        "estimated_limit_pct": None,
        "measure_basis": {
            "raw_token_units": "exact token sum; thinking is already a subset of output",
            "weighted_units": "experimental local score; not subscription usage",
            "api_equivalent_usd": "dated list-price estimate; not billed cost",
            "api_equivalent_usd_is_billed_cost": False,
            "estimates": True,
        },
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
        "price_provenance": (
            {
                "source_url": getattr(weights, "provenance", {}).get("pricing_url"),
                "retrieved_on": getattr(weights, "provenance", {}).get("retrieved_on"),
                "weights_sha256": getattr(weights, "source_sha256", None),
            }
            if weights is not None else None
        ),
        "window_semantics": {
            "verified_plan_scope": "Max plans only",
            "documented_scope": "For Max plans, Anthropic documents five-hour session resets and an account-assigned fixed weekly reset; semantics for other plans are unverified.",
            "source_url": "https://support.claude.com/en/articles/11049741-what-is-the-max-plan",
            "unverified": [
                "exact session start without two observed reset timestamps",
                "the token or model formula behind subscription-limit percentages",
                "other plan-specific or discretionary caps",
                "reset semantics for plans other than Max",
            ],
        },
        "coverage_note": "No percentage is inferred from a reset time or rejection. Other devices and shared account surfaces can consume the same allowance.",
    }
