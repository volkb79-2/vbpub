"""P100 — Close diagnostic scoring and rule coverage gaps.

Targets every missing line and branch in diag/__init__.py, diag/rules.py,
and diag/score.py. Extends the fixture patterns from test_diag.py.
"""

from __future__ import annotations

import pytest

from topos.config import ToposConfig
from topos.diag import annotate, _positive_float, _annotate_host_network_loss, _needs_pressure
from topos.diag.rules import evaluate_rules, _protected_disk_refault, _protected_file_refault, _memory_high_rising, _memory_high_user_visible, _io_cap_expected_throttle, _governance_drift, _socket_buffers_material, _host_netns_na, _confidence
from topos.diag.score import score_entity, _metric_confidence, _rounded_contributions
from topos.model import Entity, EntityFrame, Finding, Frame, MetricValue

CONFIG = ToposConfig()


def _base_metrics() -> dict[str, MetricValue]:
    return {
        "pressure": MetricValue(None, "unavail_kernel"),
        "ram": MetricValue(64, "exact"),
        "mem_high": MetricValue(128, "exact"),
        "psi_mem_full_avg10": MetricValue(0.0, "exact"),
        "psi_mem_some_avg10": MetricValue(0.0, "exact"),
        "psi_io_full_avg10": MetricValue(0.0, "exact"),
        "psi_io_some_avg10": MetricValue(0.0, "exact"),
        "psi_cpu_some_avg10": MetricValue(0.0, "exact"),
        "rf_d_per_s": MetricValue(0.0, "derived"),
        "rf_f_per_s": MetricValue(0.0, "derived"),
        "mem_events_high_per_s": MetricValue(0.0, "derived"),
        "mem_events_oom_kill_per_s": MetricValue(0.0, "derived"),
        "io_max_capped": MetricValue(0, "exact"),
        "sock": MetricValue(0, "exact"),
        "net_rx_pps": MetricValue(0.0, "netns"),
        "net_tx_pps": MetricValue(0.0, "netns"),
        "mem_min": MetricValue(0, "exact"),
        "mem_low": MetricValue(0, "exact"),
        "effective_memory_min": MetricValue(0, "derived"),
        "governance_drift": MetricValue(0, "derived"),
    }


def _eframe(metrics=None, *, protected=False, governance=None, network=None, findings=None) -> EntityFrame:
    entity = Entity(key="svc.scope", kind="scope", parent="", tier="prod", is_protected=protected)
    return EntityFrame(
        entity=entity,
        metrics={**_base_metrics(), **(metrics or {})},
        findings=list(findings or ()),
        governance=governance,
        network=network,
    )


# ====================================================================
# diag/__init__.py — 3 lines, 3 branches → CLOSED
# ====================================================================

class TestDiagInit:
    def test_annotate_host_network_loss_no_net_devices(self):
        frame = Frame(1, 100.0, 5.0, {}, {"": _eframe()})
        frame.host_meta = {"net_devices": "not_a_list"}
        _annotate_host_network_loss(frame)
        ef = frame.entities[""]  # root entity
        assert all(f.rule_id != "host_network_loss" for f in ef.findings)

    def test_annotate_host_network_loss_skips_non_dict_device(self):
        frame = Frame(1, 100.0, 5.0, {}, {"": _eframe()})
        frame.host_meta = {"net_devices": ["not_a_dict"]}
        _annotate_host_network_loss(frame)
        ef = frame.entities[""]  # root entity
        assert all(f.rule_id != "host_network_loss" for f in ef.findings)

    def test_annotate_host_network_loss_tx_errors(self):
        frame = Frame(1, 100.0, 5.0, {}, {"": _eframe()})
        frame.host_meta = {
            "net_devices": [{"name": "eth0", "rx_drops_s": 0, "tx_drops_s": 0, "rx_errors_s": 0, "tx_errors_s": 5.0}]
        }
        _annotate_host_network_loss(frame)
        ef = frame.entities[""]  # root entity
        finding = next((f for f in ef.findings if f.rule_id == "host_network_loss"), None)
        assert finding is not None
        assert "tx errors" in finding.message

    def test_positive_float_none(self):
        assert _positive_float(None) is None
    def test_positive_float_type_error(self):
        assert _positive_float("bad") is None
    def test_positive_float_non_positive(self):
        assert _positive_float(0.0) is None
        assert _positive_float(-1.0) is None
    def test_positive_float_valid(self):
        assert _positive_float(3.5) == 3.5
    def test_needs_pressure_none(self):
        assert _needs_pressure(None) is True
    def test_needs_pressure_none_value(self):
        assert _needs_pressure(MetricValue(None, "exact")) is True
    def test_needs_pressure_derived(self):
        assert _needs_pressure(MetricValue(1.0, "derived")) is False
    def test_needs_pressure_non_derived(self):
        assert _needs_pressure(MetricValue(1.0, "sysfs")) is True

    def test_annotate_preserve_existing_findings(self):
        finding = Finding(rule_id="existing", severity="warn", message="keep", remedy=None, source_metrics=(), confidence="exact")
        ef = _eframe(findings=[finding], protected=True)
        frame = Frame(1, 100.0, 5.0, {}, {"svc.scope": ef})
        annotate(frame, CONFIG, preserve_existing_findings=True)
        ids = [f.rule_id for f in ef.findings]
        assert "existing" in ids

    def test_annotate_host_network_loss_no_root(self):
        frame = Frame(1, 100.0, 5.0, {}, {})
        result = _annotate_host_network_loss(frame)
        assert result is None
        assert frame.entities == {}

    def test_annotate_host_network_loss_no_host_meta(self):
        ef = _eframe()
        frame = Frame(1, 100.0, 5.0, {}, {"": ef})
        frame.host_meta = None
        _annotate_host_network_loss(frame)
        assert all(f.rule_id != "host_network_loss" for f in ef.findings)


