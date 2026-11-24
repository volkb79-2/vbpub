"""IOB-TGT-12: raw-partition mutation under one held kernel O_EXCL claim."""
from __future__ import annotations

import dataclasses
import errno
import json
import os
import stat
import struct
import sys
from types import SimpleNamespace

import pytest
from helpers import INVERSION, config, job, make_host, target_ref
from helpers import calibrate as run_calibration

from iobench import calibrate, cli, devio, guard, restore, stages
from iobench import host as host_mod
from iobench.errors import Aborted, Refusal
from iobench.host import HostError, RealHost, TargetRef
from iobench.testing import GIB, R1002_PARTUUID, R1002_SIZE, FakeHost, standard_host

DEV = "/dev/vda7"
CREATED = "2026-10-21T03:00:00Z"
WRITE_REFUSAL = (
    f"refused: raw-device fio writes to {DEV} are disabled: fio opens the path itself and cannot use iobench's "
    "kernel O_EXCL claim for the whole write interval; use a file target; raw-device writes require a writer that "
    "performs I/O through the same held O_EXCL descriptor"
)
DISCARD_REFUSAL = (
    f"refused: raw-device discard on {DEV} is disabled: blkdiscard cannot use iobench's kernel O_EXCL claim for "
    "the whole discard interval; use a file target; raw-device mutation requires a writer that performs I/O through "
    "the same held O_EXCL descriptor"
)
PREAMBLE = "fio: cache invalidation of /dev/fd/3 failed: Permission denied"


def claimed_write_job(*, name: str = "claim-write") -> devio.FioJob:
    return job("randwrite", size=1 << 20, io=1 << 20, name=name)


def cleaned(host: FakeHost) -> None:
    assert host.claims == {} and host.claim_fds == {} and host.locks == {}


class RealClaim:
    """A real-host claim with only the device syscall boundary faked."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, fd: int = 71, block: bool = True,
                 rdev: int | None = None, size: int = R1002_SIZE) -> None:
        self.host = RealHost()
        self.host.allow_scratch("/run/iobench")
        self.fd = fd
        self.open_calls: list[tuple[str, int]] = []
        self.closed: list[int] = []
        real_open, real_close, real_fstat = os.open, os.close, os.fstat
        monkeypatch.setattr(self.host, "_verify_live", lambda _target: None)

        def fstat(candidate: int):
            if candidate != fd:
                return real_fstat(candidate)
            return SimpleNamespace(st_mode=stat.S_IFBLK if block else stat.S_IFREG,
                                   st_rdev=os.makedev(254, 7) if rdev is None else rdev, st_size=0)

        def open_device(path: str, flags: int, *args: object, **kwargs: object) -> int:
            if os.fspath(path) != DEV:
                return real_open(path, flags, *args, **kwargs)
            self.open_calls.append((os.fspath(path), flags))
            return fd

        def close_device(candidate: int) -> None:
            if candidate == fd:
                self.closed.append(candidate)
                return
            real_close(candidate)

        monkeypatch.setattr(host_mod.os, "fstat", fstat)
        monkeypatch.setattr(host_mod.os, "open", open_device)
        monkeypatch.setattr(host_mod.os, "close", close_device)
        monkeypatch.setattr(host_mod.fcntl, "ioctl", self.ioctl_size if size == R1002_SIZE else self._wrong_size_ioctl(size))
        self.ref = TargetRef("device", DEV, R1002_SIZE, devno="254:7", partuuid=R1002_PARTUUID)

    @staticmethod
    def ioctl_size(fd: int, request: int, buf: bytearray, mutate: bool) -> None:
        assert (fd, request, mutate) == (71, host_mod.BLKGETSIZE64, True)
        buf[:] = R1002_SIZE.to_bytes(8, "little")

    def _wrong_size_ioctl(self, size: int):
        def ioctl(fd: int, request: int, buf: bytearray, mutate: bool) -> None:
            assert (fd, request, mutate) == (self.fd, host_mod.BLKGETSIZE64, True)
            buf[:] = size.to_bytes(8, "little")
        return ioctl

    def claim(self) -> int:
        return self.host.claim(self.ref)


class LifecycleRunner(stages.Runner):
    """Exercise Runner.execute's raw-claim cleanup envelope without running a full calibration."""

    def __init__(self, host: FakeHost, cfg: stages.Config, action: str) -> None:
        super().__init__(host, cfg, command="calibrate")
        self.action = action

    def body(self) -> None:
        self.s0(need_workdir=False)
        if self.action == "normal":
            return
        if self.action == "refusal":
            raise Refusal("test_refusal", "the lifecycle test refused after taking the claim", "none")
        if self.action == "exception":
            raise RuntimeError("the lifecycle test raised after taking the claim")
        if self.action == "first-signal":
            writer = self.job("first-signal", "randwrite", 4096, 1, 10, 0, GIB, io_size=4096)
            self.allowed_writes.add(0, GIB)
            handle = self.spawn(writer)
            self.host.deliver_signal("SIGTERM")
            self.supervise([handle])
            return
        if self.action == "second-signal":
            self.host.pending = "SIGTERM"

            def second_signal(_host: FakeHost, _offset: int, _length: int) -> None:
                self.host.deliver_signal("SIGTERM")

            self.host.discard_hook = second_signal
            guard.verify_target(self.host, self.target)
            self.host.discard_range(devio.blkdiscard_argv(DEV, 0, GIB + 1), self.target.ref, 0, GIB + 1)
            return
        if self.action == "unreaped":
            self.host.unreaped_children.add(4242)
            return
        raise AssertionError(self.action)

    def conclude(self, status: str, message: str, code: int) -> stages.Outcome:
        return stages.Outcome(status, code, message, None, None)


