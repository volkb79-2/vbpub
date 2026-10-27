"""Raw reads: read-only by construction, aligned, bounded, whole-device walk without repeats, zero-block handling."""

from __future__ import annotations

import math
import os

import pytest
from conftest import make_env

from iogov import rawread
from iogov.bench import tick_reads
from iogov.budget import BudgetExceeded, DailyBudget
from iogov.gating import RunGuard
from iogov.collectors import Collector
from iogov.rawread import AlignedBuffer, RawReader, RawReadError, block_at, coprime_stride, log2_hist, summarize_latencies


def test_device_is_only_ever_opened_read_only(world, monkeypatch):
    env, _ = make_env()
    flags_seen = []
    real_open = os.open

    def spy(path, flags, *a, **k):
        if str(path).startswith(str(world.dev)):
            flags_seen.append(flags)
        return real_open(path, flags, *a, **k)

    monkeypatch.setattr(os, "open", spy)
    forbidden = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    cfg = world.config()
    with RawReader(str(world.disk), env, allow_regular=True) as r:
        buf = AlignedBuffer(4096)
        r.read_at(buf, 0, 4096)
    assert flags_seen and all(not (f & forbidden) for f in flags_seen)
    assert all(f & os.O_CLOEXEC for f in flags_seen)


def test_open_helper_asserts_against_write_flags(monkeypatch):
    monkeypatch.setattr(rawread, "FORBIDDEN_FLAGS", os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECT", 0))
    with pytest.raises(AssertionError):
        rawread._open_ro("/dev/null", True)


def test_no_write_syscalls_are_issued_against_the_device(world, monkeypatch):
    env, _ = make_env()
    cfg = world.config()
    calls = []
    for name in ("write", "pwrite", "ftruncate", "fsync", "fdatasync"):
        real = getattr(os, name)
        monkeypatch.setattr(os, name, lambda *a, _n=name, _r=real, **k: (calls.append((_n, a[0])), _r(*a, **k))[1])
    with RawReader(str(world.disk), env, allow_regular=True) as r:
        fd = r.fd
        guard = RunGuard(cfg, Collector(cfg, [], env), env, False)
        tick_reads(r, cfg, env, guard, DailyBudget({}, cfg, env.wall), {})
    assert all(a != fd for _, a in calls)


def test_write_check_uses_access_not_an_o_wronly_open(world, monkeypatch):
    opened = []
    real_open = os.open
    monkeypatch.setattr(os, "open", lambda p, f, *a, **k: (opened.append(f), real_open(p, f, *a, **k))[1])
    rawread.write_denied(str(world.disk))
    assert opened == []  # a close-after-write on a disk makes udev re-probe it: never do that
    os.chmod(world.disk, 0o444)
    monkeypatch.setattr(os, "access", lambda p, m: False)
    assert rawread.write_denied(str(world.disk)) is True


def test_refuses_non_block_nodes_and_unlisted_paths(world):
    env, _ = make_env()
    with pytest.raises(RawReadError, match="not a block device"):
        RawReader(str(world.disk), env, allow_regular=False).open()
    with pytest.raises(RawReadError, match="allow-list"):
        RawReader(str(world.disk), env, allow_regular=True, allowed=("/dev/other",)).open()
    with pytest.raises(RawReadError, match="not a block device"):
        RawReader("/dev/null", env, allow_regular=True).open()


def test_reads_are_aligned_bounded_and_buffer_is_wiped(world):
    env, _ = make_env()
    with RawReader(str(world.disk), env, allow_regular=True) as r:
        buf = AlignedBuffer(4096)
        res = r.read_at(buf, 4096, 4096)
        assert res.nonzero and res.nbytes == 4096
        assert not buf.nonzero()  # content wiped after the check
        for off, length in ((1, 4096), (0, 100), (r.size, 4096), (-4096, 4096)):
            with pytest.raises(RawReadError):
                r.read_at(buf, off, length)


def test_zero_blocks_are_detected(world):
    env, _ = make_env()
    world.make_disk(16 * 1024 * 1024, zero_ranges=[(8192, 12288)])
    with RawReader(str(world.disk), env, allow_regular=True) as r:
        buf = AlignedBuffer(4096)
        assert r.read_at(buf, 4096, 4096).nonzero
        assert not r.read_at(buf, 8192, 4096).nonzero


@pytest.mark.parametrize("n", [16, 17, 100, 1024, 4096, 65537, 262144, 10**6 + 3])
def test_walk_visits_every_block_exactly_once_before_repeating(n):
    stride = coprime_stride(n)
    assert math.gcd(stride, n) == 1
    start = (n * 7) // 11
    sample = n if n <= 70000 else 70000
    seen = {block_at(i, n, stride, start) for i in range(sample)}
    assert len(seen) == sample  # no repeat inside a full period
    if n <= 70000:
        assert seen == set(range(n))


def test_walk_is_spread_out_not_sequential():
    n = 100000
    stride = coprime_stride(n)
    pos = [block_at(i, n, stride, 0) for i in range(50)]
    gaps = [abs(b - a) for a, b in zip(pos, pos[1:])]
    assert min(gaps) > n // 50  # consecutive probes land far apart


def test_tick_reads_exclude_zero_blocks_and_keep_witnesses(world):
    env, _ = make_env()
    world.make_disk(16 * 1024 * 1024, zero_ranges=[(0, 4 * 1024 * 1024)])
    cfg = world.config(read_samples_per_tick=10, read_max_attempts=30)
    state: dict = {}
    with RawReader(str(world.disk), env, allow_regular=True) as r:
        guard = RunGuard(cfg, Collector(cfg, [], env), env, False)
        out = tick_reads(r, cfg, env, guard, DailyBudget(state, cfg, env.wall), state)
        n_blocks = r.n_blocks()
    cold = out["r_cold"]
    assert cold["n"] == 10 and cold["attempts"] >= 10
    assert cold["zero"] == cold["attempts"] - cold["n"]
    dev = state["devices"]["vda"]
    assert dev["n_blocks"] == n_blocks and 0 < len(dev["witness"]) <= cfg.witness_keep
    # a resized device restarts the walk with a stride that is coprime to the NEW size
    world.make_disk(8 * 1024 * 1024)
    with RawReader(str(world.disk), env, allow_regular=True) as r:
        tick_reads(r, cfg, env, guard, DailyBudget(state, cfg, env.wall), state)
        assert state["devices"]["vda"]["n_blocks"] == r.n_blocks()
        assert math.gcd(state["devices"]["vda"]["stride"], r.n_blocks()) == 1


def test_read_budget_stops_reads_before_submission(world):
    env, _ = make_env()
    cfg = world.config(max_read_bytes_per_day=4096 * 3)
    state: dict = {}
    with RawReader(str(world.disk), env, allow_regular=True) as r:
        guard = RunGuard(cfg, Collector(cfg, [], env), env, False)
        with pytest.raises(BudgetExceeded):
            tick_reads(r, cfg, env, guard, DailyBudget(state, cfg, env.wall), state)
    assert state["budget"]["read_bytes"] == 4096 * 3  # never charged beyond the cap


def test_read_rate_is_capped_by_the_bucket(world):
    env, clock = make_env()
    cfg = world.config(read_iops_max=20.0, read_samples_per_tick=10, witness_reads=0)
    state: dict = {}
    with RawReader(str(world.disk), env, allow_regular=True) as r:
        guard = RunGuard(cfg, Collector(cfg, [], env), env, False)
        t0 = clock.t
        tick_reads(r, cfg, env, guard, DailyBudget(state, cfg, env.wall), state)
    # 10 reads at <= 20 IOPS (burst of 1) need ~9/20 s of (fake) time
    assert clock.t - t0 >= 0.44


def test_slow_read_stops_further_submissions(world):
    env, clock = make_env()
    cfg = world.config(abort_latency_ms=50.0)
    clock.step = 0.2  # every measured IO "takes" 200 ms
    state: dict = {}
    with RawReader(str(world.disk), env, allow_regular=True) as r:
        guard = RunGuard(cfg, Collector(cfg, [], env), env, False)
        from iogov.env import StopRequested
        with pytest.raises(StopRequested, match="slow_io"):
            tick_reads(r, cfg, env, guard, DailyBudget(state, cfg, env.wall), state)
    assert state["budget"]["read_ops"] <= 2


def test_histogram_and_summary():
    assert log2_hist([0, 1, 2, 3, 4, 1000, 10**9])[0] == 2
    s = summarize_latencies(list(range(1, 201)))
    assert s["n"] == 200 and s["p50"] == 100 and s["max"] == 200 and len(s["lat_us"]) <= 32
    assert sum(s["hist"]) == 200
