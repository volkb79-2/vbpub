"""Filesystem fakes shared by the unit tests."""
from __future__ import annotations

import os
import shutil
from typing import ClassVar

from cowtree.backends import LinuxOps
from cowtree.repo_paths import Btrfs

REPO_PATHS_TEXT = """\
schema = 1
repo = "myrepo"

[paths.iso]
repo = "downloads/iso"
cow = "downloads/iso"
compression = "none"

[paths.vm-images]
repo = "downloads/vm-images"
cow = "downloads/vm-images"
compression = "zstd"

[paths.vm-lab]
repo = "{project}/vm-lab"
cow = "{project}/vm-lab"
compression = "zstd"

[paths.downloads]
repo = "downloads"

[paths.bare]
repo = "bare"
cow = "bare"

[paths.local-lab]
repo = "{project}/local-lab"
"""


def write_repo(tmp_path, text=REPO_PATHS_TEXT, name="myrepo"):
    """A checkout or worktree directory (its directory name is irrelevant to the resolver) holding a
    vm-lab.toml; returns its path."""
    root = tmp_path / name
    root.mkdir()
    (root / "vm-lab.toml").write_text(text, encoding="utf-8")
    return root


def btrfs_area(tmp_path, name="area"):
    """A valid btrfs CoW area built by the real detect() over fake ops: returns (root path, environ, ops)."""
    root = tmp_path / name
    root.mkdir()
    return root, {"MDT_COW_ROOT": str(root)}, FakeOps(filesystem="btrfs")


class FakeBtrfs:
    """Records the btrfs commands and mimics their effect on the real temporary directories."""

    def __init__(self, *, props=None, fail=None):
        self.calls = []
        self.props = dict(props or {})
        self.subvolumes = set()
        self.fail = fail

    def run(self, argv):
        self.calls.append(tuple(argv))
        verb = tuple(argv[1:3])
        if verb == self.fail:
            return 1, "boom\n"
        if verb == ("subvolume", "create"):
            assert os.path.isdir(os.path.dirname(argv[3])), "parents must exist before the subvolume is created"
            os.mkdir(argv[3])
            self.subvolumes.add(argv[3])
            return 0, ""
        if verb == ("property", "get"):
            value = self.props.get(argv[3], "")
            return 0, (f"compression={value}\n" if value else "")
        assert verb == ("property", "set") and argv[4] == "compression"
        self.props[argv[3]] = argv[5]
        return 0, ""

    def tools(self):
        return Btrfs(run=self.run, is_subvolume=lambda path: path in self.subvolumes)


class StatResultProxy:
    """A stat_result view that changes selected fields without losing ns data."""

    _indexes: ClassVar[dict[str, int]] = {
        "st_mode": 0,
        "st_ino": 1,
        "st_dev": 2,
        "st_nlink": 3,
        "st_uid": 4,
        "st_gid": 5,
        "st_size": 6,
        "st_atime": 7,
        "st_mtime": 8,
        "st_ctime": 9,
    }

    def __init__(self, result, **overrides):
        self._result = result
        self._overrides = overrides

    def __getattr__(self, name):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._result, name)

    def __getitem__(self, index):
        name = next((field for field, field_index in self._indexes.items() if field_index == index), None)
        if name in self._overrides:
            return self._overrides[name]
        return self._result[index]

    def __iter__(self):
        return iter(tuple(self[index] for index in range(len(self))))

    def __len__(self):
        return len(self._result)


class FakeOps(LinuxOps):
    def __init__(self, *, filesystem="xfs", clone_error=None, subvol_error=None, snapshot_error=None, destroy_error=None, uid=None):
        self.filesystem = filesystem
        self.clone_error = clone_error
        self.subvol_error = subvol_error
        self.snapshot_error = snapshot_error
        self.destroy_error = destroy_error
        self.uid = uid
        self.clone_calls = 0
        self.create_calls = []
        self.snapshot_calls = []
        self.destroy_calls = []
        self.xattrs = []
        self.flags = {}

    def btrfs_fs_id(self, _fd):
        return bytes.fromhex("00112233445566778899aabbccddeeff")

    def effective_uid(self):
        return os.geteuid() if self.uid is None else self.uid

    def read_mountinfo(self):
        return f"36 25 0:32 / / rw - {self.filesystem} /dev/fake rw\n"

    def ficlone(self, dst_fd, src_fd):
        self.clone_calls += 1
        if self.clone_error is not None:
            raise OSError(self.clone_error, os.strerror(self.clone_error))
        os.lseek(src_fd, 0, os.SEEK_SET)
        while True:
            block = os.read(src_fd, 65536)
            if not block:
                break
            os.write(dst_fd, block)

    def setxattr(self, path, name, value, *, follow_symlinks=False):
        self.xattrs.append((path, name, value, follow_symlinks))

    @staticmethod
    def _fd_path(fd):
        return os.readlink(f"/proc/self/fd/{fd}")

    def subvol_create(self, parent_fd, name):
        if self.subvol_error is not None:
            raise OSError(self.subvol_error, os.strerror(self.subvol_error))
        self.create_calls.append(name)
        os.mkdir(name, 0o700, dir_fd=parent_fd)

    def snapshot_create(self, parent_fd, src_fd, name):
        if self.snapshot_error is not None:
            raise OSError(self.snapshot_error, os.strerror(self.snapshot_error))
        self.snapshot_calls.append((src_fd, name))
        source = self._fd_path(src_fd)
        destination = os.path.join(self._fd_path(parent_fd), name)
        shutil.copytree(source, destination, symlinks=True)
        destination_inode = os.lstat(destination).st_ino
        source_inode = os.fstat(src_fd).st_ino
        self.flags[destination_inode] = self.flags.get(source_inode, 0)

    def subvol_destroy(self, parent_fd, name):
        self.destroy_calls.append(name)
        if self.destroy_error is not None:
            raise OSError(self.destroy_error, os.strerror(self.destroy_error))
        path = os.path.join(self._fd_path(parent_fd), name)
        shutil.rmtree(path)

    def subvol_getflags(self, fd):
        return self.flags.get(os.fstat(fd).st_ino, 0)

    def subvol_setflags(self, fd, flags):
        self.flags[os.fstat(fd).st_ino] = flags
