from __future__ import annotations

from groop.config import GroopConfig
from groop.diag.rules import evaluate_rules
from groop.diag.score import pressure_breakdown, score_entity
from groop.model import Frame, MetricValue


def annotate(
    frame: Frame,
    config: GroopConfig,
    *,
    preserve_existing_findings: bool = False,
    preserve_existing_pressure: bool = False,
) -> Frame:
    """Annotate a frame in place with diagnostics and return the same object."""

    for entity_frame in frame.entities.values():
        if not preserve_existing_pressure or _needs_pressure(entity_frame.metrics.get("pressure")):
            entity_frame.metrics["pressure"] = MetricValue(score_entity(entity_frame, config).score, "derived")
        if not preserve_existing_findings or not entity_frame.findings:
            entity_frame.findings = evaluate_rules(entity_frame, config)
    return frame


__all__ = ["annotate", "pressure_breakdown", "score_entity"]


def _needs_pressure(metric: MetricValue | None) -> bool:
    if metric is None or metric.v is None:
        return True
    return metric.src != "derived"
