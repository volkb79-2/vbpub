from __future__ import annotations

"""Collector/provider fixes from the 2026-11-05 review round (A-01, A-02, A-04..A-07, A-09, A-15, A-18, A-19)."""

import subprocess
from pathlib import Path

import pytest

from topos.collect.cgroup import BlockStackClassifier, collect_cgroup, read_io_stat
from topos.collect.collector import Collector
from topos.collect.dockerjoin import DockerInspectCache, docker_id_from_key, enrich_entities
from topos.collect.iocost import collect_io_cost
from topos.collect.netfilter import is_virtual_interface
from topos.config import ToposConfig
from topos.drift.origin import ShowResult, UnitViewCache, _load_unit_views
from topos.model import Entity, EntityFrame, MetricValue
from topos.providers.net_host import NetHostProvider
from topos.registry import REGISTRY

CID = "a" * 64

LIVE_ROOT_IO_STAT = """11:0
253:2 rbytes=241821134848 wbytes=205971349504 rios=5790793 wios=9646584 dbytes=162520244224 dios=245158
253:0 rbytes=3062133642240 wbytes=1333041840128 rios=127290545 wios=151645283 dbytes=0 dios=0
(unknown)
254:0 rbytes=3558045340672 wbytes=1624971561984 rios=208590639 wios=77055314 dbytes=255385190400 dios=256665 cost.vrate=50.00 cost.usage=20059513136
"""


def block_sys(tmp_path: Path, stacked: tuple[str, ...], bottom: tuple[str, ...]) -> Path:
    sys_root = tmp_path / "sys"
    for dev in stacked:
        (sys_root / "dev" / "block" / dev / "slaves").mkdir(parents=True)
        (sys_root / "dev" / "block" / dev / "slaves" / "sda").mkdir()
    for dev in bottom:
        (sys_root / "dev" / "block" / dev / "slaves").mkdir(parents=True)
    return sys_root


def make_cgroup(root: Path, key: str = "", **files: str) -> Path:
    path = root if key == "" else root / key
    path.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (path / name.replace("__", ".")).write_text(text)
    return path


# --- item 1: stacked block devices ---------------------------------------------------------------


def test_io_totals_exclude_stacked_devices(tmp_path: Path) -> None:
    root = tmp_path / "cg"
    make_cgroup(root, io__stat="253:0 rbytes=100 wbytes=1 rios=1 wios=1 dbytes=0\n254:0 rbytes=100 wbytes=1 rios=1 wios=1 dbytes=0\n")
    stack = BlockStackClassifier(block_sys(tmp_path, stacked=("253:0",), bottom=("254:0",)))
    sample = collect_cgroup(root, "", Entity(key="", kind="root", parent=None), stack)
    assert sample.raw_counters["io.stat:rbytes"] == 100
    assert "io.stat:_unclassified" not in sample.raw_counters
    # per-device counters survive untouched
    assert sample.raw_counters["io.stat@253:0:rbytes"] == 100
    assert sample.raw_counters["io.stat@254:0:rbytes"] == 100


def test_io_totals_fall_back_to_sum_all_and_mark_unclassified(tmp_path: Path) -> None:
    root = tmp_path / "cg"
    make_cgroup(root, io__stat="253:0 rbytes=100\n254:0 rbytes=100\n")
    stack = BlockStackClassifier(tmp_path / "no-sys")
    sample = collect_cgroup(root, "", Entity(key="", kind="root", parent=None), stack)
    assert sample.raw_counters["io.stat:rbytes"] == 200
    assert sample.raw_counters["io.stat:_unclassified"] == 1


def test_classification_is_cached_per_device(tmp_path: Path) -> None:
    sys_root = block_sys(tmp_path, stacked=("253:0",), bottom=())
    stack = BlockStackClassifier(sys_root)
    assert stack.is_stacked("253:0") is True
    (sys_root / "dev" / "block" / "253:0" / "slaves" / "sda").rmdir()
    assert stack.is_stacked("253:0") is True  # cached
    assert stack.is_stacked("9:9") is None  # unknown, not cached as a verdict


