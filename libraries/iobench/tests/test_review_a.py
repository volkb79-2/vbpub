"""IOB-03 P3 review A (safety and the real host): one test per finding, each red without its fix."""
import copy
import json

import pytest

from helpers import CREATED, INVERSION, calibrate, make_host
from iobench import devio, guard, restore
from iobench.errors import Refusal, VerificationFailure
from iobench.host import HostError, RunResult, check_argv
from iobench.restore import Safety
from iobench.testing import R1002_PARTUUID, R1002_SIZE, FakeHost, standard_host

DEV = "/dev/vda7"
DIRTY = "/proc/sys/vm/dirty_bytes"
DIRTY_BG = "/proc/sys/vm/dirty_background_bytes"
RATIO = "/proc/sys/vm/dirty_ratio"
RATIO_BG = "/proc/sys/vm/dirty_background_ratio"
NR = "/sys/class/block/vda/queue/nr_requests"
PRESSURE = "/sys/fs/cgroup/wings.slice/io.pressure"
KW = dict(confirm_quiet_window=False, installer=False, production_pattern=None, production_units=[], protect_cgroup="wings.slice", abort_psi=40.0)


def holder(host, run="run-a"):
    s = Safety(host, run, CREATED)
    s.acquire()
    return s


def init(host, **kw):
    args = dict(own=True, expect_size=R1002_SIZE, expect_partuuid=R1002_PARTUUID, expect_serial=None, created=CREATED)
    args.update(kw)
    return guard.init_device(host, DEV, **args)


def pressure_host():
    host = standard_host()
    host.dynamic[PRESSURE] = lambda: "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
    return host


# -- A-B1: only the lock holder touches the journal ---------------------------------------------------
def test_rev_a_b1_a_refused_run_leaves_the_live_runs_journal_alone():
    host = make_host(cache=INVERSION)
    other = copy.copy(host)  # a second process: shared files and lock registry
    other.holding = None
    a = holder(host)
    a.set_knob(DIRTY, "16777216")  # run A has journalled a knob and holds the lock
    journal = host.read_text(restore.JOURNAL)
    with pytest.raises(Refusal) as err:
        calibrate(other)
    assert err.value.condition == "already_running"
    assert host.read_text(restore.JOURNAL) == journal and host.read_text(DIRTY) == "16777216\n"
    # a refusal BEFORE the lock (the fio floor) must not touch it either
    other.probe_failures["fio"] = RunResult(0, "fio-3.20\n", "")
    with pytest.raises(Refusal, match="older than 3.30"):
        calibrate(other)
    assert host.read_text(restore.JOURNAL) == journal
    a.restore()
    assert not host.exists(restore.JOURNAL) and host.read_text(DIRTY) == "0\n"


def test_rev_a_b1_a_session_that_wrote_nothing_removes_nothing():
    host = standard_host()
    a = holder(host)
    a.set_knob(DIRTY, "16777216")
    journal = host.read_text(restore.JOURNAL)
    bystander = Safety(host, "run-b", CREATED)  # never acquired, never wrote
    bystander.restore()
    assert host.read_text(restore.JOURNAL) == journal
    for action in (lambda: bystander.set_knob(DIRTY, "1"), lambda: bystander.register_file("/x/iobench-f"), bystander.recover_previous):
        with pytest.raises(HostError, match="lock holder"):
            action()


# -- A-B2: coupled sysctl pairs ---------------------------------------------------------------------
def test_rev_a_b2_restore_returns_the_ratio_knobs_too():
    host = standard_host()  # production: bytes 0, ratio 20 / 10
    s = holder(host)
    s.set_knob(DIRTY, "16777216")
    s.set_knob(DIRTY_BG, "4194304")
    assert host.read_text(RATIO) == "0\n" and host.read_text(RATIO_BG) == "0\n"  # the kernel zeroed them
    s.set_knob(DIRTY, "33554432")  # a second candidate must not re-journal the already-zeroed ratio as "original"
    s.restore()
    assert [host.read_text(p).strip() for p in (DIRTY, DIRTY_BG, RATIO, RATIO_BG)] == ["0", "0", "20", "10"]
    assert s.report.verified and not host.exists(restore.JOURNAL)