def test_tgt_12_default_raw_write_and_discard_refusals_are_word_for_word():
    host = make_host(cache=INVERSION)
    runner = calibrate.Calibrator(host, config(raw_write=False))
    runner.s0(need_workdir=False)
    runner.allowed_writes.add(0, 1 << 20)
    writer = runner.job("default-refusal", "randwrite", 4096, 1, 1, 0, 1 << 20, io_size=4096)
    before = host.fio_spawns
    try:
        with pytest.raises(Refusal) as write_error:
            runner.spawn(writer)
        assert write_error.value.condition == "raw_device_write_unsupported"
        assert str(write_error.value) == WRITE_REFUSAL
        with pytest.raises(Refusal) as discard_error:
            runner.discard((0, 1 << 20))
        assert discard_error.value.condition == "raw_device_write_unsupported"
        assert str(discard_error.value) == DISCARD_REFUSAL
        assert host.fio_spawns == before
        assert not any(kind == "discard" for kind, _, _ in host.devices[DEV].log)
        assert host.claims == {}
    finally:
        runner.cleanup("complete", "", 0)
    cleaned(host)


def test_tgt_12_opt_in_claims_before_the_first_raw_stage_and_releases_after():
    host = make_host(cache=INVERSION)
    runner = calibrate.Calibrator(host, config(raw_write=True))
    runner.s0(need_workdir=False)
    assert host.claims[DEV] == target_ref(host)
    runner.allowed_writes.add(0, 1 << 20)
    writer = runner.job("claimed-write", "randwrite", 4096, 1, 1, 0, 1 << 20, io_size=4096)
    assert f"--filename={DEV}" in writer.argv()
    handle = runner.spawn(writer)
    handle.wait()
    launch, pass_fds = host.process_launches[-1]
    assert f"--filename={DEV}" not in launch
    assert launch.count("--filename=/dev/fd/9000") == 1
    assert pass_fds == (9000,)
    runner.cleanup("complete", "", 0)
    cleaned(host)


def test_tgt_12_opt_in_without_a_marker_never_claims():
    host = make_host(cache=INVERSION)
    host.devices[DEV].marker = None
    runner = calibrate.Calibrator(host, config(raw_write=True))
    try:
        with pytest.raises(Refusal) as err:
            runner.s0(need_workdir=False)
        assert err.value.condition == "no_marker"
        assert host.claims == {}
    finally:
        runner.safety.release()
    cleaned(host)


