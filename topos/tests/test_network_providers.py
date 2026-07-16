from __future__ import annotations

import json
from pathlib import Path

from conftest import fixture_root
from topos.collect.cgroup import walk_entities
from topos.config import load
from topos.providers.net_bpf import BpfProvider
from topos.providers.net_host import NetHostProvider
from topos.providers.net_netns import NetnsProvider
from topos.model import Entity

QDISC_OUTPUT = """
qdisc fq_codel 0: dev eth0 root refcnt 2 limit 10240p flows 1024 quantum 1514 target 5.0ms interval 100.0ms memory_limit 32Mb ecn drop_batch 64
 Sent 12345 bytes 67 pkt (dropped 9, overlimits 11 requeues 0)
 backlog 2048b 3p requeues 0
qdisc fq_codel 0: dev veth0 root refcnt 2 limit 10240p flows 1024 quantum 1514 target 5.0ms interval 100.0ms memory_limit 32Mb ecn drop_batch 64
 Sent 23456 bytes 89 pkt (dropped 1, overlimits 2 requeues 0)
 backlog 512b 1p requeues 0
""".strip()


def proc_fixture() -> Path:
    return fixture_root() / "procfs" / "network"


def test_host_provider_parses_proc_snapshot_and_tc_status() -> None:
    provider = NetHostProvider(proc_root=proc_fixture(), command_runner=lambda _argv: QDISC_OUTPUT)
    sample = provider.collect({"": walk_entities(fixture_root() / "cgroupfs" / "gstammtisch")[""]})[""]
    assert sample.source_label == "net:HOST"
    assert sample.rx_bytes == 15100
    assert sample.tx_bytes == 27100
    assert sample.proto == {
        "tcp": {"retrans_segs": 6, "out_rsts": 4, "timeouts": 8, "syn_retrans": 7},
        "udp": {"in_errors": 2, "rcvbuf_errors": 3, "sndbuf_errors": 4},
    }
    status = provider.status()
    assert status["softnet"] == {"cpu_count": 2, "dropped": 6, "time_squeeze": 8}
    assert status["qdisc"]["eth0"]["dropped"] == 9
    assert status["qdisc"]["eth0"]["backlog_packets"] == 3


def test_netns_provider_dedupes_same_namespace_within_one_entity() -> None:
    cgroup_root = fixture_root() / "cgroupfs" / "gstammtisch"
    provider = NetnsProvider(cgroup_root, proc_root=proc_fixture(), host_netns_id=(proc_fixture() / "ns" / "host").stat().st_ino)
    entities = walk_entities(cgroup_root)
    samples = provider.collect(entities)
    game = samples["system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope"]
    assert game.source_label == "net:NS"
    assert game.rx_bytes == 1250
    assert game.tx_bytes == 2250


def test_netns_provider_labels_host_network_entities_as_na() -> None:
    cgroup_root = fixture_root() / "cgroupfs" / "gstammtisch"
    provider = NetnsProvider(cgroup_root, proc_root=proc_fixture(), host_netns_id=(proc_fixture() / "ns" / "host").stat().st_ino)
    entities = walk_entities(cgroup_root)
    samples = provider.collect(entities)
    pak = samples["soulmask.slice/soulmask-paks.slice"]
    assert pak.source_label == "net:N/A"
    assert pak.confidence == "n/a"
    assert pak.unavailable_reason == "host netns"


def test_netns_provider_aggregates_only_when_children_are_distinct_private_namespaces(tmp_path: Path) -> None:
    cgroup_root = tmp_path / "cg"
    (cgroup_root / "apps.slice" / "alpha.scope").mkdir(parents=True)
    (cgroup_root / "apps.slice" / "beta.scope").mkdir(parents=True)
    (cgroup_root / "apps.slice" / "alpha.scope" / "cgroup.procs").write_text("3001\n")
    (cgroup_root / "apps.slice" / "beta.scope" / "cgroup.procs").write_text("3002\n")
    (cgroup_root / "apps.slice" / "cgroup.procs").write_text("")
    provider = NetnsProvider(cgroup_root, proc_root=proc_fixture(), host_netns_id=(proc_fixture() / "ns" / "host").stat().st_ino)
    entities = walk_entities(cgroup_root)
    sample = provider.collect(entities)["apps.slice"]
    assert sample.source_label == "net:NS"
    assert sample.aggregation == "private_ns_only"
    assert sample.rx_bytes == 12030
    assert sample.tx_bytes == 15030


