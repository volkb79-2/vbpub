"""IOB-SAF: restore journal, io.max, abort, single instance."""
import json

import pytest

from iobench import restore
from iobench.errors import Aborted, Refusal, VerificationFailure
from iobench.host import HostError
from iobench.restore import Safety
from iobench.testing import FakeHost, standard_host

DIRTY = "/proc/sys/vm/dirty_bytes"
NR = "/sys/class/block/vda/queue/nr_requests"
MS = "/sys/class/block/vda/queue/max_sectors_kb"


def safety(host, **kw):
    return Safety(host, "run-1", "2026-10-21T03:00:00Z", **kw)


def test_saf_01_journal_before_first_write():
    host = standard_host()
    s = safety(host)
    s.register_file("/bench/w/f0")
    s.set_knob(DIRTY, "16777216")
    # the journal already held the entry when the knob was written
    assert host.write_log == [(DIRTY, "16777216")]
    doc = json.loads(host.read_text(restore.JOURNAL))
    assert doc["run"] == "run-1" and doc["files"] == ["/bench/w/f0"]
    assert doc["entries"] == [{"path": DIRTY, "original": "0", "applied": "16777216"}]
    # ordering: the journal write precedes the first sysfs write (the entry is journalled with the ORIGINAL value)
    journal_first = []
    host2 = standard_host()
    orig = host2.write_sysfs
    host2.write_sysfs = lambda p, v: (journal_first.append(host2.exists(restore.JOURNAL) and json.loads(host2.read_text(restore.JOURNAL))["entries"][-1]["applied"]), orig(p, v))
    s2 = safety(host2)
    s2.set_knob(NR, "16")
    assert journal_first == ["16"]
    with pytest.raises(HostError):
        s2.set_knob("/sys/fs/cgroup/wings.slice/io.max", "x")
    with pytest.raises(HostError):
        s2.set_knob("/proc/sys/vm/swappiness", "1")


def test_saf_02_restore_on_exit():
    host = standard_host()
    s = safety(host)
    s.set_knob(DIRTY, "16777216")
    s.set_knob(NR, "16")
    s.register_file("/bench/w/f0")
    host.put("/bench/w/f0", "x")
    report = s.restore()
    assert host.read_text(DIRTY) == "0\n" and host.read_text(NR) == "64\n"
    assert report.verified and report.journal_removed and report.skipped == []
    assert not host.exists(restore.JOURNAL) and not host.exists("/bench/w/f0")


def test_saf_02_restore_on_exception_and_signal():
    host = standard_host()
    s = safety(host)
    s.set_knob(MS, "64")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        s.restore()
    assert host.read_text(MS) == "1280\n"
    # SIGTERM: the host reports a pending signal; the supervisor turns it into an abort and restore runs
    host.deliver_signal("SIGTERM")
    assert host.signal_pending() == "SIGTERM"


def test_saf_02_restore_on_signal():
    host = standard_host()
    s = safety(host)
    s.begin()
    assert host.signals_installed
    s.set_knob(NR, "8")
    host.deliver_signal("SIGHUP")
    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        host.deliver_signal(name)
        assert host.signal_pending() == name
    s.restore()
    assert host.read_text(NR) == "64\n"


def test_saf_02_restore_on_next_start():
    host = standard_host()
    dead = safety(host)
    dead.set_knob(DIRTY, "16777216")
    dead.register_file("/bench/w/leak")
    host.put("/bench/w/leak", "x")
    # the process died; a fresh invocation restores the leftover journal first
    fresh = safety(host)
    report = fresh.recover_previous()
    assert host.read_text(DIRTY) == "0\n" and report.verified
    assert not host.exists(restore.JOURNAL) and not host.exists("/bench/w/leak")
    assert safety(host).recover_previous() is None  # idempotent


def test_saf_02_does_not_clobber_newer_value():
    host = standard_host()
    s = safety(host)
    s.set_knob(NR, "16")
    host.put("/sys/devices/pci0/block/vda/queue/nr_requests", "32\n")  # someone else changed it meanwhile
    report = s.restore()
    assert host.read_text(NR) == "32\n"
    assert report.skipped == [{"path": NR, "reason": "changed_by_other"}] and report.warnings
    assert report.verified