def test_tgt_12_a_claim_blocks_fake_mount_and_swapon_until_release():
    host = make_host(cache=INVERSION)
    host.add_swap("/dev/vda5")  # an unclaimed sibling exercises the already newline-terminated /proc/swaps path
    ref = target_ref(host)
    assert host.claim(ref) == 9000
    with pytest.raises(Refusal) as mount_error:
        host.add_mount("254:7", "/mnt/claimed", DEV)
    assert mount_error.value.condition == "device_in_use"
    with pytest.raises(Refusal) as swap_error:
        host.add_swap(DEV)
    assert swap_error.value.condition == "device_in_use"
    assert host.claims[DEV] == ref
    assert host.release_claim() is None
    host.add_mount("254:7", "/mnt/released", DEV)
    host.put("/proc/swaps", "Filename Type Size Used Priority")
    host.add_swap(DEV)
    assert "/mnt/released" in {entry[2] for entry in host.mounts}
    assert DEV in host.swap_lines()
    assert host.read_text("/proc/swaps").endswith("\n")


@pytest.mark.parametrize(
    ("disk", "partition", "major", "condition"),
    (
        ("dm-0", "dm-0p1", 253, "raw_target_dm"),
        ("md0", "md0p1", 9, "raw_target_md"),
        ("loop0", "loop0p1", 7, "raw_target_loop"),
    ),
)
def test_tgt_12_refuses_dm_md_and_loop_devices(disk, partition, major, condition):
    host = standard_host()
    host.add_disk(disk, major, 10 * GIB)
    dev = host.add_partition(disk, partition, major, 1, 4 * GIB, "virtual-partuuid")
    with pytest.raises(Refusal) as err:
        guard.init_device(host, dev.path, own=True, expect_size=dev.size, expect_partuuid=dev.partuuid,
                          expect_serial=None, created=CREATED)
    assert err.value.condition == condition
    assert dev.marker is None and host.claims == {}


def test_tgt_12_refuses_a_whole_disk_before_any_claim():
    host = standard_host()
    with pytest.raises(Refusal) as err:
        guard.init_device(host, "/dev/vda", own=True, expect_size=500 * GIB, expect_partuuid=None,
                          expect_serial=None, created=CREATED)
    assert err.value.condition == "raw_target_not_partition"
    assert "whole block device" in str(err.value)
    assert host.claims == {} and host.devices["/dev/vda"].log == []


def test_tgt_12_the_target_model_still_reports_a_mounted_child_of_a_whole_disk():
    host = standard_host()
    host.add_mount("254:7", "/mnt/child", DEV)
    facts = guard.read_device(host, "/dev/vda")
    found = guard.violations(host, facts)
    assert any(v.condition == "parent_of_mounted" for v in found)


def test_tgt_12_target_init_requires_explicit_ownership_even_for_a_blank_partition():
    host = standard_host()
    with pytest.raises(Refusal) as err:
        guard.init_device(host, DEV, own=False, expect_size=R1002_SIZE, expect_partuuid=R1002_PARTUUID,
                          expect_serial=None, created=CREATED)
    assert err.value.condition == "own_device_required"
    assert host.devices[DEV].marker is None
    assert host.claims == {} and host.devices[DEV].log == []


def test_tgt_12_target_init_reports_a_claim_release_problem_and_keeps_the_claim(monkeypatch):
    host = standard_host()

    def release() -> str:
        return "raw_claim_release_failed: synthetic close failure"

    monkeypatch.setattr(host, "release_claim", release)
    with pytest.raises(Refusal) as err:
        guard.init_device(host, DEV, own=True, expect_size=R1002_SIZE, expect_partuuid=R1002_PARTUUID,
                          expect_serial=None, created=CREATED)
    assert err.value.condition == "identity_changed"
    assert "raw_claim_release_failed" in str(err.value)
    assert host.claims[DEV] == target_ref(host)
    host.claims.clear()
    host.claim_fds.clear()