def test_netns_provider_refuses_branch_aggregation_when_a_child_is_host_networked(tmp_path: Path) -> None:
    cgroup_root = tmp_path / "cg"
    (cgroup_root / "apps.slice" / "alpha.scope").mkdir(parents=True)
    (cgroup_root / "apps.slice" / "hosted.scope").mkdir(parents=True)
    (cgroup_root / "apps.slice" / "alpha.scope" / "cgroup.procs").write_text("3001\n")
    (cgroup_root / "apps.slice" / "hosted.scope" / "cgroup.procs").write_text("2001\n")
    (cgroup_root / "apps.slice" / "cgroup.procs").write_text("")
    provider = NetnsProvider(cgroup_root, proc_root=proc_fixture(), host_netns_id=(proc_fixture() / "ns" / "host").stat().st_ino)
    entities = walk_entities(cgroup_root)
    sample = provider.collect(entities)["apps.slice"]
    assert sample.source_label == "net:N/A"
    assert sample.aggregation == "none"
    assert sample.unavailable_reason == "aggregation proof failed"


def test_load_parses_observe_only_net_classes(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            (
                "[net.classes]",
                'interactive_admin = [22, "443", "8000-8002"]',
                "background = [6881]",
            )
        )
    )
    config = load(config_path)
    assert config.net.classes["interactive_admin"] == (22, 443, 8000, 8001, 8002)
    assert config.net.classify_port(6881) == "background"


# ---------------------------------------------------------------------------
# P18 - BPF provider
# ---------------------------------------------------------------------------


def test_bpf_provider_reads_snapshot_and_returns_net_bpf_samples() -> None:
    """Basic BPF snapshot parsing: mapped entities get net:BPF samples."""
    bpf_root = fixture_root() / "bpf" / "working"
    provider = BpfProvider(bpf_root)
    entities = {
        "system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope": Entity(
            key="system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope",
            kind="scope",
            parent="system.slice",
        ),
        "soulmask.slice/soulmask-paks.slice/soulmask-0.scope": Entity(
            key="soulmask.slice/soulmask-paks.slice/soulmask-0.scope",
            kind="scope",
            parent="soulmask.slice/soulmask-paks.slice",
        ),
    }
    samples = provider.collect(entities)
    assert len(samples) == 2

    game = samples["system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope"]
    assert game.source_label == "net:BPF"
    assert game.confidence == "exact"
    assert game.aggregation == "exact"
    assert game.unavailable_reason is None
    assert game.rx_bytes == 96000  # 90000 (tcp4) + 5000 (udp4) + 1000 (tcp6)
    assert game.tx_bytes == 152500  # 150000 (tcp4) + 2000 (udp4) + 500 (tcp6)
    assert game.rx_pkts == 645  # 600 (tcp4) + 40 (udp4) + 5 (tcp6)
    assert game.tx_pkts == 518  # 500 (tcp4) + 15 (udp4) + 3 (tcp6)
    assert game.proto is not None

    other = samples["soulmask.slice/soulmask-paks.slice/soulmask-0.scope"]
    assert other.source_label == "net:BPF"
    assert other.rx_bytes == 18000
    assert other.tx_bytes == 26000
    assert other.rx_pkts == 120
    assert other.tx_pkts == 160


def test_bpf_provider_entity_without_bpf_mapping_returns_unavailable() -> None:
    """Entities not in the cgroup_map get net:N/A with explanation."""
    bpf_root = fixture_root() / "bpf" / "working"
    provider = BpfProvider(bpf_root)
    entities = {
        "soulmask.slice/soulmask-paks.slice": Entity(
            key="soulmask.slice/soulmask-paks.slice",
            kind="slice",
            parent="soulmask.slice",
        ),
        "system.slice": Entity(key="system.slice", kind="slice", parent=""),
    }
    samples = provider.collect(entities)
    assert len(samples) == 2
    for key, sample in samples.items():
        assert sample.source_label == "net:N/A", f"{key} should be net:N/A"
        assert sample.unavailable_reason == "no BPF counter mapping for this entity", key


def test_bpf_provider_missing_root_returns_unavailable() -> None:
    """No bpf_root configured => empty result with status reason."""
    provider = BpfProvider(bpf_root=None)
    samples = provider.collect({"": Entity(key="", kind="root", parent=None)})
    assert samples == {}
    status = provider.status()
    assert status["loaded"] is False
    assert status["attached"] is False
    assert any("no BPF root configured" in e for e in status["errors"])