def test_rev_a_b2_restore_keeps_a_bytes_configured_host():
    host = standard_host()
    for path, value in ((DIRTY, "1000000"), (DIRTY_BG, "500000"), (RATIO, "0"), (RATIO_BG, "0")):
        host.put(path, value + "\n")
    s = holder(host)
    s.set_knob(DIRTY, "16777216")
    s.set_knob(DIRTY_BG, "4194304")
    s.restore()
    assert [host.read_text(p).strip() for p in (DIRTY, DIRTY_BG, RATIO, RATIO_BG)] == ["1000000", "500000", "0", "0"]


def test_rev_a_b2_the_pair_is_verified_by_read_back():
    host = standard_host()
    s = holder(host)
    s.set_knob(DIRTY, "16777216")
    host.sysfs_swallow.add(RATIO)  # the ratio write is silently ignored, so the pair stays at ratio 0
    with pytest.raises(VerificationFailure) as err:
        s.restore()
    assert err.value.exit_code == 9 and "dirty_ratio" in str(err.value) and "'20'" in str(err.value)
    assert host.exists(restore.JOURNAL) and not s.report.verified


def test_rev_a_b2_a_leftover_journal_restores_the_ratio_too():
    host = standard_host()
    dead = holder(host, "run-dead")
    dead.set_knob(DIRTY, "16777216")
    fresh = Safety(host, "run-new", CREATED)
    fresh.acquire()
    assert fresh.recover_previous().verified
    assert host.read_text(RATIO) == "20\n" and host.read_text(DIRTY) == "0\n"


# -- A-B3: containers -----------------------------------------------------------------------------------
@pytest.mark.parametrize("change,condition", [
    (lambda h: h.ns_links.update({"/proc/self/ns/pid": "pid:[4026532999]", "/proc/1/ns/pid": "pid:[4026532999]"}), "container_pid_namespace"),
    (lambda h: h.ns_links.update({"/proc/1/ns/pid": "pid:[4026532999]"}), "container_pid_namespace"),
    (lambda h: h.ns_links.update({"/proc/1/ns/mnt": "mnt:[4026532000]"}), "mount_namespace_mismatch"),
    (lambda h: h.ns_links.pop("/proc/1/ns/pid"), "namespace_unprovable"),
])
def test_rev_a_b3_a_container_is_refused_before_anything_is_written(change, condition):
    host = make_host(cache=INVERSION)
    change(host)
    with pytest.raises(Refusal) as err:
        calibrate(host)
    assert err.value.condition == condition and err.value.exit_code == 2 and "run on the host, not in a container" in str(err.value)
    assert host.devices[DEV].write_bytes == 0 and not host.exists(restore.JOURNAL)


def test_rev_a_b3_target_init_and_the_mount_check_refuse_too():
    host = standard_host()
    host.ns_links["/proc/self/ns/pid"] = "pid:[4026532999]"
    with pytest.raises(Refusal) as err:
        init(host)
    assert err.value.condition == "container_pid_namespace" and host.devices[DEV].marker is None
    with pytest.raises(Refusal, match="host"):
        guard.check_namespaces(host, {"254:7"}, {DEV}, DEV)


def test_rev_a_b3_unreadable_pid1_mountinfo_is_a_refusal_not_a_traceback():
    host = standard_host()

    def denied():
        raise PermissionError("/proc/1/mountinfo")
    host.dynamic["/proc/1/mountinfo"] = denied
    with pytest.raises(Refusal) as err:
        init(host)
    assert err.value.condition == "namespace_unprovable" and err.value.exit_code == 2


