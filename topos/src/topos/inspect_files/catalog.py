"""Allowlisted inspection catalog — defines every permitted file/log inspection
kind and how to build its path-preview and command-preview information.

No file content reads, no subprocess, no host mutation. Every kind is an enum
member so unknown kinds are rejected at import time rather than at runtime.

Path safety rules:
- Path previews are normalised lexically without touching the filesystem.
- Absolute path targets supplied directly by users are rejected unless they are
  derived from the allowlisted kind (e.g. cgroup-files paths are always
  relative to a cgroup root, not arbitrary absolute paths).
- Symlinks are never followed; no files are opened.
"""

from __future__ import annotations

import enum
import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


class InspectFilesKind(str, enum.Enum):
    """Allowlisted file/log inspection kinds. Add new kinds here (and a builder)."""

    DOCKER_JSON_LOG = "docker-json-log"
    SYSTEMD_JOURNAL = "systemd-journal"
    CGROUP_FILES = "cgroup-files"


# ---------------------------------------------------------------------------
# Builder helpers — return (path_previews, command_previews) using
# Path objects and argv lists, never shell strings.
# ---------------------------------------------------------------------------

# Pattern: a Docker container id is 64 hex chars (full) or leading unique prefix.
_DOCKER_ID_PATTERN = re.compile(r"^[a-f0-9]{6,64}$")
# Docker names are not paths and should stay shell-token boring.
_DOCKER_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
# Systemd unit name pattern: must not start with a dash (would parse as an
# option), must not contain glob/reserved characters, whitespace, or shell
# metacharacters.  Alphanumeric, dots, dashes, underscores, @, and + are safe.
_SYSTEMD_UNIT_PATTERN = re.compile(r"^[a-zA-Z0-9@._+-]+$")
# Cgroup path segment pattern: safe characters only.
_CGROUP_PATH_PATTERN = re.compile(r"^[a-zA-Z0-9@._/-]+$")


def _path_preview_normalise(p: Path) -> Path:
    """Lexically normalise a path preview without touching the filesystem.

    - Expands ~ via Path.home().
    - Collapses repeated separators and '..' segments lexically.
    """
    expanded = p.expanduser()
    return Path(posixpath.normpath(str(expanded)))


def _docker_json_log(target: str) -> tuple[list[Path], list[list[str]]]:
    """Plan the expected Docker json-file log path for a container id/name.

    Returns (path_previews, command_previews) where command_previews are
    example read commands (for display only — never executed).
    """
    _validate_docker_target(target)
    log_dir = Path("/var/lib/docker/containers") / target
    log_file = log_dir / f"{target}-json.log"
    path_previews = [_path_preview_normalise(log_dir), _path_preview_normalise(log_file)]
    # Preview-only read commands — never executed.
    command_previews = [
        ["cat", str(_path_preview_normalise(log_file))],
        ["tail", "-n", "50", str(_path_preview_normalise(log_file))],
    ]
    return path_previews, command_previews


def _systemd_journal(target: str) -> tuple[list[Path], list[list[str]]]:
    """Plan a journalctl query argv for a systemd unit.

    Returns (path_previews, command_previews) — the journalctl argv is
    a structured list, never a shell string.
    """
    _validate_systemd_target(target)
    # The journal path itself is system-managed; preview the unit's known
    # paths (cgroup, service file).
    cgroup_path = Path("/sys/fs/cgroup/system.slice") / target
    unit_file = Path("/etc/systemd/system") / target
    path_previews = [
        _path_preview_normalise(cgroup_path),
        _path_preview_normalise(unit_file),
    ]
    # Structured argv list — never a shell string.
    command_previews = [
        ["/usr/bin/journalctl", "--unit", target, "--no-pager", "-n", "100"],
        ["/usr/bin/journalctl", "--unit", target, "--follow", "-n", "10"],
        ["/usr/bin/systemctl", "status", target, "--no-pager"],
    ]
    return path_previews, command_previews


