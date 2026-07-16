from __future__ import annotations

from topos.config import ToposConfig
from topos.diag.rules import evaluate_rules
from topos.diag.score import pressure_breakdown, score_entity
from topos.model import Finding, Frame, MetricValue


def annotate(
    frame: Frame,
    config: ToposConfig,
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

    # Add host-level network loss/error diagnostics to the root entity
    _annotate_host_network_loss(frame)

    return frame


__all__ = ["annotate", "pressure_breakdown", "score_entity"]


def _needs_pressure(metric: MetricValue | None) -> bool:
    if metric is None or metric.v is None:
        return True
    return metric.src != "derived"


def _annotate_host_network_loss(frame: Frame) -> None:
    """Add host-network loss/error diagnostics to the root entity from host_meta.

    Produces a Finding on the root entity when any interface shows non-zero
    drop or error rates. The finding is explicitly host-scoped and does not
    attribute loss to any single cgroup.
    """
    root_key = ""
    root_ef = frame.entities.get(root_key)
    if root_ef is None:
        return
    meta = frame.host_meta
    if meta is None:
        return
    net_devices = meta.get("net_devices")
    if not isinstance(net_devices, list):
        return
    root_ef.findings = [
        finding for finding in root_ef.findings if finding.rule_id != "host_network_loss"
    ]

    dropping: list[str] = []
    for device in net_devices:
        if not isinstance(device, dict):
            continue
        name = str(device.get("name", "?"))
        rx_d = _positive_float(device.get("rx_drops_s"))
        tx_d = _positive_float(device.get("tx_drops_s"))
        rx_e = _positive_float(device.get("rx_errors_s"))
        tx_e = _positive_float(device.get("tx_errors_s"))
        reasons: list[str] = []
        if rx_d is not None:
            reasons.append(f"rx drops {rx_d:.1f}/s")
        if tx_d is not None:
            reasons.append(f"tx drops {tx_d:.1f}/s")
        if rx_e is not None:
            reasons.append(f"rx errors {rx_e:.1f}/s")
        if tx_e is not None:
            reasons.append(f"tx errors {tx_e:.1f}/s")
        if reasons:
            dropping.append(f"host interface {name} has loss/errors ({'; '.join(reasons)})")

    if not dropping:
        return

    detail = "; ".join(dropping)
    finding = Finding(
        rule_id="host_network_loss",
        severity="warn",
        message=(
            f"{detail}; per-cgroup attribution requires BPF. "
            f"These are host/interface-level counters and do not imply any "
            f"specific cgroup caused the loss."
        ),
        remedy="Check physical/virtual link state, NIC driver errors, ring buffer drops, or switch/network infrastructure issues.",
        source_metrics=(),
        confidence="exact",
    )
    root_ef.findings.append(finding)


def _positive_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
