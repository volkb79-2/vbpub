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
    with pytest.raises(HostError, match="preallocate"):
        check_argv(devio.fallocate_argv("/bench/t.img", 4096))  # preallocation has its own bound entry point (Host.preallocate)
    check_argv(devio.fallocate_argv("/bench/t.img", 4096), bound=True)
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
    assert proc_callers == {"spawn", "write_range"} and polled_callers == {"run", "discard_range", "preallocate"}
    assert {"run", "spawn", "write_range", "discard_range", "preallocate"} <= checked
    for name in ("os.system", "os.popen", "os.exec", "os.spawn", "os.posix_spawn"):
        assert name not in inspect.getsource(host_mod)


# -- round 3: every non-fio shape pinned to its builder; fallocate only through preallocate/discard_range; caps; scratch dir ---------
@pytest.mark.parametrize("argv", [
    ["fallocate", "-l", "1", "-p", "/dev/sda"], ["fallocate", "-l", "1", "-d", "/dev/sda"], ["fallocate", "-l", "1", "--punch-hole", "/x"],
    ["fallocate", "-l", "1", "-z", "/x"], ["fallocate", "-l", "1", "--dig-holes", "/x"], ["fallocate", "-d", "/x"], ["fallocate", "-z", "-l", "1", "/x"],
    ["fallocate", "-l", "1", "/x", "-p"], ["fallocate", "-l", "-1", "/x"], ["fallocate", "-l", "1", "x"], ["fallocate", "-p", "-o", "0", "-l", "1", "/x", "-d"],
    ["fallocate", "-p", "-o", "0", "-l", "1"], ["fallocate", "-n", "-l", "1", "/x"],
    ["blkid", "-p", "-o", "export", "/dev/x", "--probe-extra"], ["blkid", "-p", "-o", "export"], ["blkid", "-p", "-o", "export", "dev/x"],
    ["docker", "ps", "-a"], ["docker", "ps"], ["docker", "ps", "--format", "{{.ID}}"],
    ["systemctl", "is-active", "a", "b"], ["systemctl", "is-active"], ["systemctl", "is-active", "--all"], ["systemctl", "is-active", "-"],
    ["systemctl", "show", "-p", "ExecStart", "--value", "x"], ["systemctl", "show", "-p", "LoadState", "--value", "x", "y"],
    ["systemctl", "show", "-p", "LoadState", "x"], ["systemctl", "show", "--property=LoadState", "--value", "x"],
    ["wipefs", "-n", "/dev/x", "-a"], ["wipefs", "-n", "-p"], ["wipefs", "-n", "-f", "/dev/x"], ["swapon", "--show", "-a"], ["swapon", "--show=NAME"],
    ["blkdiscard", "-o", "0", "-l", "1", "/dev/x", "-f"], ["blkdiscard", "-o", "x", "-l", "1", "/dev/x"],
])
def test_rev_e_non_fio_shapes_are_pinned_to_their_builders(argv):
    assert not is_allowed_argv(argv), argv
    with pytest.raises(HostError):
        check_argv(argv, bound=True)


def test_rev_e_the_builders_themselves_still_pass():
    for argv in (devio.fallocate_argv("/x", 1), devio.punch_hole_argv("/x", 0, 1), devio.blkid_argv("/dev/x"), devio.wipefs_check_argv("/dev/x"),
                 devio.swapon_show_argv(), devio.docker_ps_argv(), devio.unit_active_argv("game-server.service"), devio.unit_loadstate_argv("a@b.service"),
                 devio.unit_state_argv("iobench-probe-x"), devio.unit_stop_argv("iobench-probe-x"), devio.blkdiscard_argv("/dev/x", 0, 1)):
        assert is_allowed_argv(argv), argv


