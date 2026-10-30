from __future__ import annotations

import errno
import importlib
import logging
import os
from pathlib import Path

import pytest
from cowtree.backends import LinuxOps
from cowtree.detect import _read_mountinfo, _reset_info_for_tests, detect
from cowtree.errors import RootInvalid
from cowtree.paths import mount_fstype, parse_mountinfo

from .fakes import FakeOps

detect_module = importlib.import_module("cowtree.detect")


class MountOps(FakeOps):
    def __init__(self, filesystem, mountpoint, **kwargs):
        super().__init__(filesystem=filesystem, **kwargs)
        self.mountpoint = mountpoint

    def read_mountinfo(self):
        escaped = self.mountpoint.replace(" ", "\\040")
        return f"36 25 0:32 / {escaped} rw - {self.filesystem} /dev/fake rw\n"


def test_unset_root_plain_run_and_single_info_line():
    _reset_info_for_tests()
    logs = []
    assert detect(environ={}, ops=FakeOps(), log=logs.append) is None
    assert logs == ["cowtree: MDT_COW_ROOT unset; using plain directories"]


def test_explicitly_empty_root_is_invalid():
    with pytest.raises(RootInvalid, match="absolute"):
        detect(environ={"MDT_COW_ROOT": ""}, ops=FakeOps(), log=lambda _message: None)


def test_default_logger_emits_single_info(caplog):
    _reset_info_for_tests()
    with caplog.at_level(logging.INFO, logger="cowtree"):
        detect(environ={})
        detect(environ={})
    records = [record.getMessage() for record in caplog.records if record.name == "cowtree"]
    assert records == ["cowtree: MDT_COW_ROOT unset; using plain directories"]


def test_mode_off_overrides_set_root():
    _reset_info_for_tests()
    logs = []
    result = detect(
        environ={"MDT_COW_ROOT": "/a/path/that/does/not/exist", "COWTREE_MODE": "off"},
        ops=FakeOps(),
        log=logs.append,
    )
    assert result is None
    assert logs == ["cowtree: COWTREE_MODE=off; using plain directories"]


@pytest.mark.parametrize("mode", ["", "disabled", "AUTO", "1"])
def test_invalid_mode_refuses_before_root_allocation(tmp_path, mode):
    root = tmp_path / "area"
    root.mkdir()
    with pytest.raises(RootInvalid, match="COWTREE_MODE"):
        detect(
            environ={"COWTREE_MODE": mode, "MDT_COW_ROOT": str(root)},
            ops=FakeOps(),
            log=lambda _message: None,
        )
    assert not (root / ".cowtree").exists()


@pytest.mark.parametrize("value", ["relative", "\x00", "/tmp/../tmp"])
def test_root_validation_rejects_nonabsolute_or_non_normalized_before_allocating(tmp_path, value):
    root = tmp_path / "area"
    root.mkdir()
    supplied = value
    if value == "relative":
        supplied = "relative"
    if value == "\x00":
        supplied = str(root) + "\x00"
    if value == "/tmp/../tmp":
        supplied = str(root) + "/../area"
    with pytest.raises(RootInvalid):
        detect(environ={"MDT_COW_ROOT": supplied}, ops=MountOps("xfs", "/"), log=lambda _message: None)
    assert not (root / ".cowtree").exists()


