"""Resolve every absolute runtime path beneath one optional sysroot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True, slots=True)
class Paths:
    """The complete path vocabulary for a dr-state invocation."""

    sysroot: Path

    def __post_init__(self) -> None:
        root = Path(self.sysroot)
        if not root.is_absolute():
            raise ValueError("sysroot must be an absolute path")
        object.__setattr__(self, "sysroot", root)

    @classmethod
    def from_sysroot(cls, value: str | Path | None = None) -> Paths:
        """Build paths for an optional command-line sysroot."""

        return cls(Path("/") if value is None else Path(value))

    def absolute(self, path: str | PurePosixPath) -> Path:
        """Place a namespaced absolute path under this sysroot."""

        requested = PurePosixPath(path)
        if not requested.is_absolute():
            raise ValueError(f"runtime path is not absolute: {path!s}")
        if ".." in requested.parts:
            raise ValueError(f"runtime path escapes the sysroot: {path!s}")
        return self.sysroot.joinpath(*requested.parts[1:])

    @property
    def top(self) -> Path:
        return self.absolute("/.top")

    @property
    def control_dir(self) -> Path:
        return self.top / ".dr-state"

    @property
    def metadata_dir(self) -> Path:
        return self.control_dir / "metadata"

    @property
    def cache_dir(self) -> Path:
        return self.control_dir / "cache"

    @property
    def counter_file(self) -> Path:
        return self.control_dir / "last-sequence"

    @property
    def control_format_file(self) -> Path:
        return self.control_dir / "format"

    @property
    def esp(self) -> Path:
        return self.absolute("/boot/efi")

    @property
    def esp_dir(self) -> Path:
        return self.esp / "EFI" / "dr"

    @property
    def esp_format_file(self) -> Path:
        return self.esp_dir / "format"

    @property
    def esp_marker(self) -> Path:
        return self.esp_dir / "bootloader"

    @property
    def esp_kernel_dir(self) -> Path:
        return self.esp_dir / "kernels"

    @property
    def esp_grub_cfg(self) -> Path:
        return self.esp_dir / "grub.cfg"

    @property
    def esp_grubenv(self) -> Path:
        return self.esp_dir / "grubenv"

    @property
    def loader_dir(self) -> Path:
        return self.esp / "loader"

    @property
    def loader_conf(self) -> Path:
        return self.loader_dir / "loader.conf"

    @property
    def entry_dir(self) -> Path:
        return self.loader_dir / "entries"

    @property
    def lock_file(self) -> Path:
        return self.absolute("/run/dr-state.lock")

    @property
    def os_release(self) -> Path:
        return self.absolute("/etc/os-release")

    @property
    def fstab(self) -> Path:
        return self.absolute("/etc/fstab")

    @property
    def proc_cmdline(self) -> Path:
        return self.absolute("/proc/cmdline")

    @property
    def proc_osrelease(self) -> Path:
        return self.absolute("/proc/sys/kernel/osrelease")

    @property
    def efivars_dir(self) -> Path:
        return self.absolute("/sys/firmware/efi/efivars")
