"""Write bench: region sizing, scratch lifecycle, budget enforcement, rate ceilings, abort paths, O_DIRECT alignment."""

from __future__ import annotations

import os
import types

import pytest
from conftest import make_env

from iogov.bench import over_4k, run_bench, seq_write_phase
from iogov.budget import BudgetExceeded, DailyBudget, TokenBucket, added_wait_ms, compute_budget
from iogov.collectors import Collector
from iogov.config import GiB, MiB, ConfigError, from_mapping
from iogov.gating import RunGuard
from iogov.scratch import PREFIX, Scratch, ScratchBusy, fstype_of, plan_region, remove_stale


def sv(total, free):
    return lambda p: types.SimpleNamespace(f_blocks=total // 4096, f_frsize=4096, f_bavail=free // 4096, f_bfree=free // 4096)


# --- region sizing ------------------------------------------------------------------------------------------


def test_region_is_512mib_on_a_big_disk_and_shrinks_on_a_small_one(world):
    cfg = from_mapping({"scratch_dir": str(world.scratch), "scratch_fs_allowed": []})
    big = plan_region(cfg, 1024 * GiB, sv(1024 * GiB, 500 * GiB))
    assert (big.mode, big.region_bytes, big.probe_bytes, big.escalation_bytes) == ("file", 512 * MiB, 64 * MiB, 192 * MiB)
    small = plan_region(cfg, 32 * GiB, sv(32 * GiB, 20 * GiB))  # 1% of 32 GiB = 327 MiB
    assert small.mode == "file" and small.region_bytes == 327 * MiB and small.probe_bytes < 64 * MiB + 1
    tight = plan_region(cfg, 32 * GiB, sv(32 * GiB, int(2.5 * GiB)))  # reserve 2 GiB leaves 0.5 GiB: 25% = 128 MiB
    assert tight.region_bytes == 128 * MiB and tight.probe_bytes == 16 * MiB


def test_region_degrades_to_none_with_a_stated_reason(world):
    cfg = from_mapping({"scratch_dir": str(world.scratch), "scratch_fs_allowed": []})
    p = plan_region(cfg, 32 * GiB, sv(32 * GiB, 1 * GiB))
    assert p.mode == "none" and "below the reserve" in p.reason
    p = plan_region(cfg, 2 * GiB, sv(32 * GiB, 20 * GiB))  # 1% of 2 GiB = 20 MiB -> ok; 100 MiB disk -> 1 MiB < region_min
    assert plan_region(cfg, 100 * MiB, sv(32 * GiB, 20 * GiB)).mode == "none"
    cfg2 = from_mapping({"scratch_dir": str(world.scratch)})  # default allowed list: ext4/xfs only
    assert plan_region(cfg2, 1024 * GiB, sv(1024 * GiB, 500 * GiB), fstype="btrfs").reason.startswith("filesystem type")


def test_region_never_exceeds_the_hard_cap_or_the_reserve(world):
    cfg = from_mapping({"scratch_dir": str(world.scratch), "scratch_fs_allowed": [], "region_default": 1 * GiB})
    p = plan_region(cfg, 100 * 1024 * GiB, sv(100 * 1024 * GiB, 90 * 1024 * GiB))
    assert p.region_bytes <= cfg.region_hard_cap
    assert p.free_bytes - p.region_bytes >= cfg.free_reserve_bytes
    with pytest.raises(ConfigError):
        from_mapping({"region_default": 4 * GiB})


def test_fstype_of_picks_the_longest_mount(tmp_path):
    mi = tmp_path / "mountinfo"
    mi.write_text("1 0 8:1 / / rw - ext4 /dev/a rw\n2 1 8:2 / /var/lib rw - xfs /dev/b rw\n3 1 0:5 / /tmp rw - tmpfs t rw\n")
    assert fstype_of("/var/lib/iogov", str(mi)) == "xfs"
    assert fstype_of("/etc", str(mi)) == "ext4"


# --- scratch lifecycle --------------------------------------------------------------------------------------


def test_scratch_file_is_exclusive_locked_and_removed(world):
    env, _ = make_env()
    cfg = world.config()
    with Scratch(cfg, env, 1 * MiB) as sc:
        assert os.path.exists(sc.path) and os.stat(sc.path).st_mode & 0o777 == 0o600
        with pytest.raises(ScratchBusy):
            with Scratch(cfg, env, 1 * MiB):
                pass
        with pytest.raises(ScratchBusy):
            remove_stale(cfg.scratch_dir)
        path = sc.path
    assert not os.path.exists(path)


def test_scratch_removed_when_the_body_raises(world):
    env, _ = make_env()
    cfg = world.config()
    with pytest.raises(KeyError):
        with Scratch(cfg, env, 1 * MiB) as sc:
            path = sc.path
            raise KeyError("x")
    assert not os.path.exists(path)


def test_scratch_uses_aligned_direct_io_or_reports_the_fallback(world):
    env, _ = make_env()
    cfg = world.config()
    with Scratch(cfg, env, 1 * MiB) as sc:
        assert sc.direct in (True, False)
        guard = RunGuard(cfg, Collector(cfg, [], env), env, True)
        lane = seq_write_phase(sc, cfg, env, guard, DailyBudget({}, cfg, env.wall), 0, 256 * 1024, 1)
        assert lane["bytes"] == 256 * 1024
        data = os.pread(sc.fd, 256 * 1024, 0) if not sc.direct else open(sc.path, "rb").read(256 * 1024)
        assert len(data) == 256 * 1024 and all(data[i:i + 4096].count(0) < 4096 for i in range(0, len(data), 4096))  # non-zero data


# --- lanes: sync boundaries, budget, rate ceilings -----------------------------------------------------------


def test_each_phase_has_its_own_sync_boundaries(world):
    env, _ = make_env()
    cfg = world.config()
    guard = RunGuard(cfg, Collector(cfg, [], env), env, True)
    budget = DailyBudget({}, cfg, env.wall)
    with Scratch(cfg, env, 2 * MiB) as sc:
        a = seq_write_phase(sc, cfg, env, guard, budget, 0, 1 * MiB, 1)
        b = seq_write_phase(sc, cfg, env, guard, budget, 1 * MiB, 1 * MiB, 4)
    assert a["syncs"] == b["syncs"] == 2  # sync_every_bytes=512 KiB
    assert a["rate_durable_bps"] <= a["rate_write_bps"] and b["qd"] == 4


def test_daily_write_budget_is_enforced_before_submission(world):
    env, _ = make_env()
    cfg = world.config(max_write_bytes_per_day=300 * 1024)
    state: dict = {}
    guard = RunGuard(cfg, Collector(cfg, [], env), env, True)
    budget = DailyBudget(state, cfg, env.wall)
    with Scratch(cfg, env, 2 * MiB) as sc:
        lane = seq_write_phase(sc, cfg, env, guard, budget, 0, 1 * MiB, 1)
    assert lane["aborted"].startswith("budget:")
    assert state["budget"]["write_bytes"] <= 300 * 1024  # never charged past the cap
    assert lane["bytes"] <= 300 * 1024


def test_budget_day_rollover_and_remaining(world):
    env, clock = make_env()
    cfg = world.config(max_write_bytes_per_day=10_000)
    b = DailyBudget({}, cfg, env.wall)
    b.charge("write", 9_000)
    with pytest.raises(BudgetExceeded):
        b.charge("write", 2_000)
    assert b.remaining("write") == 1_000
    clock.t += 86400 * 1.5
    assert b.remaining("write") == 10_000


def test_token_bucket_enforces_the_rate_ceiling():
    env, clock = make_env()
    bucket = TokenBucket(100.0, env.mono, env.sleep, burst=1)
    t0 = clock.t
    for _ in range(11):
        bucket.acquire(1)
    assert clock.t - t0 >= 10 / 100 - 0.002  # 11 ops at <= 100/s need ~0.1 s


def test_4k_sync_lane_never_exceeds_its_iops_ceiling(world):
    env, clock = make_env()
    cfg = world.config(sync4k_iops_max=100.0, sync4k_samples=40)
    guard = RunGuard(cfg, Collector(cfg, [], env), env, True)
    stamps = []
    orig = os.pwrite
    os.pwrite = lambda fd, d, off: (stamps.append(clock.t), orig(fd, d, off))[1]
    try:
        with Scratch(cfg, env, 1 * MiB) as sc:
            os.pwrite(sc.fd, bytes(0x41 for _ in range(4096)) * 16, 0) if not sc.direct else None
            lane = over_4k(sc, cfg, env, guard, DailyBudget({}, cfg, env.wall), 256 * 1024)
    finally:
        os.pwrite = orig
    stamps = stamps[-40:]
    assert lane["n"] == 40
    assert (stamps[-1] - stamps[0]) / 39 >= 1 / 100 - 0.002


def test_slow_io_aborts_the_bench_and_cleans_up(world):
    env, clock = make_env()
    cfg = world.config(abort_latency_ms=50.0)
    clock.step = 0.5  # every IO "takes" 500 ms
    guard = RunGuard(cfg, Collector(cfg, [], env), env, True)
    plan = plan_region(cfg, 16 * MiB)
    out = run_bench(cfg, env, guard, DailyBudget({}, cfg, env.wall), plan, [], False)
    assert out["status"] == "aborted" and out["abort"].startswith("slow_io")
    assert not [n for n in os.listdir(world.scratch) if n.startswith(PREFIX)]
    assert out["lanes"]["w_fresh_qd1"]["bytes"] <= 64 * 1024 * 2  # stopped submitting almost at once


def test_region_none_runs_read_lanes_only(world):
    env, _ = make_env()
    cfg = world.config(free_reserve_bytes=10**15)
    guard = RunGuard(cfg, Collector(cfg, [], env), env, True)
    plan = plan_region(cfg, 16 * MiB)
    assert plan.mode == "none"
    from iogov.rawread import RawReader
    with RawReader(str(world.disk), env, allow_regular=True) as r:
        out = run_bench(cfg, env, guard, DailyBudget({}, cfg, env.wall), plan, [r], False)
    assert set(out["lanes"]) == {"r_seq"} and out["region"]["disabled"]
    assert out["io"]["write_bytes"] == 0


def test_computed_budget_matches_the_design_note_numbers():
    cfg = from_mapping({})
    b = compute_budget(cfg, probe_bytes=64 * MiB)
    assert b["ticks_per_day"] == 288 and b["benches_per_day"] == 4
    assert b["read_ops_per_day_typical"] == 288 * 14 and b["read_ops_per_day_worst"] == 288 * 38
    assert b["read_bytes_per_day_worst"] <= cfg.max_read_bytes_per_day
    assert 250 * MiB < b["write_bytes_per_day"] < 320 * MiB
    assert b["write_bytes_per_day"] <= cfg.max_write_bytes_per_day
    assert b["ceilings"]["max_bytes_in_flight"] == 256 * 1024
    assert b["added_wait_ms_at_device_rate"]["15 MiB/s"] == pytest.approx(16.67, abs=0.01)
    assert added_wait_ms(256 * 1024, 340 * MiB) < 1.0
    assert compute_budget(cfg, region_mode="none")["write_bytes_per_day"] == 0