def test_rev_e_fallocate_is_refused_unbound_and_preallocate_is_the_bound_way():
    host = scratch_host()
    for argv in (devio.fallocate_argv("/bench/w/iobench-x", 1), devio.fallocate_argv("/etc/x", 1), devio.punch_hole_argv("/bench/w/iobench-x", 0, 1)):
        assert is_allowed_argv(argv)
        with pytest.raises(HostError, match="preallocate"):
            host.run(argv)
    assert not any(a[0] == "fallocate" for a in host.argv_log)
    out = host.preallocate("/bench/t.img", 4096)
    assert out.returncode == 0 and host.argv_log[-1] == devio.fallocate_argv("/bench/t.img", 4096) and "/bench/t.img" in host.devices


def test_rev_e_realhost_preallocate_binds_and_refuses_a_symlink(tmp_path, monkeypatch):
    from iobench import host as host_mod
    from iobench.host import RealHost
    monkeypatch.setattr(host_mod.subprocess, "Popen", _FakePopen)
    _FakePopen.calls = []
    host = RealHost()
    path = tmp_path / "target.img"
    host.allow_scratch(str(tmp_path))
    assert host.preallocate(str(path), 4096).returncode == 0 and _FakePopen.calls == [devio.fallocate_argv(str(path), 4096)]
    link = tmp_path / "link.img"
    link.symlink_to("/etc/passwd")
    with pytest.raises(HostError, match="symlink"):
        host.preallocate(str(link), 4096)
    with pytest.raises(HostError):
        host.preallocate(str(tmp_path / "a:b"), 4096)  # the shape check still runs on the bound path
    with pytest.raises(HostError):
        host.run(devio.fallocate_argv(str(path), 4096))
    assert len(_FakePopen.calls) == 1


@pytest.mark.parametrize("seconds", [0, 601, 10 ** 9])
def test_rev_e_runtime_max_sec_is_positive_and_capped(seconds):
    argv = unit_argv(seconds=seconds)
    assert not is_allowed_argv(argv)
    argv = unit_argv(seconds=1)
    assert is_allowed_argv(argv) and is_allowed_argv(unit_argv(seconds=600))
    assert not is_allowed_argv(unit_argv(seconds=1, slice_name="-.slice")) and not is_allowed_argv(unit_argv(seconds=1, slice_name="--uid=0.slice"))
    assert not is_allowed_argv(["systemd-run", "--wait", "--unit=iobench-probe-x", "--slice=iobench-probe.slice", "--collect", "-p", "RuntimeMaxSec=090",
                                "python3", "-c", sweep.PROBE_SOURCE, *PROBE_ARGS])


@pytest.mark.parametrize("token", ["--runtime=7201", "--iodepth=257", "--bs=16777217", "--numjobs=9", "--log_avg_msec=60001", "--log_hist_msec=60001",
                                   "--log_hist_coarseness=7", "--rate_iops=10000001", "--fsync=1000001", "--offset=1125899906842625", "--size=1125899906842625",
                                   "--io_size=1125899906842625"])
def test_rev_e_fio_magnitudes_are_capped(token):
    base = ["fio", "--name=x", "--filename=/bench/w/x", "--rw=read"]
    assert not is_allowed_argv([*base, token])
    key, _, value = token.partition("=")
    assert is_allowed_argv([*base, f"{key}={int(value) - 1}"])


