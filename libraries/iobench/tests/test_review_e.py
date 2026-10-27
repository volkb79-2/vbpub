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
def scratch_host():
    host = standard_host()
    host.makedirs("/bench/w")
    return host


def test_rev_e_probe_unit_argv_passes_and_only_that_shape_does():
    host = scratch_host()
    check_argv(unit_argv(), host=host)
    check_argv(unit_argv(unit="iobench-probe-run-20261027T120000Z-a1b2-s4-rep-1", seconds=1), host=host)
    with pytest.raises(HostError):
        check_argv(unit_argv())  # without a host no directory is authorised
    bad = [
        unit_argv(unit="game-server"),  # not a probe unit
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
        # the script slot is THE probe script, byte for byte: a unit runs as root
        unit_argv(script="pass"),
        unit_argv(script=sweep.PROBE_SOURCE + "#"),
        unit_argv(script="import os; os.system('id')"),
        unit_argv(script=sweep.PROBE_SOURCE.replace("os.unlink(path)", "os.system('id')")),
        # every fixed slot is a literal: an option token in the `-p` slot or the property slot is refused
        ["systemd-run", "--wait", "--unit=iobench-probe-x", "--slice=iobench-probe.slice", "--collect", "--uid=0", "RuntimeMaxSec=90",
         "python3", "-c", sweep.PROBE_SOURCE, *PROBE_ARGS],
        ["systemd-run", "--wait", "--unit=iobench-probe-x", "--slice=iobench-probe.slice", "--collect", "--setenv=LD_PRELOAD=/x", "RuntimeMaxSec=90",
         "python3", "-c", sweep.PROBE_SOURCE, *PROBE_ARGS],
        ["systemd-run", "--wait", "--unit=iobench-probe-x", "--slice=iobench-probe.slice", "--collect", "-p", "ExecStartPre=/bin/sh",
         "python3", "-c", sweep.PROBE_SOURCE, *PROBE_ARGS],
        ["systemd-run", "--uid=0", "--unit=iobench-probe-x", "--slice=iobench-probe.slice", "--collect", "-p", "RuntimeMaxSec=90",
         "python3", "-c", sweep.PROBE_SOURCE, *PROBE_ARGS],
        ["systemd-run", "--wait", "--unit=iobench-probe-x", "--slice=iobench-probe.slice", "--setenv=A=1", "-p", "RuntimeMaxSec=90",
         "python3", "-c", sweep.PROBE_SOURCE, *PROBE_ARGS],
        unit_argv(args=["/bench/w/../w", *PROBE_ARGS[1:]]),
    ]
    for argv in bad:
        assert not is_allowed_argv(argv), argv
        with pytest.raises(HostError):
            check_argv(argv, host=host)


def test_rev_e_probe_unit_paths_are_bound_to_the_scratch_roots():
    host = scratch_host()
    host.symlinks["/bench/w/esc"] = "/etc"
    for args in (["/etc", *PROBE_ARGS[1:]],  # directory outside the roots
                 ["/bench/w", "/etc/.iobench-probe-x", "60", "0.5"],  # output outside the roots
                 ["/bench/w", "/bench/w/out", "60", "0.5"],  # output not iobench-named
                 ["/bench/w/esc", "/bench/w/.iobench-probe-x", "60", "0.5"],  # a symlink escape resolves outside
                 ["/bench/w", "/bench/w/esc/.iobench-probe-x", "60", "0.5"],
                 ["/bench/missing", *PROBE_ARGS[1:]]):  # the directory must exist
        argv = unit_argv(args=args)
        assert is_allowed_argv(argv)  # the shape is fine; the binding is the Host's
        with pytest.raises(HostError, match="scratch"):
            check_argv(argv, host=host)


# -- fio: only the four planned workloads; a writer only through write_range unless it is a load file --------------------------------
@pytest.mark.parametrize("rw", ["trim", "trimwrite", "randtrim", "rw", "randrw", "readwrite", "write_", ""])
def test_rev_e_fio_rw_outside_the_planned_workloads_is_refused(rw):
    assert not is_allowed_argv(["fio", "--name=x", f"--rw={rw}", "--filename=/dev/vda7"])


def test_rev_e_fio_without_rw_is_a_read_and_passes():
    check_argv(["fio", "--ioengine=libaio", "--iodepth=32", "--filename=/dev/vda7"])
    check_argv(["fio", "--version"])
    check_argv(["fio", "--cmdhelp=exclusive_open"])


@pytest.mark.parametrize("token", ["--offset=-5", "--size=1k", "--bs=", "--iodepth=0x10", "--runtime=3.5", "--direct=2", "--exclusive_open=yes",
                                   "--numjobs=+1", "--io_size=1G", "--rate_iops=-1", "--fsync=1e3", "--log_avg_msec= 1", "--ioengine=sync",
                                   "--output-format=terse", "--filename=rel/x", "--filename=/a:/b", "--filename=/a,b", "--filename=/a b",
                                   "--filename=/a/../b", "--filename=/a//b", "--filename=/a/", "--write_bw_log=rel", "--write_hist_log=/x\n"])