def test_root_validation_rejects_symlink_and_missing_without_creation(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    missing = tmp_path / "missing"
    for value in (str(link), str(missing)):
        with pytest.raises(RootInvalid):
            detect(environ={"MDT_COW_ROOT": value}, ops=MountOps("xfs", "/"), log=lambda _message: None)
    assert not (target / ".cowtree").exists()
    assert not missing.exists()


@pytest.mark.parametrize(
    "uid, mode, filesystem",
    [
        ("other", 0o700, "xfs"),
        (0, 0o700, "xfs"),
        ("self", 0o707, "xfs"),
        ("self", 0o700, "overlay"),
        ("self", 0o700, "tmpfs"),
    ],
)
def test_root_validation_table_owner_mode_and_filesystem(tmp_path, uid, mode, filesystem):
    root = tmp_path / f"area-{filesystem}-{uid}-{mode:o}"
    root.mkdir(mode=0o700)
    os.chmod(root, mode)
    fake_uid = os.geteuid() + 1 if uid == "other" else 0 if uid == 0 else None
    ops = MountOps(filesystem, "/", uid=fake_uid)
    with pytest.raises(RootInvalid):
        detect(environ={"MDT_COW_ROOT": str(root)}, ops=ops, log=lambda _message: None)
    assert not (root / ".cowtree").exists()


def test_mountinfo_longest_prefix_and_octal_escape():
    text = (
        "36 25 0:32 / / rw - ext4 /dev/root rw\n"
        "37 36 0:45 / /mnt/my\\040disk rw - btrfs /dev/cow rw\n"
    )
    rows = parse_mountinfo(text)
    assert rows == [("/", "ext4"), ("/mnt/my disk", "btrfs")]
    assert mount_fstype("/mnt/my disk/runs", text) == "btrfs"
    assert mount_fstype("/var/tmp", text) == "ext4"


def test_mountinfo_malformed_or_unmatched_refuses():
    with pytest.raises(RootInvalid, match="malformed"):
        parse_mountinfo("broken row\n")
    with pytest.raises(RootInvalid, match="cannot determine"):
        mount_fstype("relative", "36 25 0:32 / / rw - ext4 /dev/root rw\n")


def test_probe_selects_reflink_and_reports_area(tmp_path):
    _reset_info_for_tests()
    root = tmp_path / "area"
    root.mkdir()
    logs = []
    area = detect(environ={"MDT_COW_ROOT": str(root)}, ops=FakeOps(filesystem="xfs"), log=logs.append)
    assert area is not None
    assert area.caps.backend == "reflink"
    assert area.caps.cheap_clone and not area.caps.tree_snapshot
    assert area.runs_dir == str(root / ".cowtree" / "runs")
    assert logs == [f"cowtree: reflink area at {root} (xfs)"]


def test_probe_selects_btrfs_when_subvolume_snapshot_works(tmp_path):
    _reset_info_for_tests()
    root = tmp_path / "btrfs-area"
    root.mkdir()
    ops = FakeOps(filesystem="btrfs")
    area = detect(environ={"MDT_COW_ROOT": str(root)}, ops=ops, log=lambda _message: None)
    assert area is not None and area.caps.backend == "btrfs"
    assert area.caps.tree_snapshot and area.caps.per_tree_compression
    assert ops.create_calls == ["source"] and ops.snapshot_calls
    assert ops.destroy_calls == ["snapshot", "source"]


def test_btrfs_probe_failure_can_select_reflink(tmp_path):
    root = tmp_path / "area"
    root.mkdir()
    ops = FakeOps(filesystem="btrfs", snapshot_error=errno.EPERM)
    area = detect(environ={"MDT_COW_ROOT": str(root)}, ops=ops, log=lambda _message: None)
    assert area is not None and area.caps.backend == "reflink"


def test_btrfs_probe_removes_source_when_destroy_fails(tmp_path):
    root = tmp_path / "btrfs-area"
    root.mkdir()
    ops = FakeOps(filesystem="btrfs", snapshot_error=errno.EIO, destroy_error=errno.EPERM)
    area = detect(environ={"MDT_COW_ROOT": str(root)}, ops=ops, log=lambda _message: None)
    assert area is not None and area.caps.backend == "reflink"
    assert not list((root / ".cowtree").glob("probe-*"))


def test_reflink_probe_short_write_and_probe_cleanup_failure(tmp_path, monkeypatch):
    root = tmp_path / "area"
    root.mkdir()

    class ShortWriteOps(FakeOps):
        def write(self, _fd, _data):
            return 0

    with pytest.raises(RootInvalid, match="cannot clone files"):
        detect(environ={"MDT_COW_ROOT": str(root)}, ops=ShortWriteOps(), log=lambda _message: None)
    assert not list((root / ".cowtree").glob("probe-*"))

    root2 = tmp_path / "cleanup-area"
    root2.mkdir()
    real_rmtree = detect_module.shutil.rmtree

    def fail_probe_remove(path, *args, **kwargs):
        if os.path.basename(path).startswith("probe-"):
            raise OSError(errno.EIO, "probe cleanup denied")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(detect_module.shutil, "rmtree", fail_probe_remove)
    with pytest.raises(RootInvalid, match="cannot remove cowtree capability probe"):
        detect(environ={"MDT_COW_ROOT": str(root2)}, ops=FakeOps(), log=lambda _message: None)


def test_private_layout_accepts_existing_version_and_directories(tmp_path):
    root = tmp_path / "area"
    root.mkdir()
    private = root / ".cowtree"
    private.mkdir(mode=0o700)
    (private / "VERSION").write_text("1\n")
    runs = private / "runs"
    shared = root / "shared"
    runs.mkdir(mode=0o700)
    shared.mkdir(mode=0o700)
    assert detect_module._ensure_private_layout(str(root)) == (str(private), str(runs), str(shared))


def test_private_layout_wraps_existing_version_read_failure(tmp_path, monkeypatch):
    root = tmp_path / "area"
    root.mkdir()
    private = root / ".cowtree"
    private.mkdir(mode=0o700)
    (private / "VERSION").write_text("1\n")
    real_read_text = Path.read_text

    def fail_version(path, *args, **kwargs):
        if str(path) == str(private / "VERSION"):
            raise OSError(errno.EACCES, "version read denied")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_version)
    with pytest.raises(RootInvalid, match="cannot read .cowtree/VERSION"):
        detect_module._ensure_private_layout(str(root))