def test_tgt_12_refuses_serial_only_identity_and_load_uses_the_same_rule():
    host = standard_host()
    host.add_disk("vdb", 8, 10 * GIB, serial="SERIAL-ONLY")
    dev = host.add_partition("vdb", "vdb1", 8, 1, 4 * GIB, None)
    with pytest.raises(Refusal) as err:
        guard.init_device(host, dev.path, own=True, expect_size=dev.size, expect_partuuid=None,
                          expect_serial=dev.serial, created=CREATED)
    assert err.value.condition == "raw_target_partuuid_required"
    assert "serial alone" in str(err.value)
    dev.marker = guard.marker_text(dev.size, None, "SERIAL-ONLY", CREATED)
    with pytest.raises(Refusal) as loaded:
        guard.load_target(host, dev.path)
    assert loaded.value.condition == "raw_target_partuuid_required"
    assert host.claims == {}


@pytest.mark.parametrize(
    ("field", "value", "condition"),
    (
        ("size", 1, "raw_target_size_changed"),
        ("devno", "254:8", "raw_target_devno_changed"),
        ("partuuid", "different-partuuid", "raw_target_partuuid_changed"),
    ),
)
def test_tgt_12_live_identity_change_after_claim_has_its_own_refusal(field, value, condition):
    host = make_host(cache=INVERSION)
    ref = target_ref(host)
    host.claim(ref)
    writer = claimed_write_job(name=f"drift-{field}")
    setattr(host.devices[DEV], "size" if field == "size" else field, value)
    try:
        with pytest.raises(Refusal) as err:
            host.write_range(writer.argv(), ref, 0, writer.size)
        assert err.value.condition == condition
        assert host.claims[DEV] == ref
        assert host.fio_spawns == 0
    finally:
        host.release_claim()
    cleaned(host)


@pytest.mark.parametrize(
    ("field", "value", "condition"),
    (
        ("size_bytes", R1002_SIZE + 1, "raw_target_size_changed"),
        ("devno", "254:8", "raw_target_devno_changed"),
        ("partuuid", "different-partuuid", "raw_target_partuuid_changed"),
    ),
)
def test_tgt_12_a_changed_target_reference_cannot_reuse_the_claim(field, value, condition):
    host = make_host(cache=INVERSION)
    ref = target_ref(host)
    host.claim(ref)
    writer = claimed_write_job(name=f"reference-{field}")
    try:
        with pytest.raises(Refusal) as err:
            host.write_range(writer.argv(), dataclasses.replace(ref, **{field: value}), 0, writer.size)
        assert err.value.condition == condition
    finally:
        host.release_claim()
    cleaned(host)


def test_tgt_12_a_claim_is_idempotent_and_excludes_other_targets():
    host = make_host(cache=INVERSION)
    ref = target_ref(host)
    first = host.claim(ref)
    assert first == host.claim(ref)
    host.add_disk("vdb", 8, 10 * GIB)
    second = host.add_partition("vdb", "vdb1", 8, 1, 4 * GIB, "second-partuuid")
    with pytest.raises(HostError, match="another raw target"):
        host.claim(second.ref if hasattr(second, "ref") else TargetRef("device", second.path, second.size,
                                                                      devno=second.devno, partuuid=second.partuuid))
    with pytest.raises(HostError, match="only a block device"):
        host.claim(TargetRef("file", "/bench/t.img", 4 * GIB, inode=1, fs_uuid="uuid"))
    assert host.release_claim() is None
    host.exclusive_device_conflicts.add(DEV)
    with pytest.raises(Refusal) as busy:
        host.claim(ref)
    assert busy.value.condition == "device_in_use"
    cleaned(host)


def test_tgt_12_no_claim_refusal_covers_the_internal_mutation_entry():
    host = make_host(cache=INVERSION)
    ref = target_ref(host)
    with pytest.raises(Refusal) as err:
        host._claim_fd_for(ref)
    assert err.value.condition == "raw_device_write_unsupported"
    assert "without a matching held O_EXCL claim" in str(err.value)