def test_rev_e_fio_values_are_typed_in_the_shape_check(token):
    argv = ["fio", "--name=x", "--filename=/bench/w/x", "--rw=read", token]
    if token.startswith("--filename="):
        argv = ["fio", "--name=x", "--rw=read", token]
    assert not is_allowed_argv(argv), argv
    assert is_allowed_argv(["fio", "--name=x", "--filename=/bench/w/x", "--rw=read", "--offset=5", "--direct=1", "--ioengine=libaio"])


def test_rev_e_unbound_fio_writer_on_anything_but_a_load_file_is_refused():
    host = scratch_host()
    host.put("/bench/w/iobench-existing", "x")
    host.symlinks["/bench/w/esc"] = "/etc"
    host.symlinks["/bench/w/iobench-link"] = "/etc/passwd"
    host.devices["/bench/w/iobench-node"] = FakeDevice("/bench/w/iobench-node", GIB, devno="8:0")
    for name in (DEV, "/bench/target.img", "/bench/w/data", "/etc/passwd",
                 "/etc/iobench-x",  # the right name in the wrong directory
                 "/dev/mapper/iobench-lv",  # an LVM volume that happens to carry the name
                 "/bench/w/xiobench-y",  # the name is anchored on the whole basename
                 "/bench/w/iobench-x/y",
                 "/bench/w/esc/iobench-x",  # a symlink escape resolves outside the root
                 "/bench/w/iobench-link",  # a symlink in place of the load file
                 "/bench/w/iobench-node",  # a device node in place of the load file
                 "/bench/missing/iobench-x"):  # not inside a root that exists? (the parent resolves under /bench: allowed by root, so the file rule decides)
        argv = writer(name, exclusive=name == DEV)
        if name == "/bench/missing/iobench-x":
            check_argv(argv, host=host)  # a new file under an authorised root: that is what a load file is
            continue
        assert is_allowed_argv(argv), name  # the shape is fine: it is the binding that is missing
        with pytest.raises(HostError, match="write_range"):
            check_argv(argv, host=host)
        with pytest.raises(HostError):
            check_argv(argv)  # and never without a host
    for name in ("/dev/sda:/bench/w/iobench-x", "/bench/w/iobench-x,/dev/sda", "/bench/w/iobench-x\n", "/bench/w/iobench-x\x7f", "/bench/w/../iobench-x"):
        argv = writer(name)
        assert not is_allowed_argv(argv), name  # fio splits a file list on ':' (and the rest is never a clean path): refused by shape
    check_argv(writer(DEV, exclusive=True), bound=True, host=host)  # write_range has verified the target and the extent
    check_argv(writer("/bench/w/iobench-run-20261027T120000Z-a1b2-s3-w"), host=host)  # the matrix load files
    check_argv(writer("/bench/w/iobench-run-x-s3-f", "randwrite"), host=host)
    check_argv(writer("/bench/w/.iobench-x"), host=host)
    check_argv(writer("/bench/w/iobench-existing"), host=host)  # an existing regular file (a re-run cell) is fine
    for rw in ("read", "randread"):
        check_argv(writer("/bench/w/data", rw), host=host)  # reads are not bound


def test_rev_e_fio_log_prefixes_are_bound_to_the_run_directory_bound_or_not():
    host = scratch_host()
    base = ["fio", "--name=x", "--filename=/dev/vda7", "--rw=read", "--ioengine=libaio"]
    for prefix in ("/etc/passwd", "/run/iobench/../x", "/tmp/x", "/bench/w/esc/x"):
        host.symlinks["/bench/w/esc"] = "/etc"
        for key in ("--write_bw_log", "--write_iops_log", "--write_hist_log"):
            for bound in (False, True):
                with pytest.raises(HostError, match="log prefix|allowlist"):
                    check_argv([*base, f"{key}={prefix}"], bound=bound, host=host)
    check_argv([*base, "--write_bw_log=/run/iobench/run-x/s1"], host=host)
    check_argv([*base, "--write_hist_log=/run/iobench/run-x/s1", "--write_iops_log=/run/iobench/run-x/s1"], host=host)
    with pytest.raises(HostError):
        check_argv([*base, "--write_bw_log=/run/iobench/run-x/s1"])  # no host, no root


def test_rev_e_scratch_name_is_anchored_on_the_whole_basename():
    from iobench.host import is_scratch_name
    assert is_scratch_name("/bench/w/iobench-run-x-w") and is_scratch_name("/bench/w/.iobench-probe") and is_scratch_name("iobench-")
    for bad in ("/bench/w/xiobench-y", "/bench/w/iobench-x/y", "/bench/w/iobench x", "/bench/w/iobench-x:y", "/bench/w/", "/bench/w/iobench", "/iobenchx"):
        assert not is_scratch_name(bad), bad
    doc = {"run": "r", "started": "s", "entries": [], "files": ["/bench/w/xiobench-y"], "coupled": {}}
    import json
    with pytest.raises(Refusal) as err:
        restore.parse_journal(json.dumps(doc))
    assert err.value.condition == "journal_corrupt"
    doc["files"] = ["/bench/w/iobench-y"]
    assert restore.parse_journal(json.dumps(doc))["files"] == doc["files"]


