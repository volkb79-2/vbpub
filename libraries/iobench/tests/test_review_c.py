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
    j = devio.FioJob("j", DEV, "randwrite", 4096, 8, 5, 0, GIB, 4096 * 100, prefix="/run/iobench/j", exclusive=True)
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
    # straight into the unit model: the argv allowlist would refuse a probe unit without --wait before it ran (test_rev_e_*)
    host.spawn = lambda argv: host._spawn_probe([a for a in argv if a != "--wait"]) if argv[0] == "systemd-run" else real(argv)
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


# -- ruling 3: the write BPS cap is not proven by the 4 KiB random series --------------------------------------------------------------
def test_rev_c_3_write_bps_is_unproven_until_the_sequential_series_is_time_proven():
    from test_sta import key_reasons, run_cal
    out = run_cal()[1]
    # the IOPS caps are proven; the sequential 30 s cell cannot prove a bytes/s figure over 120 s
    assert key_reasons(out.result, "caps.WIOPS")[:2] == ("proven", set())
    status, reasons, _ = key_reasons(out.result, "caps.WBPS")
    assert status == "unproven" and "short_observation" in reasons
    cb = out.calibration["calibration"]["cap_basis"]["bps"]
    assert cb["write"]["status"] == "unproven" and "short_observation" in cb["write"]["reasons"]
    # reads are untouched by the write rule: the read BPS cap follows the read cache state only
    assert "short_observation" not in key_reasons(out.result, "caps.RBPS")[1]
    assert "short_observation" not in cb["read"]["reasons"]
    # the model's write keys inherit it
    assert out.result["proposals"]["iocost_model"]["wbps"]["status"] == "unproven"


# -- B r2: the 120 s margin ----------------------------------------------------------------------------------------------------------
def test_rev_c_margin_a_run_exactly_as_long_as_the_rule_can_never_prove_the_fresh_half():
    from iobench import cache, ladder
    from iobench.series import reduce_series
    stamped = [(1.0 * i - 0.03, 21600.0) for i in range(1, 121)]  # fio's last sample of a 120 s run lands at 119.97 s
    assert stamped[-1][0] == pytest.approx(119.97)
    short = cache.write_time_proof(reduce_series(stamped), 4096)
    assert not short.proven and short.reason == "short_observation" and short.observation_s < 120
    longer = [(1.0 * i - 0.03, 21600.0) for i in range(1, 151)]
    assert cache.write_time_proof(reduce_series(longer), 4096).proven
    assert ladder.FRESH_OBS_S == 150 and ladder.observation_plan("standard")["fresh"] == 150 == ladder.observation_plan("thorough")["fresh"]
    # no tolerance was added to the rule: 119.97 s is still short
    assert cache.PROOF_MIN_AFTER_S == 120


# -- B S13: a rung that fails Little's law is dropped before every consumer ------------------------------------------------------------
def test_rev_c_s13_a_faked_rung_feeds_no_cliff_no_cached_ref_no_best_rung_and_no_caps():
    from test_sta import key_reasons, seeded

    def go(fake):
        host = seeded()
        real = host.spawn

        def sp(argv):
            if fake and any(a.startswith("--name=") and a.endswith("-rung-r32-1024") for a in argv):
                host.scenario.little_factor = 2.0
            try:
                return real(argv)
            finally:
                host.scenario.little_factor = 1.0

        host.spawn = sp
        return calibrate(host)

    base, faked = go(False), go(True)
    bl, fl = base.calibration["calibration"]["ladder"], faked.calibration["calibration"]["ladder"]
    assert bl["excluded_rungs"] == [] and bl["best_read_rung_iops"] == pytest.approx(73228, rel=0.01)  # the 1 GiB rung was the fastest
    assert fl["excluded_rungs"] == [{"dir": "read", "ws_bytes": 1 << 30, "check": "IOB-PLA-06"}]
    assert all(r["ws_bytes"] != 1 << 30 for r in fl["rungs"] if r["dir"] == "read")
    assert fl["best_read_rung_iops"] < 0.5 * bl["best_read_rung_iops"]  # not the faked figure
    assert fl["cached_ref_iops"] != bl["cached_ref_iops"]  # computed without the dropped rung
    assert key_reasons(faked.result, "caps.RIOPS")[2] == "stage_failed"  # the PLA-06 effect on the caps stays
    # QoS max does not use the faked rung either
    qos = faked.result["proposals"]["iocost_qos"]["max"]
    assert qos["status"] == "absent" or qos["inputs"]["best_read_rung"] == fl["best_read_rung_iops"]
    # a faked depth rung voids QD90 for its direction
    host = seeded()
    real = host.spawn

    def sp(argv):
        if any(a.startswith("--name=") and a.endswith("-d") and "-rung-r8-" in a for a in argv):
            host.scenario.little_factor = 2.0
        try:
            return real(argv)
        finally:
            host.scenario.little_factor = 1.0

    host.spawn = sp
    assert calibrate(host).calibration["calibration"]["ladder"]["qd90"]["read"] is None