# -- A-B4: probe return codes ---------------------------------------------------------------------------------
def test_rev_a_b4_a_failing_blkid_or_wipefs_is_a_refusal_naming_the_probe():
    for tool in ("blkid", "wipefs"):
        host = standard_host()
        host.probe_failures[tool] = RunResult(1, "", f"{tool}: permission denied")
        with pytest.raises(Refusal) as err:
            init(host)
        assert err.value.condition == "probe_failed" and tool in str(err.value) and "permission denied" in str(err.value)
        assert host.devices[DEV].marker is None and host.devices[DEV].write_bytes == 0
    host = standard_host()
    host.probe_failures["blkid"] = RunResult(2, "", "")  # 2 is the documented "nothing found": a normal answer, judged on its own
    with pytest.raises(Refusal) as err:
        init(host)
    assert err.value.condition == "identity_missing"


def test_rev_a_b4_a_failing_docker_ps_is_not_a_quiet_host():
    host = pressure_host()
    host.probe_failures["docker"] = RunResult(1, "", "Cannot connect to the Docker daemon")
    with pytest.raises(Refusal) as err:
        restore.live_preflight(host, **KW)
    assert err.value.condition == "probe_failed" and "docker ps" in str(err.value)


def test_rev_a_b4_production_unit_typos_and_failing_systemctl():
    host = pressure_host()
    kw = {**KW, "production_units": ["wing.service"]}
    with pytest.raises(Refusal) as err:
        restore.live_preflight(host, **kw)
    assert err.value.condition == "unit_not_found" and "wing.service" in str(err.value)
    host.known_units.add("wing.service")
    assert restore.live_preflight(host, **kw)["units_active"] == []  # a real, inactive unit
    host.probe_failures["systemctl"] = RunResult(1, "", "Failed to connect to bus")
    with pytest.raises(Refusal) as err:
        restore.live_preflight(host, **kw)
    assert err.value.condition == "probe_failed"
    # is-active answering something other than 0 / 3 is a failed probe as well
    host2 = pressure_host()
    host2.known_units.add("u.service")
    real_run = host2.run
    host2.run = lambda argv, timeout=None: RunResult(4, "", "no such unit") if argv[1] == "is-active" else real_run(argv, timeout)
    with pytest.raises(Refusal) as err:
        restore.live_preflight(host2, **{**KW, "production_units": ["u.service"]})
    assert err.value.condition == "probe_failed"


def test_rev_a_b4_fio_version_probe_failure_names_the_probe():
    host = make_host(cache=INVERSION)
    host.probe_failures["fio"] = RunResult(127, "", "fio: not found")
    with pytest.raises(Refusal) as err:
        calibrate(host)
    assert err.value.condition == "probe_failed" and "fio --version" in str(err.value)


# -- S3: abort guards are validated ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [float("nan"), 0.0, -5.0, 101.0, float("inf")])
def test_rev_a_s3_abort_psi_must_be_a_real_percentage(bad):
    with pytest.raises(Refusal) as err:
        restore.live_preflight(pressure_host(), **{**KW, "abort_psi": bad})
    assert err.value.condition == "abort_psi_invalid"


def test_rev_a_s3_protect_cgroup_must_have_readable_pressure():
    host = standard_host()  # no wings.slice/io.pressure
    with pytest.raises(Refusal) as err:
        restore.live_preflight(host, **KW)
    assert err.value.condition == "protect_cgroup_unreadable"
    # --installer: a half guard is refused, a complete one is validated, and a threshold-less watch cannot raise
    with pytest.raises(Refusal) as err:
        restore.live_preflight(host, **{**KW, "installer": True, "abort_psi": None})
    assert err.value.condition == "abort_guard_incomplete"
    with pytest.raises(Refusal) as err:
        restore.live_preflight(host, **{**KW, "installer": True})
    assert err.value.condition == "protect_cgroup_unreadable"
    assert restore.live_preflight(pressure_host(), **{**KW, "installer": True})["bypassed"]
    restore.AbortWatch(host, "wings.slice", None).check()