def test_private_layout_refuses_bad_existing_metadata(tmp_path):
    root = tmp_path / "area"
    root.mkdir()
    private = root / ".cowtree"
    private.mkdir(mode=0o700)
    (private / "VERSION").write_text("2\n")
    with pytest.raises(RootInvalid, match="version"):
        detect(environ={"MDT_COW_ROOT": str(root)}, ops=FakeOps(), log=lambda _message: None)


def test_private_layout_refuses_symlink(tmp_path):
    root = tmp_path / "area"
    root.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    (root / ".cowtree").symlink_to(target, target_is_directory=True)
    with pytest.raises(RootInvalid, match="real directory"):
        detect(environ={"MDT_COW_ROOT": str(root)}, ops=FakeOps(), log=lambda _message: None)


def test_private_layout_requires_mode_and_regular_version(tmp_path):
    root = tmp_path / "area"
    root.mkdir()
    private = root / ".cowtree"
    private.mkdir(mode=0o700)
    os.chmod(private, 0o500)
    with pytest.raises(RootInvalid, match="mode 0700"):
        detect(environ={"MDT_COW_ROOT": str(root)}, ops=FakeOps(), log=lambda _message: None)

    second = tmp_path / "second"
    second.mkdir()
    second_private = second / ".cowtree"
    second_private.mkdir(mode=0o700)
    target = tmp_path / "version-target"
    target.write_text("1\n")
    (second_private / "VERSION").symlink_to(target)
    with pytest.raises(RootInvalid, match="regular file"):
        detect(environ={"MDT_COW_ROOT": str(second)}, ops=FakeOps(), log=lambda _message: None)


def test_read_mountinfo_fallback_and_error(tmp_path, monkeypatch):
    assert " - " in _read_mountinfo(LinuxOps())

    class BrokenOps(FakeOps):
        def read_mountinfo(self):
            raise PermissionError(errno.EACCES, "denied")

    with pytest.raises(RootInvalid, match="cannot read"):
        detect(environ={"MDT_COW_ROOT": str(tmp_path)}, ops=BrokenOps(), log=lambda _message: None)

    real_read_text = Path.read_text

    def fail_mountinfo(path, *args, **kwargs):
        if str(path) == "/proc/self/mountinfo":
            raise OSError(errno.EACCES, "mountinfo denied")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_mountinfo)
    with pytest.raises(RootInvalid, match="cannot read /proc/self/mountinfo"):
        _read_mountinfo(LinuxOps())


