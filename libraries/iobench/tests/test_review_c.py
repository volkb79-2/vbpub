"""IOB-03 P3 coordinator rulings after the first review wave (dry-run preflight, exclusive open, runtime bindings, probe, namespaces)."""
import errno
import json

import pytest

from helpers import CREATED, INVERSION, calibrate, config, make_host, matrix
from iobench import devio, guard
from iobench.errors import Refusal, RunFailed
from iobench.host import HostError, RealHost, RunResult, TargetRef, check_argv, is_allowed_argv
from iobench.testing import GIB, R1002_PARTUUID, R1002_SIZE, standard_host
from test_cli import run
from test_cov import calibrated
from test_review_a import finit, init

DEV = "/dev/vda7"


# -- ruling 1: a dry run says it did not run the preflight ----------------------------------------------------------------
def test_rev_c_1_dry_run_states_that_the_preflight_was_not_run():
    host = make_host(cache=INVERSION)
    code, out, _ = run(host, "calibrate", "--target", DEV, "--work-dir", "/bench/w", "--dry-run", "--assume-r-est", "1.36e9", "--json")
    doc = json.loads(out)
    assert code == 0 and doc["preflight"] == "not_run" and "NOT run" in doc["preflight_note"]
    code, human, _ = run(host, "calibrate", "--target", DEV, "--work-dir", "/bench/w", "--dry-run", "--assume-r-est", "1.36e9")
    assert code == 0 and '"preflight": "not_run"' in human
    # a clean dry run does not mean the target will be accepted: the same invocation without --dry-run is refused (no abort guards)
    with pytest.raises(Refusal) as err:
        calibrate(host, protect_cgroup=None, abort_psi=None)
    assert err.value.condition == "abort_guard_required"
    assert calibrate(host).status == "complete"
    code, out, _ = run(host, "matrix", "--target", DEV, "--work-dir", "/bench/w", "--dry-run", "--json")
    assert code == 0 and json.loads(out)["preflight"] == "not_run"


# -- ruling 2: exclusive open, fail closed ---------------------------------------------------------------------------------
def test_rev_c_2_device_jobs_open_exclusively_and_an_unsupported_fio_is_refused():
    from helpers import fio_jobs
    host = make_host(cache=INVERSION)
    assert calibrate(host).status == "complete"
    jobs = fio_jobs(host)
    assert jobs and all(j.exclusive for j in jobs if j.filename == DEV)
    assert all("--exclusive_open=1" in a for a in host.argv_log if a[0] == "fio" and any(t == f"--filename={DEV}" for t in a))
    assert ["fio", "--cmdhelp=exclusive_open"] in host.argv_log
    # the installed fio does not know the option: refused before anything is written, never silently dropped
    old = make_host(cache=INVERSION)
    old.fio_options.clear()
    with pytest.raises(Refusal) as err:
        calibrate(old)
    assert err.value.condition == "fio_option_unsupported" and "exclusive_open" in str(err.value) and old.devices[DEV].write_bytes == 0
    # a failing probe is a probe failure, not "supported"
    broken = make_host(cache=INVERSION)
    real = broken.run
    broken.run = lambda argv, timeout=None: RunResult(1, "", "fio: boom") if len(argv) == 2 and argv[1].startswith("--cmdhelp") else real(argv, timeout)
    with pytest.raises(Refusal) as err:
        calibrate(broken)
    assert err.value.condition == "probe_failed" and "cmdhelp" in str(err.value)
    # the option round-trips through the argv parser; a file target never gets it
    j = jobs[0]
    assert devio.parse_job(j.argv()).exclusive and not devio.parse_job(j.with_(exclusive=False).argv()).exclusive


# -- A-S1: the runtime allowlist and the extent binding -----------------------------------------------------------------------
def target_ref(host):
    d = host.devices[DEV]
    return TargetRef("device", DEV, d.size, devno=d.devno, partuuid=d.partuuid)


def test_rev_c_s1_argv_must_match_the_authorised_extent():
    host = standard_host()
    ref = target_ref(host)
    j = devio.FioJob("j", DEV, "randwrite", 4096, 8, 5, 0, GIB, 4096 * 100, prefix="/run/iobench/j")
    host.write_range(j.argv(), ref, 0, GIB)
    for bad in (j.with_(filename="/dev/vda3"), j.with_(offset=4096), j.with_(size=2 * GIB)):
        with pytest.raises(HostError, match="authorised"):
            host.write_range(bad.argv(), ref, 0, GIB)
    tail = j.with_(offset=R1002_SIZE - GIB, size=GIB)
    with pytest.raises(HostError, match="tail|reserved"):
        devio.assert_writer_bound(tail.argv(), ref, tail.offset, tail.size)
    with pytest.raises(HostError, match="tail|reserved|extent"):
        devio.assert_writer_bound(j.with_(size=0).argv(), ref, 0, 0)
    assert host.discard_range(devio.blkdiscard_argv(DEV, 0, GIB), ref, 0, GIB).returncode == 0
    with pytest.raises(HostError, match="discard argv"):
        host.discard_range(devio.blkdiscard_argv(DEV, 0, 9 * GIB), ref, 0, GIB)
    with pytest.raises(HostError, match="discard argv"):
        host.discard_range(devio.blkdiscard_argv("/dev/vda3", 0, GIB), ref, 0, GIB)
    file_ref = TargetRef("file", "/bench/t.img", 8 * GIB)
    devio.assert_discard_bound(devio.punch_hole_argv("/bench/t.img", 0, GIB), file_ref, 0, GIB)


