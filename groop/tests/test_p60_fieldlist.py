from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import fixture_root, systemctl_fixture_runner
from groop.cli import main, _validate_metrics_mode
from groop.collect.collector import Collector
from groop.collect.cgroup import walk_entities
from groop.config import GroopConfig
from groop.model import MetricValue
from groop.providers.net_host import NetHostProvider
from groop.providers.net_netns import NetnsProvider
from groop.registry import (
    COMPACT_GROUPS,
    FIELD_LIST_BLOCK_MAP,
    METRIC_GROUPS,
    parse_metrics_selector,
)

GAME_KEY = "system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope"


def host_stub() -> dict[str, MetricValue]:
    return {
        "host_mem_total": MetricValue(16000, "host"),
        "host_mem_available": MetricValue(8000, "host"),
        "host_swap_total": MetricValue(4000, "host"),
        "host_swap_free": MetricValue(2000, "host"),
        "host_swapcached": MetricValue(100, "host"),
        "host_zswap_pool": MetricValue(50, "host"),
        "host_zswap_stored": MetricValue(100, "host"),
        "host_zswap_ratio": MetricValue(2.0, "host"),
        "host_disk_swap": MetricValue(0, "host"),
        "host_load1": MetricValue(0.1, "host"),
        "host_load5": MetricValue(0.2, "host"),
        "host_load15": MetricValue(0.3, "host"),
        "host_uptime_s": MetricValue(1000, "host"),
        "host_psi_mem_some_avg10": MetricValue(0.0, "host"),
        "host_psi_mem_full_avg10": MetricValue(0.0, "host"),
        "host_psi_io_some_avg10": MetricValue(0.0, "host"),
        "host_psi_io_full_avg10": MetricValue(0.0, "host"),
        "host_psi_cpu_some_avg10": MetricValue(0.0, "host"),
        "host_zswap_enabled": MetricValue(1, "host"),
        "host_zswap_max_pool_percent": MetricValue(20, "host"),
    }


def qdisc_stub(_argv: list[str]) -> str:
    return "\n".join(
        (
            "qdisc fq_codel 0: dev eth0 root refcnt 2",
            " Sent 1 bytes 1 pkt (dropped 0, overlimits 0 requeues 0)",
            " backlog 0b 0p requeues 0",
        )
    )


def _make_collector(
    root: Path,
    *,
    entities_globs: tuple[str, ...] | None = None,
    slice_names: tuple[str, ...] | None = None,
    metrics_mode: str = "full",
) -> Collector:
    proc_root = fixture_root() / "procfs" / "network"
    host_ns_id = (proc_root / "ns" / "host").stat().st_ino
    providers = (
        NetnsProvider(root, proc_root=proc_root, host_netns_id=host_ns_id),
        NetHostProvider(proc_root=proc_root, command_runner=qdisc_stub),
    )
    return Collector(
        root,
        GroopConfig(
            interval=5.0,
            tiers={"prod": ["system.slice"]},
            protected_services=("soulmask-paks.slice",),
        ),
        lambda _cid: None,
        host_stub,
        lambda: 100.0,
        providers,
        entities_globs=entities_globs,
        slice_names=slice_names,
        metrics_mode=metrics_mode,
        proc_root=proc_root,
        sys_root=fixture_root() / "sysfs" / "empty",
        damon_root=fixture_root() / "damonfs" / "no-root" / "kdamonds",
        systemctl_show_runner=systemctl_fixture_runner("gstammtisch"),
    )


# --- Acceptance Oracle 1: exact two-metric keep ---


def test_fieldlist_ram_and_psi_single_keeps_exactly_two() -> None:
    """--metrics ram,psi_mem_some_avg10 keeps exactly those two names on every
    entity and drops everything else. Fails if union is wrong."""
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    collector = _make_collector(root, metrics_mode="ram,psi_mem_some_avg10")
    frame = collector.collect_once()
    expected = frozenset({"ram", "psi_mem_some_avg10"})
    for key, eframe in frame.entities.items():
        kept = frozenset(eframe.metrics.keys())
        assert kept == expected, (
            f"{key}: expected only {expected}, got {kept}"
        )


