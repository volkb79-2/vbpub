"""IOB-TGT-12: raw-partition mutation under one held kernel O_EXCL claim."""
from __future__ import annotations

import dataclasses
import errno
import io
import json
import os
import signal
import stat
import struct
import sys
from types import SimpleNamespace

import pytest
from helpers import INVERSION, config, job, make_host, target_ref
from helpers import calibrate as run_calibration

from iobench import calibrate, cli, devio, guard, restore, result, stages
from iobench import host as host_mod
from iobench.errors import Aborted, Refusal
from iobench.host import HostError, RealHost, TargetRef
from iobench.series import IntegrityError
from iobench.testing import GIB, R1002_PARTUUID, R1002_SIZE, FakeDevice, FakeHost, standard_host

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


def legacy_marked_target(case: str) -> tuple[FakeHost, str, str]:
    """A marker the pre-claim guard accepted, before raw mutation became the gate."""
    host = standard_host()
    host.dynamic["/sys/fs/cgroup/game-server.slice/io.pressure"] = (
        lambda: "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
    )
    size = 4 * GIB
    if case == "whole-disk":
        serial = "LEGACY-WHOLE-DISK"
        host.add_disk("vdb", 8, size, serial=serial)
        dev = FakeDevice("/dev/vdb", size, devno="8:0", serial=serial, disk="vdb")
        host.devices[dev.path] = dev
        dev.marker = guard.marker_text(size, None, serial, CREATED)
        return host, dev.path, "raw_target_not_partition"
    if case == "serial-only":
        serial = "LEGACY-SERIAL-ONLY"
        host.add_disk("vdb", 8, size, serial=serial)
        dev = host.add_partition("vdb", "vdb1", 8, 1, size, None)
        dev.marker = guard.marker_text(size, None, serial, CREATED)
        return host, dev.path, "raw_target_partuuid_required"
    disk, name, major, condition = {
        "dm": ("dm-0", "dm-0p1", 253, "raw_target_dm"),
        "md": ("md0", "md0p1", 9, "raw_target_md"),
        "loop": ("loop0", "loop0p1", 7, "raw_target_loop"),
    }[case]
    host.add_disk(disk, major, size + 6 * GIB)
    dev = host.add_partition(disk, name, major, 1, size, f"legacy-{case}-partuuid")
    dev.marker = guard.marker_text(size, dev.partuuid, None, CREATED)
    return host, dev.path, condition


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
        if self.action == "refusal-unreaped":
            self.host.unreaped_children.add(4242)
            raise Refusal("test_refusal", "the lifecycle test refused after taking the claim", "none")
        if self.action == "exception-unreaped":
            self.host.unreaped_children.add(4242)
            raise RuntimeError("the lifecycle test raised after taking the claim")
        if self.action == "base-exception-unreaped":
            self.host.unreaped_children.add(4242)
            raise KeyboardInterrupt("the lifecycle test was interrupted after taking the claim")
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


def test_tgt_12_signature_remedy_never_offers_serial_only_identity():
    host = standard_host()
    host.devices[DEV].signatures.append("ext4")
    with pytest.raises(Refusal) as err:
        guard.init_device(host, DEV, own=False, expect_size=R1002_SIZE, expect_partuuid=R1002_PARTUUID,
                          expect_serial=None, created=CREATED)
    assert err.value.condition == "signature"
    assert "or --expect-serial" not in err.value.remedy
    assert "--expect-partuuid" in err.value.remedy


def test_tgt_12_refuses_serial_only_init_and_an_existing_serial_only_marker():
    host = standard_host()
    host.dynamic["/sys/fs/cgroup/game-server.slice/io.pressure"] = (
        lambda: "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
    )
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
    cleaned(host)


@pytest.mark.parametrize("case", ("whole-disk", "dm", "md", "loop", "serial-only"))
def test_tgt_12_old_marker_kinds_are_refused_by_target_show(case):
    host, path, condition = legacy_marked_target(case)
    runtime = SimpleNamespace(dry_run=False, json_mode=True, output=None)
    with pytest.raises(Refusal) as err:
        cli._target_show(SimpleNamespace(path=path), runtime, lambda: host)
    assert err.value.condition == condition
    assert host.claims == {}
    cleaned(host)


