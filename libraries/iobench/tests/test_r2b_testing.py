"""IOB-R2-01 (group B): behaviour oracles for the FakeHost estate (``iobench.testing``).

Every test states one behaviour of the fake that the rest of the suite relied on without pinning it
(boundaries of the cache LRU, the token bucket, the handle lifecycle, stat flags, fallocate space, the
interrupted-writer budget).
"""
import dataclasses

import pytest

from iobench import devio, testing
from iobench.host import TAIL_BYTES, HostError, RunResult
from iobench.testing import (GIB, Bucket, CacheModel, DirScenario, FakeDevice, FakeHandle, FakeHost, Scenario,
                             SweepOutcome, TailViolation, mixture, mode_profile, standard_host)

from conftest import TimeLimitExceeded
from helpers import job, run_job


# -- CacheModel ---------------------------------------------------------------------------------------------------
def test_cache_eviction_tolerates_an_overshoot_of_exactly_the_epsilon():
    cache = CacheModel(0, 1.0)
    cache.order[0] = 1e-9  # exactly the tolerated overshoot over a zero capacity
    cache._evict()
    assert cache.order == {0: 1e-9}


def test_cache_eviction_drops_the_oldest_bucket_first_and_whole_buckets_vanish():
    cache = CacheModel(1.0, 1.0)
    cache.order[0] = 1.0
    cache.order[1] = 1.0
    cache._evict()
    assert list(cache.order) == [1] and cache.order[1] == 1.0


# -- Bucket -------------------------------------------------------------------------------------------------------
def test_bucket_demand_at_the_sustained_rate_is_served_unchanged_and_never_drains():
    bucket = Bucket(burst_bps=3.0, sustained_bps=1.0, burst_s=1.0)
    assert bucket.step(1.0, 1.0) == 1.0
    assert bucket.credit == bucket.capacity


def test_bucket_that_empties_exactly_at_the_end_of_the_step_delivers_the_exact_burst_rate():
    bucket = Bucket(burst_bps=3.0, sustained_bps=1.0, burst_s=0.1)  # credit 0.2, drain 2/s -> empty at exactly dt = 0.1
    assert bucket.step(3.0, 0.1) == 3.0
    assert bucket.credit == pytest.approx(0.0, abs=1e-15)


# -- latency distributions ----------------------------------------------------------------------------------------
def test_a_mode_in_the_lowest_bins_uses_the_open_left_edge_not_the_log_of_a_zero_bin():
    profile = mode_profile(0.001, 0.12)  # bin 1 is the lowest usable bin: bin 0 has a value of exactly 0
    assert [i for i, _ in profile] == [1]
    assert sum(w for _, w in profile) == pytest.approx(1.0)


def test_a_zero_weight_component_leaves_no_bins_in_the_mixture():
    only = mixture([(1.0, 100.0)], 0.12)
    assert mixture([(0.0, 300.0), (1.0, 100.0)], 0.12) == only


# -- FakeDevice ---------------------------------------------------------------------------------------------------
def test_a_marker_may_fill_the_reserved_tail_to_its_last_byte_but_not_one_beyond():
    dev = FakeDevice("/dev/x", 10 * GIB)
    dev.check_range(dev.tail_start, TAIL_BYTES, "marker")  # exactly the tail
    with pytest.raises(TailViolation):
        dev.check_range(dev.tail_start, TAIL_BYTES + 1, "marker")
    with pytest.raises(TailViolation):
        dev.check_range(dev.tail_start - 1, 1, "marker")


def test_a_data_range_ends_exactly_at_the_tail_start():
    dev = FakeDevice("/dev/x", 10 * GIB)
    dev.check_range(0, dev.tail_start, "fio")
    with pytest.raises(TailViolation):
        dev.check_range(0, dev.tail_start + 1, "fio")


# -- SweepOutcome / FakeHandle ----------------------------------------------------------------------------------
def test_a_sweep_outcome_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        SweepOutcome().load_iops = 1.0


def test_a_handle_reports_completion_when_the_clock_reaches_its_end_exactly():
    host = FakeHost()
    handle = FakeHandle(host, host.now() + 5.0, RunResult(0, "out", ""))
    assert handle.poll() is None
    host.clock += 5.0
    assert handle.poll() == 0


def test_a_handle_runs_its_finish_callback_once_however_often_it_is_polled():
    host = FakeHost()
    handle = FakeHandle(host, host.now() + 5.0, RunResult(0, "", ""))
    seen = []
    handle.on_finish = seen.append
    host.clock += 5.0
    handle.poll()
    handle.poll()
    handle.wait()
    assert seen == [1.0]


def test_killing_a_finished_handle_keeps_its_result():
    host = FakeHost()
    handle = FakeHandle(host, host.now() + 5.0, RunResult(0, "done", ""))
    host.clock += 5.0
    handle.kill()
    assert handle.poll() == 0 and handle.wait().stdout == "done"


