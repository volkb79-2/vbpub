"""systemd-boot behavior at fake filesystem, space, efivar, and bootctl boundaries."""

from __future__ import annotations

import hashlib
import io
import unittest
from unittest import mock
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import _support
from dr_state import core, ids
from dr_state.boot import BootTarget
from dr_state.boot.sdboot import (
    CAPACITY_MARGIN,
    DEFAULT_EFIVAR,
    ENTRY_MARKER,
    ENTRY_BYTES,
    ONESHOT_EFIVAR,
    Filesystem,
    SdbootBoot,
    _Entry,
    _Inventory,
    _Pair,
    _entry_refs,
    _pair_id,
)
from dr_state.errors import BootConfigError, BootError, CapacityError
from dr_state.storage import KernelPair, Stored


@dataclass
class Completed:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.fail = False

    def run(self, argv: list[str], *, input: str | None = None) -> Completed:
        _ = input
        self.calls.append(argv)
        if self.fail:
            return Completed(1, "", "simulated bootctl failure")
        return Completed()


class FakeStorage:
    name = "btrfs"

    def __init__(self, states: list[ids.StateId], paths: Path) -> None:
        self.states = states
        self.paths = paths

    def list(self) -> list[Stored]:
        return [Stored(state.name, None, None) for state in self.states]

    def running(self) -> str | None:
        return self.states[0].name if self.states else None

    def kernels(self, state: str) -> list[KernelPair]:
        boot = self.paths.top / state / "boot"
        pairs: dict[str, tuple[Path, Path]] = {}
        for kernel in boot.glob("vmlinuz-*"):
            version = kernel.name.removeprefix("vmlinuz-")
            initrd = boot / f"initrd.img-{version}"
            if kernel.is_file() and initrd.is_file():
                pairs[version] = (kernel, initrd)
        return [
            KernelPair(
                version=version,
                vmlinuz=pairs[version][0],
                initrd=pairs[version][1],
                fs_vmlinuz=f"/{state}/boot/vmlinuz-{version}",
                fs_initrd=f"/{state}/boot/initrd.img-{version}",
            )
            for version in ids.newest_first(pairs)
        ]

    def activate(self, state: ids.StateId) -> None:
        _ = state

    def locator(self, state: ids.StateId) -> str:
        _ = state
        return "11111111-2222-3333-4444-555555555555"

    def root_args(self, state: ids.StateId) -> str:
        return (
            "root=UUID=11111111-2222-3333-4444-555555555555 "
            f"rootflags=subvol={state.name}"
        )


class FakeSpaceFilesystem(Filesystem):
    def __init__(self, free: int = 1 << 40) -> None:
        self.free = free

    def statvfs(self, path: Path) -> object:
        _ = path
        return SimpleNamespace(f_bavail=self.free, f_frsize=1)


class RecordingFilesystem(FakeSpaceFilesystem):
    def __init__(self, free: int = 1 << 40) -> None:
        super().__init__(free)
        self.events: list[tuple[str, str]] = []
        self.space_reads = 0
        self.fail_on: tuple[str, str] | None = None

    def _record(self, operation: str, path: Path) -> None:
        event = (operation, path.name)
        self.events.append(event)
        if self.fail_on == event:
            raise OSError(f"simulated failure at {operation} {path}")

    def statvfs(self, path: Path) -> object:
        self.space_reads += 1
        self.events.append(("statvfs", path.name))
        return super().statvfs(path)

    def mkdir(self, path: Path) -> None:
        self._record("mkdir", path)
        super().mkdir(path)

    def unlink(self, path: Path) -> None:
        self._record("unlink", path)
        if not path.exists():
            raise FileNotFoundError(path)
        super().unlink(path)

    def rmtree(self, path: Path) -> None:
        self._record("rmtree", path)
        super().rmtree(path)

    def copy_file(self, source: Path, destination: Path) -> tuple[str, int]:
        self._record("copy", destination)
        return super().copy_file(source, destination)

    def replace(self, source: Path, destination: Path) -> None:
        self._record("replace", destination)
        super().replace(source, destination)

    def write_atomic(self, path: Path, data: bytes) -> None:
        self._record("write", path)
        super().write_atomic(path, data)

    def lookup(self, path: Path) -> Path | None:
        self._record("lookup", path)
        return super().lookup(path)


def make_state(sequence: int, hour: int, slug: str = "apt") -> ids.StateId:
    value = ids.make(sequence, datetime(2026, 11, 18, hour, tzinfo=UTC), slug)
    if value is None:
        raise AssertionError("invalid test state")
    return value


