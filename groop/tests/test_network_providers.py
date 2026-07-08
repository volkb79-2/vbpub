from __future__ import annotations

from pathlib import Path

from conftest import fixture_root
from groop.collect.cgroup import walk_entities
from groop.config import load
from groop.providers.net_host import NetHostProvider
from groop.providers.net_netns import NetnsProvider

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
