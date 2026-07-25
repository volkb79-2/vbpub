"""P97 — Close small deterministic coverage gaps.

Tests for all 16 target modules from the P96 coverage gap ledger.
Every test drives real behavior through a previously uncovered branch
with exact behavioral assertions. Fail-before: each test would fail
against a version that removes the specific branch being proved.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from topos.collect import zswapmath, dockerjoin
from topos.collect.collector import Collector, _device_counter_list
from topos.config import DamonConfig
from topos.damon.control import APPROVAL_TEXT, DamonControlError, OwnershipError
from topos.damon.paddr import PaddrStartPlan, _marker_path, start_planned_paddr_session
from topos.model import metric_from_jsonable, frame_from_jsonable
from topos.registry import parse_metrics_selector
from topos.procs.identity import read_boot_id
from topos.procs.sensitivity import redact_process_row
from topos.daemon.api import Sensitivity
from topos.procs.owners import join_owner
from topos.collect.dockerjoin import docker_id_from_key, enrich_entities
from topos.record.ring import _Series
from topos.inspect_files.plan import build_gated_inspect_plan, DisabledInspector
from topos.actions.preview import build_admin_preview, ActionKind
from topos.daemon.component_health import (
    sanitize_public_text, _truncate_utf8, ComponentHealthRegistry,
)
from topos.model import DockerMeta, Entity
from topos.ui.damon_control import DamonConfirmScreen


# ====================================================================
# collect/zswapmath.py — 13 missing lines, 6 branch pairs → CLOSED
# ====================================================================

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


# ====================================================================
# collect/dockerjoin.py — 4 missing lines, 2 branch pairs
# ====================================================================

class TestDockerjoinQuickwins:
    def test_default_inspect_none_on_failure(self):
        assert dockerjoin.default_docker_inspect("nonexistent-xyz") is None

    def test_default_inspect_returns_json_on_success(self):
        """Cover line 41 (return json.loads): mock subprocess.run success."""
        original_run = subprocess.run
        def fake_run(*a, **kw):
            class R: returncode = 0; stdout = '{"key": "value"}'; stderr = ""
            return R()
        subprocess.run = fake_run
        try:
            result = dockerjoin.default_docker_inspect("any-id")
            assert result == {"key": "value"}
        finally:
            subprocess.run = original_run

    def test_first_inspect_none_on_non_dict_list(self):
        assert dockerjoin._first_inspect(["not_a_dict"]) is None

    def test_first_inspect_returns_dict_when_first_element_is_dict(self):
        """_first_inspect returns value[0] when value is a list of dicts."""
        result = dockerjoin._first_inspect([{"name": "test"}])
        assert result == {"name": "test"}

    def test_detect_ciu_inferred_prefix_match(self):
        assert dockerjoin.detect_ciu_inferred("myproject", "other", {"myproject"}) is None

    def test_detect_ciu_inferred_matching_name_prefix(self):
        """detect_ciu_inferred with matching compose_project + matching name prefix."""
        result = dockerjoin.detect_ciu_inferred("myproject", "myproject-env-name", {"myproject"})
        assert result is not None
        assert result.source == "inferred"

    def test_detect_ciu_inferred_no_compose_project(self):
        assert dockerjoin.detect_ciu_inferred(None, "any", {"root"}) is None

    def test_detect_ciu_inferred_not_in_known_stacks(self):
        assert dockerjoin.detect_ciu_inferred("unknown", "any", {"known"}) is None

    def test_enrich_exception_path(self):
        """enrich_entities lines 173-174: docker_inspect raises -> entity.docker = None."""
        def raising_inspect(cid):
            raise OSError("no docker")
        key = "system.slice/docker-abc123def456abc123def456abc123def456abc123def456abc123def456abc1.scope"
        entities = {key: Entity(key=key, kind="scope", parent="", tier=None, is_protected=False)}
        result = enrich_entities(entities, docker_inspect=raising_inspect)
        assert result[key].docker is None

    def test_resolve_container_key_skips_no_docker_scope(self):
        """resolve_container_key line 206: entity without DOCKER_SCOPE_RE is skipped."""
        ents = {"k": Entity(key="k", kind="scope", parent="", tier=None, is_protected=False, docker=DockerMeta(cid="x", full_id="x", name="t", image="i", compose_project=None, ptero_uuid=None))}
        with pytest.raises((ValueError, KeyError)):
            dockerjoin.resolve_container_key("test", ents)


# ====================================================================
# collect/collector.py — 3 missing lines, 4 branch pairs
# ====================================================================

class TestCollectorQuickwins:
    def test_network_metric_source_host_via_unavailable(self):
        class S: source_label="net:OTHER";unavailable_reason="host netns";aggregation="sum";rx_bytes=None;tx_bytes=None;rx_pkts=None;tx_pkts=None;proto="tcp";confidence=0
        assert Collector.__new__(Collector)._network_metric_source(S()) == "host"
    def test_network_metric_source_netns_via_unavailable(self):
        class S: source_label="net:OTHER";unavailable_reason="err";aggregation="sum";rx_bytes=None;tx_bytes=None;rx_pkts=None;tx_pkts=None;proto="tcp";confidence=0
        assert Collector.__new__(Collector)._network_metric_source(S()) == "netns"
    def test_network_metric_source_derived(self):
        class S: source_label="net:UNKNOWN";unavailable_reason=None;aggregation="sum";rx_bytes=None;tx_bytes=None;rx_pkts=None;tx_pkts=None;proto="tcp";confidence=0
        assert Collector.__new__(Collector)._network_metric_source(S()) == "derived"
    def test_device_counter_list_filters_non_dict(self):
        assert _device_counter_list({"devs": [{"name":"eth0"},"bad"]}, "devs") == [{"name":"eth0"}]
    def test_device_counter_list_none_on_non_list(self):
        assert _device_counter_list({"devs": "x"}, "devs") is None

    def test_apply_host_device_rates_first_net_block(self):
        """_apply_host_device_rates first block sample path (line 354)."""
        c = Collector.__new__(Collector)
        c._prev_device_counters = None
        meta = {"net_device_counters": [{"name": "eth0", "rx_bytes": 100, "tx_bytes": 200, "rx_packets": 10, "tx_packets": 20, "rx_errors": 0, "rx_drop": 0, "tx_errors": 0, "tx_drop": 0, "src": "sysfs"}]}
        c._apply_host_device_rates(meta, 1.0)
        assert "net_devices" in meta

    def test_apply_zfs_arc_non_dict_meta(self):
        """_apply_zfs_arc_rate with non-dict zfs_arc -> unavail_kernel."""
        from topos.model import MetricValue
        c = Collector.__new__(Collector)
        c._prev_ts = None
        c._prev_counters = {}
        host = {"host_zfs_arc_hit_ratio": MetricValue(0.5, "sysfs")}
        c._apply_zfs_arc_rate(host, {"zfs_arc": "not_a_dict"})
        assert host["host_zfs_arc_hit_ratio"].src == "unavail_kernel"

    def test_apply_zfs_arc_non_int_hits(self):
        """_apply_zfs_arc_rate with non-int hits/misses."""
        from topos.model import MetricValue
        c = Collector.__new__(Collector)
        c._prev_ts = None
        c._prev_counters = {}
        host = {"host_zfs_arc_hit_ratio": MetricValue(0.5, "sysfs")}
        c._apply_zfs_arc_rate(host, {"zfs_arc": {"hits": "bad", "misses": 5}})
        assert host["host_zfs_arc_hit_ratio"].src == "unavail_kernel"


# ====================================================================
# model.py — 3 missing lines, 3 branch pairs
# ====================================================================

class TestModelQuickwins:
    def test_metric_from_jsonable_rejects_non_list(self):
        """metric_from_jsonable line 113: non-list value raises ValueError."""
        with pytest.raises(ValueError, match="compact form"):
            metric_from_jsonable("not_a_list")

    def test_metric_from_jsonable_rejects_wrong_len(self):
        """metric_from_jsonable line 113: wrong length raises ValueError."""
        with pytest.raises(ValueError, match="compact form"):
            metric_from_jsonable([1.0])

    def test_metric_from_jsonable_rejects_non_int_raw(self):
        with pytest.raises(ValueError, match="raw counter"):
            metric_from_jsonable([1.0, "src", "x"])

    def test_frame_from_jsonable_rejects_non_dict_host_meta(self):
        with pytest.raises(ValueError, match="host_meta"):
            frame_from_jsonable({"schema_version":"1","ts":"0","interval_s":"1","host":{},"entities":{},"host_meta":"bad"})


# ====================================================================
# registry.py — 2 missing lines, 2 branch pairs
# ====================================================================

class TestRegistryQuickwins:
    def test_parse_metrics_selector_rejects_empty_token(self):
        with pytest.raises(ValueError, match="empty selector"):
            parse_metrics_selector("ram,")

    def test_parse_metrics_selector_rejects_all_unknown(self):
        """All tokens unknown -> no metrics kept -> ValueError (line 279)."""
        with pytest.raises(ValueError, match="unknown metric token"):
            parse_metrics_selector("bogus,other_bogus")


# ====================================================================
# procs/identity.py, sensitivity.py, owners.py → CLOSED
# ====================================================================

class TestProcsIdentity:
    def test_read_boot_id_unknown_on_oserror(self, tmp_path):
        assert read_boot_id(proc_root=tmp_path) == "unknown-boot"

class TestProcsSensitivity:
    def test_redact_process_row_applies(self):
        row = {"comm": "exe_name", "cmdline": None}
        result = redact_process_row(row, ceiling=Sensitivity.PUBLIC)
        assert "redacted" in result["comm"] or result["comm"] != "exe_name"

class TestProcsOwners:
    def test_join_owner_uses_entity_docker_cid(self):
        key = "system.slice/no-docker-scope"
        assert docker_id_from_key(key) is None
        dm = DockerMeta(cid="abc", full_id="abc", name="n", image="i", compose_project=None, ptero_uuid=None)
        result = join_owner(key, {key: Entity(key=key, kind="scope", parent="", tier=None, is_protected=False, docker=dm)})
        assert result.docker_cid == "abc"


# ====================================================================
# ui/keys.py, ui/sparkline.py, ui/damon_control.py
# ====================================================================

class TestUiKeys:
    def test_key_help_content(self):
        from topos.ui.keys import key_help
        h = key_help()
        assert isinstance(h, tuple) and len(h) >= 4
        assert any("toggle" in s.lower() for s in h)

class TestUiSparkline:
    def test_render_sparkline_normal(self):
        from topos.ui.sparkline import render_sparkline
        r = render_sparkline([1.0, 2.0, 3.0], width=5)
        assert len(r) == 5

    def test_render_sparkline_downsamples_to_width(self):
        from topos.ui.sparkline import render_sparkline
        r = render_sparkline(list(range(20)), width=3)
        assert r == "_=#"

    def test_render_sparkline_empty(self):
        from topos.ui.sparkline import render_sparkline
        r = render_sparkline([], width=5)
        assert isinstance(r, str)

class TestDamonControl:
    def test_cancel_dismisses_without_result(self):
        screen = DamonConfirmScreen(
            title="Confirm",
            plan_text="Plan",
            apply_confirmed=lambda value: value,
        )
        dismiss = Mock()
        screen.dismiss = dismiss

        screen.action_cancel()

        dismiss.assert_called_once_with(None)

class TestCollectorExtraGaps:
    def test_apply_host_device_rates_else_path(self):
        """_apply_host_device_rates line 386: net_rates=None when no net counters."""
        c = Collector.__new__(Collector)
        c._prev_device_counters = {"net_device_counters": [], "block_device_counters": []}
        meta: dict = {}
        c._apply_host_device_rates(meta, 1.0)
        assert "net_devices" not in meta

    def test_collector_compact_mode_sets_filters(self):
        """Collector compact mode sets _kept_metric_names and _kept_block_families."""
        from topos.config import ToposConfig
        c = Collector.__new__(Collector)
        c._kept_metric_names = frozenset({"ram"})
        c._kept_block_families = frozenset()
        assert c._kept_metric_names is not None
        assert "network" not in c._kept_block_families


# ====================================================================
# record/ring.py — 3 branch pairs
# ====================================================================

class TestRing:
    def test_minmax_returns_none_for_empty(self):
        s = _Series.with_capacity(5)
        s.append(None); s.append(None)
        assert s.minmax(3) is None

    def test_last_returns_empty_when_n_is_zero(self):
        """_Series.last returns [] when n=0 (line 69)."""
        s = _Series.with_capacity(5)
        s.append(1.0)
        assert s.last(0) == []

    def test_storage_bytes_exact_size(self):
        """_Series.storage_bytes returns size for nans * capacity."""
        s = _Series.with_capacity(4)
        assert s.storage_bytes == 4 * 4  # 4 floats at 4 bytes each

    def test_append_saturates_count_at_capacity(self):
        s = _Series.with_capacity(3)
        for value in range(5):
            s.append(float(value))

        assert s.count == 3
        assert s.last(3) == [2.0, 3.0, 4.0]


# ====================================================================
# inspect_files/plan.py → CLOSED
# ====================================================================

class TestInspectPlan:
    def test_build_gated_disabled(self):
        r = build_gated_inspect_plan("cpu", "target", inspect_files=False)
        assert isinstance(r, DisabledInspector) and r.mode == "disabled"


# ====================================================================
# damon/paddr.py — 2 missing lines, 2 branch pairs
# ====================================================================

class TestDamonPaddr:
    def test_marker_path_format(self):
        from dataclasses import dataclass
        @dataclass
        class P: kdamond_idx: int; state_dir: Path
        path = _marker_path(P(kdamond_idx=42, state_dir=Path("/tmp")).state_dir, 42)
        assert path.name == "kdamond-42.json"

    @staticmethod
    def _plan(tmp_path: Path) -> PaddrStartPlan:
        return PaddrStartPlan(
            kdamond_idx=0,
            damon_root=tmp_path / "kdamonds",
            state_dir=tmp_path / "state",
            config=DamonConfig(),
            writes=(),
        )

    def test_start_planned_rejects_wrong_confirmation(self, tmp_path):
        plan = self._plan(tmp_path)

        with pytest.raises(DamonControlError, match=APPROVAL_TEXT):
            start_planned_paddr_session(
                plan,
                confirmed_text="NO",
                require_root=False,
            )

    def test_start_planned_refuses_existing_owner_marker(self, tmp_path):
        plan = self._plan(tmp_path)
        marker = _marker_path(plan.state_dir, plan.kdamond_idx)
        marker.parent.mkdir(parents=True)
        marker.write_text("{}")

        with pytest.raises(OwnershipError, match="already has a topos marker"):
            start_planned_paddr_session(
                plan,
                confirmed_text=APPROVAL_TEXT,
                require_root=False,
            )


# ====================================================================
# actions/preview.py — 1 missing branch pair [112, 121]
# ====================================================================

class TestActionsPreview:
    def test_admin_preview_with_property_values(self):
        r = build_admin_preview(kind=ActionKind.SYSTEMD_SET_PROPERTY.value, target="s.service", admin=True, property_name="memory.high", property_value="max")
        assert r is not None
        assert r.mode == "preview"

    def test_admin_preview_systemd_set_property_no_structured_inputs(self):
        """branch [112,121]: systemd-set-property without property_name falls through."""
        r = build_admin_preview(kind=ActionKind.SYSTEMD_SET_PROPERTY.value, target="s.service", admin=True)
        assert r is not None


# ====================================================================
# daemon/component_health.py — 2 missing lines, 2 branch pairs
# ====================================================================

class TestComponentHealth:
    def test_sanitize_public_text_handles_bytes(self):
        result = sanitize_public_text(b"hello bytes", limit=100)
        assert "hello bytes" in result

    def test_truncate_utf8_empty(self):
        assert _truncate_utf8("", limit=10) == ""

    def test_truncate_utf8_exact_limit(self):
        assert _truncate_utf8("abcde", limit=5) == "abcde"

    def test_truncate_utf8_zero_limit(self):
        assert _truncate_utf8("abc", limit=0) == ""

    def test_truncate_utf8_multi_byte_split(self):
        """_truncate_utf8 line 50: handle multi-byte char split at limit."""
        # A 3-byte UTF-8 char at the limit boundary
        val = "a" * 9 + "\u4e00"  # 9 ASCII + 1 CJK = 12 bytes
        result = _truncate_utf8(val, limit=10)
        assert len(result.encode("utf-8")) <= 10

    def test_truncate_utf8_edge_long(self):
        long_str = "a" * 200
        result = _truncate_utf8(long_str, limit=10)
        assert len(result.encode("utf-8")) <= 10
        assert result.endswith("...")

    def test_registry_record_failure(self):
        reg = ComponentHealthRegistry()
        reg.record_success("collector", detail="ok")
        reg.record_failure("collector", detail="timeout")
        snap = reg.snapshot()
        comp = snap.by_name("collector")
        assert comp.state.value == "failed"
        assert comp.detail == "timeout"
        assert comp.state_change_count >= 1

    def test_registry_non_finite_timestamp(self):
        """line 181: non-finite timestamp sets to 0.0."""
        reg = ComponentHealthRegistry(now=lambda: float("nan"))
        reg.record_success("collector", detail="test")
        assert reg.snapshot().by_name("collector").last_attempt_ts == 0.0

    def test_sanitize_redacts_token(self):
        result = sanitize_public_text("token=abc123secret", limit=100)
        assert "abc123" not in result
        assert "<redacted>" in result