def test_bpf_provider_nonexistent_snapshot_returns_unavailable() -> None:
    """Bpf root with no snapshot.json => empty result."""
    bpf_root = fixture_root() / "bpf" / "unavailable"
    provider = BpfProvider(bpf_root)
    samples = provider.collect({"": Entity(key="", kind="root", parent=None)})
    assert samples == {}
    status = provider.status()
    assert status["loaded"] is False
    assert any("no BPF snapshot" in e for e in status["errors"])


def test_bpf_provider_corrupt_json_returns_unavailable() -> None:
    """Corrupt snapshot JSON => error in status, empty collect."""
    bpf_root = fixture_root() / "bpf" / "corrupt"
    provider = BpfProvider(bpf_root)
    samples = provider.collect({"": Entity(key="", kind="root", parent=None)})
    assert samples == {}
    status = provider.status()
    assert status["loaded"] is False
    assert any("JSON parse error" in e for e in status["errors"])


def test_bpf_provider_status_returns_snapshot_metadata() -> None:
    """Successful collect populates status with entity counts."""
    bpf_root = fixture_root() / "bpf" / "working"
    provider = BpfProvider(bpf_root)
    entities = {
        "system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope": Entity(
            key="system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope",
            kind="scope",
            parent="system.slice",
        ),
    }
    provider.collect(entities)
    status = provider.status()
    assert status["loaded"] is True
    assert status["attached"] is False
    assert status["last_read"] is not None
    assert status["entities_seen"] == 1
    assert status["entities_with_bpf"] == 1
    assert status["snapshot_path"].endswith("snapshot.json")


def test_bpf_provider_ignores_malformed_entries(tmp_path: Path) -> None:
    """Malformed BPF map rows do not crash or inflate counters."""
    bpf_root = tmp_path / "bpf"
    bpf_root.mkdir()
    (bpf_root / "snapshot.json").write_text(
        json.dumps(
            {
                "maps": {
                    "topos_cgroup_skb": [
                        {"cgroup_id": 42, "direction": "ingress", "bytes": 100, "packets": 4},
                        {"cgroup_id": 42, "direction": "egress", "bytes": "bad", "packets": True},
                        {"cgroup_id": 42, "direction": "sideways", "bytes": 999, "packets": 999},
                        {"cgroup_id": "42", "direction": "ingress", "bytes": 999, "packets": 999},
                    ],
                },
                "cgroup_map": {"42": "alpha.scope"},
            }
        ),
        encoding="utf-8",
    )
    provider = BpfProvider(bpf_root)
    samples = provider.collect({"alpha.scope": Entity(key="alpha.scope", kind="scope", parent="")})
    sample = samples["alpha.scope"]
    assert sample.source_label == "net:BPF"
    assert sample.rx_bytes == 100
    assert sample.rx_pkts == 4
    assert sample.tx_bytes == 0
    assert sample.tx_pkts == 0


def test_bpf_provider_ranking_in_collector() -> None:
    """BPF provider outranks host/netns when all three are present."""
    from topos.collect.collector import Collector
    from topos.config import ToposConfig

    bpf_root = fixture_root() / "bpf" / "working"
    cgroup_root = fixture_root() / "cgroupfs" / "gstammtisch"
    proc_root = fixture_root() / "procfs" / "network"
    host_ns_id = (proc_root / "ns" / "host").stat().st_ino

    def host_stub() -> dict:
        from topos.model import MetricValue
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

    providers = (
        BpfProvider(bpf_root),
        NetnsProvider(cgroup_root, proc_root=proc_root, host_netns_id=host_ns_id),
        NetHostProvider(proc_root=proc_root, command_runner=lambda _: ""),
    )
    collector = Collector(
        cgroup_root,
        ToposConfig(interval=5.0),
        lambda _cid: None,
        host_stub,
        lambda: 100.0,
        providers,
        proc_root=proc_root,
        damon_root=fixture_root() / "damonfs" / "no-root" / "kdamonds",
    )
    frame = collector.collect_once()
    game_key = "system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope"
    game = frame.entities[game_key].metrics
    # BPF ranks highest => the entity with BPF mapping gets net_bpf metrics
    # (source label encoded in the metric value src)
    net_meta = frame.entities[game_key].network
    assert net_meta is not None
    assert net_meta["source_label"] == "net:BPF"
    assert game["net_rx_bps"].src == "bpf"
    assert game["net_tx_bps"].src == "bpf"


def test_bpf_provider_is_publicly_exported() -> None:
    from topos.providers import BpfProvider as ExportedBpfProvider

    assert ExportedBpfProvider is BpfProvider