# ====================================================================
# diag/rules.py — gaps: line 207 (host net_* network-confidence arm)
# ====================================================================

class TestRulesGaps:
    def test_io_cap_expected_throttle_psi_below_warn(self):
        ef = _eframe({"psi_io_full_avg10": MetricValue(0.5, "exact"), "io_max_capped": MetricValue(1, "exact")})
        assert _io_cap_expected_throttle(ef, CONFIG) is None

    def test_governance_drift_detail_from_reasons(self):
        ef = _eframe(governance={"summary": {"drift": True, "severity": "red", "reasons": ["specific issue"]}})
        result = _governance_drift(ef, CONFIG)
        assert result is not None and "specific issue" in result.message

    def test_governance_drift_no_reasons(self):
        ef = _eframe(governance={"summary": {"drift": True, "severity": "warn"}})
        assert _governance_drift(ef, CONFIG) is not None

    def test_socket_buffers_material_pps_below_warn(self):
        ef = _eframe({"sock": MetricValue(1024*1024*64, "exact"), "net_rx_pps": MetricValue(10, "netns"), "net_tx_pps": MetricValue(10, "netns")})
        assert _socket_buffers_material(ef, CONFIG) is None

    def test_confidence_host_network_confidence(self):
        """_confidence uses network_confidence for host net_* metrics (line 207)."""
        ef = _eframe(
            {"net_rx_bps": MetricValue(1000.0, "host")},
            network={"confidence": "estimated"},
        )
        result = _confidence(ef, ("net_rx_bps",))
        assert result == "estimated"

    def test_confidence_estimated_in_values(self):
        ef = _eframe({"net_rx_pps": MetricValue(10.0, "netns")})
        assert _confidence(ef, ("net_rx_pps",)) == "estimated"

    def test_confidence_n_a_on_missing(self):
        ef = _eframe({"rf_d_per_s": MetricValue(None, "derived")})
        assert _confidence(ef, ("rf_d_per_s",)) == "n/a"

    def test_confidence_exact(self):
        ef = _eframe({"psi_mem_full_avg10": MetricValue(0.5, "exact")})
        assert _confidence(ef, ("psi_mem_full_avg10",)) == "exact"


class TestRulesAdditional:
    def test_protected_disk_refault_non_protected(self):
        assert _protected_disk_refault(_eframe(), CONFIG) is None
    def test_protected_disk_refault_no_rate(self):
        ef = _eframe({"rf_d_per_s": MetricValue(None, "derived")}, protected=True)
        assert _protected_disk_refault(ef, CONFIG) is None
    def test_protected_file_refault_no_rate(self):
        ef = _eframe({"rf_f_per_s": MetricValue(None, "derived")}, protected=True)
        assert _protected_file_refault(ef, CONFIG) is None
    def test_memory_high_rising_no_rate(self):
        ef = _eframe({"mem_events_high_per_s": MetricValue(None, "derived")})
        assert _memory_high_rising(ef, CONFIG) is None
    def test_memory_high_user_visible_no_metric(self):
        ef = _eframe({"ram": MetricValue(None, "exact")})
        assert _memory_high_user_visible(ef, CONFIG) is None
    def test_io_cap_expected_throttle_no_cap(self):
        ef = _eframe({"io_max_capped": MetricValue(0, "exact")})
        assert _io_cap_expected_throttle(ef, CONFIG) is None
    def test_governance_drift_no_summary(self):
        ef = _eframe(governance={})
        assert _governance_drift(ef, CONFIG) is None
    def test_host_netns_na_fires(self):
        ef = _eframe(network={"source_label": "net:N/A", "unavailable_reason": "host netns"})
        result = _host_netns_na(ef, CONFIG)
        assert result is not None and "host network" in result.message
    def test_host_netns_na_no_match(self):
        ef = _eframe(network={"source_label": "net:NS", "unavailable_reason": None})
        assert _host_netns_na(ef, CONFIG) is None


