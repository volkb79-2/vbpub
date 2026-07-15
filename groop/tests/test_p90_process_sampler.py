from __future__ import annotations

import time
from pathlib import Path

import pytest

from groop.config import ProcessConfig, ProcessConfigError
from groop.model import CiuMeta, DockerMeta, Entity, frame_from_jsonable, frame_to_jsonable
from groop.procs import candidates as candidates_mod
from groop.procs import procfs
from groop.procs.candidates import ProcessKey, select_candidates
from groop.procs.owners import join_owner
from groop.procs.sampler import ProcessSampler
from groop.procs.sensitivity import redact_process_row
from groop.daemon.api import Sensitivity
from groop.query.engine import MetricRef, Query, run_query


# ---------------------------------------------------------------------------
# Fixture helper: a synthetic /proc tree the sampler can read directly.
# ---------------------------------------------------------------------------


class FakeProcTree:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "stat").write_text(
            "cpu  0 0 0 0 0 0 0 0 0 0\ncpu0 0 0 0 0 0 0 0 0 0\nbtime 1700000000\n"
        )
        boot_dir = self.root / "sys" / "kernel" / "random"
        boot_dir.mkdir(parents=True, exist_ok=True)
        (boot_dir / "boot_id").write_text("fixture-boot-id\n")

    def write_pid(
        self,
        pid: int,
        *,
        comm: str = "worker",
        state: str = "S",
        ppid: int = 1,
        utime: int = 0,
        stime: int = 0,
        minflt: int = 0,
        majflt: int = 0,
        num_threads: int = 1,
        starttime: int = 10,
        blkio_ticks: int | None = 0,
        read_bytes: int = 0,
        write_bytes: int = 0,
        cancelled_write_bytes: int = 0,
        uid: int = 1000,
        vm_rss_kb: int = 1000,
        vm_size_kb: int = 2000,
        vm_swap_kb: int = 0,
        vol_ctxt: int = 0,
        invol_ctxt: int = 0,
        cmdline: tuple[str, ...] = ("worker",),
        cgroup_path: str = "",
    ) -> None:
        d = self.root / str(pid)
        d.mkdir(parents=True, exist_ok=True)
        fields = ["0"] * 40
        fields[0] = state
        fields[1] = str(ppid)
        fields[2] = "1"
        fields[3] = "1"
        fields[4] = "0"
        fields[5] = "-1"
        fields[6] = "0"
        fields[7] = str(minflt)
        fields[8] = "0"
        fields[9] = str(majflt)
        fields[10] = "0"
        fields[11] = str(utime)
        fields[12] = str(stime)
        fields[13] = "0"
        fields[14] = "0"
        fields[15] = "20"
        fields[16] = "0"
        fields[17] = str(num_threads)
        fields[18] = "0"
        fields[19] = str(starttime)
        if blkio_ticks is not None:
            fields[39] = str(blkio_ticks)
        (d / "stat").write_text(f"{pid} ({comm}) " + " ".join(fields) + "\n")
        (d / "io").write_text(
            "rchar: 0\nwchar: 0\nsyscr: 0\nsyscw: 0\n"
            f"read_bytes: {read_bytes}\nwrite_bytes: {write_bytes}\n"
            f"cancelled_write_bytes: {cancelled_write_bytes}\n"
        )
        (d / "status").write_text(
            f"Name:\t{comm}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n"
            f"VmRSS:\t{vm_rss_kb} kB\nVmSize:\t{vm_size_kb} kB\nVmSwap:\t{vm_swap_kb} kB\n"
            f"Threads:\t{num_threads}\n"
            f"voluntary_ctxt_switches:\t{vol_ctxt}\nnonvoluntary_ctxt_switches:\t{invol_ctxt}\n"
        )
        (d / "cmdline").write_bytes(("\0".join(cmdline) + "\0").encode())
        (d / "cgroup").write_text(f"0::/{cgroup_path}\n")

    def remove_pid(self, pid: int) -> None:
        import shutil

        shutil.rmtree(self.root / str(pid), ignore_errors=True)


def _make_key(pid: int, start_ticks: int = 10, boot: str = "b") -> ProcessKey:
    return ProcessKey(pid=pid, start_ticks=start_ticks, boot_id=boot)


# ---------------------------------------------------------------------------
# Config validation (Required contract 3 / D-019).
# ---------------------------------------------------------------------------


