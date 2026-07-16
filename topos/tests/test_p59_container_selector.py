from __future__ import annotations

from pathlib import Path

import pytest

from conftest import fixture_root, systemctl_fixture_runner
from topos.collect.collector import Collector
from topos.collect.dockerjoin import ContainerResolveError
from topos.config import ToposConfig
from topos.model import MetricValue
from topos.providers.net_host import NetHostProvider
from topos.providers.net_netns import NetnsProvider
from topos.registry import COMPACT_GROUPS, METRIC_GROUPS

GAME_KEY = "system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope"
OTHER_KEY = "besteffort.slice/docker-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.scope"


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


def _docker_inspect(cid: str) -> list[dict] | None:
    if cid == "a" * 64:
        return [{"Id": cid, "Name": "/my-game", "Config": {"Image": "game:latest", "Labels": {}}}]
    if cid == "b" * 64:
        return [{"Id": cid, "Name": "/other-game", "Config": {"Image": "other:latest", "Labels": {}}}]
    return None


def _make_collector(
    root: Path,
    *,
    container_selectors: tuple[str, ...] | None = None,
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
        _docker_inspect,
        host_stub,
        lambda: 100.0,
        providers,
        container_selectors=container_selectors,
        slice_names=slice_names,
        metrics_mode=metrics_mode,
        proc_root=proc_root,
        sys_root=fixture_root() / "sysfs" / "empty",
        damon_root=fixture_root() / "damonfs" / "no-root" / "kdamonds",
        systemctl_show_runner=systemctl_fixture_runner("gstammtisch"),
    )


# --- Test 1: --container <exact-name> ---

def test_container_exact_name() -> None:
    """--container <exact-name> on collect_once collects that container's
    EntityKey plus ancestors and nothing else (assert exact key set, incl.
    root "" ancestor; assert a sibling scope is absent)."""
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    collector = _make_collector(root, container_selectors=("my-game",))
    frame = collector.collect_once()
    keys = set(frame.entities.keys())
    assert "" in keys
    assert "system.slice" in keys
    assert GAME_KEY in keys
    assert OTHER_KEY not in keys
    assert "besteffort.slice" not in keys
    assert "soulmask.slice" not in keys


# --- Test 2: --container <prefix> ---

def test_container_prefix_match() -> None:
    """--container <prefix> unambiguous resolves the same single key as
    exact name."""
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    collector = _make_collector(root, container_selectors=("my-g",))
    frame = collector.collect_once()
    keys = set(frame.entities.keys())
    assert GAME_KEY in keys
    assert "" in keys
    assert OTHER_KEY not in keys


# --- Test 3: --container union with --slice ---

def test_container_union_with_slice() -> None:
    """--container and --slice produce a union: both the resolved container
    key and the slice subtree appear."""
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    collector = _make_collector(
        root,
        container_selectors=("my-game",),
        slice_names=("soulmask.slice",),
    )
    frame = collector.collect_once()
    keys = set(frame.entities.keys())
    assert "" in keys
    assert GAME_KEY in keys
    assert "soulmask.slice" in keys
    assert "soulmask.slice/soulmask-paks.slice" in keys


# --- Test 4: --container <nonexistent> exits 2 ---

