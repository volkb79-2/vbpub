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
