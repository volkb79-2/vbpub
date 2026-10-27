"""Fable pre-release review (2026-10-27): host-level bindings that the Runner used to be the only guard of, work-dir footprint,
abort-guard failure, preflight input validation.  Each test was red before its fix."""
import pytest

from helpers import matrix
from iobench import devio, restore, sweep
from iobench.errors import Refusal
from iobench.host import HostError, check_argv, is_allowed_argv
from iobench.testing import GIB, FakeDevice, standard_host
from test_cov import calibrated

DEV = "/dev/vda7"
PROBE_ARGS = ["/bench/w", "/bench/w/.iobench-probe-run-1-s4-0", "60", "0.5"]


def unit_argv(unit="iobench-probe-run-1-s4-0", slice_name="iobench-probe.slice", seconds=90, script=sweep.PROBE_SOURCE, args=PROBE_ARGS):
    return devio.probe_unit_argv(unit, slice_name, seconds, script, args)


def writer(filename, rw="write", exclusive=False):
    return devio.FioJob("w", filename, rw, 1 << 20, 4, 5, 0, 1 << 30, prefix="/run/iobench/w", exclusive=exclusive).argv()


# -- the systemd-run argv shape is pinned (IOB-BL-06 "still open") -----------------------------------------------------------------
def test_rev_e_probe_unit_argv_passes_and_only_that_shape_does():
    check_argv(unit_argv())
    check_argv(unit_argv(unit="iobench-probe-run-20261027T120000Z-a1b2-s4-rep-1", seconds=1))
    bad = [
        unit_argv(unit="wings"),  # not a probe unit
        unit_argv(unit="iobench-probe-"),
        unit_argv(slice_name="system"),  # not a slice
        unit_argv(slice_name="iobench probe.slice"),
        unit_argv(args=PROBE_ARGS[:3]),  # the probe takes exactly four arguments
        unit_argv(args=[*PROBE_ARGS, "x"]),
        unit_argv(args=["w", *PROBE_ARGS[1:]]),  # relative directory
        unit_argv(args=[*PROBE_ARGS[:2], "sixty", "0.5"]),
        unit_argv(script=""),
        ["systemd-run", "-p", "RuntimeMaxSec=5", "sh", "-c", "true"],  # the old allowlist accepted any command with a RuntimeMaxSec
        ["systemd-run", "--wait", "--unit=iobench-probe-x", "--slice=iobench-probe.slice", "--collect", "-p", "RuntimeMaxSec=90",
         "-p", "ExecStartPre=/bin/true", "python3", "-c", "pass", *PROBE_ARGS],
        ["systemd-run", "--wait", "--unit=iobench-probe-x", "--slice=iobench-probe.slice", "--collect", "-p", "RuntimeMaxSec=90",
         "bash", "-c", "pass", *PROBE_ARGS],
        ["systemd-run", "--wait", "--unit=iobench-probe-x", "--slice=iobench-probe.slice", "--collect", "-p", "RuntimeMaxSec=abc",
         "python3", "-c", "pass", *PROBE_ARGS],
        ["systemd-run", "--wait", "--unit=iobench-probe-x", "--slice=iobench-probe.slice", "--no-block", "-p", "RuntimeMaxSec=90",
         "python3", "-c", "pass", *PROBE_ARGS],
    ]
    for argv in bad:
        assert not is_allowed_argv(argv), argv
        with pytest.raises(HostError):
            check_argv(argv)


# -- fio: only the four planned workloads; a writer only through write_range unless it is a load file --------------------------------
@pytest.mark.parametrize("rw", ["trim", "trimwrite", "randtrim", "rw", "randrw", "readwrite", "write_", ""])
def test_rev_e_fio_rw_outside_the_planned_workloads_is_refused(rw):
    assert not is_allowed_argv(["fio", "--name=x", f"--rw={rw}", "--filename=/dev/vda7"])


def test_rev_e_fio_without_rw_is_a_read_and_passes():
    check_argv(["fio", "--ioengine=libaio", "--iodepth=32", "--filename=/dev/vda7"])
    check_argv(["fio", "--version"])
    check_argv(["fio", "--cmdhelp=exclusive_open"])


def test_rev_e_unbound_fio_writer_on_anything_but_a_load_file_is_refused():
    for argv in (writer(DEV, exclusive=True), writer(DEV, "randwrite", exclusive=True), writer("/bench/target.img"),
                 writer("/bench/w/data"), writer("/etc/passwd")):
        assert is_allowed_argv(argv)  # the shape is fine: it is the binding that is missing
        with pytest.raises(HostError, match="write_range"):
            check_argv(argv)
        check_argv(argv, bound=True)  # write_range has verified the target and the extent
    check_argv(writer("/bench/w/iobench-run-20261027T120000Z-a1b2-s3-w"))  # the matrix load files
    check_argv(writer("/bench/w/iobench-run-x-s3-f", "randwrite"))
    check_argv(writer("/bench/w/.iobench-x"))
    for rw in ("read", "randread"):
        check_argv(writer("/bench/w/data", rw))  # reads are not bound