def test_effective_uid_failure_refuses(tmp_path):
    class BrokenUid(FakeOps):
        def effective_uid(self):
            raise OSError(errno.EIO, "uid unavailable")

    root = tmp_path / "area"
    root.mkdir()
    with pytest.raises(RootInvalid, match="effective uid"):
        detect(environ={"MDT_COW_ROOT": str(root)}, ops=BrokenUid(), log=lambda _message: None)


@pytest.mark.parametrize("path_kind", ["private", "runs", "shared"])
def test_private_layout_rejects_non_directory_child(tmp_path, path_kind):
    root = tmp_path / "area"
    root.mkdir()
    private = root / ".cowtree"
    if path_kind == "private":
        private.write_text("file")
    else:
        private.mkdir(mode=0o700)
    if path_kind == "runs":
        (private / "runs").write_text("file")
    elif path_kind == "shared":
        (private / "runs").mkdir(mode=0o700)
        (root / "shared").write_text("file")
    with pytest.raises(RootInvalid):
        detect_module._ensure_private_layout(str(root))


def test_private_layout_refuses_child_with_wrong_mode(tmp_path):
    root = tmp_path / "area"
    root.mkdir()
    private = root / ".cowtree"
    private.mkdir(mode=0o700)
    runs = private / "runs"
    runs.mkdir(parents=True, mode=0o700)
    os.chmod(private, 0o700)
    os.chmod(runs, 0o755)
    with pytest.raises(RootInvalid, match="cowtree directory must have mode 0700"):
        detect_module._ensure_private_layout(str(root))


@pytest.mark.parametrize("child", ["runs", "shared"])
def test_private_layout_wraps_child_inspection_failure(tmp_path, monkeypatch, child):
    root = tmp_path / "area"
    root.mkdir()
    private = root / ".cowtree"
    private.mkdir(mode=0o700)
    (private / "VERSION").write_text("1\n")
    (private / "runs").mkdir(mode=0o700)
    (root / "shared").mkdir(mode=0o700)
    target = private / child if child == "runs" else root / child
    real_lstat = detect_module.os.lstat

    def fail_child_lstat(value, *args, **kwargs):
        if os.fspath(value) == str(target):
            raise PermissionError(errno.EACCES, "child stat denied")
        return real_lstat(value, *args, **kwargs)

    monkeypatch.setattr(detect_module.os, "lstat", fail_child_lstat)
    with pytest.raises(RootInvalid, match="cannot inspect cowtree directory"):
        detect_module._ensure_private_layout(str(root))


def test_private_layout_reports_create_and_secure_failures(tmp_path, monkeypatch):
    root = tmp_path / "area"
    root.mkdir()
    real_mkdir = detect_module.os.mkdir

    def deny_private(path, *args, **kwargs):
        if path == str(root / ".cowtree"):
            raise PermissionError(errno.EACCES, "private denied")
        return real_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(detect_module.os, "mkdir", deny_private)
    with pytest.raises(RootInvalid, match="cannot create cowtree metadata"):
        detect_module._ensure_private_layout(str(root))

    monkeypatch.setattr(detect_module.os, "mkdir", real_mkdir)
    real_chmod = detect_module.os.chmod

    def deny_chmod(path, *args, **kwargs):
        if path == str(root / ".cowtree"):
            raise PermissionError(errno.EACCES, "chmod denied")
        return real_chmod(path, *args, **kwargs)

    monkeypatch.setattr(detect_module.os, "chmod", deny_chmod)
    with pytest.raises(RootInvalid, match="cannot secure cowtree metadata"):
        detect_module._ensure_private_layout(str(root))


@pytest.mark.parametrize("failure_kind", ["error", "short-write"])
def test_private_layout_reports_probe_write_failure(tmp_path, monkeypatch, failure_kind):
    root = tmp_path / "area"
    root.mkdir()
    real_write = detect_module.os.write

    def short_write(fd, data):
        if os.readlink(f"/proc/self/fd/{fd}").endswith("/.cowtree/VERSION"):
            if failure_kind == "short-write":
                return 1
            raise OSError(errno.EIO, "version write failed")
        return real_write(fd, data)

    monkeypatch.setattr(detect_module.os, "write", short_write)
    with pytest.raises(RootInvalid, match="cannot initialize"):
        detect_module._ensure_private_layout(str(root))