def test_rev_e_fakehost_refuses_unbound_writers_like_realhost():
    host = scratch_host()
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
    path = "/sys/fs/cgroup/game-server.slice/io.pressure"

    def gone():
        raise FileNotFoundError(path)

    host.at(host.now() + 200, lambda: host.dynamic.__setitem__(path, gone))
    out = matrix(host)
    assert out.status == "aborted" and out.exit_code == 8 and "abort guard" in out.message
    assert out.result["restore"]["verified"]


# -- preflight inputs ----------------------------------------------------------------------------------------------------------
def test_rev_e_invalid_production_pattern_is_a_refusal():
    host = standard_host()
    host.dynamic["/sys/fs/cgroup/game-server.slice/io.pressure"] = lambda: "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
    with pytest.raises(Refusal) as err:
        restore.live_preflight(host, confirm_quiet_window=False, installer=False, production_pattern="(", production_units=[],
                               protect_cgroup="game-server.slice", abort_psi=40.0)
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


# -- RealHost: the bindings run before any process starts (a fake subprocess layer; no real fio, fallocate or systemd-run) ------------
class _FakePopen:
    calls: list = []

    def __init__(self, argv, **kw):
        _FakePopen.calls.append(list(argv))
        self.pid, self.returncode = 4242, 0

    def communicate(self, timeout=None):
        return "", ""

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


def _file_target(tmp_path):
    import os
    from iobench.host import TAIL_BYTES, TargetRef
    path = tmp_path / "target.img"  # a file target: not a load-file name, so only write_range may write it
    path.touch()
    size = 3 * (1 << 20)
    os.truncate(path, size)
    return str(path), TargetRef("file", str(path), size, inode=os.stat(path).st_ino), TAIL_BYTES


def test_rev_e_realhost_write_range_and_discard_range_bind_before_any_process_starts(tmp_path, monkeypatch):
    from iobench import host as host_mod
    from iobench.host import RealHost
    calls = []
    real_check = host_mod.check_argv
    monkeypatch.setattr(host_mod, "check_argv", lambda argv, **kw: (calls.append(kw), real_check(argv, **kw)))
    monkeypatch.setattr(host_mod.subprocess, "Popen", _FakePopen)
    _FakePopen.calls = []
    path, ref, tail = _file_target(tmp_path)
    mib = 1 << 20
    host = RealHost()
    host.allow_scratch(str(tmp_path))
    good = devio.FioJob("w", path, "write", mib, 1, 5, 0, mib, prefix=str(tmp_path / "w"))
    # refusals raise before Popen: wrong extent, a log prefix outside the root, a tail overlap, a foreign discard argv
    for argv, off, length in ((good.argv(), mib, mib), (good.with_(prefix="/etc/x").argv(), 0, mib),
                              (good.with_(size=3 * mib - tail + 1).argv(), 0, 3 * mib - tail + 1)):
        with pytest.raises(HostError):
            host.write_range(argv, ref, off, length)
    with pytest.raises(HostError):
        host.discard_range(devio.punch_hole_argv(path, 0, 2 * mib), ref, 0, mib)
    assert _FakePopen.calls == []
    # the bound calls reach the fake process layer, and each went through check_argv(bound=True, host=self)
    host.write_range(good.argv(), ref, 0, mib)
    out = host.discard_range(devio.punch_hole_argv(path, 0, mib), ref, 0, mib)
    assert out.returncode == 0 and _FakePopen.calls == [good.argv(), devio.punch_hole_argv(path, 0, mib)]
    assert calls[-2:] == [{"bound": True, "host": host}, {"bound": True, "host": host}]
    # the unbound entry points refuse the same writer and discard (never a process)
    for fn in (host.run, host.spawn):
        with pytest.raises(HostError):
            fn(good.argv())
    with pytest.raises(HostError):
        host.run(devio.punch_hole_argv(path, 0, mib))
    assert len(_FakePopen.calls) == 2


def test_rev_e_every_subprocess_call_site_sits_behind_a_checked_entry_point():
    import ast
    import inspect
    from iobench import host as host_mod
    tree = ast.parse(inspect.getsource(host_mod))
    sub_callers, proc_callers, polled_callers, checked = set(), set(), set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            f = call.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "subprocess" and f.attr in ("Popen", "run", "call", "check_output"):
                sub_callers.add(node.name)
            if isinstance(f, ast.Name) and f.id == "_ProcHandle":
                proc_callers.add(node.name)
            if isinstance(f, ast.Attribute) and f.attr == "_run_polled":
                polled_callers.add(node.name)
            if isinstance(f, ast.Name) and f.id == "check_argv":
                checked.add(node.name)
    assert sub_callers == {"_run_polled", "__init__", "_verify_live"}  # _ProcHandle.__init__; _verify_live runs the literal blkid probe
    assert proc_callers == {"spawn", "write_range"} and polled_callers == {"run", "discard_range"}
    assert {"run", "spawn", "write_range", "discard_range"} <= checked
    for name in ("os.system", "os.popen", "os.exec", "os.spawn", "os.posix_spawn"):
        assert name not in inspect.getsource(host_mod)