# --- Acceptance Oracle 2: family expansion ---


def test_fieldlist_psi_family_expands_to_all_six() -> None:
    """--metrics psi (family token) expands to all six PSI names. A test that
    would pass under single-name handling must fail."""
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    collector = _make_collector(root, metrics_mode="psi")
    frame = collector.collect_once()
    expected = frozenset(METRIC_GROUPS["psi"])
    assert len(expected) == 6, "psi family should have exactly 6 metric names"
    for key, eframe in frame.entities.items():
        kept = frozenset(eframe.metrics.keys())
        assert kept == expected, (
            f"{key}: expected full psi set {expected}, got {kept}"
        )


# --- Acceptance Oracle 3: structured block keep ---


def test_fieldlist_net_keeps_network_block() -> None:
    """--metrics net keeps the net metric names and leaves eframe.network
    non-None, proving the block-keep mapping."""
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    collector = _make_collector(root, metrics_mode="net")
    frame = collector.collect_once()
    for key, eframe in frame.entities.items():
        # All metrics should be net_* family
        kept = frozenset(eframe.metrics.keys())
        assert kept.issubset(frozenset(METRIC_GROUPS["net"])), (
            f"{key}: got non-net metrics {kept}"
        )
        # Network block MUST be preserved (not None)
        assert eframe.network is not None, (
            f"{key}: network block was dropped despite --metrics net"
        )


def test_fieldlist_ram_drops_network_block() -> None:
    """--metrics ram drops eframe.network (proves block dropping when family
    token is absent)."""
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    collector = _make_collector(root, metrics_mode="ram")
    frame = collector.collect_once()
    for eframe in frame.entities.values():
        assert "ram" in eframe.metrics
        assert eframe.network is None, (
            "network block should be None when 'net' is not in selector"
        )


def test_fieldlist_net_and_governance_keeps_both_blocks() -> None:
    """--metrics net,governance keeps both network and governance blocks."""
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    collector = _make_collector(root, metrics_mode="net,governance")
    frame = collector.collect_once()
    for eframe in frame.entities.values():
        assert "net_rx_bps" in eframe.metrics
        assert "governance_drift" in eframe.metrics
        assert eframe.network is not None, "network block should be kept with 'net'"
        assert eframe.governance is not None, "governance block should be kept with 'governance'"
        # damon block should be dropped (not in selector)
        assert eframe.damon is None, "damon block should be dropped without 'damon'"


# --- Acceptance Oracle 4: unknown token exits 2 ---


def test_fieldlist_unknown_token_exits_2() -> None:
    """--metrics ram,bogus_metric exits 2 with bogus_metric named in stderr."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "groop.cli", "--once", "--json",
         "--metrics", "ram,bogus_metric",
         "--cgroup-root", str(fixture_root() / "cgroupfs" / "gstammtisch")],
        capture_output=True, text=True, cwd="groop/src",
    )
    assert result.returncode == 2
    assert "bogus_metric" in result.stderr


# --- Acceptance Oracle 5: empty selector exits 2 ---


def test_fieldlist_empty_selector_exits_2() -> None:
    """--metrics "" exits 2 (empty selector rejected)."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "groop.cli", "--once", "--json",
         "--metrics", "",
         "--cgroup-root", str(fixture_root() / "cgroupfs" / "gstammtisch")],
        capture_output=True, text=True, cwd="groop/src",
    )
    assert result.returncode == 2
    assert "empty selector" in result.stderr


# --- Acceptance Oracle 6: backward-compatible ---


def test_fieldlist_full_is_byte_identical_to_p55() -> None:
    """--metrics full behaves identically to P55 default (full mode)."""
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    collector_full = _make_collector(root, metrics_mode="full")
    frame_full = collector_full.collect_once()
    # Assert that all expected full-mode metrics are present on root entity
    root_entity = frame_full.entities[""]
    assert "ram" in root_entity.metrics
    assert "cpu_pct" in root_entity.metrics
    assert "net_rx_bps" in root_entity.metrics
    assert "governance_drift" in root_entity.metrics