def test_saf_02_removes_leaked_files():
    host = standard_host()
    s = safety(host)
    for name in ("a", "b"):
        s.register_file(f"/bench/w/{name}")
        host.put(f"/bench/w/{name}", "x")
    s.restore()
    assert not host.exists("/bench/w/a") and not host.exists("/bench/w/b")


def test_saf_03_readback_mismatch_exit_9():
    host = standard_host()
    s = safety(host)
    s.set_knob(NR, "16")
    host.sysfs_swallow.add(NR)  # the kernel silently refuses the restore write
    with pytest.raises(VerificationFailure) as err:
        s.restore()
    assert err.value.exit_code == 9 and NR in str(err.value) and "'64'" in str(err.value)
    assert host.exists(restore.JOURNAL) and not s.report.verified and not s.report.journal_removed


def test_saf_04_io_max_never_written():
    host = standard_host()
    s = safety(host)
    with pytest.raises(HostError):
        host.write_sysfs("/sys/fs/cgroup/system.slice/io.max", "8:0 rbps=max")
    with pytest.raises(HostError):
        s.set_knob("/sys/fs/cgroup/system.slice/io.max", "8:0 rbps=max")
    for bad in (["systemctl", "set-property", "wings.slice", "IOReadBandwidthMax=/dev/vda 1M"], ["systemctl", "daemon-reload"],
                ["systemd-run", "-p", "IOWeight=10", "true"]):
        with pytest.raises(HostError):
            host.run(bad)
    assert not any("io.max" in p for p, _ in host.write_log)


def test_saf_04_io_max_changed_by_others_detected():
    host = standard_host()
    host.put("/sys/fs/cgroup/system.slice/io.max", "254:0 rbps=max wbps=max\n")
    host.put("/sys/fs/cgroup/wings.slice/io.max", "254:0 rbps=1000000\n")
    host.put("/sys/fs/cgroup/io.max", "")
    s = safety(host)
    s.begin()
    assert set(s.report.io_max_before) == {"/sys/fs/cgroup/system.slice", "/sys/fs/cgroup/wings.slice", "/sys/fs/cgroup"}
    s.verify_io_max()
    assert s.report.slice_io_max_unchanged
    host.put("/sys/fs/cgroup/wings.slice/io.max", "254:0 rbps=2000000\n")  # a neighbour edits a slice meanwhile
    with pytest.raises(VerificationFailure) as err:
        s.verify_io_max()
    assert err.value.exit_code == 9 and "wings.slice" in str(err.value) and not s.report.slice_io_max_unchanged
    assert s.report.as_dict()["slice_io_max_unchanged"] is False


def test_saf_05_refuses_with_containers_and_units():
    host = standard_host()
    kw = dict(confirm_quiet_window=False, installer=False, production_pattern=None, production_units=[], protect_cgroup="wings.slice", abort_psi=40.0)
    assert restore.live_preflight(host, **kw)["containers_active"] == 0
    host.containers = ["mm-1", "wings-a", "game"]
    with pytest.raises(Refusal) as err:
        restore.live_preflight(host, **kw)
    assert err.value.condition == "production_running" and "(3)" in str(err.value) and "not detected" in str(err.value)
    assert restore.live_preflight(host, **{**kw, "confirm_quiet_window": True})["confirmed_quiet_window"]
    with pytest.raises(Refusal):  # the pattern matches wings-a
        restore.live_preflight(host, **{**kw, "production_pattern": "^wings"})
    host2 = standard_host()
    host2.containers = ["wings-a", "mm"]
    with pytest.raises(Refusal):
        restore.live_preflight(host2, **{**kw, "production_pattern": "^wings"})
    host3 = standard_host()
    host3.containers = ["mm"]
    assert restore.live_preflight(host3, **{**kw, "production_pattern": "^wings"})["containers_active"] == 0
    # listed units
    host4 = standard_host()
    host4.active_units.add("wings.service")
    with pytest.raises(Refusal, match="listed units are active"):
        restore.live_preflight(host4, **{**kw, "production_units": ["wings.service", "other.service"]})
    assert restore.live_preflight(host4, **{**kw, "production_units": ["other.service"]})["containers_active"] == 0
    # --installer bypasses this check (and SAF-06) only
    host.containers = ["x"]
    assert restore.live_preflight(host, **{**kw, "installer": True, "protect_cgroup": None, "abort_psi": None})["bypassed"]


