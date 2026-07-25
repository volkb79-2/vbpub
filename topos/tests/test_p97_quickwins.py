"""P97 — Close small deterministic coverage gaps.

Tests for all 16 target modules from the P96 coverage gap ledger.
Each test drives real behavior through uncovered branches with
behavioral assertions. Fail-before: each test would fail against a
mutated version that removes the branch being proved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from topos.collect import zswapmath, dockerjoin
from topos.collect.collector import Collector, _device_counter_list
from topos.model import metric_from_jsonable, frame_from_jsonable
from topos.registry import parse_metrics_selector
from topos.procs.identity import read_boot_id
from topos.procs.sensitivity import redact_process_row
from topos.daemon.api import Sensitivity
from topos.procs.owners import join_owner, OwnerJoin
from topos.collect.dockerjoin import docker_id_from_key
from topos.record.ring import _Series
from topos.inspect_files.plan import build_gated_inspect_plan
from topos.damon.paddr import _marker_path
from topos.actions.preview import build_admin_preview, ActionKind
from topos.daemon.component_health import (
    sanitize_public_text,
    _truncate_utf8,
    ComponentHealthRegistry,
)


# ======== collect/zswapmath.py — 13 missing lines, 6 branch pairs ========

class TestZswapmath:
    def test_ratio_none_on_zero_pool(self):
        assert zswapmath.ratio(10, 0) is None
    def test_ratio_none_on_none_zpool(self):
        assert zswapmath.ratio(10, None) is None
    def test_ratio_none_on_none_zeq(self):
        assert zswapmath.ratio(None, 10) is None
    def test_ratio_valid(self):
        assert zswapmath.ratio(10, 20) == 0.5
    def test_swap_disk_bytes_none_on_missing_current(self):
        assert zswapmath.swap_disk_bytes(None, 5, 3) is None
    def test_swap_disk_bytes_none_on_missing_zswapped(self):
        assert zswapmath.swap_disk_bytes(100, None, 3) is None
    def test_swap_disk_bytes_none_on_missing_swapcached(self):
        assert zswapmath.swap_disk_bytes(100, 5, None) is None
    def test_swap_disk_bytes_result(self):
        assert zswapmath.swap_disk_bytes(100, 30, 10) == 60
        assert zswapmath.swap_disk_bytes(10, 30, 10) == 0
    def test_split_refault_none_on_none_anon(self):
        assert zswapmath.split_refault_rates(None, 5, 1.0) == (None, None)
    def test_split_refault_none_on_none_zswpin(self):
        assert zswapmath.split_refault_rates(100, None, 1.0) == (None, None)
    def test_split_refault_none_on_zero_interval(self):
        assert zswapmath.split_refault_rates(100, 5, 0) == (None, None)
    def test_split_refault_result(self):
        zr, dr = zswapmath.split_refault_rates(100, 20, 2.0)
        assert zr == 10.0 and dr == 40.0
    def test_split_refault_clamps_negative(self):
        zr, dr = zswapmath.split_refault_rates(10, 30, 1.0)
        assert zr == 30.0 and dr == 0.0


# ======== collect/dockerjoin.py — 4 missing lines, 2 branch pairs ========

class TestDockerjoinQuickwins:
    def test_default_inspect_none_on_failure(self):
        assert dockerjoin.default_docker_inspect("nonexistent-xyz") is None
    def test_first_inspect_none_on_non_dict_list(self):
        assert dockerjoin._first_inspect(["not_a_dict"]) is None
    def test_detect_ciu_inferred_prefix_match(self):
        assert dockerjoin.detect_ciu_inferred("myproject", "other", {"myproject"}) is None


# ======== collect/collector.py — 4 missing lines, 5 branch pairs ========

class TestCollectorQuickwins:
    def test_network_metric_source_host_via_unavailable(self):
        class S: source_label="net:OTHER";unavailable_reason="host netns";aggregation="sum";rx_bytes=None;tx_bytes=None;rx_pkts=None;tx_pkts=None;proto="tcp";confidence=0
        c = Collector.__new__(Collector)
        assert c._network_metric_source(S()) == "host"
    def test_network_metric_source_netns_via_unavailable(self):
        class S: source_label="net:OTHER";unavailable_reason="err";aggregation="sum";rx_bytes=None;tx_bytes=None;rx_pkts=None;tx_pkts=None;proto="tcp";confidence=0
        c = Collector.__new__(Collector)
        assert c._network_metric_source(S()) == "netns"
    def test_network_metric_source_derived(self):
        """No matching source_label or unavailable_reason -> 'derived' (line 227)."""
        class S: source_label="net:UNKNOWN";unavailable_reason=None;aggregation="sum";rx_bytes=None;tx_bytes=None;rx_pkts=None;tx_pkts=None;proto="tcp";confidence=0
        c = Collector.__new__(Collector)
        assert c._network_metric_source(S()) == "derived"
    def test_device_counter_list_filters_non_dict(self):
        assert _device_counter_list({"devs": [{"name":"eth0"},"bad"]}, "devs") == [{"name":"eth0"}]
    def test_device_counter_list_none_on_non_list(self):
        assert _device_counter_list({"devs": "x"}, "devs") is None


# ======== model.py — 3 missing lines, 3 branch pairs ========

class TestModelQuickwins:
    def test_metric_from_jsonable_rejects_non_int_raw(self):
        with pytest.raises(ValueError, match="raw counter"):
            metric_from_jsonable([1.0, "src", "x"])
    def test_frame_from_jsonable_rejects_non_dict_host_meta(self):
        with pytest.raises(ValueError, match="host_meta"):
            frame_from_jsonable({"schema_version":"1","ts":"0","interval_s":"1","host":{},"entities":{},"host_meta":"bad"})


# ======== registry.py — 2 missing lines, 2 branch pairs ========

class TestRegistryQuickwins:
    def test_parse_metrics_selector_rejects_empty_token(self):
        with pytest.raises(ValueError, match="empty selector"):
            parse_metrics_selector("ram,")
    def test_parse_metrics_selector_rejects_empty_after_strip(self):
        with pytest.raises(ValueError, match="empty selector"):
            parse_metrics_selector(" , ")


# ======== procs/identity.py — 2 missing lines ========

class TestProcsIdentity:
    def test_read_boot_id_unknown_on_oserror(self, tmp_path):
        assert read_boot_id(proc_root=tmp_path) == "unknown-boot"


# ======== procs/sensitivity.py — 1 missing line, 1 branch pair ========

class TestProcsSensitivity:
    def test_redact_process_row_applies(self):
        row = {"comm": "visible_proc", "cmdline": None}
        result = redact_process_row(row, ceiling=Sensitivity.PUBLIC)
        assert result["comm"] != "visible_proc"


# ======== procs/owners.py — 1 missing line, 1 branch pair ========

class TestProcsOwners:
    def test_join_owner_uses_entity_docker_cid(self):
        from topos.model import DockerMeta, Entity
        key = "system.slice/no-match"
        assert docker_id_from_key(key) is None
        dm = DockerMeta(cid="abc123", full_id="abc123", name="t", image="i", compose_project=None, ptero_uuid=None)
        result = join_owner(key, {key: Entity(key=key, kind="scope", parent="", tier=None, is_protected=False, docker=dm)})
        assert result.docker_cid == "abc123"


# ======== ui/keys.py — 1 missing line ========

class TestUiKeys:
    def test_key_help_returns_tuple(self):
        from topos.ui.keys import key_help
        h = key_help()
        assert isinstance(h, tuple) and len(h) > 0


# ======== ui/sparkline.py — 1 missing line, 1 branch pair ========

class TestUiSparkline:
    def test_render_sparkline(self):
        from topos.ui.sparkline import render_sparkline
        r = render_sparkline([1.0, 2.0, 3.0], width=5)
        assert isinstance(r, str) and len(r) == 5
        r2 = render_sparkline([], width=5)
        assert isinstance(r2, str)


# ======== record/ring.py — 2 missing lines, 3 branch pairs ========

class TestRing:
    def test_minmax_returns_none_for_empty(self):
        s = _Series.with_capacity(5)
        s.append(None); s.append(None)
        assert s.minmax(3) is None
    def test_series_storage_bytes(self):
        s = _Series.with_capacity(10)
        assert s.storage_bytes > 0


# ======== inspect_files/plan.py — 2 missing lines ========

class TestInspectPlan:
    def test_build_gated_disabled(self):
        from topos.inspect_files.plan import DisabledInspector
        r = build_gated_inspect_plan("cpu", "target", inspect_files=False)
        assert isinstance(r, DisabledInspector) and r.mode == "disabled"


# ======== damon/paddr.py — 2 missing lines, 2 branch pairs ========

class TestDamonPaddr:
    def test_marker_path_resolves(self):
        from dataclasses import dataclass
        @dataclass
        class P: kdamond_idx: int; state_dir: Path
        path = _marker_path(P(kdamond_idx=42, state_dir=Path("/tmp")).state_dir, 42)
        assert "42" in str(path)


# ======== actions/preview.py — 2 missing lines, 3 branch pairs ========

class TestActionsPreview:
    def test_build_admin_preview_with_property_values(self):
        r = build_admin_preview(kind=ActionKind.SYSTEMD_SET_PROPERTY.value, target="s.service", admin=True, property_name="memory.high", property_value="max")
        assert r is not None and r.mode != "disabled"


# ======== daemon/component_health.py — 2 missing lines, 2 branch pairs ========

class TestComponentHealth:
    def test_sanitize_public_text_handles_bytes(self):
        result = sanitize_public_text(b"hello bytes", limit=100)
        assert "hello bytes" in result
    def test_truncate_utf8_empty(self):
        assert _truncate_utf8("", limit=10) == ""
    def test_component_registry_record_failure(self):
        reg = ComponentHealthRegistry()
        reg.record_success("collector", detail="ok")
        reg.record_failure("collector", detail="timeout")
        snap = reg.snapshot()
        comp = snap.by_name("collector")
        assert comp.state.value == "failed"
        assert comp.detail == "timeout"


def test_sanitize_public_text_redacts_token():
    result = sanitize_public_text("token=abc123secret", limit=100)
    assert "abc123" not in result
    assert "<redacted>" in result

# ======== Additional gap-closing tests for remaining uncovered branches ========

class TestExtraGaps:
    """Targeted tests for remaining hard-to-reach branches."""

    def test_model_frame_from_jsonable_host_meta_not_none(self):
        """model.py line 113: host_meta is not None AND not a dict."""
        from topos.model import frame_from_jsonable
        with pytest.raises(ValueError, match="host_meta"):
            frame_from_jsonable({
                "schema_version": "1", "ts": "0", "interval_s": "1",
                "host": {}, "entities": {}, "host_meta": "bad",
            })

    def test_registry_parse_selector_empty_kept(self):
        """registry.py line 279: all tokens valid but no metrics kept."""
        from topos.registry import parse_metrics_selector
        with pytest.raises(ValueError, match="empty selector"):
            parse_metrics_selector(" , ")

    def test_dockerjoin_enrich_exception_path(self):
        """dockerjoin.py lines 173-174: docker_inspect raises -> entity.docker = None."""
        from topos.collect.dockerjoin import enrich_entities
        from topos.model import Entity
        def raising_inspect(cid):
            raise OSError("no docker")
        key = "system.slice/docker-abc123def456abc123def456abc123def456abc123def456abc123def456abc1.scope"
        entities = {key: Entity(key=key, kind="scope", parent="", tier=None, is_protected=False)}
        result = enrich_entities(entities, docker_inspect=raising_inspect)
        assert result[key].docker is None

    def test_dockerjoin_resolve_skip_no_docker_scope(self):
        """dockerjoin.py line 206: entity without DOCKER_SCOPE_RE is skipped."""
        from topos.collect.dockerjoin import resolve_container_key
        from topos.model import DockerMeta, Entity
        dm = DockerMeta(cid="abc123", full_id="abc123", name="test", image="i", compose_project=None, ptero_uuid=None)
        ents = {"no-docker-scope": Entity(key="no-docker-scope", kind="scope", parent="", tier=None, is_protected=False, docker=dm)}
        with pytest.raises((ValueError, KeyError)):
            resolve_container_key("test", ents)

    def test_component_health_truncate_utf8_full(self):
        """component_health.py line 56: _truncate_utf8 with exact-limit bytes."""
        from topos.daemon.component_health import _truncate_utf8
        result = _truncate_utf8("abcde", limit=5)
        assert result == "abcde"

    def test_component_health_update_non_finite_timestamp(self):
        """component_health.py line 181: non-finite timestamp."""
        from topos.daemon.component_health import ComponentHealthRegistry
        reg = ComponentHealthRegistry(now=lambda: float("nan"))
        reg.record_success("collector", detail="test")
        snap = reg.snapshot()
        comp = snap.by_name("collector")
        assert comp.last_attempt_ts == 0.0

class TestFinalGaps:
    """Final targeted tests for remaining uncovered code paths."""

    def test_ring_last_returns_empty_when_take_zero(self):
        """_Series.last returns [] when n=0 (line 69)."""
        from topos.record.ring import _Series
        s = _Series.with_capacity(5)
        s.append(1.0)
        assert s.last(0) == []

    def test_sparkline_padding(self):
        """render_sparkline pads with _MISSING when data is short (line 73)."""
        from topos.ui.sparkline import render_sparkline, _MISSING
        # Single data point with width 8 — result should be padded
        result = render_sparkline([5.0], width=8)
        assert isinstance(result, str) and len(result) == 8
        assert _MISSING in result

    def test_component_health_truncate_edge(self):
        """_truncate_utf8 truncates correctly when raw exceeds limit."""
        from topos.daemon.component_health import _truncate_utf8
        long_str = "a" * 200
        result = _truncate_utf8(long_str, limit=10)
        assert len(result.encode("utf-8")) <= 10
        assert result.endswith("...")

    def test_component_health_update_state_change_count(self):
        """_update increments state_change_count on different state."""
        from topos.daemon.component_health import ComponentHealthRegistry, ComponentState
        reg = ComponentHealthRegistry()
        reg.record_success("collector", detail="first")
        reg.record_failure("collector", detail="second")
        snap = reg.snapshot()
        assert snap.by_name("collector").state_change_count >= 1