def test_rev_e_the_run_directory_is_removed_after_the_run():
    from helpers import INVERSION, calibrate, make_host
    host = make_host(cache=INVERSION)
    out = calibrate(host)
    assert out.status == "complete"
    assert not any(d.startswith("/run/iobench/run-") for d in host.dirs) and not any(f.startswith("/run/iobench/run-") for f in host.files)
    aborted = calibrated()
    _abort = aborted.at(aborted.now() + 200, lambda: aborted.put(restore.ABORT_FILE, ""))
    out = matrix(aborted)
    assert out.exit_code == 8
    assert not any(d.startswith("/run/iobench/run-") for d in aborted.dirs)  # the killed job's logs went with the directory
    host2 = scratch_host()
    host2.makedirs("/run/iobench/run-x")
    host2.put("/run/iobench/run-x/sub/keep", "x")  # a nested entry iobench did not create: the directory stays
    host2.put("/run/iobench/run-x/notes.txt", "x")  # not a fio log iobench named: left alone
    host2.put("/run/iobench/run-x/s1_bw.1.log", "x")  # a fio log of a killed job: removed
    host2.put("/run/iobench/run-x/s3-w_iops.2.log", "x")  # fio numbers the log per job (`.N.log`): the second job's log goes too
    host2.put("/run/iobench/run-x/s1_bw.log", "x")  # not fio's naming: left
    from iobench.stages import Runner
    from types import SimpleNamespace
    ns = SimpleNamespace(host=host2, scratch="/run/iobench/run-x", safety=SimpleNamespace(report=SimpleNamespace(warnings=[])))
    Runner.clear_scratch(ns)
    assert "/run/iobench/run-x" in host2.dirs and "/run/iobench/run-x/sub/keep" in host2.files and "/run/iobench/run-x/notes.txt" in host2.files
    assert "/run/iobench/run-x/s1_bw.1.log" not in host2.files and "/run/iobench/run-x/s3-w_iops.2.log" not in host2.files
    assert "/run/iobench/run-x/s1_bw.log" in host2.files and ns.safety.report.warnings == []
    ns.scratch = "/run/iobench/run-missing"
    Runner.clear_scratch(ns)
    assert ns.safety.report.warnings == []


def test_rev_e_a_failing_scratch_cleanup_never_skips_the_lock_release_or_masks_the_status():
    from helpers import INVERSION, calibrate, make_host
    from iobench.stages import Runner
    from types import SimpleNamespace
    host = scratch_host()
    host.makedirs("/run/iobench/run-x")
    host.put("/run/iobench/run-x/s1_bw.1.log", "x")
    host.remove = lambda path: (_ for _ in ()).throw(PermissionError(13, "Operation not permitted", path))
    ns = SimpleNamespace(host=host, scratch="/run/iobench/run-x", safety=SimpleNamespace(report=SimpleNamespace(warnings=[])))
    Runner.clear_scratch(ns)  # a failing unlink is a warning, never an exception out of cleanup's finally
    assert ns.safety.report.warnings and ns.safety.report.warnings[0].startswith("scratch_not_cleared: /run/iobench/run-x") and "PermissionError" in ns.safety.report.warnings[0]
    # a complete run whose directory cannot be emptied keeps its verdict and releases the lock
    host2 = make_host(cache=INVERSION)
    real_rmdir = host2.rmdir
    host2.rmdir = lambda path: (_ for _ in ()).throw(OSError(16, "busy", path))
    out2 = calibrate(host2)
    assert out2.status == "complete" and out2.exit_code == 0 and not host2.locks
    assert any(w.startswith("scratch_not_cleared") for w in out2.result["restore"]["warnings"])
    host2.rmdir = real_rmdir