def test_process_config_defaults_match_d019() -> None:
    cfg = ProcessConfig()
    assert (cfg.top_cpu, cfg.top_io, cfg.pinned_cap, cfg.recently_hot_grace_seconds, cfg.hard_cap) == (
        20,
        20,
        16,
        60.0,
        64,
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"hard_cap": 4, "pinned_cap": 16},  # hard cap below pinned allowance
        {"top_cpu": -1},
        {"top_io": -1},
        {"pinned_cap": -1},
        {"recently_hot_grace_seconds": -1.0},
        {"hard_cap": 0},
    ],
)
def test_process_config_rejects_invalid_relationships(kwargs: dict[str, object]) -> None:
    with pytest.raises(ProcessConfigError):
        ProcessConfig(**kwargs)


# ---------------------------------------------------------------------------
# O1 — bounded capped union of top-20 CPU, top-20 I/O, selected/pinned and
# recently-hot-for-60s candidates.
# ---------------------------------------------------------------------------


def test_o1_bounded_union_of_cpu_io_pinned_and_recent() -> None:
    config = ProcessConfig(top_cpu=2, top_io=2, pinned_cap=1, hard_cap=10, recently_hot_grace_seconds=60.0)
    cpu_only = [_make_key(1), _make_key(2)]  # top-2 CPU-hot
    io_only = [_make_key(3), _make_key(4)]  # top-2 IO-hot
    overlap = _make_key(5)  # hot on both axes, but ranked outside top-2 of each
    pinned_key = _make_key(6)
    cold_key = _make_key(7)  # neither hot nor pinned: must be excluded

    cpu_rate = {cpu_only[0]: 100.0, cpu_only[1]: 90.0, overlap: 10.0, cold_key: 1.0}
    io_rate = {io_only[0]: 500.0, io_only[1]: 400.0, overlap: 10.0, cold_key: 1.0}

    result = select_candidates(
        keys=[*cpu_only, *io_only, overlap, pinned_key, cold_key],
        cpu_rate=cpu_rate,
        io_rate=io_rate,
        pinned_pids=frozenset({pinned_key.pid}),
        hot_since={},
        now=1000.0,
        config=config,
        eligible_count=7,
    )

    retained = set(result.retained)
    assert retained == {cpu_only[0], cpu_only[1], io_only[0], io_only[1], pinned_key}
    assert cold_key not in retained
    assert overlap not in retained  # ranked outside top-2 of both axes, not pinned
    assert result.reasons[cpu_only[0]].cpu_hot
    assert result.reasons[io_only[0]].io_hot
    assert result.reasons[pinned_key].pinned


def test_o1_hard_cap_never_exceeded_even_when_union_is_larger() -> None:
    config = ProcessConfig(top_cpu=50, top_io=50, pinned_cap=5, hard_cap=8, recently_hot_grace_seconds=60.0)
    keys = [_make_key(pid) for pid in range(100, 140)]
    cpu_rate = {key: float(1000 - i) for i, key in enumerate(keys)}
    result = select_candidates(
        keys=keys,
        cpu_rate=cpu_rate,
        io_rate={},
        pinned_pids=frozenset(),
        hot_since={},
        now=0.0,
        config=config,
        eligible_count=len(keys),
    )
    assert len(result.retained) == config.hard_cap
    assert result.candidate_count == len(keys)
    assert len(result.omitted) == len(keys) - config.hard_cap
    assert all(reason == "hard_cap" for reason in result.omitted_reason.values())
    # The highest-CPU processes must be the ones that survive, never arbitrary ones.
    assert set(result.retained) == set(keys[: config.hard_cap])


# ---------------------------------------------------------------------------
# O2 — an I/O burst remains in the recently-hot set for the grace window.
# ---------------------------------------------------------------------------


