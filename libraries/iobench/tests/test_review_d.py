"""IOB-BL-06 (review r3 SHOULDs, safety): abort stops the probe unit, bounded reaping, exclusive device writers, tighter fio allowlist."""
import ctypes
import os
import shlex
import signal
import subprocess
import time

import pytest

from helpers import matrix
from iobench import devio, host as host_mod, restore
from iobench.host import HostError, RealHost, TargetRef, check_argv, is_allowed_argv
from iobench.testing import R1002_PARTUUID, R1002_SIZE
from test_cov import calibrated

DEV = "/dev/vda7"


def _set_child_subreaper(enabled):
    libc = ctypes.CDLL(None, use_errno=True)
    previous = ctypes.c_int()
    if libc.prctl(37, ctypes.byref(previous), 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    if libc.prctl(36, int(enabled), 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return bool(previous.value)


def _detached_sleep_script(pidfile, seconds):
    # Record the PID from inside the process that entered the new session.
    # `$!` names the outer `setsid` command, which may fork before exec when
    # its caller is already a process-group leader.
    child = f"echo $$ > {shlex.quote(str(pidfile))}; exec sleep {seconds}"
    return f"setsid sh -c {shlex.quote(child)} & sleep 28"


def _abort_when_probe_starts(host, stuck=False):
    real = host.spawn

    def spawn(argv):
        h = real(argv)
        if argv[0] == "systemd-run":
            host.at(host.now() + 5, lambda: host.put(restore.ABORT_FILE, ""))
        return h

    host.spawn = spawn
    host.unit_stop_sticks = not stuck


# -- N-r3-1: an abort stops the unit, not only its systemd-run client -----------------------------------------------------------
def test_bl06_n_r3_1_abort_stops_the_probe_unit_and_it_never_writes_its_output():
    host = calibrated()
    _abort_when_probe_starts(host)
    out = matrix(host)
    assert out.status == "aborted" and out.exit_code == 8
    assert len(host.stopped_units) == 1 and host.stopped_units[0].startswith("iobench-probe-")
    assert ["systemctl", "stop", host.stopped_units[0]] in host.argv_log
    host.advance_to(host.now() + 300)  # the unit's would-be end: it was stopped, so it must not appear and write its file
    assert not host.active_units and not any(".iobench-probe-" in p for p in host.files)  # no re-created output after cleanup
    assert not any(w.startswith("unit_not_stopped") for w in out.result["restore"]["warnings"])


def test_bl06_n_r3_1_a_unit_that_stays_active_is_reported():
    host = calibrated()
    _abort_when_probe_starts(host, stuck=True)
    t0 = host.now()
    out = matrix(host)
    assert out.exit_code == 8
    assert any(w.startswith("unit_not_stopped: iobench-probe-") for w in out.result["restore"]["warnings"])
    assert host.now() - t0 >= restore.UNIT_STOP_WAIT_S - 1  # waited for the bound, no longer


def test_bl06_n_r3_1_stop_unit_survives_a_failing_stop_command():
    from iobench.testing import standard_host
    host = standard_host()
    host.active_units.add("iobench-probe-x")

    def run(argv, timeout=None):
        if argv[1] == "stop":
            raise OSError("no systemctl")
        return type(host).run(host, argv, timeout)

    host.run = run
    assert restore.stop_unit(host, "iobench-probe-x", timeout_s=3.0) is False
    host.active_units.discard("iobench-probe-x")
    assert restore.stop_unit(host, "iobench-probe-x", timeout_s=3.0) is True


def test_bl06_stop_is_allowlisted_only_for_probe_units():
    check_argv(devio.unit_stop_argv("iobench-probe-run-1"))
    for bad in (["systemctl", "stop", "game-server.service"], ["systemctl", "stop"], ["systemctl", "restart", "iobench-probe-x"],
                ["systemctl", "stop", "iobench-probe-x", "game-server.service"]):
        assert not is_allowed_argv(bad)


# -- N-r3-2: reaping a killed command is bounded -----------------------------------------------------------------------------------
def test_bl06_n_r3_2_a_survivor_holding_the_pipe_cannot_hang_the_run(tmp_path, monkeypatch):
    from iobench.errors import Aborted
    monkeypatch.setattr(host_mod, "REAP_TIMEOUT_S", 0.5)
    pidfiles = (tmp_path / "abort.pid", tmp_path / "timeout.pid")
    # the grandchild leaves the process group (setsid), so killpg misses it and it keeps the stdout pipe open
    real = RealHost()
    real._escalated = True
    started = time.time()
    previous_subreaper = _set_child_subreaper(True)
    try:
        script = _detached_sleep_script(pidfiles[0], 29)
        with pytest.raises(Aborted, match="second signal"):
            real._run_polled(["sh", "-c", script], None)
        assert time.time() - started < 10
        assert pidfiles[0].exists()
        pid = int(pidfiles[0].read_text().strip())
        assert os.getsid(pid) == pid

        real._escalated = False
        started = time.time()
        script = _detached_sleep_script(pidfiles[1], 29)
        with pytest.raises(subprocess.TimeoutExpired):
            real._run_polled(["sh", "-c", script], 1.0)
        assert time.time() - started < 10
        assert pidfiles[1].exists()
        pid = int(pidfiles[1].read_text().strip())
        assert os.getsid(pid) == pid
    finally:
        try:
            for pidfile in pidfiles:
                raw_pid = pidfile.read_text().strip() if pidfile.exists() else ""
                if not raw_pid:
                    continue
                pid = int(raw_pid)
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    os.waitpid(pid, 0)
                except ChildProcessError as exc:
                    raise AssertionError(
                        f"detached test child {pid} was not adopted for cleanup"
                    ) from exc
        finally:
            _set_child_subreaper(previous_subreaper)


# -- N-r3-3: a device writer must be exclusive -----------------------------------------------------------------------------------
def _ref(kind):
    return TargetRef(path=DEV, kind=kind, size_bytes=R1002_SIZE, devno="254:7", partuuid=R1002_PARTUUID)


def test_bl06_n_r3_3_device_writers_without_exclusive_open_are_refused():
    job = devio.FioJob("w", DEV, "write", 1 << 20, 1, 5, 0, 1 << 30, exclusive=False, prefix="/tmp/w")
    with pytest.raises(HostError, match="exclusive_open"):
        devio.assert_writer_bound(job.argv(), _ref("device"), 0, 1 << 30)
    devio.assert_writer_bound(job.argv(), _ref("file"), 0, 1 << 30)  # a file target has no O_EXCL to arm
    from dataclasses import replace
    devio.assert_writer_bound(replace(job, exclusive=True).argv(), _ref("device"), 0, 1 << 30)


# -- N-r3-4: allowlist matches what it documents -------------------------------------------------------------------------------
BASE = ["fio", "--ioengine=libaio", "--iodepth=32", "--filename=/dev/vda7"]


@pytest.mark.parametrize("extra", [["--output=/etc/cron.d/x"], ["--out=/etc/cron.d/x"], ["--ioengine=exec"], ["--filename=/dev/vdb"],
                                   ["--ioengine=psync"], ["--lockfile=x"], ["--status-interval=1"],
                                   ["--ioeng=exec:/bin/sh"], ["--trigger-cmd=sh"], ["--write_lat_log=/etc/x"], ["--exclusive_open=1", "--exclusive_open=0"],
                                   ["--exclusive_ope=0"], ["--iodepth=1"], ["-o", "x"]])
def test_bl06_n_r3_4_fio_options_outside_what_iobench_builds_are_refused(extra):
    assert not is_allowed_argv(BASE + extra)


def test_bl06_n_r3_4_what_iobench_builds_still_passes():
    assert is_allowed_argv(BASE + ["--output-format=json", "--readonly"])
    assert not is_allowed_argv(["fio", "--ioengine=libaio"])  # no target
    assert not is_allowed_argv(["fio", "--version", "--output=x"])
    job = devio.FioJob("w", DEV, "randread", 4096, 32, 5, 0, 1 << 30, prefix="/tmp/w")
    assert is_allowed_argv(job.argv())


def test_bl06_n_r3_4_the_key_allowlist_is_exactly_what_the_builder_emits():
    emitted = set()
    for rw, kwargs in (("randread", {}), ("write", dict(exclusive=True, hist=True, rate_iops=5, fsync=1, io_size=4096, metric="bw")),
                       ("randwrite", dict(metric="iops"))):
        argv = devio.FioJob("n", DEV, rw, 4096, 1, 5, 0, 4096, prefix="/run/iobench/n", **kwargs).argv()
        assert is_allowed_argv(argv)
        emitted |= {t.split("=")[0] for t in argv[1:]}
    assert emitted == host_mod._FIO_KEYS


# -- review round 1: the abort path never raises; a unit still stopping is not gone -----------------------------------------------
def test_bl06_stop_unit_waits_out_a_deactivating_unit():
    from iobench.testing import standard_host
    host = standard_host()
    states = iter(["deactivating", "deactivating", "inactive"])
    real = type(host).run

    def run(argv, timeout=None):
        if argv[1] == "show":
            return host_mod.RunResult(0, next(states) + "\n", "")
        return real(host, argv, timeout)

    host.run = run
    t0 = host.now()
    assert restore.stop_unit(host, "iobench-probe-x", timeout_s=10.0) is True and host.now() - t0 >= 2


def test_bl06_stop_unit_reraises_the_operators_second_signal_and_stop_units_does_not_raise():
    from iobench.errors import Aborted, Refusal
    from iobench.testing import standard_host
    from types import SimpleNamespace
    from iobench.stages import Runner

    def stop_units(units):
        ns = SimpleNamespace(host=host, safety=SimpleNamespace(report=SimpleNamespace(warnings=[])))
        Runner.stop_units(ns, units)
        return ns.safety.report.warnings

    host = standard_host()
    real = type(host).run

    def second_signal(argv, timeout=None):
        if argv[1] == "stop":
            raise Aborted("second signal during a blocking command")
        return real(host, argv, timeout)

    host.run = second_signal
    with pytest.raises(Aborted):
        restore.stop_unit(host, "iobench-probe-x")
    assert any("could not be confirmed stopped" in w for w in stop_units(["iobench-probe-x"]))

    def broken(argv, timeout=None):
        if argv[1] == "show":
            return host_mod.RunResult(1, "", "boom")
        return real(host, argv, timeout)

    host.run = broken
    with pytest.raises(Refusal):
        restore.stop_unit(host, "iobench-probe-x")
    assert any("could not be confirmed stopped" in w for w in stop_units(["iobench-probe-x"]))


def test_bl06_stop_is_allowlisted_only_for_plain_probe_unit_names():
    for bad in ("iobench-probe-*", "iobench-probe-x.mount", "iobench-probe-x y", "iobench-probe-"):
        assert not is_allowed_argv(["systemctl", "stop", bad])
    assert is_allowed_argv(["systemctl", "stop", "iobench-probe-r1a2-s4.service"])


def test_bl06_unrecognised_unit_state_is_not_read_as_stopped():
    from iobench.testing import standard_host
    host = standard_host()
    real = type(host).run
    host.run = lambda argv, timeout=None: host_mod.RunResult(0, "weird\n", "") if argv[1] == "show" else real(host, argv, timeout)
    assert restore.stop_unit(host, "iobench-probe-x", timeout_s=2.0) is False


def test_bl06_sweep_repeat_cells_get_valid_unit_names_and_a_second_signal_stops_the_unit_loop():
    from types import SimpleNamespace
    from iobench import matrix as matrix_mod
    from iobench.errors import Aborted
    from iobench.stages import Runner
    from iobench.testing import standard_host
    real_choose = matrix_mod.sweep.choose

    def choose(measured, repeats):
        out = real_choose(measured, repeats)
        if not repeats:
            out = {**out, "needs_repeat": ["dirty_bytes/2", "nr_requests*2"]}  # the real cell names carry `/`, `*`, `+`
        return out

    matrix_mod.sweep.choose = choose
    try:
        host = calibrated()
        out = matrix(host)
    finally:
        matrix_mod.sweep.choose = real_choose
    assert out.status == "complete" and set(out.result["evidence"]["sweep"]["repeats"]) == {"dirty_bytes/2", "nr_requests*2"}
    units = [t.split("=", 1)[1] for a in host.argv_log if a[0] == "systemd-run" for t in a if t.startswith("--unit=")]
    assert any("-s4-rep-0" in u for u in units) and any("-s4-rep-1" in u for u in units)
    # a second signal while stopping the first unit abandons the remaining units
    h2 = standard_host()
    real = type(h2).run

    def run(argv, timeout=None):
        if argv[1] == "stop":
            raise Aborted("second signal during a blocking command")
        return real(h2, argv, timeout)

    h2.run = run
    ns = SimpleNamespace(host=h2, safety=SimpleNamespace(report=SimpleNamespace(warnings=[])))
    Runner.stop_units(ns, ["iobench-probe-a", "iobench-probe-b"])
    assert len(ns.safety.report.warnings) == 1


def test_bl06_fakehost_refuses_unit_names_systemd_would_refuse():
    from iobench.testing import standard_host
    host = standard_host()
    argv = devio.probe_unit_argv("iobench-probe-run-s4-rep-dirty_bytes/2", "s.slice", 90, "pass", ["/w", "/w/x", "10", "0.5"])
    with pytest.raises(HostError, match="allowlist"):  # refused by the pinned systemd-run shape before FakeHost's own unit-name check
        host.spawn(argv)
    with pytest.raises(HostError, match="unit name"):
        host._spawn_probe(argv)