def _cgroup_files(target: str) -> tuple[list[Path], list[list[str]]]:
    """List a fixed set of known cgroup filenames relevant to topos snapshots.

    Target is a cgroup path (e.g. 'system.slice/ssh.service').
    Returns preview paths under a cgroup v2 root, no content reads.
    """
    relative_target = _normalise_cgroup_target(target)
    cgroup_root = Path("/sys/fs/cgroup")
    resolved_target = _path_preview_normalise(cgroup_root / relative_target)
    # Allowlisted set of cgroup filenames relevant to topos snapshots.
    cgroup_files = [
        "memory.current",
        "memory.min",
        "memory.low",
        "memory.high",
        "memory.max",
        "memory.stat",
        "memory.events",
        "memory.pressure",
        "cpu.stat",
        "cpu.max",
        "cpu.weight",
        "cpu.pressure",
        "io.stat",
        "io.max",
        "io.pressure",
        "pids.current",
        "pids.max",
        "pids.events",
        "cgroup.procs",
        "cgroup.events",
        "cgroup.stat",
    ]
    path_previews = [resolved_target / fn for fn in cgroup_files]
    # No command previews for cgroup files — they are plain file reads.
    command_previews: list[list[str]] = []
    return path_previews, command_previews


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_docker_target(target: str) -> None:
    """Reject docker targets that look like absolute paths or unsafe strings."""
    if not target or target.startswith("/") or target.startswith("."):
        msg = f"docker target must be a container id or name, not a path: {target!r}"
        raise ValueError(msg)
    if not _DOCKER_ID_PATTERN.match(target) and "/" in target:
        msg = f"docker target contains unsafe path characters: {target!r}"
        raise ValueError(msg)
    if not _DOCKER_ID_PATTERN.match(target) and not _DOCKER_NAME_PATTERN.match(target):
        msg = f"docker target contains unsafe characters: {target!r}"
        raise ValueError(msg)


def _validate_systemd_target(target: str) -> None:
    """Reject systemd targets that look like absolute paths or unsafe strings."""
    if not target or target.startswith("/") or target.startswith("."):
        msg = f"systemd target must be a unit name, not a path: {target!r}"
        raise ValueError(msg)
    if target.startswith("-"):
        msg = f"systemd target must not start with dash (would parse as option): {target!r}"
        raise ValueError(msg)
    if not _SYSTEMD_UNIT_PATTERN.match(target):
        msg = f"systemd target contains unsafe characters: {target!r}"
        raise ValueError(msg)


def _validate_cgroup_target(target: str) -> None:
    """Reject cgroup targets that are unsafe."""
    _normalise_cgroup_target(target)


def _normalise_cgroup_target(target: str) -> str:
    """Return a safe path relative to /sys/fs/cgroup, or raise ValueError."""
    if not target:
        msg = "cgroup target must not be empty"
        raise ValueError(msg)
    if target.startswith("/sys/fs/cgroup/"):
        relative = target.removeprefix("/sys/fs/cgroup/")
    elif target.startswith("sys/fs/cgroup/"):
        relative = target.removeprefix("sys/fs/cgroup/")
    elif target.startswith("/"):
        msg = f"cgroup target path must be under /sys/fs/cgroup/: {target!r}"
        raise ValueError(msg)
    else:
        relative = target
    if not relative:
        msg = "cgroup target must not be empty"
        raise ValueError(msg)
    if not _CGROUP_PATH_PATTERN.match(relative):
        msg = f"cgroup target contains unsafe characters: {target!r}"
        raise ValueError(msg)
    if any(part in {"", ".", ".."} for part in relative.split("/")):
        msg = f"cgroup target contains unsafe path segments: {target!r}"
        raise ValueError(msg)
    return relative


# ---------------------------------------------------------------------------
# Catalog entry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogEntry:
    kind: InspectFilesKind
    builder: Callable[[str], tuple[list[Path], list[list[str]]]]
    description: str
    kind_label: str


INSPECT_CATALOG: dict[InspectFilesKind, CatalogEntry] = {
    InspectFilesKind.DOCKER_JSON_LOG: CatalogEntry(
        InspectFilesKind.DOCKER_JSON_LOG,
        _docker_json_log,
        "Plan the expected Docker json-file log path for a container id or name.",
        "Docker JSON log",
    ),
    InspectFilesKind.SYSTEMD_JOURNAL: CatalogEntry(
        InspectFilesKind.SYSTEMD_JOURNAL,
        _systemd_journal,
        "Plan a journalctl query for a systemd unit by unit name.",
        "Systemd journal",
    ),
    InspectFilesKind.CGROUP_FILES: CatalogEntry(
        InspectFilesKind.CGROUP_FILES,
        _cgroup_files,
        "List a fixed set of known cgroup filenames relevant to topos snapshots.",
        "Cgroup files",
    ),
}
