"""Redaction classification gaps (B-17): proc_* metrics and source-less findings."""

from __future__ import annotations

import pytest

from topos.daemon import redaction
from topos.daemon.api import Sensitivity, metric_metadata, metric_sensitivity
from topos.daemon.redaction import PayloadShape, redaction_marker
from topos.registry import REGISTRY


def test_every_per_process_metric_is_sensitive() -> None:
    proc_metrics = [name for name in REGISTRY if name.startswith("proc_")]
    assert proc_metrics, "registry no longer has proc_* metrics; revisit this test"
    for name in proc_metrics:
        assert metric_sensitivity(name) is Sensitivity.SENSITIVE
        assert metric_metadata(name)["sensitivity"] == "sensitive"
    # Non-process metrics keep their classification.
    assert metric_sensitivity("host_cpu_pct" if "host_cpu_pct" in REGISTRY else "ram") is not Sensitivity.SENSITIVE


def _entity_payload(finding: dict) -> dict:
    return {
        "entity": {"key": "x", "kind": "scope", "parent": None},
        "metrics": {},
        "findings": [finding],
    }


@pytest.mark.parametrize("source_metrics", [[], None])
@pytest.mark.parametrize(
    ("ceiling", "hidden"),
    [(Sensitivity.PUBLIC, True), (Sensitivity.OPERATIONAL, True), (Sensitivity.SENSITIVE, False)],
)
def test_finding_without_source_metrics_is_sensitive(
    source_metrics: object, ceiling: Sensitivity, hidden: bool
) -> None:
    finding = {
        "rule_id": "r",
        "severity": "warn",
        "message": "worker pid 4242 holds 90 tasks",
        "remedy": "kill 4242",
    }
    if source_metrics is not None:
        finding["source_metrics"] = source_metrics
    payload = _entity_payload(finding)

    redaction.redact_payload(
        payload, shape=PayloadShape.ENTITY_FRAME, metrics_meta={}, ceiling=ceiling
    )

    out = payload["findings"][0]
    if hidden:
        assert out["message"] == redaction_marker(Sensitivity.SENSITIVE)
        assert out["remedy"] == redaction_marker(Sensitivity.SENSITIVE)
        assert out["rule_id"] == "r"
    else:
        assert out["message"] == "worker pid 4242 holds 90 tasks"