def test_container_nonexistent_exits_2(capsys) -> None:
    """--container <nonexistent> on --once exits 2 with the P57 no-match
    message (assert on captured stderr, not just the code)."""
    from topos.cli import main

    root = str(fixture_root() / "cgroupfs" / "gstammtisch")
    rc = main(["--once", "--json", "--cgroup-root", root, "--container", "nonexistent"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "no running container" in captured.err.lower()


# --- Test 5: Ambiguous --container <prefix> exits 2 ---

def test_container_ambiguous_prefix_exits_2(capsys) -> None:
    """Ambiguous --container <prefix> (two containers sharing a prefix) exits
    2 and the message lists both candidate names."""
    from topos.cli import main

    def _docker_inspect_ambig(cid: str) -> list[dict] | None:
        if cid == "a" * 64:
            return [{"Id": cid, "Name": "/game-server-1", "Config": {"Image": "game:latest", "Labels": {}}}]
        if cid == "b" * 64:
            return [{"Id": cid, "Name": "/game-server-2", "Config": {"Image": "other:latest", "Labels": {}}}]
        return None

    proc_root = fixture_root() / "procfs" / "network"
    host_ns_id = (proc_root / "ns" / "host").stat().st_ino
    providers = (
        NetnsProvider(fixture_root() / "cgroupfs" / "gstammtisch", proc_root=proc_root, host_netns_id=host_ns_id),
        NetHostProvider(proc_root=proc_root, command_runner=qdisc_stub),
    )
    collector = Collector(
        fixture_root() / "cgroupfs" / "gstammtisch",
        ToposConfig(interval=5.0),
        _docker_inspect_ambig,
        host_stub,
        lambda: 100.0,
        providers,
        container_selectors=("game-server",),
        proc_root=proc_root,
        sys_root=fixture_root() / "sysfs" / "empty",
        damon_root=fixture_root() / "damonfs" / "no-root" / "kdamonds",
        systemctl_show_runner=systemctl_fixture_runner("gstammtisch"),
    )
    with pytest.raises(ContainerResolveError) as excinfo:
        collector.collect_once()
    assert "ambiguous" in str(excinfo.value).lower()
    assert "game-server-1" in str(excinfo.value)
    assert "game-server-2" in str(excinfo.value)


# --- Test 6: --container rejected with --replay and --attach ---

def test_container_rejected_with_replay() -> None:
    """--container is rejected with --replay (exit 2)."""
    from topos.cli import main

    rc = main(["--replay", "dummy.jsonl", "--container", "my-game"])
    assert rc == 2


def test_container_rejected_with_attach() -> None:
    """--container is rejected with --attach (exit 2)."""
    from topos.cli import main

    rc = main(["--attach", "/tmp/fake.sock", "--container", "my-game"])
    assert rc == 2


# --- Test 7: --container <name> --metrics compact ---

def test_container_metrics_compact() -> None:
    """--container <name> --metrics compact: resolved container entity is
    present and carries only the compact metric families (assert ram present,
    net_rx_bps absent)."""
    expected_compact = frozenset().union(*(METRIC_GROUPS[g] for g in COMPACT_GROUPS))
    root = fixture_root() / "cgroupfs" / "gstammtisch"
    collector = _make_collector(
        root,
        container_selectors=("my-game",),
        metrics_mode="compact",
    )
    frame = collector.collect_once()
    assert GAME_KEY in frame.entities
    eframe = frame.entities[GAME_KEY]
    assert "ram" in eframe.metrics
    assert "net_rx_bps" not in eframe.metrics
    assert set(eframe.metrics.keys()).issubset(expected_compact)
    # Per P55-R1, eframe.network is None under compact
    assert eframe.network is None


# --- Test 8: Resolution-ordering guard ---

def test_resolution_ordering_guard() -> None:
    """Prove resolution runs inside collect_once() against post-enrich
    entities, not in cli.py pre-sweep.

    Pre-enrichment entities have no DockerMeta (entity.docker is None), so
    resolve_container_key() would fail. Only after enrich_entities() runs
    inside collect_once() does the metadata exist for resolution.
    """
    from topos.collect.cgroup import walk_entities
    from topos.collect.dockerjoin import resolve_container_key

    root = fixture_root() / "cgroupfs" / "gstammtisch"
    # Walk entities WITHOUT enrichment — entity.docker is None
    pre_enrich = walk_entities(root)
    # Resolution against pre-enrich entities must fail
    with pytest.raises(ContainerResolveError):
        resolve_container_key("my-game", pre_enrich)
    # But running through the collector (which enriches then resolves) succeeds
    collector = _make_collector(root, container_selectors=("my-game",))
    frame = collector.collect_once()
    assert GAME_KEY in frame.entities