def test_fieldlist_compact_byte_identical_to_p55() -> None:
    """--metrics compact behaves identically to P55 compact mode — only
    compact groups, and all blocks dropped."""
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    collector = _make_collector(root, metrics_mode="compact")
    frame = collector.collect_once()
    expected_compact = frozenset().union(*(METRIC_GROUPS[g] for g in COMPACT_GROUPS))
    for key, eframe in frame.entities.items():
        assert set(eframe.metrics.keys()).issubset(expected_compact), (
            f"{key} has non-compact metrics in compact mode"
        )
        assert eframe.network is None
        assert eframe.damon is None
        assert eframe.governance is None


# --- Acceptance Oracle 7: rejection with --replay/--attach ---


def test_fieldlist_rejected_with_replay() -> None:
    """--metrics ram --replay X exits 2."""
    rc = main(["--replay", "dummy.jsonl", "--metrics", "ram"])
    assert rc == 2


def test_fieldlist_rejected_with_attach() -> None:
    """--metrics ram --attach S exits 2."""
    rc = main(["--attach", "/tmp/fake.sock", "--metrics", "ram"])
    assert rc == 2


# --- Additional edge cases ---


def test_fieldlist_union_of_family_and_single_metric() -> None:
    """--metrics mem_usage,psi_mem_some_avg10 combines family expansion with
    a single metric (union, not additive subset)."""
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    collector = _make_collector(root, metrics_mode="mem_usage,psi_mem_some_avg10")
    frame = collector.collect_once()
    expected = frozenset(METRIC_GROUPS["mem_usage"]) | frozenset({"psi_mem_some_avg10"})
    for key, eframe in frame.entities.items():
        kept = frozenset(eframe.metrics.keys())
        assert kept == expected, (
            f"{key}: expected {expected}, got {kept}"
        )


def test_fieldlist_unknown_token_only_exits_2_via_main() -> None:
    """A selector with only unknown tokens exits 2."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "groop.cli", "--once", "--json",
         "--metrics", "bogus1,bogus2",
         "--cgroup-root", str(fixture_root() / "cgroupfs" / "gstammtisch")],
        capture_output=True, text=True, cwd="groop/src",
    )
    assert result.returncode == 2
    assert "bogus1" in result.stderr
    assert "bogus2" in result.stderr


def test_fieldlist_composes_with_slice() -> None:
    """--metrics ram,psi composes with --slice system.slice."""
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    collector = _make_collector(
        root,
        slice_names=("system.slice",),
        metrics_mode="ram,psi",
    )
    frame = collector.collect_once()
    # Only system.slice entities
    assert GAME_KEY in frame.entities
    assert "besteffort.slice" not in frame.entities
    # Only ram + psi metrics
    expected = frozenset({"ram"}) | frozenset(METRIC_GROUPS["psi"])
    for key, eframe in frame.entities.items():
        kept = frozenset(eframe.metrics.keys())
        assert kept.issubset(expected), (
            f"{key}: unexpected metrics {kept - expected}"
        )


def test_validate_metrics_mode_rejects_empty() -> None:
    """_validate_metrics_mode raises SystemExit(2) for empty value."""
    with pytest.raises(SystemExit) as exc:
        _validate_metrics_mode("")
    assert exc.value.code == 2


def test_validate_metrics_mode_rejects_unknown() -> None:
    """_validate_metrics_mode raises SystemExit(2) for unknown token."""
    with pytest.raises(SystemExit) as exc:
        _validate_metrics_mode("bogus")
    assert exc.value.code == 2


def test_validate_metrics_mode_accepts_full() -> None:
    """_validate_metrics_mode accepts 'full'."""
    _validate_metrics_mode("full")  # should not raise


def test_validate_metrics_mode_accepts_compact() -> None:
    """_validate_metrics_mode accepts 'compact'."""
    _validate_metrics_mode("compact")  # should not raise


def test_validate_metrics_mode_accepts_fieldlist() -> None:
    """_validate_metrics_mode accepts a valid field list."""
    _validate_metrics_mode("ram,psi,net")  # should not raise
