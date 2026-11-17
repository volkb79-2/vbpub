"""One repo-root config (``repo-paths.toml``) for the shared paths of a repository, and the resolver for it.

Scripts and tests never hardcode where ISOs, base images or VM state live: they ask ``RepoPaths.resolve(name)``
(or the ``cowtree path NAME`` command). The config names each path, gives its plain location inside the checkout
and, optionally, a location below ``$MDT_COW_ROOT/<repo-name>/`` where the same folder lives as a btrfs
subvolume. The checkout itself stays where it is; only the selected folders are CoW subvolumes.

This module knows no VM or Docker concept: names such as ``iso`` exist only in the repository's own config file.
It is stdlib-only (Python >= 3.11 for ``tomllib``). Resolving never creates anything in the repository or in
the CoW area (``cowtree.detect.detect`` maintains the area's own ``.cowtree`` metadata, as everywhere else);
only ``provision`` creates directories and subvolumes.
"""
from __future__ import annotations

import os
import re
import subprocess
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .backends import Ops
from .detect import detect
from .errors import BackendFailed, PathRefused, RepoPathsInvalid, UsageError

CONFIG_NAME = "repo-paths.toml"
SCHEMA = 1
PLACEHOLDER = "{project}"
_TOP_KEYS = frozenset({"schema", "paths"})
_ENTRY_KEYS = frozenset({"repo", "cow", "compression"})
_COMPRESSION = re.compile(r"none|zstd|zstd:([1-9]|1[0-5])")
_BRACES = re.compile(r"[{}]")
_BAD_SEGMENTS = ("", ".", "..")
_SUBVOLUME_INODE = 256

Capture = Callable[[Sequence[str]], tuple[int, str]]


@dataclass(frozen=True)
class PathEntry:
    """One named path. ``cow`` is None for a path that is never CoW; ``compression`` None leaves the default."""

    name: str
    repo: str
    cow: str | None
    compression: str | None


@dataclass(frozen=True)
class Resolved:
    """The answer for one name: ``mode`` is ``cow`` (the subvolume under the CoW area) or ``plain`` (in the repo)."""

    path: Path
    mode: str
    name: str
    project: str | None

    def as_dict(self) -> dict[str, str | None]:
        return {"name": self.name, "project": self.project, "path": str(self.path), "mode": self.mode}


def _capture(argv: Sequence[str]) -> tuple[int, str]:
    """The default command runner: the exit status and the combined stdout and stderr."""
    try:
        done = subprocess.run(
            list(argv), check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
        )
    except OSError as exc:
        raise BackendFailed("btrfs", exc.errno, None, f"cannot run {argv[0]!r}: {exc}") from exc
    return done.returncode, done.stdout


def _is_subvolume(path: str) -> bool:
    return os.lstat(path).st_ino == _SUBVOLUME_INODE


@dataclass(frozen=True)
class Btrfs:
    """The injectable boundary of provisioning: run a command, and tell a subvolume from a plain directory."""

    run: Capture = _capture
    is_subvolume: Callable[[str], bool] = _is_subvolume