# -- S4: a stale abort file ---------------------------------------------------------------------------------------
def test_rev_a_s4_a_stale_abort_file_is_refused_before_any_write():
    host = make_host(cache=INVERSION)
    host.put(restore.ABORT_FILE, "")
    with pytest.raises(Refusal) as err:
        calibrate(host)
    assert err.value.condition == "stale_abort_file" and restore.ABORT_FILE in str(err.value)
    assert host.devices[DEV].write_bytes == 0 and host.exists(restore.ABORT_FILE)


# -- S5: io.max verification --------------------------------------------------------------------------------------
def test_rev_a_s5_no_io_max_verdict_without_a_before_snapshot():
    host = standard_host()
    host.put("/sys/fs/cgroup/wings.slice/io.max", "254:0 rbps=1000000\n")
    s = holder(host)  # begin() never ran (the run failed in S0)
    s.verify_io_max()
    assert s.report.slice_io_max_unchanged


def test_rev_a_s5_a_cgroup_appearing_or_vanishing_is_not_an_io_max_change():
    host = standard_host()
    host.put("/sys/fs/cgroup/wings.slice/io.max", "254:0 rbps=1000000\n")
    host.put("/sys/fs/cgroup/old.slice/io.max", "254:0 rbps=1000\n")
    s = holder(host)
    s.begin()
    host.put("/sys/fs/cgroup/new.slice/io.max", "")  # a container started
    del host.files["/sys/fs/cgroup/old.slice/io.max"]  # another stopped
    s.verify_io_max()
    assert s.report.slice_io_max_unchanged and len(s.report.warnings) == 2
    host.put("/sys/fs/cgroup/wings.slice/io.max", "254:0 rbps=2\n")
    with pytest.raises(VerificationFailure, match="wings.slice"):
        s.verify_io_max()


def test_rev_a_s5_a_cgroup_removed_mid_walk_does_not_raise():
    host = standard_host()
    host.put("/sys/fs/cgroup/gone.slice/io.max", "x\n")
    real = host.read_text

    def flaky(path):
        if path.endswith("gone.slice/io.max"):
            raise FileNotFoundError(path)
        return real(path)
    host.read_text = flaky
    assert restore.snapshot_io_max(host) == {}


# -- S6: kernel-normalised values and journal I/O errors ----------------------------------------------------
def test_rev_a_s6_a_clamped_value_is_journalled_as_applied_and_restored():
    host = standard_host()
    host.sysfs_clamp[NR] = "128"
    s = holder(host)
    assert s.set_knob(NR, "100") is False  # clamped: not what was asked for
    assert json.loads(host.read_text(restore.JOURNAL))["entries"][0]["applied"] == "128"
    host.sysfs_clamp.clear()
    report = s.restore()
    assert host.read_text(NR) == "64\n" and report.skipped == [] and report.verified


def test_rev_a_s6_rejected_write_leaves_no_entry_but_a_journal_failure_propagates():
    host = standard_host()
    host.sysfs_reject.add(NR)
    s = holder(host)
    assert s.set_knob(NR, "16") is False
    assert json.loads(host.read_text(restore.JOURNAL))["entries"] == []
    host.sysfs_reject.clear()

    def full_disk(path, text, mode=0o644):
        raise OSError(28, "No space left on device")
    host.write_atomic = full_disk
    with pytest.raises(OSError, match="No space"):
        s.set_knob(NR, "16")


# -- S7: restore completes and the journal is validated ---------------------------------------------------------
def test_rev_a_s7_one_failing_entry_does_not_strand_the_others():
    host = standard_host()
    s = holder(host)
    s.set_knob(DIRTY, "16777216")
    s.set_knob(NR, "16")
    s.register_file("/bench/w/iobench-f0")
    host.put("/bench/w/iobench-f0", "x")
    del host.files[NR.replace("/sys/class/block/vda", "/sys/devices/pci0/block/vda")]  # the device vanished
    with pytest.raises(VerificationFailure) as err:
        s.restore()
    assert "nr_requests" in str(err.value) and err.value.exit_code == 9
    assert host.read_text(DIRTY) == "0\n" and host.read_text(RATIO) == "20\n" and not host.exists("/bench/w/iobench-f0")
    assert host.exists(restore.JOURNAL)


