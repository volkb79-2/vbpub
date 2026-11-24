"""Path namespace and exception-to-exit-code tests."""

from __future__ import annotations

import unittest
from pathlib import Path

import _support  # noqa: F401
from dr_state import errors
from dr_state.paths import Paths


class PathsTest(_support.TemporaryRootTest):
    def test_every_documented_runtime_path_is_under_the_sysroot(self) -> None:
        expected = {
            "top": "/.top",
            "control_dir": "/.top/.dr-state",
            "metadata_dir": "/.top/.dr-state/metadata",
            "cache_dir": "/.top/.dr-state/cache",
            "counter_file": "/.top/.dr-state/last-sequence",
            "control_format_file": "/.top/.dr-state/format",
            "esp": "/boot/efi",
            "esp_dir": "/boot/efi/EFI/dr",
            "esp_format_file": "/boot/efi/EFI/dr/format",
            "esp_marker": "/boot/efi/EFI/dr/bootloader",
            "esp_kernel_dir": "/boot/efi/EFI/dr/kernels",
            "esp_grub_cfg": "/boot/efi/EFI/dr/grub.cfg",
            "esp_grubenv": "/boot/efi/EFI/dr/grubenv",
            "loader_dir": "/boot/efi/loader",
            "loader_conf": "/boot/efi/loader/loader.conf",
            "entry_dir": "/boot/efi/loader/entries",
            "lock_file": "/run/dr-state.lock",
            "os_release": "/etc/os-release",
            "fstab": "/etc/fstab",
            "proc_cmdline": "/proc/cmdline",
            "proc_osrelease": "/proc/sys/kernel/osrelease",
            "efivars_dir": "/sys/firmware/efi/efivars",
        }
        for property_name, logical in expected.items():
            self.assertEqual(
                getattr(self.paths, property_name),
                self.root / logical.lstrip("/"),
            )

    def test_default_sysroot_is_the_real_root(self) -> None:
        self.assertEqual(Paths.from_sysroot().sysroot, Path("/"))
        self.assertEqual(Paths.from_sysroot().top, Path("/.top"))
        with self.assertRaisesRegex(ValueError, "absolute"):
            Paths(Path("relative"))
        with self.assertRaisesRegex(ValueError, "not absolute"):
            self.paths.absolute("boot/efi")
        with self.assertRaisesRegex(ValueError, "escapes"):
            self.paths.absolute("/../outside")


class ExitCodeTest(unittest.TestCase):
    def test_exact_error_mapping(self) -> None:
        cases = (
            (errors.UsageError(), 2),
            (errors.Refused(), 3),
            (errors.FormatError(), 3),
            (errors.BootConfigError(), 3),
            (errors.Unsupported(), 4),
            (errors.CheckFailed(), 5),
            (errors.LockTimeout(), 6),
            (errors.StorageError(), 1),
            (errors.BootError(), 1),
            (errors.CapacityError(), 1),
            (RuntimeError(), 70),
        )
        for error, wanted in cases:
            with self.subTest(error=error, wanted=wanted):
                self.assertEqual(errors.exit_code(error), wanted)

    def test_exit_code_requires_an_exception(self) -> None:
        self.assertEqual(errors.exit_code(ValueError("not a dr-state error")), 70)


if __name__ == "__main__":
    unittest.main()
