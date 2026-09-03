from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import subprocess
import hashlib
import tempfile
from typing import Any


class ActionError(RuntimeError):
    pass


_SAFE_COMMANDS = {
    "apt-cache": {"policy"},
    "apt-get": {"update", "install", "upgrade"},
    "bash": {"-c"},
    "blkid": set(),
    "blockdev": {"--getsize64", "--rereadpt"},
    "btrfs": {"filesystem"},
    "curl": set(),
    "dd": set(),
    "df": {"-B1", "-h", "--output=used"},
    "dumpe2fs": {"-h"},
    "e2fsck": {"-f", "-n", "-y", "-v"},
    "fallocate": {"-l"},
    "findmnt": {"-n", "-rn", "-o", "SOURCE,TARGET,FSTYPE"},
    "sha256sum": set(),
    "free": {"-g"},
    "git": {"clone", "fetch", "reset"},
    "hostname": {"-f", "-I"},
    "lsblk": {"-no", "-o"},
    "mkdir": {"-p"},
    "mkswap": set(),
    "modprobe": {"zstd"},
    "partprobe": set(),
    "partx": {"-a", "-d", "-u"},
    "pip3": {"install"},
    "resize2fs": set(),
    "sfdisk": {"--dump", "--force", "--no-reread"},
    "sh": {"-c"},
    "swapon": {"-p", "--show=NAME,TYPE,SIZE,PRIO", "--noheadings"},
    "swapoff": {"-a"},
    "sync": set(),
    "sysctl": {"-n"},
    "systemctl": {
        "daemon-reload",
        "disable",
        "enable",
        "enable-now",
        "reboot",
        "restart",
        "show",
        "start",
    },
    "tee": {"-a"},
    "udevadm": {"settle", "trigger"},
    "update-grub": set(),
    "update-initramfs": {"-u"},
    "wget": {"-q"},
    "xfs_growfs": {"/"},
}


@dataclass(frozen=True)
class PlannedAction:
    argv: tuple[str, ...]
    description: str
    dangerous: bool = False


class HostActions:
    """All privileged operations pass through one auditable execution point."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.planned: list[PlannedAction] = []
        self.dry_run_writes: dict[str, str] = {}

    @staticmethod
    def _validate(argv: list[str], allow_shell: bool = False) -> None:
        if not argv or any(not isinstance(value, str) or not value for value in argv):
            raise ActionError("command must be a non-empty list of non-empty strings")
        executable = Path(argv[0])
        if not executable.is_absolute():
            raise ActionError(f"commands must use an absolute executable path: {argv[0]}")
        command = executable.name
        if command not in _SAFE_COMMANDS:
            raise ActionError(f"executable is not on the action allowlist: {command}")
        allowed = _SAFE_COMMANDS[command]
        if allowed:
            if command in {"apt-get", "git", "systemctl"}:
                if not any(arg in allowed for arg in argv[1:]):
                    raise ActionError(f"{command} operation is not allowlisted")
            elif command != "blkid" and not all(arg in allowed for arg in argv[1:] if arg.startswith("-")):
                unexpected = [arg for arg in argv[1:] if arg.startswith("-") and arg not in allowed]
                if unexpected:
                    raise ActionError(f"{command} option(s) are not allowlisted: {unexpected}")
        if not allow_shell and command in {"bash", "sh"}:
            raise ActionError("shell commands are only accepted through write_file templates")

    def run(self, argv: list[str], description: str = "", dangerous: bool = False) -> str | None:
        self._validate(list(argv))
        planned = PlannedAction(tuple(argv), description or shlex.join(argv), dangerous)
        self.planned.append(planned)
        if self.dry_run:
            return None
        result = subprocess.run(
            argv,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if result.returncode != 0:
            raise ActionError(
                f"action failed ({result.returncode}): {planned.description}\n{result.stdout}"
            )
        return result.stdout

    def read(self, argv: list[str]) -> str:
        output = self.run(argv, dangerous=False)
        return output or ""

    def write_file(self, path: str, content: str, mode: int = 0o644) -> None:
        target = PurePosixPath(path)
        if not path.startswith("/") or ".." in target.parts:
            raise ActionError(f"output path must be absolute and normalized: {path}")
        self.planned.append(PlannedAction(("/usr/bin/tee", path), f"write {path}", True))
        if self.dry_run:
            self.dry_run_writes[path] = content
            return
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=destination.parent, prefix=f".{destination.name}.", delete=False
            ) as handle:
                temporary_name = handle.name
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, mode)
            os.replace(temporary_name, destination)
        except BaseException:
            if temporary_name:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
            raise

    def mkdir(self, path: str) -> None:
        self.planned.append(PlannedAction(("/usr/bin/mkdir", "-p", path), f"create directory {path}", True))
        if not self.dry_run:
            Path(path).mkdir(parents=True, exist_ok=True)

    def copy_file(self, source: str, destination: str, mode: int = 0o644) -> None:
        content = Path(source).read_text(encoding="utf-8")
        self.write_file(destination, content, mode)

    def exists(self, path: str) -> bool:
        return Path(path).exists()

    def which(self, executable: str) -> bool:
        return shutil.which(executable) is not None