@pytest.mark.parametrize("case", ("whole-disk", "dm", "md", "loop", "serial-only"))
def test_tgt_12_old_marker_kinds_are_refused_by_result_identity(monkeypatch, case):
    host, path, condition = legacy_marked_target(case)
    observed: list[str] = []
    real_load = guard.load_target

    def load(host_arg, path_arg):
        try:
            return real_load(host_arg, path_arg)
        except Refusal as exc:
            observed.append(exc.condition)
            raise

    monkeypatch.setattr(result.guard, "load_target", load)
    assert result.live_identity(host, {"target": {"path": path}, "disk_scope": None}) is None
    assert observed == [condition]
    assert host.claims == {}
    cleaned(host)


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


def test_tgt_12_fake_claim_refuses_to_close_under_an_active_raw_handle():
    host = make_host(cache=INVERSION)
    ref = target_ref(host)
    host.claim(ref)
    writer = claimed_write_job(name="active-fake-handle")
    handle = host.write_range(writer.argv(), ref, 0, writer.size)
    problem = host.release_claim()
    assert problem is not None and problem.startswith("raw_claim_child_unreaped")
    assert host.claims[DEV] == ref
    handle.kill()
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


def test_tgt_12_realhost_default_write_and_discard_refusals_never_launch(monkeypatch):
    real = RealClaim(monkeypatch)
    process_calls: list[object] = []
    ioctl_calls: list[object] = []
    monkeypatch.setattr(host_mod, "_ProcHandle", lambda *_args, **_kwargs: process_calls.append(object()))
    monkeypatch.setattr(host_mod.fcntl, "ioctl", lambda *_args, **_kwargs: ioctl_calls.append(object()))
    writer = claimed_write_job(name="real-default-refusal")
    with pytest.raises(Refusal) as write_error:
        real.host.write_range(writer.argv(), real.ref, 0, writer.size)
    assert str(write_error.value) == WRITE_REFUSAL
    with pytest.raises(Refusal) as discard_error:
        real.host.discard_range(devio.blkdiscard_argv(DEV, 0, writer.size), real.ref, 0, writer.size)
    assert str(discard_error.value) == DISCARD_REFUSAL
    assert process_calls == [] and ioctl_calls == []
    assert real.host._claim_fd is None


def test_tgt_12_real_claim_rejects_file_and_different_targets_before_open(monkeypatch):
    host = RealHost()
    with pytest.raises(HostError, match="only a block device"):
        host.claim(TargetRef("file", "/bench/target.img", 4 * GIB, inode=12, fs_uuid="uuid"))
    ref = TargetRef("device", DEV, R1002_SIZE, devno="254:7", partuuid=R1002_PARTUUID)
    monkeypatch.setattr(host_mod.os, "open", lambda *_args, **_kwargs: 71)
    monkeypatch.setattr(host_mod.os, "fstat", lambda _fd: SimpleNamespace(
        st_mode=stat.S_IFBLK, st_rdev=os.makedev(254, 7), st_size=0))
    monkeypatch.setattr(host_mod.fcntl, "ioctl", RealClaim.ioctl_size)
    monkeypatch.setattr(host, "_verify_live", lambda _target: None)
    monkeypatch.setattr(host_mod.os, "close", lambda _fd: None)
    assert host.claim(ref) == 71
    with pytest.raises(HostError, match="another raw target"):
        host.claim(TargetRef("device", "/dev/vdb7", R1002_SIZE, devno="254:9", partuuid=R1002_PARTUUID))


def test_tgt_12_real_devno_parser_refuses_the_invalid_shape():
    with pytest.raises(HostError, match="invalid device number"):
        host_mod._devno_pair(TargetRef("device", DEV, R1002_SIZE, devno="not-a-major-minor"))


@pytest.mark.parametrize(
    ("field", "value", "condition"),
    (
        ("devno", "254:8", "raw_target_devno_changed"),
        ("size_bytes", R1002_SIZE + 1, "raw_target_size_changed"),
        ("partuuid", "different-partuuid", "raw_target_partuuid_changed"),
        ("serial", "different-serial", "raw_target_claim_mismatch"),
    ),
)
def test_tgt_12_real_held_claim_refuses_every_identity_mismatch(monkeypatch, field, value, condition):
    real = RealClaim(monkeypatch)
    assert real.claim() == 71
    changed = dataclasses.replace(real.ref, **{field: value})
    with pytest.raises(Refusal) as err:
        real.host._claim_fd_for(changed, write=True)
    assert err.value.condition == condition
    assert real.closed == []