class SdbootTest(_support.TemporaryRootTest):
    def setUp(self) -> None:
        super().setUp()
        self.paths.top.mkdir()
        self.paths.esp.mkdir(parents=True)
        self.paths.esp_dir.mkdir(parents=True)
        self.paths.esp_marker.write_text("systemd-boot\n", encoding="utf-8")
        self.paths.loader_dir.mkdir(parents=True)
        self.paths.entry_dir.mkdir(parents=True)
        self.paths.esp_kernel_dir.mkdir(parents=True)
        self.paths.efivars_dir.mkdir(parents=True)
        self.install = make_state(1, 12, "install")
        self.apt = make_state(2, 13)
        self.states = [self.install, self.apt]
        self.add_state(self.install, kernel=b"install-kernel\n", initrd=b"install-initrd\n")
        self.add_state(self.apt, kernel=b"apt-kernel\n", initrd=b"apt-initrd\n")
        self.storage = FakeStorage(self.states, self.paths)
        self.runner = FakeRunner()
        self.warnings: list[str] = []
        self.boot = self.make_boot()

    def make_boot(
        self,
        *,
        filesystem: Filesystem | None = None,
    ) -> SdbootBoot:
        return SdbootBoot(
            self.paths,
            self.storage,
            self.runner,
            filesystem=filesystem,
            warn=self.warnings.append,
        )

    def add_state(
        self,
        value: ids.StateId,
        *,
        kernel: bytes | None = None,
        initrd: bytes | None = None,
    ) -> None:
        boot = self.paths.top / value.name / "boot"
        boot.mkdir(parents=True, exist_ok=True)
        (boot / "vmlinuz-6.12.117+deb13-amd64").write_bytes(
            kernel if kernel is not None else f"{value.name}-kernel\n".encode()
        )
        (boot / "initrd.img-6.12.117+deb13-amd64").write_bytes(
            initrd if initrd is not None else f"{value.name}-initrd\n".encode()
        )
        if value not in self.states:
            self.states.append(value)

    def target(self, value: ids.StateId) -> BootTarget:
        kernel = self.paths.top / value.name / "boot" / "vmlinuz-6.12.117+deb13-amd64"
        initrd = self.paths.top / value.name / "boot" / "initrd.img-6.12.117+deb13-amd64"
        return BootTarget(
            state=value,
            title=f"Debian {value.name} (kernel 6.12.117+deb13-amd64)",
            kernel=KernelPair(
                version="6.12.117+deb13-amd64",
                vmlinuz=kernel,
                initrd=initrd,
                fs_vmlinuz=f"/{value.name}/boot/vmlinuz-6.12.117+deb13-amd64",
                fs_initrd=f"/{value.name}/boot/initrd.img-6.12.117+deb13-amd64",
            ),
            root_args=(
                "root=UUID=11111111-2222-3333-4444-555555555555 "
                f"rootflags=subvol={value.name}"
            ),
            locator="11111111-2222-3333-4444-555555555555",
        )

    def entry(self, value: ids.StateId) -> Path:
        return self.paths.entry_dir / ids.entry_id(value)

    def pair_path(self, value: ids.StateId) -> Path:
        entry = self.entry(value).read_text(encoding="utf-8")
        for line in entry.splitlines():
            if line.startswith("linux "):
                return self.paths.esp_kernel_dir / PurePosixPath(line.split()[1]).parts[4]
        raise AssertionError(f"{value.name} entry has no linux path")

    def publish(self, values: tuple[ids.StateId, ...], default: ids.StateId) -> None:
        self.boot.publish(tuple(self.target(value) for value in values), default)

    def write_efivar(self, name: str, value: str) -> None:
        data = b"\x07\x00\x00\x00" + value.encode("utf-16-le")
        (self.paths.efivars_dir / name).write_bytes(data)

    def remove_efivar(self, name: str) -> None:
        (self.paths.efivars_dir / name).unlink(missing_ok=True)

    def test_install_uses_the_bootctl_boundary_and_writes_marker_and_format(self) -> None:
        self.paths.esp_marker.unlink()
        self.boot.install()
        self.assertEqual(
            self.runner.calls,
            [[
                "bootctl",
                "install",
                f"--esp-path={self.paths.esp}",
                "--no-variables",
            ]],
        )
        self.assertEqual(self.paths.esp_marker.read_text(encoding="utf-8"), "systemd-boot\n")
        self.assertEqual(self.paths.esp_format_file.read_text(encoding="utf-8"), "1\n")

    def test_selectors_use_strict_and_publication_read_modes(self) -> None:
        self.assertIsNone(self.boot.default(strict=False))
        self.paths.loader_conf.write_text(
            f"default {ids.entry_id(self.apt)}\ntimeout 5\n",
            encoding="utf-8",
        )
        selected = self.boot.default(strict=False)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.state, self.apt.name)
        self.write_efivar(DEFAULT_EFIVAR, ids.entry_id(self.install))
        selected = self.boot.default(strict=False)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.state, self.install.name)
        self.assertEqual(
            self.boot.protected(),
            frozenset({self.install.name, self.apt.name}),
        )
        self.remove_efivar(DEFAULT_EFIVAR)
        self.paths.loader_conf.write_text(
            "default os-000002-20261118-120100Z-apt\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(BootConfigError, "malformed"):
            self.boot.default(strict=True)

        self.write_efivar(ONESHOT_EFIVAR, "os-bad.conf.conf")
        pending = self.boot.oneshot(strict=False)
        self.assertIsNotNone(pending)
        self.assertFalse(pending.known)
        with self.assertRaisesRegex(BootConfigError, "names no existing"):
            self.boot.oneshot(strict=True)

    def test_unreadable_loader_entry_default_does_not_fall_back(self) -> None:
        self.paths.loader_conf.write_text(
            f"default {ids.entry_id(self.apt)}\ntimeout 5\n",
            encoding="utf-8",
        )
        (self.paths.efivars_dir / DEFAULT_EFIVAR).write_bytes(b"\x07\x00\x00")
        self.assertIsNone(self.boot.default(strict=False))
        with self.assertRaisesRegex(BootConfigError, "cannot decode"):
            self.boot.protected()
        with self.assertRaisesRegex(BootConfigError, "cannot decode"):
            self.boot.default(strict=True)

    def test_publish_writes_exact_bls_entry_and_payload(self) -> None:
        self.publish((self.install, self.apt), self.apt)
        entry = self.entry(self.install).read_text(encoding="utf-8")
        pair = self.pair_path(self.install)
        self.assertTrue(entry.startswith(f"{ENTRY_MARKER}\n"))
        self.assertIn(f"linux /EFI/dr/kernels/{pair.name}/vmlinuz\n", entry)
        self.assertIn(f"initrd /EFI/dr/kernels/{pair.name}/initrd.img\n", entry)
        self.assertIn(
            "options root=UUID=11111111-2222-3333-4444-555555555555 "
            f"rootflags=subvol={self.install.name} ro net.ifnames=0 "
            "console=tty0 console=ttyS0,115200\n",
            entry,
        )
        self.assertIn(f"sort-key {self.install.bare}\n", entry)
        self.assertEqual(
            (pair / "vmlinuz").read_bytes(),
            (self.paths.top / self.install.name / "boot" / "vmlinuz-6.12.117+deb13-amd64").read_bytes(),
        )
        selected = self.boot.default(strict=False)
        self.assertIsNotNone(selected)
        self.assertEqual(selected.state, self.apt.name)

    def test_sdboot_refuses_case_alias_foreign_entry(self) -> None:
        alias = self.paths.entry_dir / ids.entry_id(self.install).upper()
        alias.write_text("title foreign owner\n", encoding="utf-8")
        boot = self.make_boot()
        with self.assertRaisesRegex(BootError, "case-insensitively collides"):
            boot.publish((self.target(self.install),), self.install)
        self.assertEqual(alias.read_text(encoding="utf-8"), "title foreign owner\n")
        self.assertFalse(self.paths.loader_conf.exists())

    def test_filesystem_lookup_prefers_exact_then_returns_the_vfat_alias(self) -> None:
        filesystem = Filesystem()
        filesystem.mkdir(self.paths.entry_dir / "exact")
        exact_upper = self.paths.entry_dir / "exact" / "Entry.Conf"
        exact_lower = self.paths.entry_dir / "exact" / "entry.conf"
        exact_upper.write_text("upper\n", encoding="utf-8")
        exact_lower.write_text("lower\n", encoding="utf-8")
        self.assertEqual(filesystem.lookup(exact_lower), exact_lower)
        requested = self.paths.entry_dir / "alias" / "Mixed-Name.CONF"
        actual = self.paths.entry_dir / "alias" / "mixed-name.conf"
        filesystem.mkdir(actual.parent)
        actual.write_text("alias\n", encoding="utf-8")
        self.assertEqual(filesystem.lookup(requested), actual)

    def test_publish_refuses_foreign_exact_entry(self) -> None:
        self.entry(self.install).write_text("title foreign exact name\n", encoding="utf-8")
        with self.assertRaisesRegex(BootError, "is not owned by dr-state"):
            self.publish((self.install,), self.install)
        self.assertEqual(
            self.entry(self.install).read_text(encoding="utf-8"),
            "title foreign exact name\n",
        )

    def test_sdboot_publish_unknown_oneshot_keeps_stale_owned_entries(self) -> None:
        self.publish((self.install, self.apt), self.install)
        stale_entry = self.entry(self.apt)
        stale_pair = self.pair_path(self.apt)
        self.storage.states.remove(self.apt)
        self.write_efivar(ONESHOT_EFIVAR, "os-invalid.conf.conf")
        self.publish((self.install,), self.install)
        self.assertTrue(stale_entry.is_file())
        self.assertTrue(stale_pair.is_dir())
        self.assertTrue(any("pending one-shot is unknown" in text for text in self.warnings))

    def test_sdboot_publish_cleared_oneshot_removes_stale_owned_entry(self) -> None:
        self.publish((self.install, self.apt), self.install)
        stale_entry = self.entry(self.apt)
        stale_pair = self.pair_path(self.apt)
        self.storage.states.remove(self.apt)
        self.remove_efivar(ONESHOT_EFIVAR)
        self.publish((self.install,), self.install)
        self.assertFalse(stale_entry.exists())
        self.assertFalse(stale_pair.exists())

    def test_pending_oneshot_entry_and_pair_survive(self) -> None:
        self.publish((self.install, self.apt), self.install)
        pending_entry = self.entry(self.apt)
        pending_pair = self.pair_path(self.apt)
        self.storage.states.remove(self.apt)
        self.write_efivar(ONESHOT_EFIVAR, ids.entry_id(self.apt))
        self.publish((self.install,), self.install)
        self.assertTrue(pending_entry.is_file())
        self.assertTrue(pending_pair.is_dir())

    def test_shared_pair_survives_shrink_and_is_not_recopied(self) -> None:
        shared_kernel = b"shared-kernel\n"
        shared_initrd = b"shared-initrd\n"
        self.add_state(self.apt, kernel=shared_kernel, initrd=shared_initrd)
        filesystem = RecordingFilesystem()
        self.boot = self.make_boot(filesystem=filesystem)
        self.publish((self.install, self.apt), self.install)
        shared_pair = self.pair_path(self.apt)
        self.assertNotEqual(self.pair_path(self.install), shared_pair)
        later = make_state(3, 14, "manual-later")
        self.add_state(later, kernel=shared_kernel, initrd=shared_initrd)
        self.storage.states.remove(self.apt)
        filesystem.events.clear()
        self.publish((self.install, later), self.install)
        self.assertFalse(self.entry(self.apt).exists())
        self.assertTrue(shared_pair.is_dir())
        self.assertNotIn(("rmtree", shared_pair.name), filesystem.events)
        self.assertNotIn(("replace", "vmlinuz"), filesystem.events)

    def test_copy_check_failure_after_shrink_keeps_shrunk_menu(self) -> None:
        filesystem = RecordingFilesystem()
        self.boot = self.make_boot(filesystem=filesystem)
        self.publish((self.install, self.apt), self.install)
        stale_entry = self.entry(self.apt)
        stale_pair = self.pair_path(self.apt)
        later = make_state(3, 14, "manual-later")
        self.add_state(later, kernel=b"x" * 20000, initrd=b"y" * 5000)
        self.storage.states.remove(self.apt)
        filesystem.events.clear()
        filesystem.space_reads = 0
        filesystem.free = 10
        with self.assertRaisesRegex(CapacityError, "keeping the shrunken menu"):
            self.publish((self.install, later), self.install)
        self.assertFalse(stale_entry.exists())
        self.assertFalse(stale_pair.exists())
        self.assertTrue(self.entry(self.install).is_file())
        self.assertFalse(self.entry(later).exists())
        self.assertEqual(
            self.paths.loader_conf.read_text(encoding="utf-8").splitlines()[0],
            f"default {ids.entry_id(self.install)}",
        )
        first_shrink = min(
            index
            for index, event in enumerate(filesystem.events)
            if event[0] in ("unlink", "rmtree")
        )
        copy_check = next(
            index
            for index, event in enumerate(filesystem.events)
            if event[0] == "statvfs"
        )
        self.assertLess(first_shrink, copy_check)
        self.assertFalse(any(event[0] == "write" for event in filesystem.events))

    def test_capacity_excludes_margin_and_protected_default_and_oneshot(self) -> None:
        filesystem = FakeSpaceFilesystem()
        self.boot = self.make_boot(filesystem=filesystem)
        self.publish((self.install, self.apt), self.install)
        filesystem.free = CAPACITY_MARGIN + 10
        later = make_state(3, 14, "manual-later")
        self.add_state(later)
        self.storage.states.remove(self.install)
        self.storage.states.remove(self.apt)
        self.write_efivar(ONESHOT_EFIVAR, ids.entry_id(self.apt))
        self.assertEqual(
            self.make_boot(filesystem=filesystem).capacity((self.target(later),)),
            10,
        )

    def test_target_pair_only_referenced_by_a_protected_entry_is_credited(self) -> None:
        shared_kernel = b"shared-kernel\n"
        shared_initrd = b"shared-initrd\n"
        self.add_state(self.install, kernel=shared_kernel, initrd=shared_initrd)
        self.add_state(self.apt, kernel=shared_kernel, initrd=shared_initrd)
        self.publish((self.install, self.apt), self.install)
        shared_pair = self.pair_path(self.apt)
        self.storage.states.remove(self.install)
        self.storage.states.remove(self.apt)
        later = make_state(3, 14, "manual-later")
        self.add_state(later, kernel=shared_kernel, initrd=shared_initrd)
        self.write_efivar(ONESHOT_EFIVAR, ids.entry_id(self.apt))
        filesystem = FakeSpaceFilesystem(CAPACITY_MARGIN)
        expected = (shared_pair / "vmlinuz").stat().st_size + (
            shared_pair / "initrd.img"
        ).stat().st_size
        self.assertEqual(
            self.make_boot(filesystem=filesystem).capacity((self.target(later),)),
            expected,
        )

    def test_real_backend_selection_fit_is_refused_one_byte_early_and_published_at_equality(
        self,
    ) -> None:
        shared_kernel = b"shared kernel payload\n" * 4096
        shared_initrd = b"shared initrd payload\n" * 1024
        self.add_state(self.install, kernel=shared_kernel, initrd=shared_initrd)
        self.add_state(self.apt, kernel=shared_kernel, initrd=shared_initrd)
        self.paths.loader_conf.write_text(
            f"default {ids.entry_id(self.install)}\ntimeout 5\n",
            encoding="utf-8",
        )
        filesystem = FakeSpaceFilesystem()
        self.boot = self.make_boot(filesystem=filesystem)
        context = core.Context(
            paths=self.paths,
            storage=self.storage,
            runner=self.runner,
            clock=lambda: datetime(2026, 11, 18, 14, tzinfo=UTC),
            env={},
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            boot=self.boot,
        )
        need = len(shared_kernel) + len(shared_initrd) + 2 * ENTRY_BYTES
        filesystem.free = CAPACITY_MARGIN + need - 1
        with self.assertRaisesRegex(CapacityError, "cannot fit"):
            core._publish(context)
        self.assertFalse(self.entry(self.install).exists())
        self.assertFalse(self.entry(self.apt).exists())

        filesystem.free = CAPACITY_MARGIN + need
        core._publish(context)
        published_pairs = [
            path
            for path in self.paths.esp_kernel_dir.iterdir()
            if path.is_dir() and not path.name.startswith(".dr-state.tmp.")
        ]
        self.assertEqual(len(published_pairs), 1)
        self.assertEqual(
            (published_pairs[0] / "vmlinuz").stat().st_size,
            len(shared_kernel),
        )
        self.assertEqual(
            (published_pairs[0] / "initrd.img").stat().st_size,
            len(shared_initrd),
        )
        self.assertTrue(self.entry(self.install).is_file())
        self.assertTrue(self.entry(self.apt).is_file())

    def test_foreign_entry_keeps_referenced_pair_during_shrink(self) -> None:
        self.publish((self.install, self.apt), self.install)
        protected_pair = self.pair_path(self.apt)
        (self.paths.entry_dir / "foreign.conf").write_text(
            "title foreign\n"
            f"linux /EFI/dr/kernels/{protected_pair.name}/vmlinuz\n"
            f"initrd /EFI/dr/kernels/{protected_pair.name}/initrd.img\n",
            encoding="utf-8",
        )
        self.storage.states.remove(self.apt)
        self.publish((self.install,), self.install)
        self.assertFalse(self.entry(self.apt).exists())
        self.assertTrue(protected_pair.is_dir())

    def test_loader_conf_rename_is_last_after_pairs_and_entries(self) -> None:
        filesystem = RecordingFilesystem()
        self.boot = self.make_boot(filesystem=filesystem)
        self.publish((self.install, self.apt), self.apt)
        loader_index = max(
            index
            for index, event in enumerate(filesystem.events)
            if event == ("write", "loader.conf")
        )
        entry_writes = [
            index
            for index, event in enumerate(filesystem.events)
            if event[0] == "write" and event[1] in (
                ids.entry_id(self.install),
                ids.entry_id(self.apt),
            )
        ]
        pair_replaces = [
            index
            for index, event in enumerate(filesystem.events)
            if event[0] == "replace"
        ]
        self.assertTrue(entry_writes)
        self.assertTrue(pair_replaces)
        self.assertLess(max(entry_writes), loader_index)
        self.assertLess(max(pair_replaces), loader_index)

    def test_repeated_identical_publish_has_no_mutating_filesystem_event(self) -> None:
        filesystem = RecordingFilesystem()
        self.boot = self.make_boot(filesystem=filesystem)
        self.publish((self.install, self.apt), self.install)
        filesystem.events.clear()

        with mock.patch("dr_state.boot.sdboot.write_esp_format") as write_format:
            self.publish((self.install, self.apt), self.install)

        mutating = {"mkdir", "unlink", "rmtree", "copy", "replace", "write"}
        self.assertEqual(
            [],
            [event for event in filesystem.events if event[0] in mutating],
        )
        write_format.assert_not_called()

    def test_loader_rewrite_preserves_settings_and_collapses_duplicates(self) -> None:
        self.paths.loader_conf.write_text(
            "# local settings\n"
            f"default {ids.entry_id(self.install)}\n"
            "auto-entries yes\n"
            f"default {ids.entry_id(self.apt)}\n"
            "timeout 10\n"
            "auto-firmware no\n",
            encoding="utf-8",
        )
        self.publish((self.install, self.apt), self.apt)
        self.assertEqual(
            self.paths.loader_conf.read_text(encoding="utf-8"),
            "# local settings\n"
            "auto-entries yes\n"
            "auto-firmware no\n"
            f"default {ids.entry_id(self.apt)}\n"
            "timeout 5\n",
        )

    def test_strict_loader_reader_refuses_malformed_default_and_timeout(self) -> None:
        for line, field in (("default\n", "default"), ("timeout later\n", "timeout")):
            with self.subTest(field=field):
                self.paths.loader_conf.write_text(line, encoding="utf-8")
                with self.assertRaisesRegex(BootConfigError, "malformed"):
                    self.boot.default(strict=True)
                self.assertIsNone(self.make_boot().default(strict=False))

    def test_entry_write_failure_keeps_the_old_default_resolvable(self) -> None:
        filesystem = RecordingFilesystem()
        self.boot = self.make_boot(filesystem=filesystem)
        self.publish((self.install, self.apt), self.install)
        later = make_state(3, 14, "manual-later")
        self.add_state(later)
        filesystem.fail_on = ("write", ids.entry_id(later))
        with self.assertRaisesRegex(BootError, "simulated failure"):
            self.publish((self.install, later), self.install)
        self.assertTrue(self.entry(self.install).is_file())
        self.assertEqual(
            self.paths.loader_conf.read_text(encoding="utf-8").splitlines()[0],
            f"default {ids.entry_id(self.install)}",
        )

    def test_loader_write_failure_keeps_the_old_default_resolvable(self) -> None:
        filesystem = RecordingFilesystem()
        self.boot = self.make_boot(filesystem=filesystem)
        self.publish((self.install, self.apt), self.install)
        later = make_state(3, 14, "manual-later")
        self.add_state(later)
        filesystem.fail_on = ("write", "loader.conf")
        with self.assertRaisesRegex(BootError, "simulated failure"):
            self.publish((self.install, later), later)
        self.assertTrue(self.entry(self.install).is_file())
        self.assertEqual(
            self.paths.loader_conf.read_text(encoding="utf-8").splitlines()[0],
            f"default {ids.entry_id(self.install)}",
        )

    def test_post_loader_failure_keeps_the_new_default_resolvable(self) -> None:
        filesystem = RecordingFilesystem()
        self.boot = self.make_boot(filesystem=filesystem)
        self.publish((self.install, self.apt), self.install)
        filesystem.fail_on = ("unlink", ids.entry_id(self.install))
        with self.assertRaisesRegex(BootError, "simulated failure"):
            self.publish((self.apt,), self.apt)
        self.assertTrue(self.entry(self.apt).is_file())
        self.assertEqual(
            self.paths.loader_conf.read_text(encoding="utf-8").splitlines()[0],
            f"default {ids.entry_id(self.apt)}",
        )

    def test_loader_entry_default_is_cleared_only_by_an_explicit_set_default(self) -> None:
        self.write_efivar(DEFAULT_EFIVAR, ids.entry_id(self.apt))
        self.publish((self.install, self.apt), self.install)
        self.assertNotIn(["bootctl", "set-default", ""], self.runner.calls)
        self.boot.publish(
            tuple(self.target(value) for value in (self.install, self.apt)),
            self.install,
            clear_default_variable=True,
        )
        self.assertIn(["bootctl", "set-default", ""], self.runner.calls)

    def test_set_default_clears_present_unreadable_loader_entry_default(self) -> None:
        (self.paths.efivars_dir / DEFAULT_EFIVAR).write_bytes(b"\x07\x00\x00")
        self.publish((self.install, self.apt), self.install)
        self.assertNotIn(["bootctl", "set-default", ""], self.runner.calls)

        self.boot.publish(
            tuple(self.target(value) for value in (self.install, self.apt)),
            self.install,
            clear_default_variable=True,
        )
        self.assertIn(["bootctl", "set-default", ""], self.runner.calls)

    def test_set_oneshot_uses_an_existing_owned_entry_and_the_bootctl_boundary(self) -> None:
        self.publish((self.install, self.apt), self.install)
        self.boot.set_oneshot(self.apt)
        self.assertEqual(
            self.runner.calls[-1],
            ["bootctl", "set-oneshot", ids.entry_id(self.apt)],
        )
        self.entry(self.apt).unlink()
        with self.assertRaisesRegex(BootError, "no bootable systemd-boot entry"):
            self.make_boot().set_oneshot(self.apt)

    def test_publish_removes_only_own_scratch(self) -> None:
        self.paths.control_dir.mkdir(parents=True)
        directories = (
            self.paths.esp_kernel_dir,
            self.paths.entry_dir,
            self.paths.loader_dir,
            self.paths.esp_dir,
            self.paths.control_dir,
        )
        for index, directory in enumerate(directories):
            (directory / f".dr-state.tmp.own-{index}").write_text("own\n", encoding="utf-8")
            (directory / f".tmp.lookalike-{index}.backup").write_text(
                "keep\n", encoding="utf-8"
            )
        self.publish((self.install,), self.install)
        for index, directory in enumerate(directories):
            self.assertFalse((directory / f".dr-state.tmp.own-{index}").exists())
            self.assertTrue(
                (directory / f".tmp.lookalike-{index}.backup").is_file()
            )

    def test_changed_initrd_at_same_kernel_version_refreshes_pair(self) -> None:
        self.publish((self.install,), self.install)
        old_pair = self.pair_path(self.install)
        old_kernel = (old_pair / "vmlinuz").read_bytes()
        source = (
            self.paths.top
            / self.install.name
            / "boot"
            / "initrd.img-6.12.117+deb13-amd64"
        )
        source.write_bytes(b"regenerated initrd for the same kernel version\n")
        self.publish((self.install,), self.install)
        new_pair = self.pair_path(self.install)
        self.assertNotEqual(new_pair, old_pair)
        self.assertEqual((new_pair / "vmlinuz").read_bytes(), old_kernel)
        self.assertEqual((new_pair / "initrd.img").read_bytes(), source.read_bytes())
        self.assertIn(
            new_pair.name,
            self.entry(self.install).read_text(encoding="utf-8"),
        )

    def test_copy_pair_checks_destinations_through_the_injected_filesystem(self) -> None:
        filesystem = RecordingFilesystem()
        self.boot = self.make_boot(filesystem=filesystem)
        self.publish((self.install,), self.install)
        pair = self.pair_path(self.install)
        self.assertIn(("lookup", f".dr-state.tmp.{pair.name}"), filesystem.events)
        self.assertIn(("lookup", pair.name), filesystem.events)

    def test_selector_regex_requires_a_literal_conf_extension(self) -> None:
        phantom = self.paths.entry_dir / (
            f"{ids.entry_id(self.install).removesuffix('.conf')}-conf"
        )
        phantom.write_text("title foreign suffix-less entry\n", encoding="utf-8")
        self.paths.loader_conf.write_text(
            f"default {phantom.name}\ntimeout 5\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(BootConfigError, "malformed"):
            self.boot.default(strict=True)

    def test_inspect_maps_counter_suffixed_owned_entry_to_its_state(self) -> None:
        self.publish((self.install,), self.install)
        pair_name = self.pair_path(self.install).name
        path = self.entry(self.install)
        suffixed = path.with_name(path.stem + "+3.conf")
        path.rename(suffixed)
        inspection = self.make_boot().inspect()
        self.assertEqual(inspection.published, frozenset({self.install.name}))
        self.assertEqual(inspection.stale_owned, ())
        self.assertEqual(_entry_refs(suffixed.read_bytes()), frozenset({pair_name}))

    def test_pair_id_matches_the_content_address_rule(self) -> None:
        kernel = hashlib.sha256(b"kernel\n").hexdigest()
        initrd = hashlib.sha256(b"initrd\n").hexdigest()
        pair = hashlib.sha256((kernel + initrd).encode("ascii")).hexdigest()
        self.assertEqual(
            _pair_id("6.12.117+deb13-amd64", kernel, initrd),
            f"6.12.117+deb13-amd64-{pair}",
        )

    def test_pair_id_refuses_each_unsafe_input(self) -> None:
        kernel = "a" * 64
        initrd = "b" * 64
        cases = (
            (
                "bad kernel hash",
                ("6.12.0", "a" * 63 + "g", initrd),
            ),
            (
                "bad initrd hash",
                ("6.12.0", kernel, "b" * 63 + "g"),
            ),
            (
                "uppercase hash",
                ("6.12.0", "A" * 64, initrd),
            ),
            ("bad kernel version", ("6.12/evil", kernel, initrd)),
            ("empty kernel version", ("", kernel, initrd)),
            ("vfat component too long", ("a" * 191, kernel, initrd)),
        )
        for label, arguments in cases:
            with self.subTest(label=label):
                with self.assertRaises(BootError):
                    _pair_id(*arguments)

    def test_pair_id_requires_both_hash_arguments(self) -> None:
        kernel = "a" * 64
        initrd = "b" * 64
        with self.assertRaises(TypeError):
            _pair_id("6.12.0", kernel)
        with self.assertRaises(TypeError):
            _pair_id("6.12.0", initrd_hash=initrd)

    def test_entry_text_refuses_an_unsafe_uuid_and_kernel_id_mismatch(self) -> None:
        kernel = "a" * 64
        initrd = "b" * 64
        unsafe_uuid = replace(
            self.target(self.install),
            locator="not/a-uuid",
        )
        with self.assertRaisesRegex(BootError, "root locator"):
            self.boot._entry_text(
                unsafe_uuid,
                _pair_id("6.12.117+deb13-amd64", kernel, initrd),
            )
        mismatch = _pair_id("6.12.107+deb13-amd64", kernel, initrd)
        with self.assertRaisesRegex(BootError, "does not name version"):
            self.boot._entry_text(self.target(self.install), mismatch)

    def test_entry_refs_cover_empty_dedupe_multi_case_and_crlf(self) -> None:
        pair_a = f"6.12.0-{'a' * 64}"
        pair_b = f"6.12.0-{'b' * 64}"
        pair_c = f"6.12.0-{'c' * 64}"
        mixed_pair = f"6.12.0-{'ABCDEF012345' * 5 + 'ABCD'}"
        cases = (
            ("empty", "title only\n", frozenset()),
            (
                "dedupe",
                f"linux /EFI/dr/kernels/{pair_a}/vmlinuz\n"
                f"initrd /EFI/dr/kernels/{pair_a}/initrd.img\n",
                frozenset({pair_a.casefold()}),
            ),
            (
                "multi-path",
                f"linux /EFI/dr/kernels/{pair_a}/vmlinuz "
                f"/EFI/dr/kernels/{pair_b}/vmlinuz\n"
                f"initrd /EFI/dr/kernels/{pair_b}/initrd.img "
                f"/EFI/dr/kernels/{pair_c}/initrd.img\n",
                frozenset(
                    {pair_a.casefold(), pair_b.casefold(), pair_c.casefold()}
                ),
            ),
            (
                "vfat-case",
                f"LINUX /eFi/DR/KeRnElS/{mixed_pair}/vmlinuz\n"
                f"INITRD /EFI/dr/kernels/{mixed_pair.casefold()}/INITRD.IMG\n",
                frozenset({mixed_pair.casefold()}),
            ),
            (
                "crlf-single",
                f"linux /EFI/dr/kernels/{pair_a}/vmlinuz\r\n"
                f"initrd /EFI/dr/kernels/{pair_b}/initrd.img\r\n",
                frozenset({pair_a.casefold(), pair_b.casefold()}),
            ),
            (
                "crlf-multi",
                f"linux /EFI/dr/kernels/{pair_a}/vmlinuz\r\n"
                f"initrd /EFI/dr/kernels/{pair_a}/initrd.img "
                f"/EFI/dr/kernels/{pair_b}/initrd.img\r\n",
                frozenset({pair_a.casefold(), pair_b.casefold()}),
            ),
        )
        for label, text, expected in cases:
            with self.subTest(label=label):
                self.assertEqual(_entry_refs(text.encode()), expected)

    def test_pair_payload_keys_casefold_dedupe_and_use_full_bytes(self) -> None:
        shared_kernel = b"shared kernel\n"
        shared_initrd = b"shared initrd\n"
        self.add_state(self.install, kernel=shared_kernel, initrd=shared_initrd)
        self.add_state(self.apt, kernel=shared_kernel, initrd=shared_initrd)
        lower = self.target(self.install)
        upper = self.target(self.apt)
        upper = replace(
            upper,
            kernel=replace(upper.kernel, version="6.12.117+DEB13-AMD64"),
        )
        lower_payload = self.boot.payload(lower)
        upper_payload = self.boot.payload(upper)
        expected_pair_bytes = (
            lower.kernel.vmlinuz.stat().st_size
            + lower.kernel.initrd.stat().st_size
        )
        self.assertEqual(lower_payload[0][0], lower_payload[0][0].casefold())
        self.assertEqual(upper_payload[0][0], upper_payload[0][0].casefold())
        self.assertEqual(lower_payload[0][0], upper_payload[0][0])
        self.assertEqual(lower_payload[0][1], expected_pair_bytes)
        self.assertEqual(upper_payload[0][1], expected_pair_bytes)
        self.assertEqual(lower_payload[1][1], ENTRY_BYTES)
        self.assertEqual(upper_payload[1][1], ENTRY_BYTES)

    def test_shrink_orphan_cases_preserve_references_and_vfat_case(self) -> None:
        def materialize(
            label: str,
            name: str,
            refs: frozenset[str],
        ) -> tuple[_Pair, _Entry]:
            pair_path = self.paths.esp_kernel_dir / name
            pair_path.mkdir()
            (pair_path / "vmlinuz").write_bytes(b"kernel\n")
            (pair_path / "initrd.img").write_bytes(b"initrd\n")
            entry_path = self.paths.entry_dir / f"{label}.conf"
            data = (
                f"linux /EFI/dr/kernels/{name}/vmlinuz\n"
                f"initrd /EFI/dr/kernels/{name}/initrd.img\n"
            ).encode()
            entry_path.write_bytes(data)
            return (
                _Pair(pair_path, name, "6.12.0"),
                _Entry(entry_path, entry_path.name, data, False, None, refs),
            )

        keep = f"6.12.0-{'a' * 64}"
        orphan_b = f"6.12.0-{'b' * 64}"
        orphan_c = f"6.12.0-{'c' * 64}"
        pair_keep, entry_keep = materialize("foreign", keep, frozenset({keep}))
        pair_orphan_b, entry_orphan_b = materialize(
            "foreign-b", orphan_b, frozenset()
        )
        pair_orphan_c, entry_orphan_c = materialize(
            "foreign-c", orphan_c, frozenset()
        )
        self.boot._shrink(
            _Inventory(
                (entry_keep, entry_orphan_b, entry_orphan_c),
                (pair_keep, pair_orphan_b, pair_orphan_c),
            ),
            target_entries=frozenset(),
            target_pairs=frozenset(),
        )
        self.assertTrue(pair_keep.path.is_dir())
        self.assertFalse(pair_orphan_b.path.exists())
        self.assertFalse(pair_orphan_c.path.exists())

        mixed = f"6.12.0-{'ABCDEF012345' * 5 + 'A' * 4}"
        pair_mixed, entry_mixed = materialize(
            "foreign-mixed",
            mixed,
            frozenset({mixed.casefold()}),
        )
        self.boot._shrink(
            _Inventory((entry_mixed,), (pair_mixed,)),
            target_entries=frozenset(),
            target_pairs=frozenset(),
        )
        self.assertTrue(pair_mixed.path.is_dir())

        no_pairs = self.paths.esp_kernel_dir / orphan_b
        self.boot._shrink(
            _Inventory((entry_orphan_b,), ()),
            target_entries=frozenset(),
            target_pairs=frozenset(),
        )
        self.assertFalse(no_pairs.exists())

    def test_shrink_orphan_table_covers_none_all_order_and_unsafe_names(self) -> None:
        def pair_name(label: str) -> str:
            name = f"6.12.0-{label.encode().hex().ljust(64, '0')[:64]}"
            if len(name) != len("6.12.0-") + 64:
                raise AssertionError(f"invalid orphan-table fixture label {label!r}")
            return name

        def make_pair(label: str) -> _Pair:
            name = pair_name(label)
            pair_path = self.paths.esp_kernel_dir / name
            pair_path.mkdir()
            (pair_path / "vmlinuz").write_bytes(b"kernel\n")
            (pair_path / "initrd.img").write_bytes(b"initrd\n")
            return _Pair(pair_path, name, "6.12.0")

        def make_entry(label: str, refs: tuple[str, ...]) -> _Entry:
            entry_path = self.paths.entry_dir / f"{label}.conf"
            text = "".join(
                f"linux /EFI/dr/kernels/{ref}/vmlinuz\n"
                f"initrd /EFI/dr/kernels/{ref}/initrd.img\n"
                for ref in refs
            )
            data = text.encode()
            entry_path.write_bytes(data)
            return _Entry(entry_path, entry_path.name, data, False, None, frozenset(refs))

        pair_none_a = make_pair("none-a")
        pair_none_b = make_pair("none-b")
        none_entry_a = make_entry("none-ref-a", (pair_none_a.name,))
        none_entry_b = make_entry(
            "none-ref-b",
            (pair_none_a.name, pair_none_b.name),
        )
        self.boot._shrink(
            _Inventory(
                (none_entry_a, none_entry_b),
                (pair_none_a, pair_none_b),
            ),
            target_entries=frozenset(),
            target_pairs=frozenset(),
        )
        self.assertTrue(pair_none_a.path.exists())
        self.assertTrue(pair_none_b.path.exists())

        pair_all_a = make_pair("all-a")
        pair_all_b = make_pair("all-b")
        all_entry_a = make_entry("all-a-entry", ())
        all_entry_b = make_entry("all-b-entry", ())
        self.boot._shrink(
            _Inventory(
                (all_entry_a, all_entry_b),
                (pair_all_a, pair_all_b),
            ),
            target_entries=frozenset(),
            target_pairs=frozenset(),
        )
        self.assertFalse(pair_all_a.path.exists())
        self.assertFalse(pair_all_b.path.exists())

        keep_pair = make_pair("keep")
        a_pair = make_pair("a-old")
        z_pair = make_pair("z-old")
        keep_entry = make_entry("order-keep", (keep_pair.name,))
        self.boot._shrink(
            _Inventory(
                (keep_entry,),
                (z_pair, keep_pair, a_pair),
            ),
            target_entries=frozenset(),
            target_pairs=frozenset(),
        )
        self.assertTrue(keep_pair.path.exists())
        self.assertFalse(z_pair.path.exists())
        self.assertFalse(a_pair.path.exists())

        invalid_pair = self.paths.esp_kernel_dir / "unsafe-existing-pair"
        invalid_pair.mkdir()
        (invalid_pair / "vmlinuz").write_bytes(b"kernel\n")
        (invalid_pair / "initrd.img").write_bytes(b"initrd\n")
        self.assertNotIn(
            invalid_pair,
            {pair.path for pair in self.boot._fresh_scan().pairs},
        )
        self.boot._shrink(
            self.boot._fresh_scan(),
            target_entries=frozenset(),
            target_pairs=frozenset(),
        )
        self.assertTrue(invalid_pair.exists())
        self.assertEqual(
            _entry_refs(b"linux /EFI/dr/kernels/../escape/vmlinuz\n"),
            frozenset(),
        )


if __name__ == "__main__":
    unittest.main()
