"""systemd-boot behavior at fake filesystem, space, efivar, and bootctl boundaries."""

from __future__ import annotations

import hashlib
import unittest
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import _support
from dr_state import ids
from dr_state.boot import BootTarget
from dr_state.boot.sdboot import (
    CAPACITY_MARGIN,
    DEFAULT_EFIVAR,
    ENTRY_MARKER,
    ENTRY_BYTES,
    ONESHOT_EFIVAR,
    Filesystem,
    SdbootBoot,
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

    def __init__(self, states: list[ids.StateId]) -> None:
        self.states = states

    def list(self) -> list[Stored]:
        return [Stored(state.name, None, None) for state in self.states]

    def running(self) -> str | None:
        return None

    def kernels(self, state: str) -> list[KernelPair]:
        _ = state
        return []


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
        self.storage = FakeStorage(self.states)
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

    def test_unreadable_loader_entry_default_is_never_cleared(self) -> None:
        (self.paths.efivars_dir / DEFAULT_EFIVAR).write_bytes(b"\x07\x00\x00")
        self.publish((self.install, self.apt), self.install)
        self.boot.publish(
            tuple(self.target(value) for value in (self.install, self.apt)),
            self.install,
            clear_default_variable=True,
        )
        self.assertNotIn(["bootctl", "set-default", ""], self.runner.calls)

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


class SdbootPayloadBoundaryTest(unittest.TestCase):
    def test_payload_reports_dedupe_keys_and_full_pair_bytes(self) -> None:
        self.assertEqual(ENTRY_BYTES, 4096)


if __name__ == "__main__":
    unittest.main()
