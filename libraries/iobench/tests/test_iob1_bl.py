"""IOB-BL-11/13/14/16/28/31: safety refusals and cleanups that must fail closed."""
import pytest

from iobench import host as host_mod
from iobench.errors import Refusal, RunFailed

from iobench.matrix import Matrixer

from helpers import calibrate, config, make_host, matrix
from test_mat import calibrated  # noqa: F401  (fixture)
from test_tgt import file_host


def test_bl16_a_vanished_diskstats_row_fails_the_run_not_a_stop_iteration(calibrated):
    host = calibrated
    host.dynamic["/proc/diskstats"] = lambda: "   8  0 sdz 1 0 8 0 0 0 0 0 0 0 0\n"
    out = matrix(host)
    assert out.status == "failed" and "no row for" in out.message
    m = Matrixer(host, config())
    m.workdir = type("W", (), {"disk_name": "sda"})()
    with pytest.raises(RunFailed, match="no row for sda"):
        m.written_bytes()


def test_bl11_a_file_target_on_a_filesystem_without_a_block_device_is_refused_before_any_job():
    host = file_host()
    host.mounts = [(ns, "0:99" if mp == "/bench" else dev, mp, src, "overlay" if mp == "/bench" else fs, opts)
                   for ns, dev, mp, src, fs, opts in host.mounts]
    before = len(host.argv_log)
    with pytest.raises(Refusal) as err:
        calibrate(host, target="/bench/target.img", work_dir="/bench/w", installer=True)
    assert err.value.condition == "target_fs_disk_unknown"
    assert not any(a[0] == "fio" and len(a) > 2 for a in host.argv_log[before:])
    assert not host.exists("/run/iobench/restore.json")  # the lock/journal were not left behind


class _Popen:
    def __init__(self, argv, **kw):
        self.pid, self.returncode = 4242, 0

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


def test_bl14_kill_closes_the_temporary_files(monkeypatch):
    monkeypatch.setattr(host_mod.subprocess, "Popen", _Popen)
    monkeypatch.setattr(host_mod, "_PRCTL", object())
    handle = host_mod._ProcHandle(["true"])
    out, err = handle._out, handle._err
    handle.kill()
    assert out.closed and err.closed
    handle.kill()  # idempotent


def test_iob_bl_28_fakehost_injected_exclusive_conflict_blocks_raw_mutation():
    from iobench.calibrate import Calibrator

    host = make_host()
    runner = Calibrator(host, config())
    runner.s0(need_workdir=False)
    runner.allowed_writes.add(0, 1 << 20)
    writer = runner.job("exclusive-probe", "randwrite", 4096, 1, 1, 0, 1 << 20, io_size=4096)
    # Inject the O_EXCL busy classification after init; this does not model an ordinary open descriptor.
    host.exclusive_device_conflicts.add("/dev/vda7")
    before_spawns = host.fio_spawns
    try:
        with pytest.raises(host_mod.HostError, match="injected exclusive block-device conflict"):
            runner.spawn(writer)
        with pytest.raises(host_mod.HostError, match="injected exclusive block-device conflict"):
            runner.discard((0, 1 << 20))
        assert host.devices["/dev/vda7"].write_bytes == 0
        assert not any(kind == "discard" for kind, _, _ in host.devices["/dev/vda7"].log)
        assert host.fio_spawns == before_spawns
    finally:
        runner.safety.release()