def test_o2_recently_hot_grace_then_expiry() -> None:
    config = ProcessConfig(top_cpu=1, top_io=1, pinned_cap=1, hard_cap=10, recently_hot_grace_seconds=60.0)
    burst_key = _make_key(50)

    # Tick 1: the process bursts I/O and is the top-1 I/O-hot candidate.
    tick1 = select_candidates(
        keys=[burst_key],
        cpu_rate={},
        io_rate={burst_key: 1_000_000.0},
        pinned_pids=frozenset(),
        hot_since={},
        now=0.0,
        config=config,
        eligible_count=1,
    )
    assert burst_key in tick1.retained
    assert tick1.reasons[burst_key].io_hot

    # Tick 2: the burst ends (no I/O rate at all), 30s later — still within grace.
    tick2 = select_candidates(
        keys=[burst_key],
        cpu_rate={},
        io_rate={},
        pinned_pids=frozenset(),
        hot_since=tick1.hot_since,
        now=30.0,
        config=config,
        eligible_count=1,
    )
    assert burst_key in tick2.retained
    assert tick2.reasons[burst_key].recently_hot
    assert not tick2.reasons[burst_key].io_hot

    # Tick 3: 61s after the burst — past the 60s grace, must be dropped.
    tick3 = select_candidates(
        keys=[burst_key],
        cpu_rate={},
        io_rate={},
        pinned_pids=frozenset(),
        hot_since=tick2.hot_since,
        now=61.0,
        config=config,
        eligible_count=1,
    )
    assert burst_key not in tick3.retained


# ---------------------------------------------------------------------------
# O3 — PID reuse via /proc/PID/stat start time; histories never join.
# ---------------------------------------------------------------------------


def test_o3_pid_reuse_never_joins_history_end_to_end(tmp_path: Path) -> None:
    tree = FakeProcTree(tmp_path / "proc")
    pid = 700
    tree.write_pid(pid, comm="original", utime=0, starttime=10)
    clock = [0.0]
    sampler = ProcessSampler(
        proc_root=tree.root,
        now=lambda: clock[0],
        pinned_pids=frozenset({pid}),
        config=ProcessConfig(pinned_cap=2, hard_cap=5),
    )
    sampler.sample({})  # cold baseline for the original incarnation

    clock[0] = 5.0
    tree.write_pid(pid, comm="original", utime=100, starttime=10)  # +100 ticks over 5s
    tick_a = sampler.sample({})
    key_a = ProcessKey(pid=pid, start_ticks=10, boot_id=sampler._boot_id)
    original_row = tick_a.frame.entities[key_a.entity_key()]
    assert original_row.metrics["proc_cpu_pct"].v == pytest.approx((100 / procfs.CLK_TCK) / 5.0 * 100.0)

    # The kernel reuses the PID: a brand-new process, different start time,
    # whose counters happen to already exceed the old process's last utime —
    # a bare-PID-keyed implementation would compute a plausible (but WRONG)
    # continuation delta here instead of correctly starting fresh.
    clock[0] = 10.0
    tree.write_pid(pid, comm="reincarnated", utime=150, starttime=999)
    tick_b = sampler.sample({})
    key_b = ProcessKey(pid=pid, start_ticks=999, boot_id=sampler._boot_id)
    assert key_a != key_b
    reused_row = tick_b.frame.entities[key_b.entity_key()]
    # First tick of the new incarnation: no prior baseline under the NEW key,
    # so the rate must be None (cold), never a spurious inherited value.
    assert reused_row.metrics["proc_cpu_pct"].v is None
    assert reused_row.process["comm"] == "reincarnated"
    # The old incarnation's row must not reappear under the new key's entity.
    assert key_a.entity_key() != key_b.entity_key()


def test_o3_process_key_identity_pure() -> None:
    old = ProcessKey(pid=42, start_ticks=100, boot_id="boot-a")
    new = ProcessKey(pid=42, start_ticks=200, boot_id="boot-a")
    assert old != new
    assert old.entity_key() != new.entity_key()


# ---------------------------------------------------------------------------
# O4 — caps and tie-breaking order are deterministic across repeated runs.
# ---------------------------------------------------------------------------


def test_o4_selection_is_deterministic_across_repeated_runs() -> None:
    config = ProcessConfig(top_cpu=5, top_io=5, pinned_cap=2, hard_cap=6, recently_hot_grace_seconds=60.0)
    keys = [_make_key(pid) for pid in range(1, 21)]
    # Deliberate ties: several equal CPU rates so tie-break-by-pid must fire.
    cpu_rate = {key: 50.0 for key in keys[:10]}
    io_rate = {key: 20.0 for key in keys[5:15]}
    pinned = frozenset({keys[19].pid})

    def run() -> tuple[list[ProcessKey], dict]:
        result = select_candidates(
            keys=list(keys),
            cpu_rate=dict(cpu_rate),
            io_rate=dict(io_rate),
            pinned_pids=pinned,
            hot_since={},
            now=42.0,
            config=config,
            eligible_count=len(keys),
        )
        return result.retained, {k: v.as_tuple() for k, v in result.reasons.items()}

    first = run()
    for _ in range(5):
        assert run() == first