def test_real_root_io_stat_shape_skips_empty_lines_and_parses_vrate(tmp_path: Path) -> None:
    path = tmp_path / "io.stat"
    path.write_text(LIVE_ROOT_IO_STAT)
    devices, src = read_io_stat(path)
    assert src == "exact"
    assert set(devices) == {"253:2", "253:0", "254:0"}
    assert devices["254:0"]["cost.vrate"] == 50.0
    assert isinstance(devices["254:0"]["cost.vrate"], float)
    assert devices["254:0"]["cost.usage"] == 20059513136


def make_collector(root: Path, times: list[float], sys_root: Path | None = None, **kwargs) -> Collector:
    return Collector(
        root,
        ToposConfig(interval=5.0),
        lambda _cid: None,
        lambda: {},
        lambda: times.pop(0),
        (),
        proc_root=root / "proc-none",
        sys_root=sys_root or root / "sys-none",
        damon_root=root / "damon-none",
        systemctl_show_runner=lambda unit, props: ShowResult(stdout="", stderr="", returncode=1, unavailable=True),
        **kwargs,
    )


def test_collector_io_rate_ignores_stacked_and_labels_exact(tmp_path: Path) -> None:
    root = tmp_path / "cg"
    sys_root = block_sys(tmp_path, stacked=("253:0",), bottom=("254:0",))
    make_cgroup(root, io__stat="253:0 rbytes=0\n254:0 rbytes=0\n")
    collector = make_collector(root, [0.0, 10.0], sys_root)
    collector.collect_once()
    make_cgroup(root, io__stat="253:0 rbytes=1000\n254:0 rbytes=1000\n")
    frame = collector.collect_once()
    metric = frame.entities[""].metrics["io_r_bps"]
    assert metric.v == 100.0
    assert metric.src == "exact"


def test_collector_io_rate_is_derived_when_sys_unreadable(tmp_path: Path) -> None:
    root = tmp_path / "cg"
    make_cgroup(root, io__stat="253:0 rbytes=0\n")
    collector = make_collector(root, [0.0, 10.0])
    collector.collect_once()
    make_cgroup(root, io__stat="253:0 rbytes=1000\n")
    metric = collector.collect_once().entities[""].metrics["io_r_bps"]
    assert metric.src == "derived"


# --- item 2: io.cost ----------------------------------------------------------------------------


def test_io_cost_host_metrics(tmp_path: Path) -> None:
    make_cgroup(tmp_path, io__cost__qos="254:0 enable=1 ctrl=user rpct=0.00 rlat=5000 wpct=0.00 wlat=5000 min=50.00 max=10000.00\n",
                io__stat="254:0 rbytes=1 cost.vrate=50.00 cost.usage=7\n253:0 rbytes=1\n")
    metrics, details = collect_io_cost(tmp_path)
    assert metrics["host_io_cost_enabled"] == MetricValue(1, "exact")
    assert metrics["host_io_cost_vrate_pct"] == MetricValue(50.0, "exact")
    assert details[0]["qos_min_pct"] == 50.0


def test_io_cost_off_and_absent_are_typed_unavailable(tmp_path: Path) -> None:
    make_cgroup(tmp_path, io__cost__qos="254:0 enable=0 ctrl=auto\n")
    metrics, _ = collect_io_cost(tmp_path)
    assert metrics["host_io_cost_enabled"] == MetricValue(0, "exact")
    assert metrics["host_io_cost_vrate_pct"].v is None
    assert metrics["host_io_cost_vrate_pct"].src == "unavail_kernel"
    metrics, details = collect_io_cost(tmp_path / "nowhere")
    assert metrics["host_io_cost_enabled"].src == "unavail_kernel"
    assert details == []


