from __future__ import annotations

import os

import pytest
from cowtree.errors import PathRefused, RootInvalid
from cowtree.paths import (
    mount_fstype,
    normalized_absolute,
    parse_mountinfo,
    require_directory,
    validate_name,
    validate_root,
    within,
)


@pytest.mark.parametrize("name", ["a", "A0", "tool.name", "x_y-z", "a" * 64])
def test_name_validation_accepts_documented_forms(name):
    assert validate_name(name) == name


@pytest.mark.parametrize("name", ["", "_bad", "-bad", ".bad", "a" * 65, "a/b", "a\x00b", None])
def test_name_validation_table(name):
    with pytest.raises(PathRefused):
        validate_name(name)


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/tmp/root", True),
        ("relative", False),
        ("/tmp/a/../b", False),
        ("/tmp/a/", False),
        ("/tmp/a\x00b", False),
        ("", False),
        (9, False),
    ],
)
def test_normalized_absolute_table(path, expected):
    assert normalized_absolute(path) is expected


def test_within_uses_component_boundary_and_handles_value_error(monkeypatch):
    assert within("/tmp/root/file", "/tmp/root")
    assert not within("/tmp/rooted/file", "/tmp/root")
    monkeypatch.setattr(os.path, "commonpath", lambda _paths: (_ for _ in ()).throw(ValueError("different drives")))
    assert not within("/tmp/root", "/tmp/root")


def test_mountinfo_empty_rows_exact_mount_and_spaces():
    text = "\n36 25 0:32 / /mnt/my\\040disk rw - xfs /dev/a rw\n"
    rows = parse_mountinfo(text)
    assert rows == [("/mnt/my disk", "xfs")]
    assert mount_fstype("/mnt/my disk", text) == "xfs"


def test_require_directory_rejects_file_and_symlink(tmp_path):
    regular = tmp_path / "file"
    regular.write_text("x")
    link = tmp_path / "link"
    link.symlink_to(regular)
    with pytest.raises(RootInvalid, match="real directory"):
        require_directory(str(regular), label="root")
    with pytest.raises(RootInvalid, match="real directory"):
        require_directory(str(link), label="root")
    with pytest.raises(RootInvalid, match="cannot be inspected"):
        require_directory(str(tmp_path / "missing"), label="root")


def test_validate_root_realpath_error(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(os.path, "realpath", lambda _path: (_ for _ in ()).throw(OSError("resolve failed")))
    with pytest.raises(RootInvalid, match="cannot resolve"):
        validate_root(str(root), euid=os.geteuid(), mountinfo="36 25 0:32 / / rw - ext4 /dev/a rw\n")