def test_killing_a_running_handle_cuts_it_and_a_second_kill_changes_nothing():
    host = FakeHost()
    handle = FakeHandle(host, host.now() + 10.0, RunResult(0, "", ""))
    seen = []
    handle.on_finish = seen.append
    host.clock += 5.0
    handle.kill()
    handle.kill()
    assert handle.poll() == -9 and seen == [0.5]


def test_a_new_host_has_not_installed_signal_handlers():
    host = FakeHost()
    assert host.signals_installed is False
    host.install_signals()
    assert host.signals_installed is True


# -- stat / filesystem --------------------------------------------------------------------------------------------
def test_stat_flags_of_every_kind_of_path():
    host = standard_host()
    host.symlinks["/link"] = "/bench"
    link = host.stat("/link")
    assert (link.is_symlink, link.is_dir, link.is_file) == (True, False, False)
    node = host.stat("/dev/vda7")
    assert (node.is_symlink, node.is_dir, node.is_file, node.inode) == (False, False, False, 0)
    host.put("/bench/f", "abc")
    regular = host.stat("/bench/f")
    assert (regular.is_symlink, regular.is_dir, regular.is_file, regular.size) == (False, False, True, 3)
    folder = host.stat("/bench")
    assert (folder.is_symlink, folder.is_dir, folder.is_file) == (False, True, False)
    assert host.stat("/nowhere") is None


def test_stat_of_a_file_target_is_a_regular_file_with_its_inode():
    host = standard_host()
    assert host.preallocate("/bench/t.img", GIB).returncode == 0
    info = host.stat("/bench/t.img")
    assert (info.is_file, info.is_dir, info.is_symlink) == (True, False, False)
    assert info.inode == host.devices["/bench/t.img"].inode


def test_the_filesystem_of_a_path_is_the_longest_matching_mount():
    host = FakeHost()
    host.set_fs("/", 10, 10)
    host.set_fs("/a", 10, 10)
    host.set_fs("/a/b", 10, 10)
    assert host._mount_for("/a/b/c") == "/a/b"
    assert host._mount_for("/a/bc") == "/a"
    assert host._mount_for("/other") == "/"


def test_preallocating_exactly_the_free_space_succeeds_and_one_byte_more_fails():
    host = standard_host()
    host.set_fs("/bench", 30 * GIB, GIB)
    assert host.preallocate("/bench/full.img", GIB).returncode == 0
    assert host.fs_space["/bench"][1] == 0
    assert host.preallocate("/bench/more.img", 1).returncode == 1


def test_dirty_pages_peak_while_a_load_runs_and_drain_afterwards():
    host = standard_host()
    sc = host.scenario
    host.loads.append((host.now(), host.now() + 10.0, 1.0))
    host.clock += 10.0
    assert f"Dirty: {int(sc.dirty_peak_bytes / 1024)} kB" in host.read_text("/proc/meminfo")
    host.clock += 5.0
    drained = int(max(0.0, sc.dirty_peak_bytes - sc.drain_bps * 5.0) / 1024)
    assert f"Dirty: {drained} kB" in host.read_text("/proc/meminfo")


def test_an_unrecognised_fio_run_is_refused_not_answered_as_a_cmdhelp_query():
    host = standard_host()
    read = job("randread", size=GIB, rt=5)
    with pytest.raises(HostError, match="unsupported command"):
        host.run(read.argv())


# -- the interrupted-writer budget --------------------------------------------------------------------------------------
def _cut_host(budget):
    host = standard_host(Scenario(write=DirScenario(iops_max=71584, lat_us=440, bps_cap_seq=1.36e9)))
    host.scenario.cut_after_bytes = {"cutme": budget}
    return host


def test_a_cut_budget_larger_than_the_jobs_own_byte_cap_leaves_that_cap_in_force():
    host = _cut_host(10 * GIB)
    n = GIB
    j = job("write", size=2 * GIB, bs=1 << 20, qd=16, rt=30, io=n, metric="bw", name="cutme")
    assert run_job(host, j).stats.io_bytes == n


def test_a_cut_budget_limits_a_job_that_has_no_byte_cap_to_the_budget():
    host = _cut_host(GIB // 2)
    j = job("write", size=2 * GIB, bs=1 << 20, qd=16, rt=30, io=None, metric="bw", name="cutme")
    assert run_job(host, j).stats.io_bytes == pytest.approx(GIB // 2, rel=0.01)


def test_a_read_job_without_a_byte_cap_is_not_cut_by_an_unrelated_budget():
    host = _cut_host(1)
    j = job("randread", size=GIB, rt=4, name="other")
    assert run_job(host, j).stats.io_bytes > 1


# -- the per-test time limit (the non-terminating mutants of IOB-R2-01) ----------------------------------------------------
@pytest.mark.time_limit(1)
def test_a_non_terminating_loop_fails_the_test_instead_of_hanging_it():
    with pytest.raises(TimeLimitExceeded):
        while True:
            pass


@pytest.mark.time_limit(1)
def test_a_swallowed_time_limit_is_raised_again_until_the_loop_ends():
    caught = 0
    while caught < 2:
        try:
            while True:
                pass
        except TimeLimitExceeded:
            caught += 1
    assert caught == 2