def test_io_cost_usage_rate_per_cgroup(tmp_path: Path) -> None:
    root = tmp_path / "cg"
    make_cgroup(root, io__stat="254:0 rbytes=0 cost.usage=0\n")
    collector = make_collector(root, [0.0, 10.0])
    collector.collect_once()
    make_cgroup(root, io__stat="254:0 rbytes=0 cost.usage=500\n")
    assert collector.collect_once().entities[""].metrics["io_cost_usage_per_s"].v == 50.0


def test_new_metrics_in_registry() -> None:
    for name in ("host_io_cost_vrate_pct", "host_io_cost_enabled", "io_cost_usage_per_s"):
        assert name in REGISTRY
    assert "QoS" in REGISTRY["host_io_cost_vrate_pct"].glossary


# --- A-01: docker cache, unit cache, timeouts ---------------------------------------------------


def test_docker_inspect_cached_and_evicted(tmp_path: Path) -> None:
    calls: list[str] = []

    def inspect(cid: str):
        calls.append(cid)
        return [{"Id": cid, "Name": "/web", "Config": {"Image": "img", "Labels": {}}}]

    cache = DockerInspectCache()
    key = f"system.slice/docker-{CID}.scope"
    for _ in range(3):
        out = enrich_entities({key: Entity(key=key, kind="scope", parent="system.slice")}, inspect, cache=cache)
        assert out[key].docker is not None and out[key].docker.name == "web"
    assert calls == [CID]
    enrich_entities({}, inspect, cache=cache)
    assert len(cache) == 0
    enrich_entities({key: Entity(key=key, kind="scope", parent="system.slice")}, inspect, cache=cache)
    assert calls == [CID, CID]


def test_docker_inspect_timeout_degrades_and_is_not_cached() -> None:
    def inspect(_cid: str):
        raise subprocess.TimeoutExpired("docker", 10)

    cache = DockerInspectCache()
    key = f"system.slice/docker-{CID}.scope"
    out = enrich_entities({key: Entity(key=key, kind="scope", parent="system.slice")}, inspect, cache=cache)
    assert out[key].docker is None
    assert len(cache) == 0


def test_unit_view_ttl_cache_with_injected_clock() -> None:
    now = [0.0]
    cache = UnitViewCache(ttl=30.0, clock=lambda: now[0])
    calls: list[str] = []

    def runner(unit: str, _props):
        calls.append(unit)
        return ShowResult(stdout="MemoryMin=0\n", stderr="", returncode=0)

    frames = {"a.slice": EntityFrame(entity=Entity(key="a.slice", kind="slice", parent=""), metrics={})}
    _load_unit_views(frames, runner, cache)
    now[0] = 29.0
    _load_unit_views(frames, runner, cache)
    assert calls == ["a.slice"]
    now[0] = 31.0
    _load_unit_views(frames, runner, cache)
    assert calls == ["a.slice", "a.slice"]


def test_default_systemctl_runner_timeout_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from topos.drift import origin

    def boom(*_a, **kw):
        assert kw.get("timeout")
        raise subprocess.TimeoutExpired("systemctl", kw["timeout"])

    monkeypatch.setattr(origin.subprocess, "run", boom)
    result = origin.default_systemctl_show_runner("a.slice", ("MemoryMin",))
    assert result.unavailable is True


# --- A-02 / A-17: one interface filter -----------------------------------------------------------


def test_interface_filter_is_exact_for_lo() -> None:
    assert is_virtual_interface("lo")
    assert not is_virtual_interface("lowpan0")
    assert is_virtual_interface("veth12ab") and is_virtual_interface("br-1234") and is_virtual_interface("docker0")
    assert not is_virtual_interface("eth0")


def test_host_row_sums_only_real_interfaces(tmp_path: Path) -> None:
    net = tmp_path / "proc" / "net"
    net.mkdir(parents=True)
    zeros = " ".join(["0"] * 6)
    def line(name: str, rx: int, tx: int) -> str:
        return f"{name}: {rx} 1 0 0 0 0 0 0 {tx} 1 0 0 0 0 0 0"
    (net / "dev").write_text("h1\nh2\n" + "\n".join([line("lo", 5, 5), line("eth0", 100, 200), line("veth1", 100, 200), line("br-x", 100, 200), line("lowpan0", 7, 9)]) + "\n")
    provider = NetHostProvider(proc_root=tmp_path / "proc", command_runner=lambda argv: "")
    sample = provider.collect({"": Entity(key="", kind="root", parent=None)})[""]
    assert sample.rx_bytes == 107 and sample.tx_bytes == 209