def test_reflink_probe_returns_false_when_first_open_fails(tmp_path):
    probe = tmp_path / "probe"
    probe.mkdir()
    ops = FakeOps()
    original_open = ops.open

    def fail_source_open(path, *args, **kwargs):
        if path == str(probe / "reflink-source"):
            raise PermissionError(errno.EACCES, "probe source denied")
        return original_open(path, *args, **kwargs)

    ops.open = fail_source_open
    assert detect_module._try_reflink_probe(str(probe), ops) is False


@pytest.mark.parametrize("failure_stage", ["private-stat", "version-open", "runs-mkdir", "runs-chmod"])
def test_private_layout_wraps_existing_metadata_syscall_failures(tmp_path, monkeypatch, failure_stage):
    root = tmp_path / "area"
    root.mkdir()
    private = root / ".cowtree"
    if failure_stage == "private-stat":
        real_lstat = detect_module.os.lstat

        def fail_private_lstat(value, *args, **kwargs):
            if value == str(private):
                raise OSError(errno.EACCES, "private stat denied")
            return real_lstat(value, *args, **kwargs)

        monkeypatch.setattr(detect_module.os, "lstat", fail_private_lstat)
        expected = "cannot inspect cowtree metadata"
    elif failure_stage == "version-open":
        real_open = detect_module.os.open

        def fail_version_open(value, *args, **kwargs):
            if value == str(private / "VERSION"):
                raise PermissionError(errno.EACCES, "version create denied")
            return real_open(value, *args, **kwargs)

        monkeypatch.setattr(detect_module.os, "open", fail_version_open)
        expected = "cannot initialize .cowtree/VERSION"
    elif failure_stage == "runs-mkdir":
        real_mkdir = detect_module.os.mkdir

        def fail_runs_mkdir(value, *args, **kwargs):
            if value == str(private / "runs"):
                raise PermissionError(errno.EACCES, "runs create denied")
            return real_mkdir(value, *args, **kwargs)

        monkeypatch.setattr(detect_module.os, "mkdir", fail_runs_mkdir)
        expected = "cannot create cowtree directory"
    else:
        real_chmod = detect_module.os.chmod

        def fail_runs_chmod(value, *args, **kwargs):
            if value == str(private / "runs"):
                raise PermissionError(errno.EACCES, "runs chmod denied")
            return real_chmod(value, *args, **kwargs)

        monkeypatch.setattr(detect_module.os, "chmod", fail_runs_chmod)
        expected = "cannot secure cowtree directory"

    with pytest.raises(RootInvalid, match=expected):
        detect_module._ensure_private_layout(str(root))


def test_btrfs_probe_swallows_fallback_cleanup_error_and_probe_cleanup_missing(tmp_path, monkeypatch):
    root = tmp_path / "area"
    root.mkdir()
    real_rmtree = detect_module.shutil.rmtree

    def fail_source_cleanup(path, *args, **kwargs):
        if os.path.basename(path) == "source":
            raise OSError(errno.EIO, "source cleanup failed")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(detect_module.shutil, "rmtree", fail_source_cleanup)
    ops = FakeOps(filesystem="btrfs", snapshot_error=errno.EIO, destroy_error=errno.EIO)
    area = detect_module.detect(
        environ={"MDT_COW_ROOT": str(root)}, ops=ops, log=lambda _message: None
    )
    assert area is not None and area.caps.backend == "reflink"
    monkeypatch.setattr(detect_module.shutil, "rmtree", lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()))
    root2 = tmp_path / "area2"
    root2.mkdir()
    area = detect_module.detect(
        environ={"MDT_COW_ROOT": str(root2)}, ops=FakeOps(), log=lambda _message: None
    )
    assert area is not None
