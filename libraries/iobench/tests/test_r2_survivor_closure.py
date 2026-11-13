"""Behavioural closure tests for the iobench 0.2.0 R2 survivors.

The tests exercise public results and refusal behaviour with FakeHost or
scoped OS adapters. They do not require a device, a real fio run, or a wall
clock deadline.
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import errno
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from helpers import INVERSION, config, make_host
from iobench import cache, capability, ceilings, cli, devio, guard, plan, plausibility
from iobench import host as host_module
from iobench.calibrate import Calibrator, RunFailed
from iobench.devio import FioResult, FioStats
from iobench.errors import Refusal
from iobench.host import (
    TAIL_BYTES,
    HostError,
    RealHost,
    RunResult,
    TargetRef,
    _ProcHandle,
)
from iobench.series import SeriesError
from iobench.testing import GIB, standard_host

MIB = 1 << 20
DIGEST = "sha256:" + "a" * 64
CREATED = "2026-10-21T03:00:00Z"


def _capability_row(**changes):
    row = {
        "schema": capability.SCHEMA,
        "ts": "2026-11-13T00:00:00Z",
        "disk_key": DIGEST,
        "kind": "raw",
        "profile": "standard",
        "source": "benchmark",
        "burst_bytes": 1000.0,
        "knee_s": 10.0,
        "post_cliff_bps": 100.0,
        "pre_cliff_bps": 200.0,
        "cliff_bytes": 1 << 30,
        "cold_region_bps": 90.0,
        "write_burst_seen": None,
        "confidence": "medium",
        "note": None,
    }
    row.update(changes)
    return row


def _observation(rung=None, *, cold=None):
    cal = {
        "cache": {"write": {"time_proof": None}, "read": {"e_cold": cold}},
        "ladder": {"cliff_bytes": None, "rungs": [] if rung is None else [rung]},
    }
    identity = {"disk_scope": {"key": DIGEST}}
    return capability.run_observation(cal, identity, "standard", "raw", "2026-11-13T00:00:00Z")


def _real_file_target(tmp_path: Path, *, size: int = 3 * TAIL_BYTES):
    path = tmp_path / "target.img"
    with path.open("wb") as stream:
        stream.truncate(size)
    st = path.stat()
    target = TargetRef("file", str(path), size, inode=st.st_ino, fs_type="ext4")
    host = RealHost()
    host._verify_live = lambda _target: None
    return host, target


class _OSProxy:
    """A host-module-only OS adapter; unchanged calls delegate to the saved OS module."""

    def __init__(self, real_os, **overrides):
        self._real_os = real_os
        self._overrides = overrides

    def __getattr__(self, name):
        return self._overrides.get(name, getattr(self._real_os, name))


def _scope_file_os(monkeypatch, target: TargetRef, *, open_error=None, fstat_edit=None, lseek=None, constants=None):
    real_os = os
    opened: set[int] = set()

    def open_path(path, flags, *args, **kwargs):
        if path == target.path and open_error is not None:
            raise open_error
        fd = real_os.open(path, flags, *args, **kwargs)
        if path == target.path:
            opened.add(fd)
        return fd

    def fstat_fd(fd):
        result = real_os.fstat(fd)
        return fstat_edit(fd, result) if fd in opened and fstat_edit is not None else result

    def lseek_fd(fd, offset, whence):
        return lseek(fd, offset, whence) if fd in opened and lseek is not None else real_os.lseek(fd, offset, whence)

    overrides = {"open": open_path, "fstat": fstat_fd, "lseek": lseek_fd}
    overrides.update(constants or {})
    monkeypatch.setattr(host_module, "os", _OSProxy(real_os, **overrides))
    return opened


def _fill_run(*, planned: int = MIB, fill_s: int = 10, budget_s: int = 100):
    host = standard_host()
    run = Calibrator(host, config())
    run.target = TargetRef("device", "/dev/vda7", planned + TAIL_BYTES, devno="254:7", partuuid="part")
    run.ws_planned = planned
    run.r_est = float(MIB)
    run.alloc = plan.Allotment("standard", budget_s, (plan.Stage("fill", fill_s, True),), planned, ())
    run.fill_budget_s = lambda _budget: 12.0
    return run, host


def _fio_result(job, io_bytes: int, *, runtime_s: float, series=()):
    stats = FioStats("write", 1.0, 1.0, io_bytes, runtime_s, 1.0, {})
    return FioResult(job, 0, stats, list(series), None, 0.0)


def _completed_calibrator():
    run = Calibrator(make_host(cache=INVERSION), config())
    outcome = run.execute()
    assert outcome.status == "complete", outcome.message
    return run


def test_cache_delayed_read_hint_is_miss_like_at_the_exact_threshold():
    cold = 100.0
    delayed = {"status": "measured", "rate_bps": cache.READ_SPEEDUP_HINT * cold}
    hints = cache.read_cache_hints(delayed, {"status": "not_measured"}, cold)
    assert hints["delayed_readback"] == "miss-like"


def test_cache_repeat_hint_is_miss_like_at_the_exact_threshold():
    repeated = {"status": "measured", "first_bps": 5.0, "second_bps": 6.0}
    hints = cache.read_cache_hints({"status": "not_measured"}, repeated, 100.0)
    assert hints["repeat_pass"] == "miss-like"


def test_cache_state_remains_measured_when_only_one_probe_measured():
    hints = cache.read_cache_hints(
        {"status": "measured", "rate_bps": None},
        {"status": "not_measured"},
        100.0,
    )
    assert hints["status"] == "measured"
    assert hints["hint"] == "undetermined"


def test_cache_evidence_accepts_an_exact_one_mib_region():
    run = Calibrator(make_host(cache=None), config())
    run.fill_info = {"bytes": MIB}
    calls = []

    def measured(name, _rw, _bs, _qd, _runtime, offset, size, **_kwargs):
        calls.append((name, offset, size))
        return SimpleNamespace(stats=SimpleNamespace(bw_bps=100.0))

    run.run_job = measured
    run.cache_evidence(0)
    assert [size for _name, _offset, size in calls] == [MIB, MIB]
    assert run.delayed_readback["region_bytes"] == MIB


def test_zero_first_read_rate_has_no_speedup_ratio():
    run = Calibrator(make_host(cache=None), config())
    run.fill_info = {"bytes": MIB}
    calls = []

    def measured(name, _rw, _bs, _qd, _runtime, _offset, _size, **_kwargs):
        calls.append(name)
        rate = 0.0 if len(calls) == 1 else 100.0
        return SimpleNamespace(stats=SimpleNamespace(bw_bps=rate))

    run.run_job = measured
    run.cache_evidence(0)
    assert run.repeat_pass["speedup_ratio"] is None


def test_zero_idle_delay_does_not_enter_the_idle_wait_loop():
    run = Calibrator(make_host(cache=None), config())
    run.fill_info = {"bytes": MIB}
    calls = []
    sleeps = []

    def measured(name, _rw, _bs, _qd, _runtime, _offset, _size, **_kwargs):
        calls.append(name)
        return SimpleNamespace(stats=SimpleNamespace(bw_bps=100.0))

    def bounded_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 1:
            raise AssertionError("zero idle delay did not terminate")

    run.run_job = measured
    run.host.sleep = bounded_sleep
    run.cache_evidence(0)
    assert sleeps == [] and len(calls) == 2


def test_budget_skipped_cache_evidence_keeps_its_distinct_status():
    run = Calibrator(make_host(cache=None), config())
    allotment = plan.plan_calibration("standard", 435_853_320_192, 1.36e9)
    assert allotment.stage("cache-evidence").required is False
    run.stage = lambda name, _fn, **_kwargs: (run.stages.__setitem__(name, "skipped: budget") or False)
    run.cache_evidence_stage(allotment)
    assert run.delayed_readback["status"] == "skipped:budget"
    assert run.repeat_pass["status"] == "skipped:budget"


def test_cache_evidence_stage_preserves_the_budget_skip_reason(monkeypatch):
    run = Calibrator(make_host(cache=None), config())
    monkeypatch.setattr(plan, "cache_idle_delay", lambda *_args: 1)
    allotment = SimpleNamespace(
        stage=lambda name: plan.Stage(name, 1, False),
        seconds=lambda _name: 1,
    )
    run.stage = lambda name, _fn, **_kwargs: (run.stages.__setitem__(name, "skipped: budget") or False)
    run.cache_evidence_stage(allotment)
    assert run.delayed_readback["status"] == "skipped:budget"
    assert run.repeat_pass["status"] == "skipped:budget"


def test_absent_write_time_proof_serializes_as_null():
    run = Calibrator(make_host(cache=None), config())
    run.proof = {}
    assert run.write_time_proof_doc() is None


def test_zero_progress_fill_does_not_claim_a_fio_short_limit():
    run, _host = _fill_run()

    def no_progress(job):
        return _fio_result(job, 0, runtime_s=0)

    run.fio = no_progress
    with pytest.raises(RunFailed, match="wrote no bytes"):
        run.fill(run.alloc)
    assert run.fill_limit is None


def test_fill_replanning_carries_a_preexisting_budget_limit():
    run, host = _fill_run()
    run.budget_limited = True

    def full_chunk(job):
        host.clock = 1.0
        return _fio_result(job, job.size, runtime_s=job.runtime_s,
                           series=[(float(i), 1.0) for i in range(10)])

    run.fio = full_chunk
    run.fill(run.alloc)
    assert run.budget_limited is True
    assert "limited-by-budget" in run.limits


def test_fill_replan_at_the_full_planned_size_is_not_a_time_truncation():
    run, host = _fill_run()

    def full_chunk(job):
        host.clock = 1.0
        return _fio_result(job, job.size, runtime_s=job.runtime_s,
                           series=[(float(i), 0.1) for i in range(10)])

    run.fio = full_chunk
    run.fill(run.alloc)
    assert "limited-by-budget" not in run.limits


def test_a_full_fill_finishing_at_its_deadline_is_not_truncated():
    run, host = _fill_run()
    deadline = host.now() + run.alloc.seconds("fill")

    def full_chunk(job):
        host.clock = float(deadline)
        return _fio_result(job, job.size, runtime_s=job.runtime_s)

    run.fio = full_chunk
    run.fill(run.alloc)
    assert run.budget_limited is False
    assert "limited-by-budget" not in run.limits


def test_a_partial_fill_at_its_deadline_is_budget_truncated():
    run, host = _fill_run()
    deadline = host.now() + run.alloc.seconds("fill")

    def partial_chunk(job):
        host.clock = float(deadline)
        return _fio_result(job, job.size // 2, runtime_s=job.runtime_s)

    run.fio = partial_chunk
    run.fill(run.alloc)
    assert run.budget_limited is True
    assert "limited-by-budget" in run.limits


def test_veryquick_coarse_model_requires_both_measured_cell_shapes():
    run = Calibrator(make_host(cache=None), config(profile="veryquick"))
    run.cells = [{"dir": "read", "pattern": "rand", "bs": 4096, "state": "steady", "steady_iops": 42.0}]
    model = run.coarse_model()
    assert model["read"] is None and model["write"] is None


def test_missing_cold_raw_data_does_not_get_merged_into_the_cold_document():
    run = _completed_calibrator()
    assert run.a["cold"] is not None
    run.cold_raw = None
    calibration, _result = run.assemble(run.safety.report)
    assert "R_cold_qd1_bps" not in calibration["calibration"]["cache"]["read"]["e_cold"]


def test_unallocated_plan_is_serialized_as_null():
    run = _completed_calibrator()
    run.alloc = None
    calibration, _result = run.assemble(run.safety.report)
    assert calibration["calibration"]["plan"] is None


def test_veryquick_burst_requires_a_steady_write_series():
    run = _completed_calibrator()
    run.a = copy.copy(run.a)
    run.a["writes"] = [dict(row) for row in run.a["writes"]]
    run.a["writes"][-1].update(state="unsteady", knee_s=10.0, burst_units=2.0)
    run.vq = True
    calibration, _result = run.assemble(run.safety.report)
    assert calibration["calibration"]["veryquick"]["write_burst_seen"] is False


def test_veryquick_burst_requires_a_positive_burst_measurement():
    run = _completed_calibrator()
    run.a = copy.copy(run.a)
    run.a["writes"] = [dict(row) for row in run.a["writes"]]
    run.a["writes"][-1].update(state="steady", knee_s=10.0, burst_units=0.0)
    run.vq = True
    calibration, _result = run.assemble(run.safety.report)
    assert calibration["calibration"]["veryquick"]["write_burst_seen"] is False


def test_veryquick_s0_does_not_require_a_work_directory():
    class StopAfterS0(Exception):
        pass

    run = Calibrator(make_host(cache=None), config(profile="veryquick", work_dir=None))

    def stop_at_s1():
        raise StopAfterS0()

    run.s1 = stop_at_s1
    with pytest.raises(StopAfterS0):
        run.body_vq()


def test_veryquick_fill_failure_is_required_and_stops_later_stages():
    run = Calibrator(make_host(cache=None), config(profile="veryquick"))
    run.s0 = lambda **_kwargs: None
    run.s1 = lambda: None
    run.make_plan = lambda: object()
    run.fill = lambda _alloc: (_ for _ in ()).throw(SeriesError("fill series unusable"))
    run.e_cold = lambda: None
    run.cache_evidence_stage = lambda _alloc: None
    run.series_vq = lambda: pytest.fail("series ran after required fill failed")
    with pytest.raises(SeriesError, match="fill series unusable"):
        run.body_vq()


def test_veryquick_series_failure_is_required_and_stops_sixth_stage():
    run = Calibrator(make_host(cache=None), config(profile="veryquick"))
    run.start_budget(1000)
    run.s0 = lambda **_kwargs: None
    run.s1 = lambda: None
    run.make_plan = lambda: SimpleNamespace(seconds=lambda _name: 0)
    run.fill = lambda _alloc: None
    run.e_cold = lambda: None
    run.cache_evidence_stage = lambda _alloc: None
    run.series_vq = lambda: (_ for _ in ()).throw(SeriesError("series unusable"))
    run.s6_vq = lambda: pytest.fail("S6 ran after required series failed")
    with pytest.raises(SeriesError, match="series unusable"):
        run.body_vq()


def test_capability_row_rejects_a_nonprefixed_key_with_valid_length():
    with pytest.raises(Refusal) as error:
        capability.validate(_capability_row(disk_key="x" + "a" * 70), None)
    assert error.value.condition == "capability_identity"


def test_capability_row_rejects_a_prefixed_key_with_invalid_length():
    with pytest.raises(Refusal) as error:
        capability.validate(_capability_row(disk_key="sha256:short"), None)
    assert error.value.condition == "capability_identity"


def test_capability_row_rejects_a_wellformed_key_for_another_disk():
    other = "sha256:" + "b" * 64
    with pytest.raises(Refusal) as error:
        capability.validate(_capability_row(disk_key=other), DIGEST)
    assert error.value.condition == "capability_identity"


def test_capability_history_header_keeps_its_header_marker():
    header = json.loads(capability.header_line(DIGEST))
    assert header["header"] is True


def test_capability_history_rows_are_json_objects_independent_of_key_order():
    host = make_host(cache=None)
    path = "/var/lib/iobench/capability.jsonl"
    row = _capability_row()
    capability.append(host, path, row, DIGEST)
    lines = host.read_text(path).splitlines()
    assert json.loads(lines[1]) == row
    assert capability.load(host, path, DIGEST)[0] == [row]


def test_capability_plateau_is_not_derived_from_a_noninteger_block_size():
    rung = {"dir": "write", "state": "steady", "ws_bytes": 100, "bs": "4k", "steady_iops": 100.0,
            "burst_units": None, "knee_s": None}
    row = _observation(rung)
    assert row["post_cliff_bps"] is None


def test_capability_burst_is_not_derived_from_nonnumeric_burst_units():
    rung = {"dir": "write", "state": "steady", "ws_bytes": 100, "bs": 4096, "steady_iops": None,
            "burst_units": "two", "knee_s": None}
    row = _observation(rung)
    assert row["burst_bytes"] is None and row["pre_cliff_bps"] is None


def test_capability_precliff_rate_is_absent_without_a_plateau():
    rung = {"dir": "write", "state": "steady", "ws_bytes": 100, "bs": 4096, "steady_iops": "bad",
            "burst_units": 2.0, "knee_s": 1.0}
    row = _observation(rung)
    assert row["pre_cliff_bps"] is None


def test_capability_precliff_rate_is_absent_without_a_burst():
    rung = {"dir": "write", "state": "steady", "ws_bytes": 100, "bs": 4096, "steady_iops": 2.0,
            "burst_units": "bad", "knee_s": 1.0}
    row = _observation(rung)
    assert row["pre_cliff_bps"] is None


def test_capability_precliff_rate_is_absent_without_a_knee():
    rung = {"dir": "write", "state": "steady", "ws_bytes": 100, "bs": 4096, "steady_iops": 2.0,
            "burst_units": 2.0, "knee_s": None}
    row = _observation(rung)
    assert row["pre_cliff_bps"] is None


def test_capability_precliff_rate_is_absent_when_the_knee_is_zero():
    rung = {"dir": "write", "state": "steady", "ws_bytes": 100, "bs": 4096, "steady_iops": 2.0,
            "burst_units": 2.0, "knee_s": 0.0}
    row = _observation(rung)
    assert row["pre_cliff_bps"] is None


def test_capability_does_not_publish_a_zero_cold_region_rate():
    row = _observation(cold={"R_cold_iops": 0.0})
    assert row["cold_region_bps"] is None


def test_disk_scope_with_one_stable_identifier_does_not_bind_to_machine_id():
    first = {"model": "M", "serial": "SER-1", "wwn": None, "size_bytes": 100,
             "machine_id_hash": "host-a"}
    second = {**first, "machine_id_hash": "host-b"}
    assert ceilings.scope_key(first) == ceilings.scope_key(second)


def test_file_size_accepts_the_exact_minimum_target_size():
    assert cli._file_size_arg(str(guard.MIN_TARGET_SIZE_BYTES)) == guard.MIN_TARGET_SIZE_BYTES


def test_zero_file_size_reports_the_positive_multiple_requirement():
    with pytest.raises(argparse.ArgumentTypeError, match="positive multiple of 1 MiB"):
        cli._file_size_arg("0")


def test_capability_cli_handler_returns_success_after_emitting_output():
    host = make_host(cache=None)
    emitted = []
    runtime = SimpleNamespace(json_mode=True, output=SimpleNamespace(primary=emitted.append, info=lambda _text: None))
    args = SimpleNamespace(state_dir=None, since=None, kind=None)
    status = cli._capability(args, runtime, lambda: host)
    assert status == 0 and len(emitted) == 1


def test_fio_option_probe_error_includes_the_empty_stderr_fallback(monkeypatch):
    monkeypatch.setattr(devio, "probe", lambda *_args, **_kwargs: RunResult(2, "", ""))
    with pytest.raises(Refusal) as error:
        devio.require_fio_option(SimpleNamespace(), "--allow_mounted_write", (3, 39))
    assert "exit 2: no message" in str(error.value)


def test_four_character_repeated_disk_identifier_is_a_placeholder():
    assert guard._disk_identifier("abab") is None


def test_disk_facts_are_immutable():
    facts = guard.DiskFacts("vda", "254:0", 500 * GIB, "M", None, None, None, None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        facts.model = "changed"


def test_weak_disk_lookup_does_not_select_a_different_name():
    host = make_host(cache=None)
    host.put("/sys/class/block/vda/device/model", "M\n")
    found = guard.find_disk(host, model="M", serial=None, wwn=None, size_bytes=500 * GIB,
                            hint="vdb", machine_id_matches=True)
    assert found is None


def test_weak_disk_lookup_does_not_accept_a_strong_candidate():
    host = make_host(cache=None)
    host.put("/sys/class/block/vda/device/model", "M\n")
    host.put("/sys/block/vda/serial", "SER-1\n")
    found = guard.find_disk(host, model="M", serial=None, wwn=None, size_bytes=500 * GIB,
                            hint="vda", machine_id_matches=True)
    assert found is None


def test_empty_btrfs_member_list_is_reported_as_unknown_disk_scope():
    host = standard_host()
    uuid = "00000000-0000-0000-0000-000000000001"
    host.makedirs(f"/sys/fs/btrfs/{uuid}/devices")
    fs = guard.FsInfo("/bench", "/dev/vda8", "btrfs", "rw", "254:8", uuid, 10 * GIB, 5 * GIB)
    with pytest.raises(Refusal) as error:
        guard.backing_disk_for_fs(host, fs, "/bench/target.img")
    assert error.value.condition == "target_fs_disk_unknown"


def test_btrfs_member_lookup_without_a_filesystem_uuid_is_refused_as_unknown_scope():
    host = standard_host()
    fs = guard.FsInfo("/bench", "/dev/vda8", "btrfs", "rw", "254:8", None, 10 * GIB, 5 * GIB)
    with pytest.raises(Refusal) as error:
        guard.backing_disk_for_fs(host, fs, "/bench/target.img")
    assert error.value.condition == "target_fs_disk_unknown"


def test_mount_touching_by_raw_mount_devno_alone_is_retained():
    from test_tgt import btrfs_file_host

    host, _uuid = btrfs_file_host()
    entry = guard.MountEntry("0:52", "/raw-only", "btrfs", "/dev/vda8", "rw")
    touching = guard._touching(host, [entry], {"0:52"}, set())
    assert {row.mountpoint for row in touching} == {"/raw-only"}


def test_workdir_on_the_target_btrfs_disk_is_not_marked_cross_disk(monkeypatch):
    from test_tgt import btrfs_file_host

    host, _uuid = btrfs_file_host()
    monkeypatch.setattr(guard, "on_root_fs", lambda _host, _fs: False)
    workdir = guard.check_workdir(host, "/bench/w", planned_volume=GIB, installer=False,
                                  confirm_quiet_window=False, target_disk="vda")
    assert workdir.disk_name == "vda" and workdir.cross_disk is False


def test_btrfs_member_absence_and_an_empty_member_list_have_the_same_refusal():
    from test_tgt import btrfs_file_host

    host, uuid = btrfs_file_host()
    devices = f"/sys/fs/btrfs/{uuid}/devices"
    host.dirs.discard(devices)
    host.symlinks.pop(f"{devices}/1", None)
    with pytest.raises(Refusal) as error:
        guard.backing_disk(host, "/bench/target.img")
    assert error.value.condition == "target_fs_disk_unknown"


def test_disk_scope_strength_transitions_are_refused_by_the_stored_identifiers():
    strong = {"disk_scope": {"strength": "strong", "size_bytes": 500 * GIB, "model": "M",
                             "serial": "SER-1", "wwn": None, "machine_id_hash": None}}
    weak = {"disk_scope": {"strength": "weak", "size_bytes": 500 * GIB, "model": "M",
                           "serial": None, "wwn": None, "machine_id_hash": "host-hash"}}
    from iobench.result import identity_differs

    assert identity_differs(weak, strong) is True
    assert identity_differs(strong, weak) is True


def test_strong_disk_lookup_default_is_not_used_by_its_production_caller(monkeypatch):
    # The production call passes the host binding explicitly; changing the helper
    # default cannot change this live identity lookup.
    from iobench import result

    host = standard_host()
    calls = []

    def find_disk(_host, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(result.guard, "find_disk", find_disk)
    monkeypatch.setattr(result, "machine_id_hash", lambda _host: "live-hash")
    result.live_identity(host, {
        "target": {"path": "/dev/removed"},
        "workdir_fs": None,
        "disk_scope": {"model": "M", "serial": None, "wwn": None, "size_bytes": 500 * GIB,
                       "machine_id_hash": None, "name": "vda"},
    })
    assert calls and calls[0]["machine_id_matches"] is True


def test_fio_allowlist_accepts_an_argv_with_every_allowed_key():
    values = {
        "--name": "job", "--filename": "/bench/target.img", "--rw": "read", "--bs": "4096",
        "--iodepth": "1", "--ioengine": "libaio", "--direct": "1", "--time_based": "1",
        "--runtime": "1", "--offset": "0", "--size": "4096", "--numjobs": "1",
        "--group_reporting": "1", "--output-format": "json", "--percentile_list": "99.0",
        "--log_avg_msec": "1000", "--write_bw_log": "/tmp/bw", "--write_iops_log": "/tmp/iops",
        "--allow_mounted_write": "0", "--readonly": "1", "--io_size": "4096",
        "--write_hist_log": "/tmp/hist", "--log_hist_msec": "1000", "--log_hist_coarseness": "6",
        "--rate_iops": "1", "--fsync": "0",
    }
    assert set(values) == host_module._FIO_KEYS
    argv = ["fio", *(f"{key}={value}" for key, value in values.items())]
    assert host_module._fio_allowed(argv) is True


def test_proc_handle_runs_the_supervisor_in_a_new_session_and_returns_text():
    handle = _ProcHandle([
        sys.executable,
        "-c",
        "import os; print(int(os.getsid(os.getppid()) == os.getppid()))",
    ])
    try:
        assert handle._p.stdout is None
        result = handle.wait()
        assert result.returncode == 0
        assert result.stdout == "1\n"
        assert isinstance(result.stdout, str)
    finally:
        handle._out.close()
        handle._err.close()


def test_marker_open_error_uses_the_os_error_when_strerror_is_empty(monkeypatch, tmp_path):
    host, target = _real_file_target(tmp_path)
    host._verify_live = lambda _target: None
    _scope_file_os(monkeypatch, target, open_error=OSError(errno.EBUSY, ""))
    with pytest.raises(HostError) as error:
        host.write_marker(target, "marker")
    assert "[Errno 16]" in str(error.value)


def test_real_extent_query_refuses_a_negative_start_before_seeking(monkeypatch, tmp_path):
    host, target = _real_file_target(tmp_path)
    _scope_file_os(monkeypatch, target)
    with pytest.raises(HostError, match="outside the usable extent"):
        host.written_bytes(target, -1, 0)


def test_real_extent_query_allows_a_zero_start_and_empty_range(monkeypatch, tmp_path):
    host, target = _real_file_target(tmp_path)
    _scope_file_os(monkeypatch, target)
    assert host.written_bytes(target, 0, 0) == 0


def test_real_extent_query_rejects_a_nonregular_file_identity(monkeypatch, tmp_path):
    host, target = _real_file_target(tmp_path)

    def nonregular(_fd, result):
        return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_ino=result.st_ino, st_size=result.st_size)

    _scope_file_os(monkeypatch, target, fstat_edit=nonregular)
    with pytest.raises(HostError, match="file target identity changed"):
        host.written_bytes(target, 0, MIB)


def test_real_extent_query_rejects_an_inode_change_before_seeking(monkeypatch, tmp_path):
    host, target = _real_file_target(tmp_path)
    target = dataclasses.replace(target, inode=target.inode + 1)
    _scope_file_os(monkeypatch, target)
    with pytest.raises(HostError, match="file target identity changed"):
        host.written_bytes(target, 0, MIB)


def test_real_extent_query_refuses_when_only_one_seek_constant_is_missing(monkeypatch, tmp_path):
    host, target = _real_file_target(tmp_path)
    _scope_file_os(monkeypatch, target, constants={"SEEK_HOLE": None})
    with pytest.raises(HostError, match="does not provide SEEK_DATA/SEEK_HOLE"):
        host.written_bytes(target, 0, MIB)


def test_real_extent_query_stops_before_seeking_data_at_the_end(monkeypatch, tmp_path):
    host, target = _real_file_target(tmp_path)
    real_lseek = os.lseek

    def boundary_lseek(fd, offset, whence):
        if whence == os.SEEK_DATA and offset == 0:
            return 0
        if whence == os.SEEK_HOLE and offset == 0:
            return MIB
        if whence == os.SEEK_DATA and offset == MIB:
            raise OSError(errno.EIO, "query at the exclusive end")
        return real_lseek(fd, offset, whence)

    _scope_file_os(monkeypatch, target, lseek=boundary_lseek)
    assert host.written_bytes(target, 0, MIB) == MIB


def test_real_extent_query_stops_before_seeking_a_hole_at_the_end(monkeypatch, tmp_path):
    host, target = _real_file_target(tmp_path)
    real_lseek = os.lseek

    def boundary_lseek(fd, offset, whence):
        if whence == os.SEEK_DATA and offset == 0:
            return MIB
        if whence == os.SEEK_HOLE and offset == MIB:
            raise OSError(errno.EIO, "query at the exclusive end")
        return real_lseek(fd, offset, whence)

    _scope_file_os(monkeypatch, target, lseek=boundary_lseek)
    assert host.written_bytes(target, 0, MIB) == 0


def test_real_extent_query_refuses_a_nonadvancing_seek_hole_without_spinning(monkeypatch, tmp_path):
    host, target = _real_file_target(tmp_path)
    real_lseek = os.lseek
    data_seeks = 0

    def nonadvancing_lseek(fd, offset, whence):
        nonlocal data_seeks
        if whence == os.SEEK_DATA:
            data_seeks += 1
            if data_seeks == 1:
                return offset
            raise OSError(errno.ENXIO, "no further data")
        if whence == os.SEEK_HOLE:
            return offset
        return real_lseek(fd, offset, whence)

    _scope_file_os(monkeypatch, target, lseek=nonadvancing_lseek)
    with pytest.raises(HostError, match="SEEK_HOLE returned"):
        host.written_bytes(target, 0, MIB)


def test_marker_device_in_use_message_uses_the_os_error_when_strerror_is_empty(monkeypatch, tmp_path):
    _host, target = _real_file_target(tmp_path)
    target = dataclasses.replace(target, kind="device", devno="254:7")
    _scope_file_os(monkeypatch, target, open_error=OSError(errno.EBUSY, ""))
    with pytest.raises(Refusal) as error:
        RealHost._probe_device_exclusive_open(target)
    assert error.value.condition == "device_in_use"
    assert "[Errno 16]" in str(error.value)


def test_marker_probe_error_uses_the_os_error_when_strerror_is_empty(monkeypatch, tmp_path):
    _host, target = _real_file_target(tmp_path)
    target = dataclasses.replace(target, kind="device", devno="254:7")
    _scope_file_os(monkeypatch, target, open_error=OSError(errno.EACCES, ""))
    with pytest.raises(HostError) as error:
        RealHost._probe_device_exclusive_open(target)
    assert "[Errno 13]" in str(error.value)


def test_fakehost_extent_query_refuses_a_negative_start_with_a_range_error():
    from helpers import target_ref
    from test_tgt import file_host

    host = file_host()
    target = target_ref(host, "/bench/target.img")
    with pytest.raises(HostError, match="outside the usable extent"):
        host.written_bytes(target, -1, 0)


def test_fakehost_extent_query_allows_zero_start_and_empty_range():
    from helpers import target_ref
    from test_tgt import file_host

    host = file_host()
    target = target_ref(host, "/bench/target.img")
    assert host.written_bytes(target, 0, 0) == 0


def test_zero_read_rate_is_not_used_as_a_plausibility_ratio():
    check = plausibility.pla_19(0.0, 1.0)
    assert check.status == "not_applicable"


