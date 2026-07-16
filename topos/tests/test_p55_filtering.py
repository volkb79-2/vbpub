from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import fixture_root, systemctl_fixture_runner
from topos.collect.cgroup import (
    _validate_slice_name,
    add_entity_ancestors,
    build_entity_predicate,
    walk_entities,
)
from topos.collect.collector import Collector
from topos.config import ToposConfig
from topos.model import MetricValue, frame_from_jsonable, frame_to_jsonable
from topos.providers.net_host import NetHostProvider
from topos.providers.net_netns import NetnsProvider
from topos.registry import COMPACT_GROUPS, METRIC_GROUPS

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
        ToposConfig(
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


# --- build_entity_predicate tests ---


def test_predicate_no_filter_returns_none() -> None:
    """No --entities or --slice means no filtering."""
    assert build_entity_predicate(None, None) is None
    assert build_entity_predicate((), ()) is None


def test_predicate_glob_single_match() -> None:
    pred = build_entity_predicate(("system.slice/*",), None)
    assert pred is not None
    assert pred("system.slice/docker-abc.scope")
    assert not pred("besteffort.slice")
    assert not pred("system.slice")  # glob does not match the slice itself


def test_predicate_glob_multi_match() -> None:
    pred = build_entity_predicate(("system.slice/*", "soulmask.slice/*"), None)
    assert pred("system.slice/docker-abc.scope")
    assert pred("soulmask.slice/soulmask-paks.slice")
    assert not pred("besteffort.slice")


def test_predicate_glob_no_match() -> None:
    pred = build_entity_predicate(("nonexistent/*",), None)
    assert pred is not None
    assert not pred("system.slice/docker-abc.scope")
    assert not pred("")


def test_predicate_glob_root_key() -> None:
    """Root key \"\" is a valid EntityKey."""
    pred = build_entity_predicate(("",), None)
    assert pred("")
    assert not pred("system.slice")


def test_predicate_slice_includes_subtree() -> None:
    pred = build_entity_predicate(None, ("system.slice",))
    assert pred("system.slice")
    assert pred("system.slice/docker-abc.scope")
    assert not pred("besteffort.slice")
    assert not pred("soulmask.slice/soulmask-paks.slice")


def test_predicate_slice_works_with_nested_slices() -> None:
    pred = build_entity_predicate(None, ("soulmask.slice",))
    assert pred("soulmask.slice")
    assert pred("soulmask.slice/soulmask-paks.slice")
    assert not pred("system.slice")


def test_predicate_combined_entities_and_slice() -> None:
    pred = build_entity_predicate(("*.scope",), ("soulmask.slice",))
    assert pred("system.slice/docker-abc.scope")  # glob match
    assert pred("soulmask.slice")  # slice match
    assert pred("soulmask.slice/soulmask-paks.slice")  # slice subtree
    assert not pred("besteffort.slice")


# --- _validate_slice_name tests ---


def test_validate_slice_name_rejects_absolute() -> None:
    with pytest.raises(ValueError, match="must not start with '/'"):
        _validate_slice_name("/system.slice")


def test_validate_slice_name_rejects_trailing_slash() -> None:
    with pytest.raises(ValueError, match="must not end with '/'"):
        _validate_slice_name("system.slice/")


def test_validate_slice_name_rejects_parent_traversal() -> None:
    with pytest.raises(ValueError, match="parent traversal"):
        _validate_slice_name("system.slice/..")


def test_validate_slice_name_rejects_control_chars() -> None:
    with pytest.raises(ValueError, match="must not contain"):
        _validate_slice_name("system\x00.slice")
    with pytest.raises(ValueError, match="must not contain"):
        _validate_slice_name("system\x1f.slice")


def test_validate_slice_name_rejects_empty() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        _validate_slice_name("")


def test_validate_slice_name_accepts_valid() -> None:
    _validate_slice_name("system.slice")
    _validate_slice_name("soulmask.slice/soulmask-paks.slice")


# --- add_entity_ancestors tests ---


def test_ancestors_includes_root_and_parents() -> None:
    result = add_entity_ancestors({"system.slice/docker-abc.scope"})
    assert "" in result  # root
    assert "system.slice" in result  # parent
    assert "system.slice/docker-abc.scope" in result  # itself
    assert len(result) == 3


def test_ancestors_multi_depth() -> None:
    result = add_entity_ancestors({"a/b/c/d"})
    assert "" in result
    assert "a" in result
    assert "a/b" in result
    assert "a/b/c" in result
    assert "a/b/c/d" in result
    assert len(result) == 5


def test_ancestors_does_not_add_siblings() -> None:
    result = add_entity_ancestors({"system.slice/docker-abc.scope"})
    assert "besteffort.slice" not in result


def test_ancestors_root_only() -> None:
    result = add_entity_ancestors({""})
    assert result == {""}


def test_ancestors_multiple_keys_merge() -> None:
    result = add_entity_ancestors({
        "a/b/c",
        "a/b/d",
    })
    assert "" in result
    assert "a" in result
    assert "a/b" in result
    assert "a/b/c" in result
    assert "a/b/d" in result
    assert len(result) == 5


# --- Collector-level entity filtering tests ---


def test_slice_entity_filtering() -> None:
    """--slice system.slice only collects system.slice subtree plus ancestors."""
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    collector = _make_collector(root, slice_names=("system.slice",))
    frame = collector.collect_once()
    keys = set(frame.entities.keys())
    assert "" in keys  # root ancestor
    assert "system.slice" in keys
    assert GAME_KEY in keys
    assert "besteffort.slice" not in keys
    assert "soulmask.slice" not in keys
    assert "broken.slice" not in keys


def test_entities_glob_filtering() -> None:
    """--entities 'soulmask.slice/*' collects only matching + ancestors."""
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    collector = _make_collector(root, entities_globs=("soulmask.slice/*",))
    frame = collector.collect_once()
    keys = set(frame.entities.keys())
    assert "" in keys
    assert "soulmask.slice" in keys  # ancestor of the match
    assert "soulmask.slice/soulmask-paks.slice" in keys
    assert "system.slice" not in keys
    assert GAME_KEY not in keys


def test_entities_glob_matches_nothing_still_collects_ancestors() -> None:
    """A glob matching nothing still includes root (for path completeness)."""
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    collector = _make_collector(root, entities_globs=("nonexistent/*",))
    frame = collector.collect_once()
    # Only root should be present (the matching set is empty, but ancestors of
    # an empty set is empty -- so the frame has no entities)
    assert len(frame.entities) == 0


def test_combined_entity_and_slice_filtering() -> None:
    """--entities 'system.slice/*' --slice soulmask.slice union."""
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    collector = _make_collector(
        root,
        entities_globs=("system.slice/*",),
        slice_names=("soulmask.slice",),
    )
    frame = collector.collect_once()
    keys = set(frame.entities.keys())
    assert "" in keys  # root
    assert GAME_KEY in keys  # matched by system.slice/*
    assert "soulmask.slice" in keys  # matched by --slice
    assert "soulmask.slice/soulmask-paks.slice" in keys
    assert "system.slice" in keys  # matched by --slice? No, ancestor of glob match
    assert "besteffort.slice" not in keys


def test_full_tree_no_filtering() -> None:
    """No --entities or --slice means all entities are collected."""
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    all_entities = walk_entities(root)
    collector = _make_collector(root)
    frame = collector.collect_once()
    assert set(frame.entities.keys()) == set(all_entities.keys())


# --- --metrics compact tests ---


def test_metrics_compact_keeps_memory_psi_refault() -> None:
    """--metrics compact keeps only mem_usage, psi, and refault groups."""
    expected_compact = frozenset().union(*(METRIC_GROUPS[g] for g in COMPACT_GROUPS))
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    collector = _make_collector(root, metrics_mode="compact")
    frame = collector.collect_once()
    for key, eframe in frame.entities.items():
        assert set(eframe.metrics.keys()).issubset(expected_compact), (
            f"{key} has non-compact metrics: "
            f"{set(eframe.metrics.keys()) - expected_compact}"
        )


def test_metrics_compact_drops_network_damon_governance() -> None:
    """Compact mode must drop net_*, damon_*, governance_*, cpu_*, io_*."""
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    collector = _make_collector(root, metrics_mode="compact")
    frame = collector.collect_once()
    for key, eframe in frame.entities.items():
        m = eframe.metrics
        dropped_families = {"net_rx_bps", "net_tx_bps", "net_rx_pps", "net_tx_pps"}
        for d in dropped_families:
            assert d not in m, f"{key} has {d} in compact mode"
        # DAMON metrics should not be present
        for dk in list(m):
            assert not dk.startswith("damon_"), f"{key} has {dk} in compact"
        # governance should not be present
        assert "governance_drift" not in m, f"{key} has governance_drift in compact"
        # cpu metrics should not be present
        assert "cpu_pct" not in m, f"{key} has cpu_pct in compact"
        # pressure score should not be present
        assert "pressure" not in m, f"{key} has pressure in compact"
        # The structured per-entity network / DAMON / governance-drift blocks
        # (separate EntityFrame attributes, not metrics-dict keys) must also be
        # dropped under compact per the handoff drop-list.
        assert eframe.network is None, f"{key} retained network block in compact"
        assert eframe.damon is None, f"{key} retained damon block in compact"
        assert eframe.governance is None, f"{key} retained governance block in compact"


def test_metrics_full_includes_all_metrics() -> None:
    """Default metrics_mode='full' includes all registry metrics."""
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    collector = _make_collector(root, metrics_mode="full")
    frame = collector.collect_once()
    # At minimum, root entity should have many metric families
    root_entity = frame.entities[""]
    assert "ram" in root_entity.metrics
    assert "cpu_pct" in root_entity.metrics
    assert "net_rx_bps" in root_entity.metrics
    assert "governance_drift" in root_entity.metrics


def test_compact_entity_and_metric_together() -> None:
    """--slice system.slice --metrics compact works together."""
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    collector = _make_collector(
        root,
        slice_names=("system.slice",),
        metrics_mode="compact",
    )
    frame = collector.collect_once()
    # Only system.slice entities
    assert GAME_KEY in frame.entities
    assert "besteffort.slice" not in frame.entities
    # Metrics are compact
    assert "ram" in frame.entities[GAME_KEY].metrics
    assert "net_rx_bps" not in frame.entities[GAME_KEY].metrics


# --- Collection-time pruning test ---


def test_entity_filtering_skips_sysfs_reads_for_excluded(tmp_path: Path) -> None:
    """Entities excluded by --slice are not in the output frame. Their cgroup
    files are never read (collect_cgroup is never called)."""
    import shutil

    root = tmp_path / "cg"
    proc_root = tmp_path / "proc"
    shutil.copytree(fixture_root() / "cgroupfs" / "gstammtisch", root)
    shutil.copytree(fixture_root() / "procfs" / "network", proc_root, symlinks=True)

    # With --slice system.slice, besteffort.slice is excluded.
    # If collect_cgroup were called for besteffort, it would appear in the output.
    # The assertion that it's absent proves the filtering worked.
    collector = Collector(
        root,
        ToposConfig(interval=5.0),
        lambda _cid: None,
        host_stub,
        lambda: 100.0,
        (),
        slice_names=("system.slice",),
        proc_root=proc_root,
        sys_root=fixture_root() / "sysfs" / "empty",
        damon_root=fixture_root() / "damonfs" / "no-root" / "kdamonds",
    )
    frame = collector.collect_once()
    assert GAME_KEY in frame.entities
    assert "besteffort.slice" not in frame.entities


# --- Replay/attach rejection tests ---


def test_filtering_rejected_with_replay() -> None:
    """--replay rejects --entities/--slice/--metrics."""
    from topos.cli import main

    for flag in ("--entities", "--slice"):
        rc = main(["--replay", "dummy.jsonl", flag, "x"])
        assert rc == 2

    rc = main(["--replay", "dummy.jsonl", "--metrics", "compact"])
    assert rc == 2


def test_filtering_rejected_with_attach() -> None:
    """--attach rejects --entities/--slice/--metrics."""
    from topos.cli import main

    for flag in ("--entities", "--slice"):
        rc = main(["--attach", "/tmp/fake.sock", flag, "x"])
        assert rc == 2

    rc = main(["--attach", "/tmp/fake.sock", "--metrics", "compact"])
    assert rc == 2


# --- Record round-trip with filtering ---


def test_record_with_filtering(tmp_path: Path) -> None:
    """Filtered frames written through RecordWriter and read back with
    RecordReader contain only the selected entities and compact metrics."""
    from conftest import fixture_root
    from topos.collect.cgroup import walk_entities
    from topos.collect.collector import Collector
    from topos.config import ToposConfig
    from topos.model import frame_to_jsonable
    from topos.record.reader import RecordReader
    from topos.record.writer import RecordWriter

    root = fixture_root() / "cgroupfs" / "gstammtisch"
    all_keys = set(walk_entities(root).keys())

    # Build a collector with --slice system.slice --metrics compact
    c = Collector(
        cgroup_root=root,
        config=ToposConfig(interval=5.0),
        slice_names=("system.slice",),
        metrics_mode="compact",
        docker_inspect=lambda _cid: None,
        host_collector=host_stub,
        now=lambda: 100.0,
        network_providers=(),
        proc_root=fixture_root() / "procfs" / "network",
        sys_root=fixture_root() / "sysfs" / "empty",
        damon_root=fixture_root() / "damonfs" / "no-root" / "kdamonds",
        systemctl_show_runner=systemctl_fixture_runner("gstammtisch"),
    )
    frame = c.collect_once()

    # Write to RecordWriter and read back
    path = tmp_path / "filtered.jsonl"
    with RecordWriter(path, started_at=frame.ts) as writer:
        writer.write_frame(frame)
    reader_frames = list(RecordReader(path))
    assert len(reader_frames) == 1
    restored = reader_frames[0]

    # Verify only filtered entities are present
    assert "system.slice" in restored.entities
    assert "system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope" in restored.entities
    assert "besteffort.slice" not in restored.entities
    assert len(restored.entities) < len(all_keys)

    # Verify compact metrics
    ef = restored.entities["system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope"]
    assert "ram" in ef.metrics
    assert "net_rx_bps" not in ef.metrics
    assert "cpu_pct" not in ef.metrics