def test_tc_runs_with_timeout_and_degrades(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from topos.providers import net_host

    seen: dict[str, object] = {}

    def boom(argv, **kw):
        seen.update(kw)
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(net_host.subprocess, "run", boom)
    monkeypatch.setattr(net_host.shutil, "which", lambda _n: "/sbin/tc")
    provider = NetHostProvider(proc_root=tmp_path)
    assert provider._read_qdisc() is None
    assert seen["timeout"] == net_host.TC_TIMEOUT_S


# --- A-05 / A-15 / A-06 --------------------------------------------------------------------------


def test_swap_disk_without_swapcached_is_unsupported_not_exact_none(tmp_path: Path) -> None:
    make_cgroup(tmp_path, memory__swap__current="100\n", memory__stat="anon 1\nzswapped 5\n")
    sample = collect_cgroup(tmp_path, "", Entity(key="", kind="root", parent=None))
    assert sample.metrics["swap_disk"] == MetricValue(None, "unavail_kernel")


def test_permanent_none_is_not_warming(tmp_path: Path) -> None:
    make_cgroup(tmp_path, memory__current="10\n", memory__stat="anon 1\n")
    metrics = collect_cgroup(tmp_path, "", Entity(key="", kind="root", parent=None)).metrics
    assert metrics["ratio"].src == "unavail_kernel"  # no zswap files at all
    assert metrics["headroom_max_pct"].src == "unavail_kernel"  # memory.max unreadable


def test_refault_without_zswpin_counts_every_anon_refault_as_device(tmp_path: Path) -> None:
    root = tmp_path / "cg"
    make_cgroup(root, memory__stat="workingset_refault_anon 0\n")
    collector = make_collector(root, [0.0, 10.0])
    collector.collect_once()
    make_cgroup(root, memory__stat="workingset_refault_anon 100\n")
    metrics = collector.collect_once().entities[""].metrics
    assert metrics["rf_d_per_s"].v == 10.0
    assert metrics["rf_z_per_s"].src == "unavail_kernel"


# --- A-07 ----------------------------------------------------------------------------------------


def test_baselines_pruned_when_entity_leaves(tmp_path: Path) -> None:
    root = tmp_path / "cg"
    make_cgroup(root, "gone.slice", cpu__stat="usage_usec 1\n")
    collector = make_collector(root, [0.0, 5.0])
    collector.collect_once()
    assert any(k[0] == "gone.slice" for k in collector._prev_counters)
    (root / "gone.slice" / "cpu.stat").unlink()
    (root / "gone.slice").rmdir()
    collector.collect_once()
    assert not any(k[0] == "gone.slice" for k in collector._prev_counters)


def test_uncapped_device_baseline_stays_fresh(tmp_path: Path) -> None:
    root = tmp_path / "cg"
    make_cgroup(root, io__stat="8:0 rbytes=0\n", io__max="")
    collector = make_collector(root, [0.0, 10.0, 20.0])
    collector.collect_once()
    make_cgroup(root, io__stat="8:0 rbytes=1000000\n")  # uncapped tick
    collector.collect_once()
    # cap added now: the delta must be one interval wide, not since the first tick
    make_cgroup(root, io__stat="8:0 rbytes=1000100\n", io__max="8:0 rbps=100\n")
    metrics = collector.collect_once().entities[""].metrics
    assert metrics["io_cap_saturation_pct"].v == pytest.approx(10.0)


def test_interval_uses_monotonic_clock_not_wall_clock(tmp_path: Path) -> None:
    root = tmp_path / "cg"
    make_cgroup(root, cpu__stat="usage_usec 0\n")
    wall = iter([1000.0, 10.0])  # wall clock steps backwards
    mono = iter([0.0, 10.0])
    collector = Collector(root, ToposConfig(interval=5.0), lambda _c: None, lambda: {}, lambda: next(wall), (),
                          proc_root=root / "p", sys_root=root / "s", damon_root=root / "d",
                          systemctl_show_runner=lambda u, p: ShowResult("", "", 1, unavailable=True),
                          monotonic=lambda: next(mono))
    collector.collect_once()
    make_cgroup(root, cpu__stat="usage_usec 5000000\n")
    frame = collector.collect_once()
    assert frame.interval_s == 10.0
    assert frame.ts == 10.0
    assert frame.entities[""].metrics["cpu_pct"].v == pytest.approx(50.0)


# --- A-09: registry scopes -----------------------------------------------------------------------

# Kernel scope of each cgroup-file-backed metric (hierarchical files are "subtree").
KNOWN_SCOPES = {
    "cpu_pct": "subtree",
    "psi_mem_some_avg10": "subtree", "psi_mem_full_avg10": "subtree",
    "psi_io_some_avg10": "subtree", "psi_io_full_avg10": "subtree",
    "psi_cpu_some_avg10": "subtree", "psi_cpu_full_avg10": "subtree",
    "pids_current": "subtree",
    "ram": "subtree", "io_r_bps": "subtree", "io_w_bps": "subtree",
    "cgroup_procs": "local", "mem_max": "local", "mem_min": "local", "cpu_weight": "local",
}


@pytest.mark.parametrize("name,scope", sorted(KNOWN_SCOPES.items()))
def test_registry_scope_matches_kernel(name: str, scope: str) -> None:
    spec = REGISTRY[name]
    assert spec.locality == scope
    assert spec.branch_policy == ("kernel_subtree" if scope == "subtree" else "local_only")


# --- A-19 ----------------------------------------------------------------------------------------


def test_cgroupfs_driver_docker_id() -> None:
    assert docker_id_from_key(f"docker/{CID}") == CID
    assert docker_id_from_key(f"system.slice/docker-{CID}.scope") == CID
    assert docker_id_from_key("docker/short") is None


# --- A-18 ----------------------------------------------------------------------------------------


def test_io_max_read_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_cgroup(tmp_path, io__max="8:0 rbps=100 wbps=max\n")
    reads: list[str] = []
    real = Path.read_text

    def spy(self: Path, *a, **kw):
        reads.append(self.name)
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", spy)
    sample = collect_cgroup(tmp_path, "", Entity(key="", kind="root", parent=None))
    assert reads.count("io.max") == 1
    assert sample.raw_counters["io.max@8:0:rbps"] == 100
    assert sample.raw_counters["io.max:rbps"] == 100


# --- A-12 / B-14: BPF snapshot staleness and single-pass aggregation ------------------------------


def _bpf_snapshot(tmp_path: Path, generated_at: float | None, entries: int = 1) -> Path:
    import json

    snap = {
        "maps": {"topos_cgroup_skb": [{"cgroup_id": 7, "direction": "ingress", "family": "ipv4", "proto": "tcp", "bytes": 10 * (i + 1), "packets": 1} for i in range(entries)]},
        "cgroup_map": {"7": "a.slice"},
    }
    if generated_at is not None:
        snap["generated_at"] = generated_at
    (tmp_path / "snapshot.json").write_text(json.dumps(snap))
    return tmp_path


def test_bpf_snapshot_older_than_limit_is_not_served_as_live(tmp_path: Path) -> None:
    from topos.providers.net_bpf import BpfProvider

    entities = {"a.slice": Entity(key="a.slice", kind="slice", parent="")}
    root = _bpf_snapshot(tmp_path, generated_at=1000.0)
    fresh = BpfProvider(root, max_age_s=15.0, clock=lambda: 1010.0).collect(entities)
    assert fresh["a.slice"].source_label == "net:BPF"
    stale = BpfProvider(root, max_age_s=15.0, clock=lambda: 1016.0)
    assert stale.collect(entities) == {}
    assert stale.status()["stale"] is True


def test_bpf_aggregation_sums_all_entries_for_a_cgroup(tmp_path: Path) -> None:
    from topos.providers.net_bpf import BpfProvider

    entities = {"a.slice": Entity(key="a.slice", kind="slice", parent="")}
    root = _bpf_snapshot(tmp_path, generated_at=None, entries=3)
    sample = BpfProvider(root).collect(entities)["a.slice"]
    assert sample.rx_bytes == 60
    assert sample.rx_pkts == 3


# --- A-14 ----------------------------------------------------------------------------------------


def test_idle_processes_are_not_hot() -> None:
    from topos.config import ProcessConfig
    from topos.procs.candidates import select_candidates
    from topos.procs.identity import ProcessKey

    keys = [ProcessKey(pid=pid, start_ticks=1, boot_id="b") for pid in range(1, 50)]
    result = select_candidates(
        keys=keys, cpu_rate={k: 0.0 for k in keys}, io_rate={k: 0.0 for k in keys},
        pinned_pids=frozenset(), hot_since={}, now=1.0, config=ProcessConfig(), eligible_count=len(keys),
    )
    assert result.retained == []
    busy = {keys[3]: 5.0}
    result = select_candidates(
        keys=keys, cpu_rate={**{k: 0.0 for k in keys}, **busy}, io_rate={},
        pinned_pids=frozenset(), hot_since={}, now=1.0, config=ProcessConfig(), eligible_count=len(keys),
    )
    assert result.retained == [keys[3]]


def test_pin_follows_incarnation_not_reused_pid(tmp_path: Path) -> None:
    from topos.config import ProcessConfig
    from topos.procs.identity import ProcessKey
    from topos.procs.sampler import ProcessSampler

    sampler = ProcessSampler(proc_root=tmp_path, pinned_pids=frozenset({7}), config=ProcessConfig(pinned_cap=2, hard_cap=5))
    old = ProcessKey(pid=7, start_ticks=10, boot_id="b")
    reused = ProcessKey(pid=7, start_ticks=999, boot_id="b")
    assert sampler._bind_pins([old]) == {old}
    assert sampler._bind_pins([reused]) == {old}  # the new process does not inherit the pin


# --- A-16 ----------------------------------------------------------------------------------------


def _protected_frame(**metrics: float | None) -> EntityFrame:
    entity = Entity(key="svc.service", kind="service", parent="", is_protected=True)
    return EntityFrame(entity=entity, metrics={k: MetricValue(v, "derived") for k, v in metrics.items()})


def test_protected_disk_refault_honours_warn_band() -> None:
    from topos.diag.rules import evaluate_rules

    quiet = evaluate_rules(_protected_frame(rf_d_per_s=0.2), ToposConfig())
    assert not [f for f in quiet if f.rule_id == "protected_disk_refault"]
    loud = evaluate_rules(_protected_frame(rf_d_per_s=3.0), ToposConfig())
    assert [f for f in loud if f.rule_id == "protected_disk_refault"]


def test_memory_high_user_visible_is_red_only_at_crit() -> None:
    from topos.diag.rules import evaluate_rules

    def severity(psi: float) -> str:
        frame = _protected_frame(ram=200, mem_high=100, psi_mem_full_avg10=psi)
        return next(f.severity for f in evaluate_rules(frame, ToposConfig()) if f.rule_id == "memory_high_user_visible")

    assert severity(1.5) == "warn"
    assert severity(2.5) == "red"


def test_pressure_is_unavailable_when_no_input_is_readable() -> None:
    from topos.diag import annotate
    from topos.model import Frame

    entity = Entity(key="x.slice", kind="slice", parent="")
    frame = Frame(schema_version=1, ts=0.0, interval_s=5.0, host={}, entities={"x.slice": EntityFrame(entity=entity, metrics={})})
    annotate(frame, ToposConfig())
    assert frame.entities["x.slice"].metrics["pressure"] == MetricValue(None, "unavail_kernel")