def test_saf_06_abort_paths_and_required_flags():
    host = standard_host()
    base = dict(confirm_quiet_window=True, installer=False, production_pattern=None, production_units=[])
    for missing in ({"protect_cgroup": None, "abort_psi": 40.0}, {"protect_cgroup": "wings.slice", "abort_psi": None}, {"protect_cgroup": None, "abort_psi": None}):
        with pytest.raises(Refusal) as err:
            restore.live_preflight(host, **base, **missing)
        assert err.value.condition == "abort_guard_required" and err.value.exit_code == 2
    # pressure: above the threshold for 3 consecutive samples aborts; a dip resets the count
    values = iter([50.0, 60.0, 10.0, 50.0, 50.0, 50.0])
    host.dynamic["/sys/fs/cgroup/wings.slice/io.pressure"] = lambda: f"some avg10={next(values)} avg60=1.00 avg300=1.00 total=1\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
    watch = restore.AbortWatch(host, "wings.slice", 40.0)
    for _ in range(5):
        watch.check()
    with pytest.raises(Aborted) as err:
        watch.check()
    assert err.value.exit_code == 8 and "consecutive" in str(err.value)
    # the abort file
    quiet = standard_host()
    w = restore.AbortWatch(quiet, None, None)
    w.check()
    quiet.put(restore.ABORT_FILE, "")
    with pytest.raises(Aborted, match="abort file"):
        w.check()
    # inflight must drain to zero within 30 s
    quiet.put("/sys/class/block/vda/inflight", "0 0\n")
    assert restore.wait_inflight_zero(quiet, "vda")
    quiet.put("/sys/class/block/vda/inflight", "1 2\n")
    quiet.at(quiet.now() + 3, lambda: quiet.put("/sys/devices/pci0/block/vda/inflight", "0 0\n"))
    assert restore.wait_inflight_zero(quiet, "vda") and restore.device_inflight(quiet, "vda") == 0
    quiet.put("/sys/devices/pci0/block/vda/inflight", "5 0\n")
    assert not restore.wait_inflight_zero(quiet, "vda", timeout_s=3)


def test_saf_07_failed_run_keeps_good_result():
    from iobench import result
    host = standard_host()
    host.makedirs("/var/lib/iobench")
    result.write_json(host, "/var/lib/iobench/result.json", {"schema": 1, "good": True})
    result.write_failed(host, "/var/lib/iobench/result.json", {"schema": 1, "run": {"status": "failed"}})
    assert json.loads(host.read_text("/var/lib/iobench/result.json"))["good"] is True
    assert json.loads(host.read_text("/var/lib/iobench/result.last-failed.json"))["run"]["status"] == "failed"


def test_saf_08_single_instance():
    first = standard_host()
    second = FakeHost(lock_registry=first.locks)
    a, b = safety(first), safety(second)
    a.acquire()
    writes_before = dict(second.files)
    with pytest.raises(Refusal) as err:
        b.acquire()
    assert err.value.condition == "already_running" and err.value.exit_code == 2 and second.files == writes_before
    b.release()
    a.release()
    b.acquire()
    b.release()
    b.release()


def test_saf_04_io_max_walk_is_depth_bounded():
    host = standard_host()
    host.put("/sys/fs/cgroup/a/b/c/io.max", "x\n")
    host.put("/sys/fs/cgroup/a/b/c/d/io.max", "deep\n")
    seen = restore.snapshot_io_max(host)
    assert "/sys/fs/cgroup/a/b/c" in seen and "/sys/fs/cgroup/a/b/c/d" not in seen