def _check_relative(value: object, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise RepoPathsInvalid(f"{label} must be a non-empty string")
    if value.startswith("/"):
        raise RepoPathsInvalid(f"{label} must be relative, not absolute: {value!r}")
    if any(part in _BAD_SEGMENTS for part in value.split("/")):
        raise RepoPathsInvalid(f"{label} must not contain empty, '.' or '..' segments: {value!r}")
    if _BRACES.search(value.replace(PLACEHOLDER, "")):
        raise RepoPathsInvalid(f"{label} may only use the {PLACEHOLDER} placeholder: {value!r}")


def _parse_entry(name: str, table: object) -> PathEntry:
    if not name:
        raise RepoPathsInvalid("a path name must not be empty")
    label = f"paths.{name}"
    if not isinstance(table, dict):
        raise RepoPathsInvalid(f"{label} must be a table")
    unknown = sorted(set(table) - _ENTRY_KEYS)
    if unknown:
        raise RepoPathsInvalid(f"{label} has unknown keys: {', '.join(unknown)}")
    _check_relative(table.get("repo"), f"{label}.repo")
    if "cow" in table:
        _check_relative(table["cow"], f"{label}.cow")
    compression = table.get("compression")
    if "compression" in table and (not isinstance(compression, str) or not _COMPRESSION.fullmatch(compression)):
        raise RepoPathsInvalid(f"{label}.compression must be none, zstd or zstd:N (1..15): {compression!r}")
    cow = table.get("cow")
    if cow is not None and (PLACEHOLDER in table["repo"]) != (PLACEHOLDER in cow):
        raise RepoPathsInvalid(f"{label}: {PLACEHOLDER} must appear in both repo and cow or in neither")
    return PathEntry(name, table["repo"], cow, compression)


def parse_repo_paths(text: str, source: str = CONFIG_NAME) -> dict[str, PathEntry]:
    """Parse and validate the config text; every refusal is a ``RepoPathsInvalid`` naming *source*."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RepoPathsInvalid(f"{source}: invalid TOML: {exc}") from exc
    try:
        unknown = sorted(set(data) - _TOP_KEYS)
        if unknown:
            raise RepoPathsInvalid(f"unknown top-level keys: {', '.join(unknown)}")
        schema = data.get("schema")
        if type(schema) is not int or schema != SCHEMA:
            raise RepoPathsInvalid(f"schema must be {SCHEMA}, not {schema!r}")
        paths = data.get("paths")
        if not isinstance(paths, dict):
            raise RepoPathsInvalid("paths must be a table of named paths")
        return {name: _parse_entry(name, table) for name, table in paths.items()}
    except RepoPathsInvalid as exc:
        raise RepoPathsInvalid(f"{source}: {exc}") from exc


def find_repo_root(start: str | os.PathLike[str]) -> Path:
    """The nearest directory at or above *start* that holds ``repo-paths.toml``. *start* is always explicit:
    this never looks at the current directory by itself."""
    here = Path(os.path.abspath(start))
    if not here.is_dir():
        here = here.parent
    for candidate in (here, *here.parents):
        if (candidate / CONFIG_NAME).is_file():
            return candidate
    raise UsageError(f"no {CONFIG_NAME} at or above {str(here)!r}")


def load_repo_paths(repo_root: str | os.PathLike[str]) -> RepoPaths:
    """Load ``<repo_root>/repo-paths.toml``. The repo name (the CoW mirror folder) is the basename of the root."""
    root = Path(os.path.abspath(repo_root))
    config = root / CONFIG_NAME
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise UsageError(f"cannot read {str(config)!r}: {exc}") from exc
    return RepoPaths(root, root.name, parse_repo_paths(text, str(config)))


def _check_project(project: object) -> None:
    if (
        not isinstance(project, str)
        or project.startswith("/")
        or any(part in _BAD_SEGMENTS for part in project.split("/"))
    ):
        raise UsageError(f"project must be a relative directory without '..', '.' or empty segments: {project!r}")


def _property_value(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("compression="):
            return line[len("compression="):].strip()
    return ""


def _same_compression(current: str, wanted: str) -> bool:
    return current.replace("none", "") == wanted.replace("none", "")


def _checked(tools: Btrfs, argv: Sequence[str], path: Path) -> str:
    status, output = tools.run(argv)
    if status != 0:
        raise BackendFailed("btrfs", None, str(path), f"{' '.join(argv)} exited {status}: {output.strip()}")
    return output


@dataclass(frozen=True)
class RepoPaths:
    """The loaded config of one repository: ``root`` (the checkout), ``name`` (its basename), the entries."""

    root: Path
    name: str
    entries: Mapping[str, PathEntry]

    def _expand(self, name: str, project: str | None) -> tuple[PathEntry, str, str | None]:
        entry = self.entries.get(name)
        if entry is None:
            raise UsageError(f"unknown path name {name!r}; defined: {', '.join(sorted(self.entries))}")
        if project is not None:
            _check_project(project)
        if PLACEHOLDER not in entry.repo:
            return entry, entry.repo, entry.cow
        if project is None:
            raise UsageError(f"path {name!r} is per project: pass the project directory")
        cow = None if entry.cow is None else entry.cow.replace(PLACEHOLDER, project)
        return entry, entry.repo.replace(PLACEHOLDER, project), cow

    def _area_root(self, cow: str | None, environ: Mapping[str, str] | None, ops: Ops | None) -> str | None:
        """The validated CoW area root, or None when this entry never uses one or the host has no btrfs area.
        A configured but broken area raises (``RootInvalid``) instead of falling back."""
        if cow is None:
            return None
        area = detect(environ=environ, ops=ops)
        if area is None or area.caps.backend != "btrfs":
            return None
        return area.root

    def resolve(
        self,
        name: str,
        *,
        project: str | None = None,
        environ: Mapping[str, str] | None = None,
        ops: Ops | None = None,
    ) -> Resolved:
        """``cow`` mode only when the entry has ``cow``, ``MDT_COW_ROOT`` names a valid btrfs CoW area and
        ``<area>/<repo-name>/<cow>`` is an existing directory; otherwise the plain repo path. Creates nothing."""
        _entry, repo, cow = self._expand(name, project)
        area_root = self._area_root(cow, environ, ops)
        if area_root is not None and cow is not None:
            target = Path(area_root) / self.name / cow
            if target.is_dir():
                return Resolved(target, "cow", name, project)
        return Resolved(self.root / repo, "plain", name, project)

    def provision(
        self,
        name: str,
        *,
        project: str | None = None,
        environ: Mapping[str, str] | None = None,
        ops: Ops | None = None,
        btrfs: Btrfs | None = None,
    ) -> Resolved:
        """Create the CoW subvolume of *name* when a valid btrfs area exists and the entry has ``cow``
        (parents as plain directories, then ``btrfs subvolume create``, then the compression property); idempotent.
        An existing plain directory at the CoW location is refused. Without a usable area nothing is created
        and the plain result is returned. The plain repo location is never touched."""
        entry, repo, cow = self._expand(name, project)
        area_root = self._area_root(cow, environ, ops)
        if area_root is None or cow is None:
            return Resolved(self.root / repo, "plain", name, project)
        tools = btrfs or Btrfs()
        target = Path(area_root) / self.name / cow
        wanted = entry.compression
        if os.path.lexists(target):
            if not tools.is_subvolume(str(target)):
                raise PathRefused(
                    f"{name}: {str(target)!r} is an existing plain directory, not a subvolume; "
                    "moving existing content into a subvolume is not done here"
                )
            current = _property_value(_checked(tools, ["btrfs", "property", "get", str(target), "compression"], target))
        else:
            os.makedirs(target.parent, exist_ok=True)
            _checked(tools, ["btrfs", "subvolume", "create", str(target)], target)
            current = None
        if wanted is not None and (current is None or not _same_compression(current, wanted)):
            _checked(tools, ["btrfs", "property", "set", str(target), "compression", wanted], target)
        return Resolved(target, "cow", name, project)
