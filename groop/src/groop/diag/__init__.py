from __future__ import annotations

from groop.config import GroopConfig
from groop.diag.rules import evaluate_rules
from groop.diag.score import pressure_breakdown, score_entity
from groop.model import Finding, Frame, MetricValue


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

    dropping: list[str] = []
    for device in net_devices:
        if not isinstance(device, dict):
            continue
        name = str(device.get("name", "?"))
        rx_d = device.get("rx_drops_s")
        tx_d = device.get("tx_drops_s")
        rx_e = device.get("rx_errors_s")
        tx_e = device.get("tx_errors_s")
        reasons: list[str] = []
        if rx_d is not None and float(rx_d) > 0:
            reasons.append(f"rx drops {float(rx_d):.1f}/s")
        if tx_d is not None and float(tx_d) > 0:
            reasons.append(f"tx drops {float(tx_d):.1f}/s")
        if rx_e is not None and float(rx_e) > 0:
            reasons.append(f"rx errors {float(rx_e):.1f}/s")
        if tx_e is not None and float(tx_e) > 0:
            reasons.append(f"tx errors {float(tx_e):.1f}/s")
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
