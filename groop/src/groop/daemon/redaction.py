"""P81 — the single server-side redaction enforcement point.

Both read frontends (the HTTP gateway and the MCP stdio server) route every
value-bearing payload through :func:`redact_payload` before serialization.
Neither frontend keeps a local redaction walk: this module owns metric
classification, the marker dialect, and every typed shape visitor.

The enforcement point **fails closed**:

* A metric absent from ``metrics_meta`` is classified ``sensitive``
  (:func:`classify_metric`), so an unclassified value is hidden below the
  ``sensitive`` ceiling.
* Every shape visitor recognizes a *closed* set of fields.  A field a visitor
  does not recognize (a future ``governance``/``network``/``damon``/``host_meta``
  container, or a brand-new value-bearing field) is replaced with the typed
  marker rather than emitted above the ceiling.  Adding a value-bearing field
  therefore cannot silently widen the boundary — a typed visitor must be
  registered first.
* An unregistered :class:`PayloadShape` raises :class:`RedactionError` instead
  of shipping unredacted bytes.

One marker dialect (CONTRACTS §10): a redacted value is always
``{"redacted": True, "sensitivity": "<level>"}``.  The bare ``"__redacted__"``
string the MCP frontend used before P81 is gone.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any, Callable

from groop.daemon.api import Sensitivity

__all__ = [
    "PayloadShape",
    "RedactionError",
    "classify_metric",
    "redaction_marker",
    "redact_payload",
]


# Numeric ordering of the closed Sensitivity enum. A value is redacted when its
# classification outranks the principal's ceiling.
_RANK: dict[str, int] = {
    Sensitivity.PUBLIC.value: 0,
    Sensitivity.OPERATIONAL.value: 1,
    Sensitivity.SENSITIVE.value: 2,
}


class RedactionError(RuntimeError):
    """Fail-closed refusal to serialize a payload the enforcement point cannot redact."""


class PayloadShape(str, Enum):
    """Closed set of payload shapes the enforcement point can redact.

    A frontend must name the shape it emits.  Registering a visitor for a new
    shape is the only way to widen what the boundary will pass; an unregistered
    shape is a fail-closed error.
    """

    FRAME = "frame"  # gateway current/history: one jsonable Frame sub-dict
    ENTITY_FRAME = "entity_frame"  # gateway entity route: one jsonable EntityFrame
    MCP_OVERVIEW = "mcp_overview"  # {sort_by, rows:[{key,metric,value,...}]}
    MCP_ENTITY = "mcp_entity"  # {key,kind,...,metrics:{name:{value,...}},findings:[...]}
    MCP_HISTORY = "mcp_history"  # {entity_key,metric,sensitivity,series:[[ts,value]],count}


def redaction_marker(sensitivity: Sensitivity | str) -> dict[str, object]:
    """Return the one redaction marker dialect for a sensitivity level."""
    value = sensitivity.value if isinstance(sensitivity, Sensitivity) else sensitivity
    return {"redacted": True, "sensitivity": value}


def classify_metric(name: object, metrics_meta: Mapping[str, Mapping[str, object]]) -> Sensitivity:
    """Classify a metric, failing closed to ``sensitive`` when unclassified.

    Trust the daemon's ``metrics_meta`` when it carries a valid closed-enum
    ``sensitivity`` for ``name``; otherwise the metric is treated as
    ``sensitive`` so an unknown value is hidden below that ceiling.
    """
    metadata = metrics_meta.get(name) if isinstance(name, str) else None
    raw = metadata.get("sensitivity") if isinstance(metadata, Mapping) else None
    if isinstance(raw, str) and raw in _RANK:
        return Sensitivity(raw)
    return Sensitivity.SENSITIVE


def _above(sensitivity: Sensitivity, ceiling: Sensitivity) -> bool:
    return _RANK[sensitivity.value] > _RANK[ceiling.value]


# --- metric-map / finding primitives (shared by every shape) -------------


def _redact_metric_map(
    metrics: dict[str, Any], metrics_meta: Mapping[str, Mapping[str, object]], ceiling: Sensitivity
) -> None:
    """Replace each above-ceiling metric value with the marker; keep keys."""
    for name in list(metrics):
        sensitivity = classify_metric(name, metrics_meta)
        if _above(sensitivity, ceiling):
            metrics[name] = redaction_marker(sensitivity)


def _finding_ceiling_breach(
    source_metrics: object, metrics_meta: Mapping[str, Mapping[str, object]], ceiling: Sensitivity
) -> Sensitivity | None:
    """Return the worst above-ceiling sensitivity named by a finding, else None."""
    if not isinstance(source_metrics, Sequence) or isinstance(source_metrics, (str, bytes)):
        return None
    worst: Sensitivity | None = None
    for metric in source_metrics:
        sensitivity = classify_metric(metric, metrics_meta)
        if _above(sensitivity, ceiling) and (worst is None or _RANK[sensitivity.value] > _RANK[worst.value]):
            worst = sensitivity
    return worst


def _redact_finding(
    finding: dict[str, Any], metrics_meta: Mapping[str, Mapping[str, object]], ceiling: Sensitivity
) -> None:
    """Redact a finding's free-text prose when a named source metric is above ceiling.

    ``rule_id``, ``severity``, and ``source_metrics`` are operational facts and
    stay; ``message`` (and ``remedy`` when present) become the typed marker.
    """
    breach = _finding_ceiling_breach(finding.get("source_metrics"), metrics_meta, ceiling)
    if breach is None:
        return
    marker = redaction_marker(breach)
    if "message" in finding:
        finding["message"] = marker
    if finding.get("remedy") is not None:
        finding["remedy"] = marker


# --- shape visitors ------------------------------------------------------
#
# Each visitor mutates its payload in place. Every visitor recognizes a closed
# set of fields; an unrecognized field is failed closed to the marker.

# ``entity`` is pure identity/config metadata (key/kind/parent/docker/tier/ciu);
# it carries no metric telemetry and passes through.
_ENTITY_FRAME_PASSTHROUGH = frozenset({"entity"})
_FRAME_PASSTHROUGH = frozenset({"schema_version", "ts", "interval_s"})


def _visit_entity_frame(
    entity_frame: dict[str, Any], metrics_meta: Mapping[str, Mapping[str, object]], ceiling: Sensitivity
) -> None:
    for field in list(entity_frame):
        if field in _ENTITY_FRAME_PASSTHROUGH:
            continue
        if field == "metrics":
            if isinstance(entity_frame[field], dict):
                _redact_metric_map(entity_frame[field], metrics_meta, ceiling)
            continue
        if field == "findings":
            findings = entity_frame[field]
            if isinstance(findings, list):
                for finding in findings:
                    if isinstance(finding, dict):
                        _redact_finding(finding, metrics_meta, ceiling)
            continue
        # governance / network / damon / any future value-bearing container:
        # no typed visitor classifies its internals, so fail closed.
        entity_frame[field] = redaction_marker(Sensitivity.SENSITIVE)


def _visit_frame(
    frame: dict[str, Any], metrics_meta: Mapping[str, Mapping[str, object]], ceiling: Sensitivity
) -> None:
    for field in list(frame):
        if field in _FRAME_PASSTHROUGH:
            continue
        if field == "host":
            if isinstance(frame[field], dict):
                _redact_metric_map(frame[field], metrics_meta, ceiling)
            continue
        if field == "entities":
            entities = frame[field]
            if isinstance(entities, dict):
                for entity_frame in entities.values():
                    if isinstance(entity_frame, dict):
                        _visit_entity_frame(entity_frame, metrics_meta, ceiling)
            continue
        # host_meta or any future top-level value-bearing field: fail closed.
        frame[field] = redaction_marker(Sensitivity.SENSITIVE)


def _visit_mcp_overview(
    payload: dict[str, Any], metrics_meta: Mapping[str, Mapping[str, object]], ceiling: Sensitivity
) -> None:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict) or "value" not in row:
            continue
        sensitivity = classify_metric(row.get("metric"), metrics_meta)
        if _above(sensitivity, ceiling):
            row["value"] = redaction_marker(sensitivity)


def _visit_mcp_entity(
    payload: dict[str, Any], metrics_meta: Mapping[str, Mapping[str, object]], ceiling: Sensitivity
) -> None:
    metrics = payload.get("metrics")
    if isinstance(metrics, dict):
        for name, metric in metrics.items():
            if not isinstance(metric, dict) or "value" not in metric:
                continue
            sensitivity = classify_metric(name, metrics_meta)
            if _above(sensitivity, ceiling):
                metric["value"] = redaction_marker(sensitivity)
    findings = payload.get("findings")
    if isinstance(findings, list):
        for finding in findings:
            if isinstance(finding, dict):
                _redact_finding(finding, metrics_meta, ceiling)


def _visit_mcp_history(
    payload: dict[str, Any], metrics_meta: Mapping[str, Mapping[str, object]], ceiling: Sensitivity
) -> None:
    sensitivity = classify_metric(payload.get("metric"), metrics_meta)
    if not _above(sensitivity, ceiling):
        return
    series = payload.get("series")
    if not isinstance(series, list):
        return
    marker = redaction_marker(sensitivity)
    for point in series:
        if isinstance(point, list) and len(point) >= 2:
            point[1] = marker


_Visitor = Callable[[dict[str, Any], Mapping[str, Mapping[str, object]], Sensitivity], None]

_VISITORS: dict[PayloadShape, _Visitor] = {
    PayloadShape.FRAME: _visit_frame,
    PayloadShape.ENTITY_FRAME: _visit_entity_frame,
    PayloadShape.MCP_OVERVIEW: _visit_mcp_overview,
    PayloadShape.MCP_ENTITY: _visit_mcp_entity,
    PayloadShape.MCP_HISTORY: _visit_mcp_history,
}


def redact_payload(
    payload: dict[str, Any],
    *,
    shape: PayloadShape,
    metrics_meta: Mapping[str, Mapping[str, object]] | None,
    ceiling: Sensitivity | None,
) -> dict[str, Any]:
    """Enforce ``ceiling`` on ``payload`` in place and return it.

    ``ceiling is None`` means the principal may see everything: no redaction.
    Otherwise the visitor registered for ``shape`` redacts every above-ceiling
    value.  An unregistered shape raises :class:`RedactionError` (fail closed).

    This is the *only* redaction entry point; both frontends call it and keep no
    redaction walk of their own.  Disarming this function (making it the
    identity) must make the redaction oracles go red — that is the boundary this
    package exists to protect.
    """
    if ceiling is None:
        return payload
    visitor = _VISITORS.get(shape)
    if visitor is None:
        raise RedactionError(f"no redaction visitor registered for shape {shape!r}")
    meta = metrics_meta if isinstance(metrics_meta, Mapping) else {}
    visitor(payload, meta, ceiling)
    return payload