def test_tgt_12_a_duplicate_launch_filename_is_never_rewritten_twice(monkeypatch):
    host = make_host(cache=INVERSION)
    ref = target_ref(host)
    host.claim(ref)
    writer = claimed_write_job(name="duplicate-filename")
    argv = [*writer.argv(), f"--filename={DEV}"]
    monkeypatch.setattr(host, "_log", lambda *_args, **_kwargs: None)
    try:
        with pytest.raises(HostError, match="exactly once"):
            host.write_range(argv, ref, 0, writer.size)
        assert host.process_launches == []
    finally:
        host.release_claim()
    cleaned(host)


def test_tgt_12_missing_live_device_and_same_shape_claim_mismatch_are_distinct():
    host = make_host(cache=INVERSION)
    ref = target_ref(host)
    writer = claimed_write_job(name="missing-live-device")
    host.claim(ref)
    del host.devices[DEV]
    with pytest.raises(HostError, match="no such target"):
        host.write_range(writer.argv(), ref, 0, writer.size)
    assert host.release_claim() is None
    cleaned(host)

    host = make_host(cache=INVERSION)
    ref = target_ref(host)
    host.claim(ref)
    try:
        with pytest.raises(Refusal) as err:
            host.write_range(writer.argv(), dataclasses.replace(ref, serial="other-serial"), 0, writer.size)
        assert err.value.condition == "raw_target_claim_mismatch"
    finally:
        host.release_claim()
    cleaned(host)


def test_tgt_12_marker_is_written_only_through_the_held_claim():
    host = make_host(cache=INVERSION)
    ref = target_ref(host)
    dev = host.devices[DEV]
    dev.marker = None
    with pytest.raises(HostError, match="only through"):
        host.write_marker(ref, "{}")
    assert dev.marker is None
    host.claim(ref)
    host.write_marker(ref, "{}")
    assert dev.marker == "{}"
    assert host.release_claim() is None
    cleaned(host)


def test_tgt_12_claim_table_reports_unreaped_children_and_keeps_the_claim():
    host = make_host(cache=INVERSION)
    ref = target_ref(host)
    assert host.release_claim() is None
    host.claim(ref)
    host.unreaped_children.update({4242, 99})
    problem = host.release_claim()
    assert problem == "raw_claim_child_unreaped: child pid(s) 99, 4242 still hold the claim"
    assert host.claims[DEV] == ref
    host.unreaped_children.clear()
    assert host.release_claim() is None
    cleaned(host)


def test_tgt_12_real_claim_has_the_exact_exclusive_open_and_identity_checks(monkeypatch):
    real = RealClaim(monkeypatch)
    assert real.claim() == 71
    path, flags = real.open_calls[-1]
    assert path == DEV
    assert flags & os.O_EXCL and flags & os.O_CLOEXEC
    assert flags & os.O_ACCMODE == os.O_RDWR
    assert real.host.claim(real.ref) == 71
    assert len(real.open_calls) == 1  # the idempotent second claim does not reopen
    assert real.host.release_claim() is None
    assert real.closed == [71]


@pytest.mark.parametrize("error_number", (errno.EBUSY, errno.EAGAIN))
def test_tgt_12_real_claim_maps_busy_and_again_to_device_in_use(monkeypatch, error_number):
    host = RealHost()
    monkeypatch.setattr(host_mod.os, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        OSError(error_number, os.strerror(error_number))))
    with pytest.raises(Refusal) as err:
        host.claim(TargetRef("device", DEV, R1002_SIZE, devno="254:7", partuuid=R1002_PARTUUID))
    assert err.value.condition == "device_in_use"


@pytest.mark.parametrize(
    ("case", "condition"),
    (
        ("not-block", "raw_target_not_block_device"),
        ("devno", "raw_target_devno_changed"),
        ("size", "raw_target_size_changed"),
        ("partuuid", "raw_target_partuuid_changed"),
    ),
)
def test_tgt_12_real_claim_rechecks_descriptor_identity_and_closes_on_refusal(monkeypatch, case, condition):
    real = RealClaim(monkeypatch, block=case != "not-block", rdev=os.makedev(254, 8) if case == "devno" else None,
                     size=7 if case == "size" else R1002_SIZE)
    if case == "partuuid":
        monkeypatch.setattr(real.host, "_verify_live",
                            lambda _target: (_ for _ in ()).throw(
                                Refusal("raw_target_partuuid_changed", "PARTUUID changed", "rerun target init")))
    with pytest.raises(Refusal) as err:
        real.claim()
    assert err.value.condition == condition
    assert real.closed == [71] and real.host._claim_fd is None