def test_rev_e_preallocate_is_contained_and_never_a_device_node():
    host = scratch_host()
    for path in ("/dev/vda7", "/dev/sda", "/etc/target.img", "/bench/w/esc/t.img"):
        host.symlinks["/bench/w/esc"] = "/etc"
        with pytest.raises(HostError, match="regular file"):
            host.preallocate(path, 1)
    host.allow_scratch("/dev")
    with pytest.raises(HostError, match="regular file"):
        host.preallocate("/dev/vda7", 1)  # inside a root, but a device node
    assert not any(a[0] == "fallocate" for a in host.argv_log)
    assert host.preallocate("/bench/w/t.img", 4096).returncode == 0
    # target init authorises the verified directory itself and turns the Host's refusal into a refusal with a remedy
    from iobench import guard
    fresh = standard_host()
    with pytest.raises(Refusal) as err:
        guard.init_file(fresh, "/bench/t img", size=1 << 30, own=True, expect_fs_uuid="22222222-2222-2222-2222-222222222222",
                        confirm_quiet_window=False, allow_large_root_target=False, created="2026-10-28T00:00:00Z")
    assert err.value.condition == "path_unclean" and "/bench" in fresh.scratch_roots  # /bench was authorised BEFORE (the estate's): not this init's to revoke
    fresh.preallocate = lambda path, size: devio.RunResult(1, "", "boom")
    with pytest.raises(Refusal) as err:
        guard.init_file(fresh, "/bench/t.img", size=1 << 30, own=True, expect_fs_uuid="22222222-2222-2222-2222-222222222222",
                        confirm_quiet_window=False, allow_large_root_target=False, created="2026-10-28T00:00:00Z")
    assert err.value.condition == "fallocate_failed" and "/bench" in fresh.scratch_roots
    del fresh.preallocate
    assert guard.init_file(fresh, "/bench/t.img", size=1 << 30, own=True, expect_fs_uuid="22222222-2222-2222-2222-222222222222",
                           confirm_quiet_window=False, allow_large_root_target=False, created="2026-10-28T00:00:00Z").kind == "file"
    assert fresh.scratch_roots.count("/bench") == 1


def _init(host, path, **kw):
    from iobench import guard
    args = dict(size=1 << 30, own=True, expect_fs_uuid="22222222-2222-2222-2222-222222222222", confirm_quiet_window=False,
                allow_large_root_target=False, created="2026-10-28T00:00:00Z")
    args.update(kw)
    return guard.init_file(host, path, **args)


def test_rev_e_init_file_revokes_only_the_root_it_added():
    from iobench import guard
    from iobench.host import StatInfo
    host = standard_host()
    host.makedirs("/bench/sub")
    assert "/bench/sub" not in host.scratch_roots
    # a refused preallocation (path_unclean) and a failed one: the directory this init added is gone again, the estate's /bench stays
    with pytest.raises(Refusal) as err:
        _init(host, "/bench/sub/t img")
    assert err.value.condition == "path_unclean" and "/bench/sub" not in host.scratch_roots and "/bench" in host.scratch_roots
    host.preallocate = lambda path, size: devio.RunResult(1, "", "boom")
    with pytest.raises(Refusal) as err:
        _init(host, "/bench/sub/t.img")
    assert err.value.condition == "fallocate_failed" and "/bench/sub" not in host.scratch_roots and "/bench" in host.scratch_roots
    del host.preallocate
    # a stat that comes back empty after a "successful" fallocate is a refusal, not an AttributeError, and revokes too
    real_stat = host.stat
    host.stat = lambda path: None if path == "/bench/sub/t.img" else real_stat(path)
    with pytest.raises(Refusal) as err:
        _init(host, "/bench/sub/t.img")
    assert err.value.condition == "path_unclean" and "/bench/sub" not in host.scratch_roots
    # a symlink that appears AFTER the preallocation (the pre-check saw a plain path): refused on the post-stat, root revoked
    def swapped(path):
        if path == "/bench/sub/t2.img" and any(a[0] == "fallocate" and a[-1] == path for a in host.argv_log):
            return StatInfo(1 << 30, 5, 1, False, False, True)
        return real_stat(path)

    host.stat = swapped
    with pytest.raises(Refusal) as err:
        _init(host, "/bench/sub/t2.img")
    assert err.value.condition == "path_unclean" and "/bench/sub" not in host.scratch_roots
    host.stat = real_stat
    # a marker or sidecar write that raises leaves no root behind either, and the error itself is not swallowed
    host.write_marker = lambda ref, text: (_ for _ in ()).throw(HostError("marker write failed"))
    with pytest.raises(HostError, match="marker"):
        _init(host, "/bench/sub/t3.img")
    assert "/bench/sub" not in host.scratch_roots
    del host.write_marker
    host.write_atomic = lambda path, text, mode=0o644: (_ for _ in ()).throw(OSError(28, "No space left on device", path))
    with pytest.raises(OSError):
        _init(host, "/bench/sub/t4.img")
    assert "/bench/sub" not in host.scratch_roots
    del host.write_atomic
    # success keeps the root it added; a pre-authorised one is left as it was (count 1, never duplicated)
    assert _init(host, "/bench/sub/t5.img").kind == "file" and host.scratch_roots.count("/bench/sub") == 1
    assert _init(host, "/bench/t6.img").kind == "file" and host.scratch_roots.count("/bench") == 1
    # the pre-authorised directory survives a refused init inside it
    with pytest.raises(Refusal):
        _init(host, "/bench/sub/t img")
    assert "/bench/sub" in host.scratch_roots
    # a pseudo or volatile filesystem is never a target (or a work dir), before any path check
    for fstype in ("devtmpfs", "devpts", "proc", "sysfs", "tmpfs", "ramfs"):
        pseudo = standard_host()
        pseudo.add_mount("0:60", "/pseudo", fstype, fstype)
        pseudo.set_fs("/pseudo", 100 * GIB, 100 * GIB)
        with pytest.raises(Refusal) as err:
            guard.init_file(pseudo, "/pseudo/t.img", size=1 << 20, own=True, expect_fs_uuid=None, confirm_quiet_window=False,
                            allow_large_root_target=False, created="2026-10-28T00:00:00Z")
        assert err.value.condition == "fs_type" and not pseudo.scratch_roots[2:] and not any(a[0] == "fallocate" for a in pseudo.argv_log)
        with pytest.raises(Refusal) as err:
            guard.check_workdir(pseudo, "/pseudo/w", planned_volume=GIB, installer=True, confirm_quiet_window=True, target_disk=None)
        assert err.value.condition == "workdir_fs"


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_rev_e_init_file_revokes_added_root_and_reraises_base_exceptions(interrupt):
    host = standard_host()
    host.makedirs("/bench/sub")
    assert "/bench" in host.scratch_roots  # already authorised by the test estate
    assert "/bench/sub" not in host.scratch_roots
    failure = interrupt("marker write interrupted")

    def fail_marker(ref, text):
        raise failure

    host.write_marker = fail_marker
    with pytest.raises(interrupt) as err:
        _init(host, "/bench/sub/t.img")

    assert err.value is failure
    assert "/bench/sub" not in host.scratch_roots  # init_file added this root and revoked it
    assert "/bench" in host.scratch_roots  # init_file must keep the pre-authorised root