def test_rev_e_fakehost_refuses_unbound_writers_like_realhost():
    host = standard_host()
    with pytest.raises(HostError, match="write_range"):
        host.spawn(writer(DEV, exclusive=True))
    host.devices["/bench/iobench-t.img"] = FakeDevice("/bench/iobench-t.img", GIB, kind="file", inode=7, fs_uuid="u")
    with pytest.raises(HostError, match="write_range"):
        host.spawn(writer("/bench/iobench-t.img"))  # a load-file name on a target: the device model still insists on the binding
    assert not any(a[0] == "fio" and f"--filename={DEV}" in a for a in host.argv_log)  # refused before it was even logged


# -- a discard only through discard_range ------------------------------------------------------------------------------------------
def test_rev_e_discard_argv_is_refused_unless_bound():
    for argv in (devio.blkdiscard_argv(DEV, 0, 4096), devio.punch_hole_argv("/bench/t.img", 0, 4096)):
        assert is_allowed_argv(argv)
        with pytest.raises(HostError, match="discard_range"):
            check_argv(argv)
        check_argv(argv, bound=True)
    check_argv(devio.fallocate_argv("/bench/t.img", 4096))  # preallocation stays a plain run (target init)
    host = standard_host()
    with pytest.raises(HostError, match="discard_range"):
        host.run(devio.blkdiscard_argv(DEV, 0, 4096))
    with pytest.raises(HostError, match="discard_range"):
        host.run(devio.punch_hole_argv("/bench/t.img", 0, 4096))


# -- matrix: the work-dir footprint is one cell, not the whole run ------------------------------------------------------------------
def test_rev_e_matrix_load_files_are_removed_behind_each_cell():
    host = calibrated()
    seen = []
    real = host.spawn

    def spawn(argv):
        seen.append(sum(1 for f in host.files if f.startswith("/bench/w/iobench-")))
        return real(argv)

    host.spawn = spawn
    out = matrix(host)
    assert out.status == "complete"
    cells = len([a for a in host.argv_log if a[0] == "systemd-run"])
    assert cells >= 6 and max(seen) <= 2  # at most the cell's own two load files exist (they used to accumulate: 2 per cell)
    assert not any(f.startswith("/bench/w/iobench-") for f in host.files)


def test_rev_e_matrix_refuses_a_work_dir_without_a_block_device_behind_it():
    host = calibrated()
    host.add_mount("0:51", "/ov", "/dev/vda5", "overlay", "rw")
    host.set_fs("/ov", 100 * GIB, 100 * GIB)
    host.makedirs("/ov")
    before = len(host.argv_log)
    with pytest.raises(Refusal) as err:
        matrix(host, work_dir="/ov/w", installer=True)
    assert err.value.condition == "workdir_disk_unknown"
    assert not any(a[0] == "fio" and len(a) > 2 for a in host.argv_log[before:])  # refused in S0, before any job


# -- the abort guard cannot vanish silently --------------------------------------------------------------------------------------
def test_rev_e_an_unreadable_protect_cgroup_aborts_the_run():
    host = calibrated()
    path = "/sys/fs/cgroup/wings.slice/io.pressure"

    def gone():
        raise FileNotFoundError(path)

    host.at(host.now() + 200, lambda: host.dynamic.__setitem__(path, gone))
    out = matrix(host)
    assert out.status == "aborted" and out.exit_code == 8 and "abort guard" in out.message
    assert out.result["restore"]["verified"]


# -- preflight inputs ----------------------------------------------------------------------------------------------------------
def test_rev_e_invalid_production_pattern_is_a_refusal():
    host = standard_host()
    host.dynamic["/sys/fs/cgroup/wings.slice/io.pressure"] = lambda: "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
    with pytest.raises(Refusal) as err:
        restore.live_preflight(host, confirm_quiet_window=False, installer=False, production_pattern="(", production_units=[],
                               protect_cgroup="wings.slice", abort_psi=40.0)
    assert err.value.condition == "production_pattern_invalid"


def test_rev_e_a_missing_probe_binary_is_a_refusal_not_a_traceback():
    host = standard_host()

    def run(argv, timeout=None):
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    host.run = run
    with pytest.raises(Refusal) as err:
        devio.probe(host, "docker ps", devio.docker_ps_argv())
    assert err.value.condition == "probe_failed" and "docker ps" in str(err.value)


def test_rev_e_orphan_scan_survives_an_undecodable_cmdline():
    host = standard_host()
    host.put("/proc/4242/cmdline", "")

    def undecodable():
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    host.dynamic["/proc/4242/cmdline"] = undecodable
    host.put("/proc/4243/cmdline", "fio\0--name=run-x-fill\0")
    assert restore.orphan_fio(host) == ["4243"]


def test_rev_e_register_file_journals_each_file_once():
    host = standard_host()
    s = restore.Safety(host, "run-x", "2026-10-27T00:00:00Z")
    assert s.acquire() is None
    s.register_file("/bench/w/.iobench-probe")
    s.register_file("/bench/w/.iobench-probe")
    s.register_file("/bench/w/iobench-run-x-s3-w")
    assert s.files == ["/bench/w/.iobench-probe", "/bench/w/iobench-run-x-s3-w"]
    assert restore.parse_journal(host.read_text(restore.JOURNAL))["files"] == s.files