def test_tgt_12_real_marker_pwrite_uses_the_claim_descriptor(monkeypatch):
    real = RealClaim(monkeypatch)
    assert real.claim() == 71
    writes: list[tuple[int, int]] = []
    monkeypatch.setattr(host_mod.os, "pwritev", lambda fd, _buf, offset: writes.append((fd, offset)))
    monkeypatch.setattr(host_mod.os, "fsync", lambda _fd: None)
    real.host.write_marker(real.ref, "{}")
    assert writes == [(71, R1002_SIZE - host_mod.TAIL_BYTES)]
    assert [path for path, _flags in real.open_calls] == [DEV]
    assert real.host.release_claim() is None
    with pytest.raises(HostError, match="only through"):
        real.host.write_marker(real.ref, "{}")
    assert real.closed == [71]


def test_tgt_12_real_discard_chunks_at_one_gib_through_the_claim(monkeypatch):
    real = RealClaim(monkeypatch)
    assert real.claim() == 71
    ranges: list[tuple[int, int]] = []
    size_ioctl = real.ioctl_size

    def ioctl(fd: int, request: int, argument, *args):
        if request == host_mod.BLKGETSIZE64:
            return size_ioctl(fd, request, argument, True)
        assert fd == 71 and request == host_mod.BLKDISCARD and not args
        ranges.append(struct.unpack("QQ", argument))

    monkeypatch.setattr(host_mod.fcntl, "ioctl", ioctl)
    out = real.host.discard_range(devio.blkdiscard_argv(DEV, 0, 2 * GIB + 1), real.ref, 0, 2 * GIB + 1)
    assert out.returncode == 0
    assert ranges == [(0, 1 << 30), (1 << 30, 1 << 30), (2 << 30, 1)]
    assert real.host.release_claim() is None


def test_tgt_12_real_discard_checks_a_second_signal_between_chunks(monkeypatch):
    real = RealClaim(monkeypatch)
    assert real.claim() == 71
    calls = 0
    size_ioctl = real.ioctl_size

    def ioctl(fd: int, request: int, argument, *args):
        nonlocal calls
        if request == host_mod.BLKGETSIZE64:
            return size_ioctl(fd, request, argument, True)
        calls += 1
        real.host._escalated = True
        return None

    monkeypatch.setattr(host_mod.fcntl, "ioctl", ioctl)
    with pytest.raises(Aborted, match="second signal"):
        real.host.discard_range(devio.blkdiscard_argv(DEV, 0, 2 * GIB), real.ref, 0, 2 * GIB)
    assert calls == 1
    assert real.host.release_claim() is None


@pytest.mark.parametrize(
    ("error_number", "expected"),
    ((errno.EOPNOTSUPP, "Operation not supported"), (errno.EIO, "BLKDISCARD failed")),
)
def test_tgt_12_real_discard_names_unsupported_and_other_errors(monkeypatch, error_number, expected):
    real = RealClaim(monkeypatch)
    assert real.claim() == 71
    size_ioctl = real.ioctl_size

    def ioctl(fd: int, request: int, argument, *args):
        if request == host_mod.BLKGETSIZE64:
            return size_ioctl(fd, request, argument, True)
        raise OSError(error_number, os.strerror(error_number))

    monkeypatch.setattr(host_mod.fcntl, "ioctl", ioctl)
    if error_number == errno.EOPNOTSUPP:
        out = real.host.discard_range(devio.blkdiscard_argv(DEV, 0, GIB), real.ref, 0, GIB)
        assert out.returncode == 1 and out.stderr == expected
    else:
        with pytest.raises(HostError) as err:
            real.host.discard_range(devio.blkdiscard_argv(DEV, 0, GIB), real.ref, 0, GIB)
        assert expected in str(err.value) and "Input/output error" in str(err.value)
    assert real.host.release_claim() is None


