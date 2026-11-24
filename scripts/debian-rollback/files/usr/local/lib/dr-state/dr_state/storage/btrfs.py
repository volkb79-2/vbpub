"""Btrfs subvolume storage."""

from __future__ import annotations

import os
import re
import stat
from contextlib import nullcontext
from pathlib import Path
from typing import ContextManager

from .. import ids
from ..errors import Refused, StorageError
from ..paths import Paths
from . import KernelPair, Space, Stored

_UUID_RE = re.compile(
    r"UUID:\s*([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})\Z"
)
_PARENT_RE = re.compile(
    r"Parent\s+UUID:\s*([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})\Z"
)


class BtrfsStorage:
    """All btrfs state objects under the mounted filesystem top level."""

    name = "btrfs"

    def __init__(self, paths: Paths, runner: object) -> None:
        self.paths = paths
        self.runner = runner

    @property
    def top(self) -> Path:
        return self.paths.top

    @property
    def control_dir(self) -> Path:
        return self.paths.control_dir

    def session(self) -> ContextManager[None]:
        return nullcontext()

    def _state_path(self, name: str) -> Path:
        return self.top / name

    def _run(self, argv: list[str]) -> str:
        completed = self.runner.run(argv)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise StorageError(
                f"{' '.join(argv)} failed"
                + (f": {detail}" if detail else "")
            )
        return completed.stdout

    def list(self) -> list[Stored]:
        """Report every top-level object whose name starts with ``@os-``."""

        try:
            entries = sorted(os.scandir(self.top), key=lambda entry: entry.name)
        except FileNotFoundError as error:
            raise StorageError(f"btrfs top level {self.top} is missing") from error
        except OSError as error:
            raise StorageError(f"cannot list btrfs top level {self.top}: {error}") from error
        result: list[Stored] = []
        for entry in entries:
            if not entry.name.startswith("@os-"):
                continue
            result.append(
                Stored(
                    name=entry.name,
                    kind=None,
                    origin=None,
                )
            )
        return result

    def running(self) -> str | None:
        """Read ``rootflags=subvol=`` from the booted kernel command line."""

        try:
            command_line = self.paths.proc_cmdline.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError, UnicodeError):
            return None
        for field in command_line.split():
            if not field.startswith("rootflags="):
                continue
            for option in field.removeprefix("rootflags=").split(","):
                if option.startswith("subvol="):
                    value = option.removeprefix("subvol=")
                    return value or None
        return None

    def _subvolume_show(self, name: str) -> tuple[str | None, str | None]:
        output = self._run(
            ["btrfs", "subvolume", "show", str(self._state_path(name))]
        )
        uuid: str | None = None
        parent: str | None = None
        for raw_line in output.splitlines():
            line = raw_line.rstrip("\r")
            match = _UUID_RE.fullmatch(line.strip())
            if match is not None:
                uuid = match.group(1).lower()
                continue
            match = _PARENT_RE.fullmatch(line.strip())
            if match is not None:
                parent = match.group(1).lower()
        return uuid, parent

    def _delete_raw(self, path: Path) -> None:
        self._run(["btrfs", "subvolume", "delete", str(path)])

    def snapshot(self, source: ids.StateId, target: ids.StateId) -> None:
        """Take one atomic subvolume snapshot, leaving no target on failure."""

        source_path = self._state_path(source.name)
        target_path = self._state_path(target.name)
        try:
            self._run(
                ["btrfs", "subvolume", "snapshot", str(source_path), str(target_path)]
            )
        except BaseException:
            try:
                status = os.lstat(target_path)
            except FileNotFoundError:
                pass
            else:
                if stat.S_ISDIR(status.st_mode) and not stat.S_ISLNK(status.st_mode):
                    try:
                        self._delete_raw(target_path)
                    except BaseException as cleanup_error:
                        raise StorageError(
                            f"snapshot {target.name} failed and the partial target "
                            f"could not be removed: {cleanup_error}"
                        ) from cleanup_error
            raise

    def activate(self, state: ids.StateId) -> None:
        """A live btrfs subvolume needs no promotion."""

    def delete(self, state: ids.StateId) -> None:
        """Delete one subvolume, refusing dependents and nested children."""

        path = self._state_path(state.name)
        uuid, _ = self._subvolume_show(state.name)
        if uuid is None:
            raise StorageError(f"cannot read subvolume UUID for {state.name}")

        dependents: list[str] = []
        for stored in self.list():
            candidate = ids.parse(stored.name)
            if candidate is None or candidate == state:
                continue
            _, parent = self._subvolume_show(candidate.name)
            if parent == uuid:
                dependents.append(candidate.name)
        if dependents:
            raise Refused(
                f"cannot delete {state.name}: dependent states "
                + ", ".join(dependents)
            )

        output = self._run(["btrfs", "subvolume", "list", "-o", str(path)])
        children = tuple(
            line.strip()
            for line in output.splitlines()
            if line.strip()
        )
        if children:
            raise Refused(
                f"cannot delete {state.name}: unexpected child subvolumes "
                + ", ".join(children)
            )
        self._delete_raw(path)

    def kernels(self, state: str) -> list[KernelPair]:
        """Return complete, non-empty kernel pairs, newest first."""

        boot_dir = self._state_path(state) / "boot"
        try:
            entries = tuple(os.scandir(boot_dir))
        except (FileNotFoundError, NotADirectoryError, OSError):
            return []
        versions = {
            entry.name.removeprefix("vmlinuz-")
            for entry in entries
            if entry.name.startswith("vmlinuz-")
        }
        complete: list[KernelPair] = []
        for version in ids.newest_first(versions):
            kernel = boot_dir / f"vmlinuz-{version}"
            initrd = boot_dir / f"initrd.img-{version}"
            try:
                if kernel.stat().st_size <= 0 or initrd.stat().st_size <= 0:
                    continue
            except OSError:
                continue
            complete.append(
                KernelPair(
                    version=version,
                    vmlinuz=kernel,
                    initrd=initrd,
                    fs_vmlinuz=f"/{state}/boot/vmlinuz-{version}",
                    fs_initrd=f"/{state}/boot/initrd.img-{version}",
                )
            )
        return complete

    def parent(self, state: ids.StateId) -> ids.StateId | None:
        """Map the source subvolume's Parent UUID to a listed state name."""

        _, parent_uuid = self._subvolume_show(state.name)
        if parent_uuid is None:
            return None
        for stored in self.list():
            candidate = ids.parse(stored.name)
            if candidate is None:
                continue
            uuid, _ = self._subvolume_show(candidate.name)
            if uuid == parent_uuid:
                return candidate
        return None

    def locator(self, state: ids.StateId) -> str:
        """Return the filesystem UUID from the btrfs root fstab line."""

        _ = state
        try:
            lines = self.paths.fstab.read_text(encoding="utf-8").splitlines()
        except (FileNotFoundError, OSError, UnicodeError) as error:
            raise StorageError(f"cannot read {self.paths.fstab}: {error}") from error
        for raw_line in lines:
            fields = raw_line.split()
            if len(fields) < 3 or fields[1] != "/" or fields[2] != "btrfs":
                continue
            source = fields[0]
            if source.startswith("UUID=") and len(source) > 5:
                return source.removeprefix("UUID=")
        raise StorageError(f"no btrfs root UUID in {self.paths.fstab}")

    def root_args(self, state: ids.StateId) -> str:
        """The kernel root arguments for one subvolume."""

        return f"root=UUID={self.locator(state)} rootflags=subvol={state.name}"

    def space(self) -> Space:
        """Filesystem size and currently usable free bytes."""

        try:
            info = os.statvfs(self.top)
        except OSError as error:
            raise StorageError(f"cannot read btrfs free space: {error}") from error
        return Space(
            size=info.f_frsize * info.f_blocks,
            free=info.f_frsize * info.f_bavail,
        )