def test_rev_a_s7_a_leaked_file_that_cannot_be_removed_is_reported():
    host = standard_host()
    s = holder(host)
    s.register_file("/bench/w/iobench-f0")

    def stuck(path):
        raise PermissionError(path)
    host.remove = lambda p: stuck(p) if p.endswith("iobench-f0") else FakeHost.remove(host, p)
    with pytest.raises(VerificationFailure, match="iobench-f0"):
        s.restore()


@pytest.mark.parametrize("doc", [
    "not json", "[]", {"run": "r"},
    {"run": "r", "started": "s", "entries": {}, "files": [], "coupled": {}},
    {"run": "r", "started": "s", "entries": ["x"], "files": [], "coupled": {}},
    {"run": "r", "started": "s", "entries": [{"path": "/proc/sys/vm/swappiness", "original": "1", "applied": "2"}], "files": [], "coupled": {}},
    {"run": "r", "started": "s", "entries": [{"path": "/sys/class/block/../queue/nr_requests", "original": "1", "applied": "2"}], "files": [], "coupled": {}},
    {"run": "r", "started": "s", "entries": [], "files": ["/etc/passwd"], "coupled": {}},
    {"run": "r", "started": "s", "entries": [], "files": ["/bench/../etc/iobench-x"], "coupled": {}},
    {"run": "r", "started": "s", "entries": [], "files": [5], "coupled": {}},
    {"run": "r", "started": "s", "entries": [], "files": [], "coupled": {"/etc/shadow": "1"}},
])
def test_rev_a_s7_a_corrupt_journal_is_refused_and_acted_on_by_nothing(doc):
    host = standard_host()
    host.put(restore.JOURNAL, doc if isinstance(doc, str) else json.dumps(doc))
    before = dict(host.files)
    s = holder(host, "run-new")
    with pytest.raises(Refusal) as err:
        s.recover_previous()
    assert err.value.condition == "journal_corrupt" and host.write_log == [] and host.files == before


# -- S10: every spelling of an IO limit ----------------------------------------------------------------------
@pytest.mark.parametrize("token", ["IOWeight=10", "-pIOWeight=10", "--property=IOWriteBandwidthMax=/dev/vda 1M", "-p=IOReadIOPSMax=/dev/vda 5",
                                   "BlockIOWriteBandwidth=/dev/vda 1M", "--property=blockioweight=10", "-pIODeviceLatencyTargetSec=/dev/vda 5ms"])
def test_rev_a_s10_io_limit_spellings_are_refused(token):
    with pytest.raises(HostError, match="forbidden argument"):
        check_argv(["systemd-run", "-p", token, "true"])
    with pytest.raises(HostError):
        check_argv(["systemd-run", token, "true"])


def test_rev_a_s10_probe_unit_and_fio_argv_pass():
    check_argv(devio.probe_unit_argv("u", "iobench-probe.slice", 90, "pass", []))
    check_argv(["fio", "--ioengine=libaio", "--iodepth=32", "--io_size=1M", "--filename=/dev/vda7"])


def test_rev_a_b2_a_failing_ratio_write_is_reported_not_raised_raw():
    host = standard_host()
    s = holder(host)
    s.set_knob(DIRTY, "16777216")
    host.sysfs_reject.add(RATIO)
    with pytest.raises(VerificationFailure, match="dirty_ratio"):
        s.restore()
    host.sysfs_reject.clear()
    host.write_sysfs(DIRTY, host.read_text(DIRTY).strip())  # rewriting an unchanged value does not trigger the kernel's coupling
    assert host.read_text(RATIO) == "0\n"