def test_tgt_12_fake_discard_chunks_and_aborts_on_a_second_signal_between_chunks():
    host = make_host(cache=INVERSION)
    ref = target_ref(host)
    host.claim(ref)
    host.pending = "SIGTERM"
    chunks: list[tuple[int, int]] = []

    def second_signal(owner: FakeHost, offset: int, length: int) -> None:
        chunks.append((offset, length))
        owner.deliver_signal("SIGTERM")

    host.discard_hook = second_signal
    try:
        with pytest.raises(Aborted, match="second signal"):
            host.discard_range(devio.blkdiscard_argv(DEV, 0, GIB + 1), ref, 0, GIB + 1)
        assert chunks == [(0, 1 << 30)]
        assert host.claims[DEV] == ref
    finally:
        host.release_claim()
    cleaned(host)


def test_tgt_12_fake_discard_names_unsupported_without_mutating():
    host = make_host(cache=INVERSION)
    ref = target_ref(host)
    host.claim(ref)
    host.devices[DEV].discard_supported = False
    out = host.discard_range(devio.blkdiscard_argv(DEV, 0, GIB), ref, 0, GIB)
    assert out.returncode == 1 and out.stderr == "Operation not supported"
    assert host.devices[DEV].write_bytes == 0
    assert host.release_claim() is None
    cleaned(host)


def test_tgt_12_fio_device_preamble_is_a_warning_not_a_parse_failure():
    host = make_host(cache=INVERSION)
    runner = calibrate.Calibrator(host, config(raw_write=True))
    runner.s0(need_workdir=False)
    runner.read_extent.add(0, 4096)
    host.devices[DEV].written.add(0, 4096)
    host.fio_preamble = PREAMBLE + "\n"
    read = runner.job("preamble-read", "randread", 4096, 1, 1, 0, 4096)
    handle = runner.spawn(read)
    result = runner.finish(read, handle, host.now())
    assert result.stats is not None and result.warnings == (PREAMBLE,)
    assert f"fio_preamble: {PREAMBLE}" in runner.safety.report.warnings
    runner.cleanup("complete", "", 0)
    cleaned(host)


def test_tgt_12_fio_output_split_requires_json_and_preserves_blank_preamble_lines():
    with pytest.raises(json.JSONDecodeError, match="no JSON object"):
        devio.parse_fio_json("not JSON", "read")
    document = '{"jobs": []}'
    document, warning = devio.split_fio_output(f"\nwarning\n\n{document}")
    assert warning == ("warning",)
    assert json.loads(document) == {"jobs": []}


class _PopenStub:
    def __init__(self, argv, **kwargs) -> None:
        self.pid, self.returncode = 4242, 0
        self.argv, self.kwargs = argv, kwargs

    def poll(self) -> int:
        return 0

    def wait(self, timeout=None) -> int:
        return 0

    def send_signal(self, _signal: int) -> None:
        return None


def test_tgt_12_proc_handle_selects_close_and_inherits_the_claim_fd(monkeypatch):
    seen: list[_PopenStub] = []

    def popen(argv, **kwargs):
        proc = _PopenStub(argv, **kwargs)
        seen.append(proc)
        return proc

    monkeypatch.setattr(host_mod.subprocess, "Popen", popen)
    monkeypatch.setattr(host_mod, "_PRCTL", object())
    handle = host_mod._ProcHandle(["fio", "--filename=/dev/fd/31"], pass_fds=(31,))
    assert seen[0].kwargs["pass_fds"] == (31,)
    assert "--filename=/dev/fd/31" in seen[0].argv
    handle.kill()
    assert handle._out.closed and handle._err.closed