# ---------------------------------------------------------------------------
# O5 — selected/pinned processes survive eviction pressure.
# ---------------------------------------------------------------------------


def test_o5_pinned_survives_eviction_pressure() -> None:
    config = ProcessConfig(top_cpu=20, top_io=0, pinned_cap=1, hard_cap=3, recently_hot_grace_seconds=60.0)
    hot_keys = [_make_key(pid) for pid in range(1, 10)]
    cpu_rate = {key: float(1000 - i) for i, key in enumerate(hot_keys)}
    pinned_key = _make_key(999)  # far colder than every hot candidate
    cpu_rate[pinned_key] = 0.1

    result = select_candidates(
        keys=[*hot_keys, pinned_key],
        cpu_rate=cpu_rate,
        io_rate={},
        pinned_pids=frozenset({pinned_key.pid}),
        hot_since={},
        now=0.0,
        config=config,
        eligible_count=len(hot_keys) + 1,
    )
    assert pinned_key in result.retained
    assert result.reasons[pinned_key].pinned
    assert len(result.retained) == config.hard_cap  # 1 pinned + 2 hottest


# ---------------------------------------------------------------------------
# O6 — procfs disappearance / permission-denied surface as typed states.
# ---------------------------------------------------------------------------


def test_o6_vanished_process_is_typed_not_zero(tmp_path: Path) -> None:
    tree = FakeProcTree(tmp_path / "proc")
    pid = 800
    tree.write_pid(pid, utime=0, starttime=5)
    clock = [0.0]
    sampler = ProcessSampler(
        proc_root=tree.root,
        now=lambda: clock[0],
        pinned_pids=frozenset({pid}),
        config=ProcessConfig(pinned_cap=2, hard_cap=5),
    )
    sampler.sample({})
    clock[0] = 5.0
    tree.write_pid(pid, utime=50, starttime=5)
    sampler.sample({})  # now retained + warm

    clock[0] = 10.0
    tree.remove_pid(pid)
    tick = sampler.sample({})
    key = ProcessKey(pid=pid, start_ticks=5, boot_id=sampler._boot_id)
    row = tick.frame.entities.get(key.entity_key())
    assert row is not None, "a vanished-but-recently-relevant process must still surface a typed row"
    assert row.metrics["proc_present"].v == 0
    assert row.metrics["proc_present"].src == "exact"
    for name in ("proc_cpu_pct", "proc_rss", "proc_io_r_bps"):
        assert row.metrics[name].v is None
        assert row.metrics[name].src == "unavail_kernel"
    assert row.process["present"] is False


def test_o6_permission_denied_is_typed_not_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tree = FakeProcTree(tmp_path / "proc")
    pid = 900
    tree.write_pid(pid, utime=0, starttime=5)
    sampler = ProcessSampler(
        proc_root=tree.root,
        now=lambda: 0.0,
        pinned_pids=frozenset({pid}),
        config=ProcessConfig(pinned_cap=2, hard_cap=5),
    )

    real_read_io = procfs.read_io

    def denied_read_io(pid_dir: Path):
        if pid_dir.name == str(pid):
            return None, "unavail_perm"
        return real_read_io(pid_dir)

    monkeypatch.setattr("groop.procs.sampler.procfs.read_io", denied_read_io)
    tick = sampler.sample({})
    key = ProcessKey(pid=pid, start_ticks=5, boot_id=sampler._boot_id)
    row = tick.frame.entities[key.entity_key()]
    assert row.metrics["proc_io_r_bps"].v is None
    assert row.metrics["proc_io_r_bps"].src == "unavail_perm"
    assert row.metrics["proc_io_w_bps"].src == "unavail_perm"


# ---------------------------------------------------------------------------
# O7 — cgroup/systemd/Docker/CIU owner joins carry correct provenance without
# duplicating cgroup accounting totals.
# ---------------------------------------------------------------------------


