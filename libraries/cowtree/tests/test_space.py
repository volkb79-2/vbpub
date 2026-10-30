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