def test_rev_c_s1_run_and_spawn_enforce_the_allowlist():
    for argv in (["rm", "-rf", "/"], ["fio", "--filename=/dev/sda", "--exec_prerun=rm -rf /"],
                 ["fio", "rm", "--filename=/x"], ["fio", "--name=x"], ["systemctl", "stop", "x"], ["docker", "rm", "x"], ["blkid"], ["fio"]):
        with pytest.raises(HostError):
            check_argv(argv)
        assert not is_allowed_argv(argv)
    assert is_allowed_argv(["fio", "--version"]) and is_allowed_argv(["fio", "--cmdhelp=exclusive_open"])
    assert is_allowed_argv(["wipefs", "--no-act", "-p", "/dev/x"])
    # FakeHost enforces the same allowlist on run and spawn (parity with RealHost.run/spawn)
    host = standard_host()
    with pytest.raises(HostError, match="allowlist"):
        host.run(["python3", "-c", "pass"])
    with pytest.raises(HostError):
        host.spawn(["fio", "--name=x", "--filename=/x", "--exec_postrun=sh"])


# -- N2: the probe unit is waited for --------------------------------------------------------------------------------------------
def test_rev_c_n2_the_sync_latency_probe_is_waited_for():
    assert devio.probe_unit_argv("u", "s.slice", 90, "pass", ["a"])[:2] == ["systemd-run", "--wait"]
    host = calibrated()
    out = matrix(host)
    assert out.status == "complete" and out.result["run"]["stages"]["S4"] == "ok"  # the unit starts 2 s late and writes at its end
    assert any(a[:2] == ["systemd-run", "--wait"] for a in host.argv_log)


def test_rev_c_n2_without_wait_the_output_is_read_before_it_exists():
    host = calibrated()
    real = host.spawn
    host.spawn = lambda argv: real([a for a in argv if a != "--wait"]) if argv[0] == "systemd-run" else real(argv)
    with pytest.raises(FileNotFoundError):  # what the old argv did on a real host
        matrix(host)


def test_rev_c_n2_a_failing_probe_unit_fails_the_cell():
    host = calibrated()
    real = host.spawn

    def spawn(argv):
        h = real(argv)
        if argv[0] == "systemd-run":
            h.result = RunResult(1, "", "Failed to start transient service")
        return h

    host.spawn = spawn
    out = matrix(host)
    assert out.status == "failed" and "sync-latency probe unit failed" in out.message


# -- N-r2-1 / N-r2-2 / N-r2-3 ------------------------------------------------------------------------------------------------------
def test_rev_c_nr2_1_a_busy_device_is_a_refusal_exit_2(monkeypatch):
    real = RealHost()
    monkeypatch.setattr(RealHost, "_verify_live", lambda self, target: None)

    def busy(path, flags, *a):
        raise OSError(errno.EBUSY, "Device or resource busy")

    monkeypatch.setattr("os.open", busy)
    ref = TargetRef("device", DEV, 10 * GIB, devno="254:7", partuuid="x")
    with pytest.raises(HostError, match="exclusively"):
        real.write_marker(ref, "{}")
    # init_device turns that HostError into exit 2
    host = standard_host()

    def claim(target, text):
        raise HostError("/dev/vda7 cannot be opened exclusively (Device or resource busy): it is mounted, swap-active or claimed by md/dm")

    host.write_marker = claim
    with pytest.raises(Refusal) as err:
        init(host)
    assert err.value.exit_code == 2 and err.value.condition == "identity_changed"


def test_rev_c_nr2_2_file_targets_need_the_host_namespace_too():
    host = standard_host()
    host.ns_links["/proc/self/ns/pid"] = "pid:[4026532999]"
    with pytest.raises(Refusal) as err:
        finit(host)
    assert err.value.condition == "container_pid_namespace"
    ok = standard_host()
    target = finit(ok)
    ok.ns_links["/proc/1/ns/mnt"] = "mnt:[1]"
    with pytest.raises(Refusal) as err:
        guard.load_file_target(ok, target.path)
    assert err.value.condition == "mount_namespace_mismatch"


# -- A-S9 second half: a second signal is not deferred -------------------------------------------------------------------------------
def test_rev_c_s9_a_second_signal_interrupts_a_blocking_command():
    import threading
    import time
    from iobench.errors import Aborted
    real = RealHost()
    assert real._run_polled(["echo", "hi"], None).stdout == "hi\n"
    timer = threading.Timer(0.6, lambda: setattr(real, "_escalated", True))
    timer.start()
    started = time.time()
    with pytest.raises(Aborted, match="second signal"):
        real._run_polled(["sleep", "30"], None)
    assert time.time() - started < 5
    real._escalated = False
    with pytest.raises(__import__("subprocess").TimeoutExpired):
        real._run_polled(["sleep", "30"], 1.0)
    # the handler marks escalation only for a second signal
    real.install_signals()
    import os
    import signal
    try:
        os.kill(os.getpid(), signal.SIGHUP)
        time.sleep(0.05)
        assert real.signal_pending() == "SIGHUP" and not real._escalated
        os.kill(os.getpid(), signal.SIGHUP)
        time.sleep(0.05)
        assert real._escalated
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, signal.SIG_DFL)