def test_o7_owner_join_provenance_docker_ciu_systemd() -> None:
    docker_key = "system.slice/docker-" + "ab" * 32 + ".scope"
    entity = Entity(
        key=docker_key,
        kind="scope",
        parent="system.slice",
        docker=DockerMeta(cid="ab" * 32, full_id="ab" * 32, name="myservice", image="myimage:latest"),
        ciu=CiuMeta(stack="infra/redis-core", phase_raw="phase_2", phase=2, source="label"),
    )
    owner = join_owner(docker_key, {docker_key: entity})
    assert owner.slice_name == "system.slice"
    assert owner.docker_cid == "ab" * 32
    assert owner.docker_name == "myservice"
    assert owner.docker_image == "myimage:latest"
    assert owner.ciu_stack == "infra/redis-core"
    assert owner.ciu_phase == 2


def test_o7_owner_join_end_to_end_no_duplicated_cgroup_metrics(tmp_path: Path) -> None:
    tree = FakeProcTree(tmp_path / "proc")
    docker_key = "system.slice/docker-" + "cd" * 32 + ".scope"
    pid = 1000
    tree.write_pid(pid, utime=0, starttime=1, cgroup_path=docker_key)
    entity = Entity(
        key=docker_key,
        kind="scope",
        parent="system.slice",
        docker=DockerMeta(cid="cd" * 32, full_id="cd" * 32, name="webapp", image="web:1.0"),
    )
    sampler = ProcessSampler(
        proc_root=tree.root,
        now=lambda: 0.0,
        pinned_pids=frozenset({pid}),
        config=ProcessConfig(pinned_cap=2, hard_cap=5),
    )
    tick = sampler.sample({docker_key: entity})
    key = ProcessKey(pid=pid, start_ticks=1, boot_id=sampler._boot_id)
    row = tick.frame.entities[key.entity_key()]
    assert row.process["docker"]["name"] == "webapp"
    assert row.process["unit"] == docker_key.rsplit("/", 1)[-1]  # a docker scope IS a systemd transient unit
    assert row.entity.parent == docker_key
    # Cgroup subtree accounting metrics (e.g. "ram") never appear on a process row.
    assert not ({"ram", "cpu_pct", "io_r_bps"} & set(row.metrics))


# ---------------------------------------------------------------------------
# Required contract 5 — process current/raw/history feeds through the P88
# query engine (no second aggregation engine).
# ---------------------------------------------------------------------------


def test_contract5_process_history_feeds_p88_query_engine(tmp_path: Path) -> None:
    tree = FakeProcTree(tmp_path / "proc")
    pid = 55
    tree.write_pid(pid, utime=0, starttime=1)
    clock = [0.0]
    sampler = ProcessSampler(
        proc_root=tree.root,
        now=lambda: clock[0],
        pinned_pids=frozenset({pid}),
        config=ProcessConfig(pinned_cap=2, hard_cap=5),
    )
    for i in range(3):
        clock[0] = i * 5.0
        tree.write_pid(pid, utime=i * 100, starttime=1)
        sampler.sample({})

    source = sampler.frame_source()
    query = Query(shape="summary", metrics=(MetricRef(name="proc_cpu_pct"),), window_spec="all", projection="flat")
    result = run_query(source, query)
    assert result.rows
    row = result.rows[0]
    assert row["metrics"]["proc_cpu_pct"]["semantic"] == "rate"
    assert row["metrics"]["proc_cpu_pct"]["sample_count"] == 2  # 3 frames, 1 cold start
    assert result.meta["source"]["kind"] == "process-sampler"


# ---------------------------------------------------------------------------
# Required contract 6 — command lines and identities follow P81 sensitivity.
# ---------------------------------------------------------------------------


def test_contract6_cmdline_redacted_below_sensitive_ceiling() -> None:
    row = {"comm": "python3", "cmdline": "python3 app.py --token=secret"}
    redacted = redact_process_row(dict(row), Sensitivity.OPERATIONAL)
    assert redacted["comm"] == "python3"
    assert redacted["cmdline"] == {"redacted": True, "sensitivity": "sensitive"}
    unredacted = redact_process_row(dict(row), Sensitivity.SENSITIVE)
    assert unredacted["cmdline"] == row["cmdline"]
    passthrough = redact_process_row(dict(row), None)
    assert passthrough == row


# ---------------------------------------------------------------------------
# O8 — large-PID benchmark and mutation tests on the I/O candidate selection
# path and hard-cap enforcement.
# ---------------------------------------------------------------------------