# ---------------------------------------------------------------------------------------------------------
# integration: the safety rules inside a real run on FakeHost
# ---------------------------------------------------------------------------------------------------------
from helpers import INVERSION, calibrate, config, fio_jobs, make_host, matrix, read_json  # noqa: E402
from iobench import testing  # noqa: E402

STATE = "/var/lib/iobench"


def calibrated_host():
    host = make_host(cache=INVERSION)
    assert calibrate(host).status == "complete"
    host.sweep_model = lambda k: testing.SweepOutcome(probe_p99_us=2e5, load_iops=800.0, load_fsync_p99_us=9e4, drain_bps=10.5e6)
    return host


def test_saf_01_journal_written_before_the_first_knob_in_a_run():
    host = calibrated_host()
    order = []
    real_write, real_put = host.write_sysfs, host.write_atomic

    def sysfs(path, value):
        order.append(("knob", path, host.exists(restore.JOURNAL)))
        return real_write(path, value)

    host.write_sysfs = sysfs
    out = matrix(host)
    assert out.status == "complete" and order and all(journalled for _, _, journalled in order)
    doc_files = [p for p in host.files if "iobench-" in p and p.startswith("/bench/w/")]
    assert doc_files == []  # created work-dir files were removed again


def test_saf_02_restore_on_exception_in_a_run():
    host = calibrated_host()
    host.crash_after = host.fio_spawns + 6
    with pytest.raises(Exception, match="scenario crash"):
        matrix(host)
    assert host.read_text("/proc/sys/vm/dirty_bytes") == "0\n" and host.read_text("/sys/class/block/vda/queue/nr_requests") == "64\n"
    assert host.read_text("/sys/class/block/vda/queue/max_sectors_kb") == "1280\n"
    assert not host.exists(restore.JOURNAL) and not [p for p in host.files if p.startswith("/bench/w/")]
    assert not host.locks  # the lock is released


def test_saf_02_restore_on_signal_in_a_run():
    host = calibrated_host()
    host.at(host.now() + 300, lambda: host.deliver_signal("SIGTERM"))
    out = matrix(host)
    assert out.status == "aborted" and out.exit_code == 8 and "SIGTERM" in out.message
    assert host.read_text("/proc/sys/vm/dirty_bytes") == "0\n" and host.read_text("/sys/class/block/vda/queue/max_sectors_kb") == "1280\n"
    assert not host.exists(restore.JOURNAL)
    failed = read_json(host, f"{STATE}/result.last-failed.json")
    assert failed["run"]["status"] == "aborted" and failed["restore"]["verified"] is True


def test_saf_02_restore_at_the_start_of_the_next_run():
    host = calibrated_host()
    dead = restore.Safety(host, "run-dead", "2026-10-21T03:00:00Z")
    dead.set_knob("/sys/class/block/vda/queue/nr_requests", "4")
    dead.register_file("/bench/w/leaked")
    host.put("/bench/w/leaked", "x")
    assert host.read_text("/sys/class/block/vda/queue/nr_requests") == "4\n"
    out = matrix(host)
    assert out.status == "complete"
    assert host.read_text("/sys/class/block/vda/queue/nr_requests") == "64\n" and not host.exists("/bench/w/leaked")


def test_saf_03_readback_mismatch_exit_9_in_a_run():
    host = calibrated_host()
    host.at(host.now() + 200, lambda: host.sysfs_swallow.add("/sys/class/block/vda/queue/max_sectors_kb"))
    out = matrix(host)
    assert out.exit_code == 9 and out.status == "failed" and "max_sectors_kb" in out.message
    assert host.exists(restore.JOURNAL)  # kept for the next start
    assert out.result["restore"]["verified"] is False