def test_iob_bl_28_realhost_classifies_o_excl_busy_and_never_launches_mutators(monkeypatch):
    """Monkeypatched EBUSY checks classification and fail-closed flow, not kernel opener semantics."""
    import errno
    from iobench import devio
    from iobench.host import RealHost, TargetRef
    from iobench.testing import R1002_PARTUUID, R1002_SIZE

    host = host_mod.RealHost()
    host.allow_scratch("/run/iobench")
    monkeypatch.setattr(host, "_verify_live", lambda _target: None)
    open_calls, closes, launches = [], [], []
    o_excl_busy = True

    def exclusive_open(path, flags):
        nonlocal o_excl_busy
        open_calls.append((path, flags))
        if o_excl_busy:
            raise OSError(errno.EBUSY, "Device or resource busy")
        return 71

    monkeypatch.setattr(host_mod.os, "open", exclusive_open)
    monkeypatch.setattr(host_mod.os, "close", closes.append)
    monkeypatch.setattr(host_mod, "_ProcHandle", lambda argv: launches.append(argv))
    monkeypatch.setattr(host, "_run_polled", lambda argv, timeout: launches.append(argv))
    ref = TargetRef("device", "/dev/vda7", R1002_SIZE, devno="254:7", partuuid=R1002_PARTUUID)
    job = devio.FioJob("exclusive-probe", ref.path, "randwrite", 4096, 1, 1, 0, 1 << 20, io_size=4096,
                       device_guard=True, prefix="/run/iobench/exclusive-probe")

    with pytest.raises(Refusal) as blocked:
        host.write_range(job.argv(), ref, 0, 1 << 20)
    assert blocked.value.condition == "device_in_use"
    assert open_calls[-1][1] & host_mod.os.O_EXCL
    assert open_calls[-1][1] & host_mod.os.O_ACCMODE == host_mod.os.O_RDONLY
    o_excl_busy = False
    with pytest.raises(Refusal) as unsupported:
        host.write_range(job.argv(), ref, 0, 1 << 20)
    assert unsupported.value.condition == "raw_device_write_unsupported"
    assert closes == [71]
    with pytest.raises(Refusal) as discard:
        host.discard_range(devio.blkdiscard_argv(ref.path, 0, 1 << 20), ref, 0, 1 << 20)
    assert discard.value.condition == "raw_device_write_unsupported"
    assert not launches
    assert len(open_calls) == 3


@pytest.mark.parametrize("fs_type", ("ext4", "xfs", "btrfs"))
def test_iob_bl_31_fakehost_extent_allowlist_accepts_verified_types(fs_type):
    import dataclasses
    from iobench import guard

    host = file_host()
    target = guard.load_target(host, "/bench/target.img")
    ref = dataclasses.replace(target.ref, fs_type=fs_type)
    host.devices[ref.path].written.add(0, 1 << 20)
    assert host.written_bytes(ref, 0, target.usable_bytes) == 1 << 20


def test_iob_bl_31_unverified_filesystem_omits_written_bytes_with_a_status(monkeypatch):
    import dataclasses
    from iobench import cli, guard
    from iobench.host import RealHost, TargetRef

    host = file_host()
    target = dataclasses.replace(guard.load_target(host, "/bench/target.img"), fs_type="zfs")
    host.devices[target.path].written.add(0, 1 << 20)
    status = cli._target_status(host, target)
    assert host.written_bytes(target.ref, 0, target.usable_bytes) is None
    assert status["written_bytes"] is None and status["unwritten_bytes"] is None
    assert "filesystem type 'zfs' is not verified" in status["written_bytes_status"]
    real = RealHost()
    monkeypatch.setattr(real, "_verify_live", lambda _target: pytest.fail("unverified filesystem reached SEEK_DATA"))
    assert real.written_bytes(TargetRef("file", "/tmp/not-extents", 8 << 20, fs_type="zfs"), 0, 7 << 20) is None


def test_iob_bl_31_btrfs_target_status_refuses_when_a_second_device_appears():
    from iobench import guard
    from test_tgt import btrfs_file_host

    host, uuid = btrfs_file_host()
    target = guard.init_file(host, "/bench/target.img", size=6 * (1 << 30), own=True, expect_fs_uuid=uuid,
                             confirm_quiet_window=False, allow_large_root_target=False, created="2026-10-21T03:00:00Z")
    devices = f"/sys/fs/btrfs/{uuid}/devices"
    host.symlinks[f"{devices}/2"] = host.realpath("/sys/dev/block/254:7")
    with pytest.raises(Refusal) as err:
        guard.load_file_target(host, target.path)
    assert err.value.condition == "target_fs_multi_device"