def test_rev_e_realhost_preallocate_refuses_a_device_node_and_an_unauthorised_place(tmp_path, monkeypatch):
    from iobench import host as host_mod
    from iobench.host import RealHost
    monkeypatch.setattr(host_mod.subprocess, "Popen", _FakePopen)
    _FakePopen.calls = []
    host = RealHost()
    with pytest.raises(HostError, match="regular file"):
        host.preallocate(str(tmp_path / "t.img"), 1)  # no root authorised
    host.allow_scratch(str(tmp_path))
    host.allow_scratch("/dev")
    with pytest.raises(HostError, match="regular file"):
        host.preallocate("/dev/null", 1)  # a device node inside an authorised root
    (tmp_path / "sub").symlink_to("/etc")
    with pytest.raises(HostError, match="regular file"):
        host.preallocate(str(tmp_path / "sub" / "t.img"), 1)  # a symlinked parent resolves outside
    assert _FakePopen.calls == []
    assert host.preallocate(str(tmp_path / "t.img"), 1).returncode == 0 and len(_FakePopen.calls) == 1


@pytest.mark.parametrize("name", ["x y", "", "../x", "x;id", "x/y", "x\n", "a=b", "x:y"])
def test_rev_e_fio_job_name_is_one_clean_token(name):
    assert not is_allowed_argv(["fio", f"--name={name}", "--filename=/bench/w/x", "--rw=read"])
    assert is_allowed_argv(["fio", "--name=run-20261027T120000Z-a1b2-s4-rep-0.ext", "--filename=/bench/w/x", "--rw=read"])