def test_saf_04_io_max_changed_by_others_exit_9_in_a_run():
    host = calibrated_host()
    host.put("/sys/fs/cgroup/wings.slice/io.max", "254:0 rbps=max\n")
    host.at(host.now() + 100, lambda: host.put("/sys/fs/cgroup/wings.slice/io.max", "254:0 rbps=1000\n"))
    out = matrix(host)
    assert out.exit_code == 9 and "io.max" in out.message and out.status == "failed"
    ok = calibrated_host()
    ok.put("/sys/fs/cgroup/wings.slice/io.max", "254:0 rbps=max\n")
    res_ok = matrix(ok)
    assert res_ok.result["restore"]["slice_io_max_unchanged"] is True
    assert res_ok.result["evidence"]["preflight"]["slice_io_max_before"] == res_ok.result["evidence"]["preflight"]["slice_io_max_after"]


def test_saf_05_live_host_refuses_in_a_run():
    host = make_host(cache=INVERSION)
    host.containers = ["game-1", "mm"]
    with pytest.raises(Refusal) as err:
        calibrate(host, confirm_quiet_window=False)
    assert err.value.condition == "production_running" and not host.write_log
    assert host.devices["/dev/vda7"].write_bytes == 0
    assert calibrate(host, installer=True, confirm_quiet_window=False, protect_cgroup=None, abort_psi=None).status == "complete"


def test_saf_06_abort_in_a_run_kills_fio_and_restores():
    host = calibrated_host()
    pressure = {"v": 0.0}
    host.dynamic["/sys/fs/cgroup/wings.slice/io.pressure"] = lambda: f"some avg10={pressure['v']} avg60=0.0 avg300=0.0 total=1\n"
    host.at(host.now() + 200, lambda: pressure.update(v=95.0))
    t0 = host.now()
    out = matrix(host)
    assert out.status == "aborted" and out.exit_code == 8 and "io.pressure" in out.message
    assert host.now() - t0 < 215  # 3 consecutive samples after the pressure rose, not the end of the run
    assert host.read_text("/sys/class/block/vda/queue/nr_requests") == "64\n" and not host.exists(restore.JOURNAL)
    # the abort file
    h2 = calibrated_host()
    h2.at(h2.now() + 100, lambda: h2.put(restore.ABORT_FILE, ""))
    assert matrix(h2).exit_code == 8
    # calibrate aborted: knobs are not involved, the good calibration stays
    h3 = calibrated_host()
    good = h3.read_text(f"{STATE}/calibration.json")
    h3.at(h3.now() + 100, lambda: h3.put(restore.ABORT_FILE, ""))
    c = calibrate(h3)
    assert c.exit_code == 8 and h3.read_text(f"{STATE}/calibration.json") == good


def test_saf_07_aborted_run_keeps_good_result_in_a_run():
    host = calibrated_host()
    good_result = host.read_text(f"{STATE}/result.json")
    host.at(host.now() + 100, lambda: host.deliver_signal("SIGINT"))
    out = matrix(host)
    assert out.status == "aborted"
    assert host.read_text(f"{STATE}/result.json") == good_result
    assert read_json(host, f"{STATE}/result.last-failed.json")["run"]["status"] == "aborted"
    c = res_check(host)
    assert c.state in ("current", "current_unproven") and "last_run_aborted" in c.warnings


def res_check(host):
    from iobench import result as res
    return res.check(host, STATE, now=host.now())


def test_saf_08_single_instance_in_a_run():
    import copy
    host = calibrated_host()
    other = copy.copy(host)  # a second process on the same estate: shared files, shared lock registry, its own identity
    other.holding = None
    s = restore.Safety(host, "run-held", "2026-10-21T03:00:00Z")
    s.acquire()
    snapshot = dict(host.files)
    with pytest.raises(Refusal) as err:
        matrix(other)
    assert err.value.condition == "already_running" and err.value.exit_code == 2 and other.files == snapshot
    s.release()
    assert matrix(other).status == "complete"


def test_saf_09_namespace_mismatch_refused_in_a_run():
    host = make_host(cache=INVERSION)
    host.add_mount("254:7", "/mnt/elsewhere", "/dev/vda7", ns="init")
    with pytest.raises(Refusal) as err:
        calibrate(host)
    assert err.value.condition in ("mounted_other_namespace", "namespace_mismatch") and host.devices["/dev/vda7"].write_bytes == 0