# ====================================================================
# diag/score.py — gaps: lines 136-137 (default_band is None)
# ====================================================================

class TestScoreGaps:
    def test_score_entity_default_band_none(self):
        """When default_band is None, band=None, normalized=0.0, score=0."""
        import topos.diag.score as score_mod
        from topos.diag.score import ScoreInput

        custom = ScoreInput(
            key="test_metric", label="Test", metrics=("test_metric",),
            weight_key="test_metric", threshold_key=None,
            default_band=None, detail="test",
        )
        original = score_mod._INPUTS
        score_mod._INPUTS = (custom,)
        try:
            entity = Entity(key="t", kind="scope", parent="", tier="prod", is_protected=False)
            ef = EntityFrame(entity=entity, metrics={"test_metric": MetricValue(5.0, "exact")})
            cfg = ToposConfig()
            cfg.diagnostics.score_weights["test_metric"] = 100.0
            result = score_entity(ef, cfg)
            assert result.score == 0
            assert len(result.contributions) == 1
            item = result.contributions[0]
            assert item["normalized"] == 0.0
            assert item["thresholds"] is None
            assert item["contribution"] == 0
        finally:
            score_mod._INPUTS = original

    def test_score_raw_sum_exceeds_100_scales_to_exact(self):
        """When raw_sum > 100, scaling produces exact expected contributions."""
        import topos.diag.score as score_mod
        from topos.diag.score import ScoreInput, ThresholdBand

        # Two metrics with high normalized values and high weights -> raw_sum >> 100
        custom1 = ScoreInput(key="m1", label="M1", metrics=("m1",), weight_key="m1",
                             threshold_key="psi_full_avg10", default_band=ThresholdBand(1.0, 2.0), detail="x")
        custom2 = ScoreInput(key="m2", label="M2", metrics=("m2",), weight_key="m2",
                             threshold_key="psi_full_avg10", default_band=ThresholdBand(1.0, 2.0), detail="x")
        original = score_mod._INPUTS
        score_mod._INPUTS = (custom1, custom2)
        try:
            entity = Entity(key="t", kind="scope", parent="", tier="prod", is_protected=False)
            ef = EntityFrame(entity=entity, metrics={
                "m1": MetricValue(10.0, "exact"),
                "m2": MetricValue(10.0, "exact"),
            })
            cfg = ToposConfig()
            cfg.diagnostics.score_weights["m1"] = 100.0
            cfg.diagnostics.score_weights["m2"] = 100.0
            result = score_entity(ef, cfg)
            # raw_sum = 200, scaled by 100/200 = 0.5 -> each contribution_raw = 50
            # floors = [50, 50], sum = 100, remainder = 0
            assert result.score == 100
            for item in result.contributions:
                assert item["contribution"] == 50
        finally:
            score_mod._INPUTS = original

    def test_metric_confidence_unavail_src(self):
        ef = _eframe({"psi_mem_full_avg10": MetricValue(1.0, "unavail_kernel")})
        assert _metric_confidence(ef, ("psi_mem_full_avg10",)) == "n/a"

    def test_metric_confidence_estimated_in_values(self):
        ef = _eframe({"net_rx_pps": MetricValue(10.0, "netns")})
        assert _metric_confidence(ef, ("net_rx_pps",)) == "estimated"

    def test_metric_confidence_network_confidence(self):
        ef = _eframe({"net_rx_bps": MetricValue(1000.0, "host")}, network={"confidence": "estimated"})
        assert _metric_confidence(ef, ("net_rx_bps",)) == "estimated"

    def test_metric_confidence_exact(self):
        ef = _eframe({"psi_mem_full_avg10": MetricValue(0.5, "sysfs")})
        assert _metric_confidence(ef, ("psi_mem_full_avg10",)) == "exact"

    def test_metric_confidence_all_n_a_rejected(self):
        ef = _eframe({"psi_mem_full_avg10": MetricValue(1.0, "unavail_kernel")})
        assert _metric_confidence(ef, ("psi_mem_full_avg10",)) == "n/a"

    def test_metric_confidence_mixed_exact_and_na(self):
        ef = _eframe({"psi_mem_full_avg10": MetricValue(1.0, "unavail_kernel"), "ram": MetricValue(64, "exact")})
        assert _metric_confidence(ef, ("psi_mem_full_avg10", "ram")) == "exact"

    def test_rounded_contributions_remainder_distribution(self):
        items = [{"contribution_raw": 3.7, "weight": 1.0}, {"contribution_raw": 1.3, "weight": 1.0}, {"contribution_raw": 0.0, "weight": 1.0}]
        result = _rounded_contributions(items, 6)
        assert sum(result) == 6
        assert result[0] >= result[1]
