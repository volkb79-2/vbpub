from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from cowtree.errors import SpaceRefused
from cowtree.space import (
    SpaceEstimate,
    _pending_deletes,
    admit,
    available_bytes,
    filesystem_identity,
    pending_delete_bytes,
    record_pending_delete,
)


@pytest.mark.parametrize(
    "cheap, estimate, expected",
    [
        (True, SpaceEstimate(100, 10, 3), 130),
        (False, SpaceEstimate(100, 10, 3), 400),
        (True, SpaceEstimate(10, 100, 2), 210),
        (False, SpaceEstimate(10, 100, 2), 300),
        (False, SpaceEstimate(0, 0, 0), 0),
    ],
)
def test_space_needed_formula_table_cheap_vs_copy(cheap, estimate, expected):
    caps = SimpleNamespace(cheap_clone=cheap)
    assert estimate.needed(caps) == expected


@pytest.mark.parametrize(
    "values",
    [(-1, 0, 0), (0, -1, 0), (0, 0, -1), (True, 0, 0), (0, 1.5, 0)],
)
def test_estimate_requires_nonnegative_integers(values):
    with pytest.raises(ValueError):
        SpaceEstimate(*values)


def test_space_refused_carries_numbers():
    estimate = SpaceEstimate(100, 20, 2)
    caps = SimpleNamespace(backend="copy", cheap_clone=False)
    stat = SimpleNamespace(f_bavail=1, f_frsize=100)
    with pytest.raises(SpaceRefused) as caught:
        admit(estimate, caps, "/scratch", statvfs=lambda _path: stat)
    assert caught.value.needed_bytes == 300
    assert caught.value.available_bytes == 100
    assert caught.value.estimate == estimate.as_dict()
    assert "300 bytes" in str(caught.value) and "100 are available" in str(caught.value)


def test_available_bytes_uses_fragment_size():
    stat = SimpleNamespace(f_bavail=7, f_frsize=4096)
    assert available_bytes("/scratch", statvfs=lambda _path: stat) == 28672


def test_available_bytes_uses_os_statvfs_by_default(tmp_path):
    stat = os.statvfs(tmp_path)
    assert available_bytes(str(tmp_path)) == stat.f_bavail * stat.f_frsize


@pytest.mark.parametrize(
    "backend, pending, expected",
    [("btrfs", 50, 150), ("reflink", 50, 100), ("copy", 50, 100)],
)
def test_admission_pending_delete_is_btrfs_only(backend, pending, expected):
    estimate = SpaceEstimate(100, 0, 0)
    caps = SimpleNamespace(backend=backend, cheap_clone=True)
    stat = SimpleNamespace(f_bavail=100, f_frsize=10)
    needed, available = admit(estimate, caps, "/scratch", statvfs=lambda _path: stat, pending_bytes=pending)
    assert needed == expected
    assert available == 1000


def test_pending_delete_allowance_expires_and_ignores_nonpositive():
    _pending_deletes.clear()
    record_pending_delete(9, 100, now=10.0)
    record_pending_delete(9, 0, now=11.0)
    record_pending_delete(8, 30, now=11.0)
    assert pending_delete_bytes(9, now=12.0) == 100
    assert pending_delete_bytes(8, now=12.0) == 30
    assert pending_delete_bytes(9, now=71.0) == 0


def test_pending_delete_default_clock_is_used():
    _pending_deletes.clear()
    record_pending_delete(19, 5)
    assert pending_delete_bytes(19) == 5


def test_pending_delete_allowance_is_shared_by_btrfs_roots_on_one_device(tmp_path):
    _pending_deletes.clear()
    first_root = tmp_path / "first-area"
    second_root = tmp_path / "second-area"
    first_root.mkdir()
    second_root.mkdir()
    first_device = os.stat(first_root).st_dev
    second_device = os.stat(second_root).st_dev
    assert first_device == second_device

    record_pending_delete(first_device, 4096, now=10.0)
    pending = pending_delete_bytes(second_device, now=11.0)
    estimate = SpaceEstimate(1024, 0, 0)
    caps = SimpleNamespace(backend="btrfs", cheap_clone=True)
    available = SimpleNamespace(f_bavail=1, f_frsize=10_000)
    needed, _ = admit(
        estimate,
        caps,
        str(second_root),
        statvfs=lambda _path: available,
        pending_bytes=pending,
    )

    assert pending == 4096
    assert needed == 5120


def test_pending_delete_allowance_groups_btrfs_subvolumes_by_filesystem_id():
    filesystem_id = bytes.fromhex("00112233445566778899aabbccddeeff")

    class FakeSubvolumeOps:
        def stat(self, path):
            return SimpleNamespace(st_dev={"/btrfs/one": 101, "/btrfs/two": 202}[path])

        def open(self, path, _flags):
            assert path in {"/btrfs/one", "/btrfs/two"}
            return path

        def close(self, _descriptor):
            pass

        def btrfs_fs_id(self, descriptor):
            return {"/btrfs/one": filesystem_id, "/btrfs/two": filesystem_id}[descriptor]

    _pending_deletes.clear()
    ops = FakeSubvolumeOps()
    first = filesystem_identity("/btrfs/one", backend="btrfs", ops=ops)
    second = filesystem_identity("/btrfs/two", backend="btrfs", ops=ops)

    assert ops.stat("/btrfs/one").st_dev != ops.stat("/btrfs/two").st_dev
    assert first == second == ("btrfs", filesystem_id)
    record_pending_delete(first, 4096, now=10.0)
    assert pending_delete_bytes(second, now=11.0) == 4096


def test_filesystem_identity_uses_st_dev_for_ordinary_directories():
    class FakeDirectoryOps:
        def stat(self, _path):
            return SimpleNamespace(st_dev=303)

    assert filesystem_identity("/ordinary/dir", backend="copy", ops=FakeDirectoryOps()) == ("dev", 303)