@pytest.mark.skipif(host_mod._PRCTL is None, reason="PR_SET_PDEATHSIG is unavailable")
def test_tgt_12_supervisor_passes_the_claim_fd_to_fio():
    read_fd, write_fd = os.pipe()
    try:
        script = (
            "import os,sys\n"
            "fd=int(sys.argv[1].rsplit('/',1)[1])\n"
            "assert os.fstat(fd)\n"
            "print(fd)\n"
        )
        handle = host_mod._ProcHandle([sys.executable, "-c", script, f"--filename=/dev/fd/{read_fd}"],
                                      pass_fds=(read_fd,))
        try:
            result = handle.wait()
        finally:
            handle.kill()
        assert result.returncode == 0 and result.stdout.strip() == str(read_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)


@pytest.mark.parametrize(
    ("action", "outcome"),
    (
        ("normal", "complete"),
        ("first-signal", "aborted"),
        ("second-signal", "aborted"),
    ),
)
def test_tgt_12_claim_is_released_on_normal_and_signal_exits(action, outcome):
    host = make_host(cache=INVERSION)
    runner = LifecycleRunner(host, config(raw_write=True), action)
    result = runner.execute()
    assert result.status == outcome and result.exit_code == (0 if outcome == "complete" else 8)
    cleaned(host)


@pytest.mark.parametrize("action", ("refusal", "exception"))
def test_tgt_12_claim_is_released_when_the_body_refuses_or_raises(action):
    host = make_host(cache=INVERSION)
    runner = LifecycleRunner(host, config(raw_write=True), action)
    expected = Refusal if action == "refusal" else RuntimeError
    with pytest.raises(expected):
        runner.execute()
    assert host.claims == {} and host.locks == {}


def test_tgt_12_unreaped_child_keeps_the_claim_and_fails_the_run():
    host = make_host(cache=INVERSION)
    runner = LifecycleRunner(host, config(raw_write=True), "unreaped")
    result = runner.execute()
    assert result.status == "failed" and result.exit_code == 1
    assert result.message == "raw_claim_child_unreaped: child pid(s) 4242 still hold the claim"
    assert host.claims[DEV] == target_ref(host)
    assert host.locks == {}


def test_tgt_12_claim_release_errors_are_named_and_the_lock_is_still_dropped(monkeypatch):
    host = make_host(cache=INVERSION)
    ref = target_ref(host)
    host.claim(ref)
    safety = restore.Safety(host, "run-close", "2026-11-24T12:00:00Z")
    safety.acquire()

    def fail() -> str | None:
        raise OSError("close failed")

    monkeypatch.setattr(host, "release_claim", fail)
    problem = safety.release()
    assert problem == "raw_claim_release_failed: OSError: close failed"
    assert safety.locked is False and host.locks == {}
    assert host.claims[DEV] == ref


def test_tgt_12_claim_is_released_before_the_instance_lock(monkeypatch):
    host = make_host(cache=INVERSION)
    ref = target_ref(host)
    host.claim(ref)
    safety = restore.Safety(host, "run-order", "2026-11-24T12:00:00Z")
    safety.acquire()
    events: list[str] = []
    release_claim, unlock = host.release_claim, host.unlock

    def release() -> str | None:
        events.append("claim")
        return release_claim()

    def unlock_lock() -> None:
        events.append("lock")
        unlock()

    monkeypatch.setattr(host, "release_claim", release)
    monkeypatch.setattr(host, "unlock", unlock_lock)
    assert safety.release() is None
    assert events == ["claim", "lock"]
    cleaned(host)


def test_tgt_12_cli_flag_reaches_config_for_every_run_verb():
    from cli_extended import CliIdentity

    registry = cli.build_cli(CliIdentity("iobench", "0.1.0", "cache-proof disk benchmark", "iobench"))
    for verb in ("calibrate", "matrix", "run"):
        with_flag = registry.command_parsers[verb].parse_args(["--target", DEV, "--allow-raw-write"])
        without = registry.command_parsers[verb].parse_args(["--target", DEV])
        assert cli._config(with_flag, "0.1.0").raw_write is True
        assert cli._config(without, "0.1.0").raw_write is False


def test_tgt_12_a_full_fake_raw_calibration_completes_and_leaves_no_claim():
    host = make_host(cache=INVERSION)
    result = run_calibration(host, raw_write=True)
    assert result.status == "complete" and host.fio_spawns > 0
    cleaned(host)