# -- B r2: a fresh penalty excluded by design is not silent ------------------------------------------------------------------------------
def test_rev_c_fresh_excluded_on_a_file_target_without_discard_carries_a_reason():
    from test_tgt import file_host
    for discard in (False, True):
        host = file_host(discard=discard)
        host.scenario.write.fresh_penalty = 8.0
        out = calibrate(host, target="/bench/target.img", work_dir="/bench/w")
        wb = out.calibration["calibration"]["cap_basis"]["write"]
        assert out.calibration["calibration"]["fresh_vs_rewrite"]["measured"]
        assert ("fresh_not_measured" in wb.get("reasons", [])) is (not discard), (discard, wb)


# -- B-S15: a dynamic two-tier fixture, end to end through the Calibrator ------------------------------------------------------------------
def test_rev_c_s15_two_tier_cache_end_to_end():
    """A 40 GiB fast tier (92k IOPS) inside a 300 GiB slower tier (15k IOPS) over a 9.8k backing disk, filled with 345 GiB: the earliest
    written 45 GiB were evicted to the backing disk.  Simulated content, not a formula: E-cold reads that region and must report the SLOW
    (backing) rate, the write series has a 4 s burst that the 240 s observation outlives, and the proposals carry the statuses SPEC gives."""
    from test_sta import PLAIN_WRITE, key_reasons, seed_history
    t_obs = {}
    host = make_host(cache=dict(capacity_gib=40, hit_iops=92000, write_allocate=True, tier2_capacity_gib=300, tier2_iops=15000),
                     read=dict(iops_max=9800, lat_us=760, lat_hit_us=110, lat_hit2_us=240), write=dict(PLAIN_WRITE),
                     ceiling=lambda t: 1.0 if t < t_obs.get("t", 1e18) + 3.5 else 0.2)
    seed_history(host)
    real = host.write_range

    def wr(argv, target, offset, length):
        if any("-obs-write" in a for a in argv):
            t_obs.setdefault("t", host.now())
        return real(argv, target, offset, length)

    host.write_range = wr
    out = calibrate(host)
    assert out.status == "complete"
    cal = out.calibration["calibration"]
    read = cal["cache"]["read"]
    # E-cold picked the slow tier: the backing rate, not tier 2 (15k) nor tier 1 (92k)
    cold = read["e_cold"]
    assert cold["class"] == "miss-like" and cold["shape"] == "unimodal" and cold["state"] in ("steady", "flat")
    assert read["R_cold_iops"] == pytest.approx(9800, rel=0.05) and read["R_cold_iops"] < 12000
    # the largest rung mixes the tiers, so E2 (bimodal) disagrees with the cold probe: SPEC CCH-05 says undetermined, unproven
    assert read["E2"]["shape"] == "bimodal" and read["E2"]["R_E2_iops"] < 0.5 * read["R_cold_iops"]
    assert (read["cache_state"], read["status"]) == ("undetermined", "unproven") and "cache_model_undetermined" in read["reasons"]
    assert read["E1"]["fit"] == "insufficient"  # no cliff in a two-tier ladder: E1 never decides
    assert cal["ladder"]["cliff_bytes"] is None
    status, reasons, _ = key_reasons(out.result, "caps.RIOPS")
    assert status == "unproven" and "cache_model_undetermined" in reasons
    assert out.result["proposals"]["caps"]["dev_aggregate"]["RIOPS"]["value"] <= round(0.7 * read["R_cold_iops"]) + 1  # never above the cold rate
    # the write proof: a 4 s burst, then the sustained rate; the proof counts the bytes AFTER the knee
    proof = cal["cache"]["write"]["time_proof"]
    assert 3 <= proof["knee_s"] <= 6 and proof["burst_bytes"] > 5e8 and proof["proven"] is True
    sustained = cal["cap_basis"]["write"]["inputs"]["obs_write_steady"]
    assert proof["after_knee_bytes"] == pytest.approx(sustained * (proof["observation_s"] - proof["knee_s"]) * 4096, rel=0.001)
    assert proof["after_knee_bytes"] >= 2 * proof["burst_bytes"]
    # the write cap is the sustained rate, never the burst (SPEC CAL-07), and with history it is proven
    wb = cal["cap_basis"]["write"]
    assert wb["value"] == min(v for v in wb["inputs"].values() if v is not None) and wb["value"] < 0.3 * wb["inputs"]["rewrite_iops"]
    assert key_reasons(out.result, "caps.WIOPS")[:2] == ("proven", set())


def test_rev_c_allowed_but_unsimulated_commands_and_unbounded_probe_units_are_refused():
    host = standard_host()
    with pytest.raises(HostError, match="discard_range"):
        host.run(["fallocate", "-p", "-o", "0", "-l", "1", "/x"])  # a hole punch outside discard_range is refused before it is simulated
    with pytest.raises(HostError, match="unsupported command"):
        host.run(["fio", "--name=x", "--filename=/dev/vda7", "--rw=read"])  # allowed shape, but a job runs only through spawn
    with pytest.raises(HostError, match="allowlist"):
        host.spawn(["systemd-run", "--wait", "python3", "-c", "pass"])  # a probe unit without RuntimeMaxSec