def test_o8_large_pid_benchmark(tmp_path: Path) -> None:
    tree = FakeProcTree(tmp_path / "proc")
    n = 2500
    for pid in range(2, n + 2):
        tree.write_pid(pid, utime=pid % 50, starttime=1, read_bytes=pid % 1000)
    config = ProcessConfig(top_cpu=20, top_io=20, pinned_cap=16, hard_cap=64)
    clock = [0.0]
    sampler = ProcessSampler(proc_root=tree.root, now=lambda: clock[0], config=config)
    sampler.sample({})  # cold baseline

    clock[0] = 5.0
    for pid in range(2, n + 2):
        tree.write_pid(pid, utime=(pid % 50) + pid, starttime=1, read_bytes=(pid % 1000) + pid * 10)
    start = time.monotonic()
    tick = sampler.sample({})
    elapsed = time.monotonic() - start

    assert len(tick.frame.entities) <= config.hard_cap
    assert tick.coverage.eligible_count == n
    assert elapsed < 10.0, f"large-PID sample() took {elapsed:.2f}s, over the 10s budget"


def test_o8_mutation_io_ranking_is_load_bearing(monkeypatch: pytest.MonkeyPatch) -> None:
    config = ProcessConfig(top_cpu=0, top_io=1, pinned_cap=1, hard_cap=2, recently_hot_grace_seconds=60.0)
    high_io = _make_key(1)
    low_io = _make_key(2)
    io_rate = {high_io: 1_000_000.0, low_io: 1.0}

    correct = select_candidates(
        keys=[high_io, low_io],
        cpu_rate={},
        io_rate=io_rate,
        pinned_pids=frozenset(),
        hot_since={},
        now=0.0,
        config=config,
        eligible_count=2,
    )
    assert correct.retained == [high_io]

    def broken_rank(items: dict, top_n: int) -> list:
        # Mutant: pick the LOWEST rate instead of the highest.
        ordered = sorted(items.items(), key=lambda kv: (kv[1], kv[0].pid, kv[0].start_ticks))
        return [key for key, _ in ordered[: max(0, top_n)]]

    monkeypatch.setattr(candidates_mod, "_rank", broken_rank)
    mutated = select_candidates(
        keys=[high_io, low_io],
        cpu_rate={},
        io_rate=io_rate,
        pinned_pids=frozenset(),
        hot_since={},
        now=0.0,
        config=config,
        eligible_count=2,
    )
    assert mutated.retained == [low_io]
    assert mutated.retained != correct.retained


def test_o8_mutation_hard_cap_enforcement_is_load_bearing(monkeypatch: pytest.MonkeyPatch) -> None:
    config = ProcessConfig(top_cpu=10, top_io=0, pinned_cap=1, hard_cap=3, recently_hot_grace_seconds=60.0)
    keys = [_make_key(pid) for pid in range(1, 11)]
    cpu_rate = {key: float(1000 - i) for i, key in enumerate(keys)}

    correct = select_candidates(
        keys=keys,
        cpu_rate=cpu_rate,
        io_rate={},
        pinned_pids=frozenset(),
        hot_since={},
        now=0.0,
        config=config,
        eligible_count=len(keys),
    )
    assert len(correct.retained) == config.hard_cap

    def broken_apply_hard_cap(ordered_non_pinned: list, remaining_slots: int) -> tuple[list, list]:
        # Mutant: never truncate — the hard cap silently stops being enforced.
        return ordered_non_pinned, []

    monkeypatch.setattr(candidates_mod, "_apply_hard_cap", broken_apply_hard_cap)
    mutated = select_candidates(
        keys=keys,
        cpu_rate=cpu_rate,
        io_rate={},
        pinned_pids=frozenset(),
        hot_since={},
        now=0.0,
        config=config,
        eligible_count=len(keys),
    )
    assert len(mutated.retained) > config.hard_cap
    assert len(mutated.retained) != len(correct.retained)


# ---------------------------------------------------------------------------
# Frame round-trip / registry hygiene sanity (not a numbered oracle, but a
# structural guarantee the rest of this suite depends on).
# ---------------------------------------------------------------------------


def test_process_frame_jsonable_round_trips(tmp_path: Path) -> None:
    tree = FakeProcTree(tmp_path / "proc")
    tree.write_pid(1234, utime=0, starttime=1)
    sampler = ProcessSampler(
        proc_root=tree.root,
        now=lambda: 0.0,
        pinned_pids=frozenset({1234}),
        config=ProcessConfig(pinned_cap=1, hard_cap=5),
    )
    tick = sampler.sample({})
    payload = frame_to_jsonable(tick.frame)
    assert frame_from_jsonable(payload) == tick.frame