def test_tgt_12_real_held_claim_without_a_target_identity_is_refused(monkeypatch):
    real = RealClaim(monkeypatch)
    assert real.claim() == 71
    real.host._claim_target = None
    with pytest.raises(HostError, match="no target identity"):
        real.host._claim_fd_for(real.ref, write=False)
    assert real.host.release_claim() is None
    assert real.closed == [71]


def test_tgt_12_real_duplicate_launch_filename_is_refused_before_proc_handle(monkeypatch):
    real = RealClaim(monkeypatch)
    assert real.claim() == 71
    writer = claimed_write_job(name="real-duplicate-filename")
    argv = [*writer.argv(), f"--filename={DEV}"]
    process_calls: list[object] = []
    monkeypatch.setattr(host_mod, "check_argv", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(host_mod, "_ProcHandle", lambda *_args, **_kwargs: process_calls.append(object()))
    with pytest.raises(HostError, match="exactly once"):
        real.host.write_range(argv, real.ref, 0, writer.size)
    assert process_calls == []
    assert real.host.release_claim() is None


def test_tgt_12_real_release_keeps_the_claim_for_unreaped_children_and_close_errors(monkeypatch):
    real = RealClaim(monkeypatch)
    assert real.claim() == 71
    real.host._unreaped_children.update({99, 4242})
    problem = real.host.release_claim()
    assert problem == "raw_claim_child_unreaped: child pid(s) 99, 4242 still hold the claim"
    assert real.closed == []
    real.host._unreaped_children.clear()

    def fail_close(fd: int) -> None:
        assert fd == 71
        raise OSError(errno.EIO, os.strerror(errno.EIO))

    monkeypatch.setattr(host_mod.os, "close", fail_close)
    problem = real.host.release_claim()
    assert problem == "raw_claim_release_failed: closing /dev/vda7 claim failed (Input/output error)"
    assert real.host._claim_fd == 71


@pytest.mark.parametrize(
    ("case", "condition"),
    (
        ("devno", "raw_target_devno_changed"),
        ("size", "raw_target_size_changed"),
        ("partuuid", "raw_target_partuuid_changed"),
    ),
)
def test_tgt_12_real_verify_live_refuses_devno_size_and_partuuid_drift(monkeypatch, case, condition):
    host = RealHost()
    target = TargetRef("device", DEV, R1002_SIZE, devno="254:7", partuuid=R1002_PARTUUID)
    live_rdev = os.makedev(254, 8) if case == "devno" else os.makedev(254, 7)
    live_size = R1002_SIZE + 512 if case == "size" else R1002_SIZE
    live_partuuid = "different-partuuid" if case == "partuuid" else R1002_PARTUUID
    blkid_calls: list[list[str]] = []
    real_open = open

    monkeypatch.setattr(host_mod.os, "stat", lambda _path: SimpleNamespace(st_rdev=live_rdev))
    monkeypatch.setattr("builtins.open", lambda path, *args, **kwargs: (
        io.StringIO(str(live_size // 512) + "\n") if "/sys/dev/block/" in str(path)
        else real_open(path, *args, **kwargs)
    ))

    def run(argv, **_kwargs):
        blkid_calls.append(list(argv))
        return SimpleNamespace(returncode=0 if live_partuuid else 2,
                               stdout=f"PART_ENTRY_UUID={live_partuuid}\n" if live_partuuid else "", stderr="")

    monkeypatch.setattr(host_mod.subprocess, "run", run)
    with pytest.raises(Refusal) as err:
        host._verify_live(target)
    assert err.value.condition == condition
    assert blkid_calls == ([] if case in ("devno", "size") else [[
        "blkid", "-p", "-o", "export", DEV
    ]])


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


def test_tgt_12_fio_job_error_is_a_named_failure_even_with_a_preamble_and_rc_zero():
    host = standard_host()
    read = devio.FioJob("error-job", DEV, "randread", 4096, 1, 1, 0, 4096,
                        prefix="/run/iobench/error-job")
    sim = host.fio.simulate(read, host.now(), host.devices[DEV])
    rendered, rate_log, _hist = host.fio.render(read, sim)
    document = json.loads(rendered)
    document["jobs"][0]["error"] = 13
    host.put(read.log_path, rate_log)
    with pytest.raises(IntegrityError, match=r"IOB-PLA-20: fio job error-job reported error 13"):
        devio.collect(host, read, host_mod.RunResult(0, f"{PREAMBLE}\n{json.dumps(document)}", ""), host.now())


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


class _KillTimeoutPopen(_PopenStub):
    timeout: float | None = None

    def poll(self) -> None:
        return None

    def wait(self, timeout=None) -> int:
        self.timeout = timeout
        raise host_mod.subprocess.TimeoutExpired(["true"], timeout)


class _SentinelPopen(_PopenStub):
    def poll(self) -> int:
        return host_mod.SUPERVISOR_UNREAPED_STATUS

    def wait(self, timeout=None) -> int:
        return host_mod.SUPERVISOR_UNREAPED_STATUS


class _RawHandleStub:
    def __init__(self) -> None:
        self.pid = 5151
        self.active = True

    def poll(self) -> int | None:
        return None if self.active else 0

    def wait(self) -> host_mod.RunResult:
        return host_mod.RunResult(0, "", "")

    def kill(self) -> None:
        self.active = False


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


def test_tgt_12_proc_handle_kill_timeout_sets_and_reports_the_unreaped_pid(monkeypatch):
    seen: list[int] = []
    monkeypatch.setattr(host_mod.subprocess, "Popen", _KillTimeoutPopen)
    monkeypatch.setattr(host_mod, "_PRCTL", object())
    handle = host_mod._ProcHandle(["true"], on_unreaped=seen.append)
    handle.kill()
    assert handle.unreaped_pid == 4242 and seen == [4242]
    assert handle._p.timeout == host_mod.SUPERVISOR_EXIT_TIMEOUT_S
    assert handle._out.closed and handle._err.closed


def test_tgt_12_proc_handle_poll_reports_the_supervisor_unreaped_sentinel(monkeypatch):
    seen: list[int] = []
    monkeypatch.setattr(host_mod.subprocess, "Popen", _SentinelPopen)
    monkeypatch.setattr(host_mod, "_PRCTL", object())
    handle = host_mod._ProcHandle(["true"], on_unreaped=seen.append)
    handle._err.write(f"{host_mod.SUPERVISOR_UNREAPED_PREFIX}6001,6002\n")
    handle._err.flush()
    assert handle.poll() == host_mod.SUPERVISOR_UNREAPED_STATUS
    assert handle.unreaped_pid == 6001 and seen == [6001, 6002]
    assert handle.poll() == host_mod.SUPERVISOR_UNREAPED_STATUS and seen == [6001, 6002]
    handle.kill()
    assert handle._out.closed and handle._err.closed


def test_tgt_12_real_release_keeps_the_claim_under_a_registered_active_raw_handle(monkeypatch):
    real = RealClaim(monkeypatch)
    assert real.claim() == 71
    raw_handle = _RawHandleStub()
    monkeypatch.setattr(host_mod, "_ProcHandle", lambda _argv, **_kwargs: raw_handle)
    writer = claimed_write_job(name="registered-active-raw-handle")
    returned = real.host.write_range(writer.argv(), real.ref, 0, writer.size)
    assert returned is raw_handle
    problem = real.host.release_claim()
    assert problem == "raw_claim_child_unreaped: raw writer handle(s) 5151 still hold the claim"
    assert real.closed == [] and real.host._claim_fd == 71
    raw_handle.active = False
    assert real.host.release_claim() is None
    assert real.closed == [71]


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


@pytest.mark.skipif(host_mod._PRCTL is None, reason="PR_SET_PDEATHSIG is unavailable")
@pytest.mark.parametrize("exit_path", ("normal", "kill"))
def test_tgt_12_supervisor_reports_an_adopted_grandchild_on_every_exit_path(monkeypatch, tmp_path, exit_path):
    real = RealClaim(monkeypatch)
    assert real.claim() == 71
    claim_read, claim_write = os.pipe()
    ready_read, ready_write = os.pipe()
    pid_file = tmp_path / "grandchild.pid"
    script = (
        "import os, signal, sys, time\n"
        "claim_fd=int(sys.argv[1].rsplit('/',1)[1])\n"
        "ready_fd=int(sys.argv[2].rsplit('/',1)[1])\n"
        "pid_path=sys.argv[3]\n"
        "os.fstat(claim_fd)\n"
        "r,w=os.pipe()\n"
        "child=os.fork()\n"
        "if child == 0:\n"
        "    os.close(r)\n"
        "    os.close(ready_fd)\n"
        "    os.setsid()\n"
        "    os.write(w, str(os.getpid()).encode())\n"
        "    os.close(w)\n"
        "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "    time.sleep(60)\n"
        "    os._exit(0)\n"
        "os.close(w)\n"
        "grandchild=int(os.read(r, 32))\n"
        "os.close(r)\n"
        "with open(pid_path, 'w', encoding='ascii') as fh:\n"
        "    fh.write(str(grandchild))\n"
        "os.write(ready_fd, b'1')\n"
        "os.close(ready_fd)\n"
    )
    seen: list[int] = []
    handle = host_mod._ProcHandle(
        [sys.executable, "-c", script, f"--filename=/dev/fd/{claim_read}",
         f"--filename=/dev/fd/{ready_write}", str(pid_file)],
        pass_fds=(claim_read, ready_write), on_unreaped=real.host._unreaped_children.add,
    )
    grandchild: int | None = None
    try:
        assert os.read(ready_read, 1) == b"1"
        if exit_path == "normal":
            result = handle.wait()
            assert result.returncode == host_mod.SUPERVISOR_UNREAPED_STATUS
            assert host_mod.SUPERVISOR_UNREAPED_PREFIX in result.stderr
        else:
            handle.kill()
        grandchild = int(pid_file.read_text(encoding="ascii"))
        problem = real.host.release_claim()
        assert problem is not None and problem.startswith("raw_claim_child_unreaped")
        assert real.closed == [] and real.host._claim_fd == 71
        assert handle.unreaped_pid is not None
        assert real.host._unreaped_children
        if exit_path == "normal":
            assert handle.unreaped_pid == grandchild
        seen.extend(real.host._unreaped_children)
        assert seen
    finally:
        handle.kill()
        if grandchild is None and pid_file.exists():
            grandchild = int(pid_file.read_text(encoding="ascii"))
        if grandchild is not None:
            try:
                os.kill(grandchild, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(grandchild, 0)
            except ChildProcessError:
                pass
        real.host._unreaped_children.clear()
        if real.host._claim_fd is not None:
            real.host.release_claim()
        os.close(ready_read)
        os.close(ready_write)
        os.close(claim_read)
        os.close(claim_write)


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


@pytest.mark.parametrize(("action", "expected"), (
    ("refusal-unreaped", Refusal),
    ("exception-unreaped", RuntimeError),
    ("base-exception-unreaped", KeyboardInterrupt),
))
def test_tgt_12_refusal_and_base_exception_paths_attach_the_claim_cleanup_problem(action, expected):
    host = make_host(cache=INVERSION)
    runner = LifecycleRunner(host, config(raw_write=True), action)
    with pytest.raises(expected) as err:
        runner.execute()
    notes = "\n".join(getattr(err.value, "__notes__", ()))
    assert "raw_claim_child_unreaped" in notes
    if expected is not KeyboardInterrupt:
        assert "raw_claim_child_unreaped" in str(err.value)
    assert host.claims[DEV] == target_ref(host) and host.locks == {}
    host.unreaped_children.clear()
    host.release_claim()


def test_tgt_12_unreaped_child_keeps_the_claim_and_fails_the_run():
    host = make_host(cache=INVERSION)
    runner = LifecycleRunner(host, config(raw_write=True), "unreaped")
    result = runner.execute()
    assert result.status == "failed" and result.exit_code == 1
    assert result.message == "raw_claim_child_unreaped: child pid(s) 4242 still hold the claim"
    assert host.claims[DEV] == target_ref(host)
    assert host.locks == {}


def test_tgt_12_cleanup_preserves_verification_failure_and_claim_problem(monkeypatch):
    from iobench.errors import VerificationFailure

    host = make_host(cache=INVERSION)
    runner = LifecycleRunner(host, config(raw_write=True), "normal")

    def verification() -> None:
        raise VerificationFailure("io.max changed after restore")

    monkeypatch.setattr(runner.safety, "verify_io_max", verification)
    monkeypatch.setattr(host, "release_claim", lambda: "raw_claim_release_failed: synthetic close failure")
    result = runner.execute()
    assert result.status == "failed" and result.exit_code == VerificationFailure.exit_code == 9
    assert "io.max changed after restore" in result.message
    assert "raw_claim_release_failed: synthetic close failure" in result.message
    assert host.claims[DEV] == target_ref(host) and host.locks == {}
    host.claims.clear()
    host.claim_fds.clear()


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
