"""Committed-object repository snapshots.

P22 owns this boundary. A prepared repository transfers and validates one
exact commit once, then produces independent consumer-visible snapshots from
that private seed. No consumer command can reach the source object store.

**Why a prepared seed rather than a snapshot function** (A-184). The obvious
stateless shape -- ``materialize_snapshot(spec)`` -- has only two honest
implementations once full history is required: re-enumerate and re-pack the
whole closure for every repeated unit, or share the source store through an
alternate or a hardlink. The first multiplies cost by the mutant count (the
live vbpub closure is 26,074 objects / 273,578,621 uncompressed bytes); the
second destroys the isolation the substrate exists to provide. So the source
is read ONCE into an unexposed private seed, and every later base or
replacement repository is built from that seed alone.

**Why raw tree objects rather than a checkout** (A-186). Every convenient
materialization runs consumer-controlled code: ``git checkout`` and
``git archive`` both execute a committed ``filter`` driver, and a checkout
additionally runs hooks. The carver's tracer witnessed exactly that -- a real
``git archive`` executed the hostile filter and still produced no private
repository. Assay therefore parses raw tree objects itself and writes blob
bytes directly, so the only program that ever runs is git plumbing assay
chose.

**Namespaces never cross implicitly.** ``path`` inputs and every manifest key
are REPO-TOP-relative, matching ``git diff``'s own spelling (A-145);
``project_root`` is the one derived project-relative anchor. A replacement
path is repo-top-relative even when ``project_prefix`` is not ``.`` -- the
second silent re-interpretation under the project root is precisely the
false-PASS attack O4 is written against.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import stat
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, ContextManager, Iterator, Mapping

from . import git as _git
from .errors import AssayError, Outcome, ReasonCode

__all__ = [
    "DEFAULT_SNAPSHOT_LIMITS",
    "Snapshot",
    "SnapshotLimits",
    "SnapshotRepository",
    "SnapshotSpec",
    "prepare_snapshot",
]

#: The fixed mtime every materialized path receives (A-186). Git records no
#: mtime, so any value assay chose from the clock would be a fact the commit
#: does not contain -- and would make two snapshots of one commit differ.
#: ``946684800`` is 2000-01-01T00:00:00Z.
_FIXED_MTIME = 946684800

#: The RAW tree-object mode spellings P22 supports. Git writes a tree entry's
#: mode without a leading zero (``40000``, verified against a real tree
#: object), so the documented ``040000`` is the human spelling and this is
#: the wire spelling. A non-canonical variant is refused rather than
#: normalized: two spellings for one mode is how a validator and a
#: materializer come to disagree about what they checked.
_MODE_TREE = "40000"
_MODE_REGULAR = "100644"
_MODE_EXECUTABLE = "100755"
_MODE_SYMLINK = "120000"
_MODE_GITLINK = "160000"

_BLOB_MODES = frozenset({_MODE_REGULAR, _MODE_EXECUTABLE, _MODE_SYMLINK})
_SUPPORTED_MODES = _BLOB_MODES | {_MODE_TREE}

_OID_RE = re.compile(r"[0-9a-f]{40}")


def _git_failed(message: str) -> AssayError:
    return AssayError(
        message, outcome=Outcome.ERROR, reason_code=ReasonCode.GIT_FAILED
    )


def _limit_exceeded(message: str) -> AssayError:
    return AssayError(
        message,
        outcome=Outcome.BUDGET_EXCEEDED,
        reason_code=ReasonCode.SNAPSHOT_LIMIT_EXCEEDED,
    )


def _stale_site(message: str) -> AssayError:
    return AssayError(
        message,
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.MUTATION_DISCOVERY_FAILED,
    )


@dataclass(frozen=True, kw_only=True)
class SnapshotLimits:
    """Fixed ceilings for source inspection, transfer, and materialization."""

    max_objects: int
    max_entries: int
    max_path_bytes: int
    max_total_path_bytes: int
    max_blob_bytes: int
    max_total_object_bytes: int
    max_pack_bytes: int

    def __post_init__(self) -> None:
        """Reject booleans/non-positive/incoherent bounds."""
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer, got {value!r}")
        if self.max_blob_bytes > self.max_total_object_bytes:
            raise ValueError(
                "max_blob_bytes cannot exceed max_total_object_bytes "
                f"({self.max_blob_bytes} > {self.max_total_object_bytes})"
            )


DEFAULT_SNAPSHOT_LIMITS = SnapshotLimits(
    max_objects=100_000,
    max_entries=200_000,
    max_path_bytes=4_096,
    max_total_path_bytes=64 * 1024 * 1024,
    max_blob_bytes=64 * 1024 * 1024,
    max_total_object_bytes=1024 * 1024 * 1024,
    max_pack_bytes=512 * 1024 * 1024,
)


@dataclass(frozen=True, kw_only=True)
class SnapshotSpec:
    """One source commit and caller-owned scratch namespace."""

    repo_top: Path
    commit: str
    project_prefix: PurePosixPath
    scratch_root: Path
    limits: SnapshotLimits = DEFAULT_SNAPSHOT_LIMITS

    def __post_init__(self) -> None:
        """Validate exact SHA-1, prefix, roots, and concrete limits."""
        if not isinstance(self.limits, SnapshotLimits):
            raise ValueError(f"limits must be SnapshotLimits, got {self.limits!r}")
        for name in ("repo_top", "scratch_root"):
            value = getattr(self, name)
            if not isinstance(value, Path) or not value.is_absolute():
                raise ValueError(f"{name} must be an absolute Path, got {value!r}")
            try:
                resolved = value.resolve(strict=True)
            except OSError as exc:
                raise ValueError(f"{name} does not resolve: {value!r}") from exc
            if resolved != value or value.is_symlink() or not value.is_dir():
                raise ValueError(
                    f"{name} must be an existing resolved non-symlink directory, "
                    f"got {value!r}"
                )
        if self.scratch_root.is_relative_to(self.repo_top):
            raise ValueError("scratch_root must be outside repo_top")
        if not isinstance(self.commit, str) or re.fullmatch(r"[0-9a-f]{40}", self.commit) is None:
            raise ValueError(f"commit must be exactly 40 lowercase hex characters, got {self.commit!r}")
        if not isinstance(self.project_prefix, PurePosixPath):
            raise ValueError(
                f"project_prefix must be PurePosixPath, got {self.project_prefix!r}"
            )
        if self.project_prefix.is_absolute() or ".." in self.project_prefix.parts:
            raise ValueError(
                "project_prefix must be a normalized repo-relative POSIX path, "
                f"got {self.project_prefix!s}"
            )


@dataclass(frozen=True, kw_only=True)
class Snapshot:
    """One independently owned working repository, valid for its context."""

    root: Path
    project_root: Path
    commit: str


@dataclass(frozen=True, kw_only=True)
class _Entry:
    """One validated leaf of the prepared commit's tree, repo-top-relative."""

    path: PurePosixPath
    mode: str
    oid: str
    size: int
    #: Only for :data:`_MODE_SYMLINK`: the exact target bytes, already proven
    #: to resolve lexically inside the snapshot root.
    target: str | None = None


@dataclass(frozen=True, kw_only=True)
class _Manifest:
    """The frozen content of one commit: what materialization may write."""

    entries: tuple[_Entry, ...]
    directories: tuple[PurePosixPath, ...]

    def by_path(self) -> Mapping[PurePosixPath, _Entry]:
        return {entry.path: entry for entry in self.entries}


class _BatchReader:
    """Incremental parser for ``git cat-file --batch``.

    Content is dispatched to a per-object sink as it arrives rather than
    accumulated, so materializing a large tracked file never requires holding
    that file in memory -- the per-object ceiling is the declared
    ``max_blob_bytes`` and nothing larger is ever buffered here.
    """

    def __init__(
        self,
        open_sink: Callable[[str, str, int], Callable[[bytes], None] | None],
        close_sink: Callable[[str], None] | None = None,
    ) -> None:
        self._open = open_sink
        self._close = close_sink
        self._buffer = bytearray()
        self._sink: Callable[[bytes], None] | None = None
        self._current: str | None = None
        self._remaining = 0
        self._trailer = 0
        self._seen = 0

    def _finish_object(self) -> None:
        """Release the current object's sink the moment its bytes are complete.

        Dropping only the reference here -- what this reader did before the
        independent review -- leaves every object's resources live until the
        whole batch ends, which is how "one open descriptor per tracked path"
        rather than "one at a time" became the real bound.
        """
        current, self._current = self._current, None
        self._sink = None
        if self._close is not None and current is not None:
            self._close(current)

    def feed(self, chunk: bytes) -> None:
        self._buffer.extend(chunk)
        while True:
            if self._remaining:
                take = min(self._remaining, len(self._buffer))
                if take:
                    if self._sink is not None:
                        self._sink(bytes(self._buffer[:take]))
                    del self._buffer[:take]
                    self._remaining -= take
                if self._remaining:
                    return
                self._finish_object()
                self._trailer = 1
            if self._trailer:
                if not self._buffer:
                    return
                del self._buffer[:1]
                self._trailer = 0
            newline = self._buffer.find(b"\n")
            if newline == -1:
                return
            header = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            self._start(header)

    def _start(self, header: bytes) -> None:
        fields = header.split(b" ")
        if len(fields) != 3:
            raise _git_failed(
                f"git cat-file --batch reported {header!r}; the requested "
                f"object is missing or the batch grammar was violated"
            )
        oid = fields[0].decode("ascii", errors="replace")
        kind = fields[1].decode("ascii", errors="replace")
        try:
            size = int(fields[2])
        except ValueError as exc:
            raise _git_failed(f"git cat-file --batch reported a non-integer size in {header!r}") from exc
        self._seen += 1
        self._remaining = size
        self._current = oid
        self._sink = self._open(oid, kind, size)
        if size == 0:
            self._finish_object()
            self._trailer = 1

    def finish(self, expected: int) -> None:
        if self._remaining or self._buffer.strip():
            raise _git_failed("git cat-file --batch ended mid-object")
        if self._seen != expected:
            raise _git_failed(
                f"git cat-file --batch returned {self._seen} objects, expected {expected}"
            )


class SnapshotRepository:
    """Validated private seed for repeated independent materializations."""

    def __init__(
        self,
        *,
        spec: SnapshotSpec,
        source: "_git._P22Source",
        seed_git_dir: Path,
        template: Path,
        manifest: _Manifest,
    ) -> None:
        self._spec = spec
        self._source = source
        self._seed_git_dir = seed_git_dir
        self._template = template
        self._manifest = manifest
        self._lock = threading.Lock()
        self._closed = False
        self._live: set[Path] = set()

    @property
    def spec(self) -> SnapshotSpec:
        return self._spec

    def _check_open(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError(
                    "the prepared snapshot repository is closed; prepare_snapshot's "
                    "context must still be open when a method is called"
                )

    def _track(self, root: Path) -> None:
        """Record a live materialization so the outer context can neither
        lose track of it nor leave it behind in caller-owned scratch."""
        with self._lock:
            self._live.add(root)

    def _untrack(self, root: Path) -> None:
        with self._lock:
            self._live.discard(root)

    def read_regular_file(self, path: PurePosixPath, *, timeout: float) -> bytes:
        """Read one repo-relative regular blob from the prepared commit."""
        _check_timeout(timeout)
        deadline = _git._P22Deadline(timeout)
        self._check_open()
        entry = self._manifest.by_path().get(_normalize_path(path))
        if entry is None or entry.mode not in (_MODE_REGULAR, _MODE_EXECUTABLE):
            raise _git_failed(
                f"{path!s} is not a regular tracked file in {self._spec.commit}"
            )
        return self._read_blob(entry.oid, entry.size, deadline)

    def _read_blob(self, oid: str, size: int, deadline: "_git._P22Deadline") -> bytes:
        collected = bytearray()
        _batch_objects(
            self._source.git_executable,
            git_dir=self._seed_git_dir,
            oids=(oid,),
            deadline=deadline,
            open_sink=lambda _oid, _kind, _size: collected.extend,
        )
        if len(collected) != size:
            raise _git_failed(
                f"blob {oid} read back as {len(collected)} bytes, expected {size}"
            )
        return bytes(collected)

    def materialize(self, *, timeout: float) -> ContextManager[Snapshot]:
        """Return a context for one independent exact base snapshot."""
        _check_timeout(timeout)
        return self._materialize(timeout=timeout, replacement=None)

    def materialize_replacement(
        self,
        *,
        path: PurePosixPath,
        expected: bytes,
        replacement: bytes,
        timeout: float,
    ) -> ContextManager[Snapshot]:
        """Return a fixed-identity child commit replacing one whole blob."""
        _check_timeout(timeout)
        if not isinstance(expected, bytes) or not isinstance(replacement, bytes):
            raise ValueError("expected and replacement must both be whole blob bytes")
        return self._materialize(
            timeout=timeout,
            replacement=(_normalize_path(path), expected, replacement),
        )

    @contextmanager
    def _materialize(
        self,
        *,
        timeout: float,
        replacement: tuple[PurePosixPath, bytes, bytes] | None,
    ) -> Iterator[Snapshot]:
        deadline = _git._P22Deadline(timeout)
        self._check_open()
        root: Path | None = None
        try:
            if replacement is not None:
                self._check_replacement_site(replacement, deadline)
            root = Path(
                tempfile.mkdtemp(dir=self._spec.scratch_root, prefix="assay-p22-snap-")
            )
            self._track(root)
            commit = self._build(root, replacement, deadline)
            snapshot = Snapshot(
                root=root,
                project_root=_join_prefix(root, self._spec.project_prefix),
                commit=commit,
            )
            yield snapshot
        except BaseException:
            # An exception is already in flight. Discard best-effort so a
            # cleanup failure cannot replace the real terminal with a less
            # informative one.
            if root is not None:
                self._untrack(root)
                shutil.rmtree(root, ignore_errors=True)
            raise
        else:
            self._untrack(root)
            _remove_owned_tree(root)

    def _check_replacement_site(
        self,
        replacement: tuple[PurePosixPath, bytes, bytes],
        deadline: "_git._P22Deadline",
    ) -> None:
        """Prove the frozen mutation descriptor still names committed bytes.

        A descriptor that no longer matches is not a snapshot failure and not
        a limit: it means the site P21 froze is no longer the syntax it
        claimed, so the honest terminal is ``MUTATION_DISCOVERY_FAILED``
        (A-186) rather than a plausible-looking Git error.
        """
        path, expected, _new = replacement
        entry = self._manifest.by_path().get(path)
        if entry is None:
            raise _stale_site(
                f"the replacement path {path!s} is not tracked in "
                f"{self._spec.commit}; note the path is repo-top-relative, not "
                f"relative to project_prefix {self._spec.project_prefix!s}"
            )
        if entry.mode not in (_MODE_REGULAR, _MODE_EXECUTABLE):
            raise _stale_site(
                f"the replacement path {path!s} is mode {entry.mode} in "
                f"{self._spec.commit}, not a regular file"
            )
        actual = self._read_blob(entry.oid, entry.size, deadline)
        if actual != expected:
            raise _stale_site(
                f"the replacement path {path!s} holds {len(actual)} bytes in "
                f"{self._spec.commit} that differ from the {len(expected)} "
                f"expected bytes; the frozen mutation site is stale"
            )

    def _build(
        self,
        root: Path,
        replacement: tuple[PurePosixPath, bytes, bytes] | None,
        deadline: "_git._P22Deadline",
    ) -> str:
        executable = self._source.git_executable
        spec = self._spec
        _git._p22_init_private(
            executable,
            path=root,
            template=self._template,
            bare=False,
            deadline=deadline,
        )
        git_dir = root / ".git"
        _copy_objects(self._seed_git_dir, git_dir)

        def run(*args: str, stdin_bytes: bytes = b"", identity: bool = False) -> bytes:
            return _git._p22_git(
                executable,
                git_dir=git_dir,
                work_tree=root,
                args=args,
                deadline=deadline,
                cwd=root,
                stdin_bytes=stdin_bytes,
                identity=identity,
            )

        run("read-tree", spec.commit)
        commit = spec.commit
        entries = self._manifest.entries
        if replacement is not None:
            path, _expected, new_bytes = replacement
            entry = self._manifest.by_path()[path]
            new_oid = _decode(
                run("hash-object", "-w", "--stdin", stdin_bytes=new_bytes)
            ).strip()
            run("update-index", "--cacheinfo", f"{entry.mode},{new_oid},{path!s}")
            tree = _decode(run("write-tree")).strip()
            commit = _decode(
                run(
                    "commit-tree",
                    tree,
                    "-p",
                    spec.commit,
                    stdin_bytes=_git._P22_REPLACEMENT_MESSAGE,
                    identity=True,
                )
            ).strip()
            if _OID_RE.fullmatch(commit) is None:
                raise _git_failed(f"commit-tree returned {commit!r}, not a full OID")
            entries = tuple(
                _Entry(
                    path=item.path,
                    mode=item.mode,
                    oid=new_oid if item.path == path else item.oid,
                    size=len(new_bytes) if item.path == path else item.size,
                    target=item.target,
                )
                for item in entries
            )
            self._enforce_child_closure(git_dir, commit, deadline)

        _write_worktree(
            executable,
            git_dir=git_dir,
            root=root,
            entries=entries,
            directories=self._manifest.directories,
            deadline=deadline,
        )
        (git_dir / "HEAD").write_text(f"{commit}\n", encoding="ascii")
        self._verify(root, git_dir, commit, run)
        return commit

    def _enforce_child_closure(
        self, git_dir: Path, commit: str, deadline: "_git._P22Deadline"
    ) -> None:
        """Bound the child's own closure (A-186).

        The replacement introduces a new blob, new trees and a new commit.
        Checking only the base closure would let the declared ceilings be
        true of the prepared seed and false of every repository actually
        handed to a consumer command.
        """
        limits = self._spec.limits
        oids = _closure_oids(
            self._source.git_executable,
            git_dir=git_dir,
            work_tree=None,
            cwd=git_dir,
            commit=commit,
            deadline=deadline,
            max_objects=limits.max_objects,
        )
        _enforce_object_limits(
            _object_metadata(
                self._source.git_executable,
                git_dir=git_dir,
                work_tree=None,
                cwd=git_dir,
                oids=oids,
                deadline=deadline,
            ),
            limits,
        )

    def _verify(
        self,
        root: Path,
        git_dir: Path,
        commit: str,
        run: Callable[..., bytes],
    ) -> None:
        """Prove the yielded repository before a consumer can observe it."""
        head = _decode(run("rev-parse", "HEAD")).strip()
        if head != commit:
            raise _git_failed(f"private HEAD is {head}, expected {commit}")
        status = run("status", "--porcelain=v1", "-z")
        if status:
            raise _git_failed(
                f"the private snapshot at {root} is not clean: {status[:200]!r}"
            )
        if (git_dir / "objects" / "info" / "alternates").exists():
            raise _git_failed(
                f"the private snapshot at {root} depends on an alternate object store"
            )
        hooks = git_dir / "hooks"
        if hooks.is_dir() and any(hooks.iterdir()):
            raise _git_failed(f"the private snapshot at {root} carries hooks")
        config = (git_dir / "config").read_text(encoding="utf-8", errors="replace")
        if str(self._source.repo_top) in config or str(self._source.common_dir) in config:
            raise _git_failed(
                f"the private snapshot at {root} references the source repository"
            )
        project_root = _join_prefix(root, self._spec.project_prefix)
        if not project_root.is_dir():
            raise _git_failed(
                f"the project prefix {self._spec.project_prefix!s} is not a "
                f"directory in {commit}"
            )

def _close_repository(repository: "SnapshotRepository | None") -> set[Path]:
    """Close *repository* and return the snapshot roots still live at closure."""
    if repository is None:
        return set()
    with repository._lock:
        live = set(repository._live)
        repository._live.clear()
        repository._closed = True
    return live


def _remove_owned_tree(path: Path) -> None:
    """Remove one assay-owned scratch child, REPORTING failure.

    ``ignore_errors=True`` is correct only while an exception is already in
    flight. On a normal exit it converts "assay could not clean up after
    itself" into silence and leaves the tree in the CALLER's scratch
    namespace, which is the silent state leak the terminal table forbids --
    and which P23, reusing that scratch root per lane, would inherit.
    """
    try:
        shutil.rmtree(path)
    except OSError as exc:
        raise _git_failed(
            f"the private tree at {path} could not be removed: {exc}; refusing "
            f"to leave state behind in caller-owned scratch"
        ) from exc


def _check_timeout(timeout: float) -> None:
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError(f"timeout must be a positive finite number, got {timeout!r}")


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _git_failed(f"git output is not valid UTF-8: {raw[:200]!r}") from exc


def _normalize_path(path: PurePosixPath) -> PurePosixPath:
    if not isinstance(path, PurePosixPath):
        raise ValueError(f"path must be PurePosixPath, got {path!r}")
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"path must be a normalized repo-relative POSIX path, got {path!s}")
    return path


def _join_prefix(root: Path, prefix: PurePosixPath) -> Path:
    if str(prefix) == ".":
        return root
    return root.joinpath(*prefix.parts)


def _copy_objects(seed_git_dir: Path, destination_git_dir: Path) -> None:
    """Copy the seed's packed objects into a fresh independent store.

    :func:`shutil.copyfile` is deliberate: a hardlink or a shared alternate
    would make two snapshots one object store, which is exactly the
    isolation failure O2's disjoint-inode assertion is written to catch.
    """
    source_pack = seed_git_dir / "objects" / "pack"
    destination_pack = destination_git_dir / "objects" / "pack"
    destination_pack.mkdir(parents=True, exist_ok=True)
    for item in sorted(source_pack.iterdir()):
        if item.is_file() and not item.is_symlink():
            shutil.copyfile(item, destination_pack / item.name)


def _batch_objects(
    git_executable: Path,
    *,
    git_dir: Path,
    oids: tuple[str, ...],
    deadline: "_git._P22Deadline",
    open_sink: Callable[[str, str, int], Callable[[bytes], None] | None],
    close_sink: Callable[[str], None] | None = None,
) -> None:
    """Read *oids* from *git_dir*, dispatching each object to a per-object sink.

    The object names are DEDUPLICATED first, and that is a correctness
    requirement rather than an optimization. ``cat-file --batch`` answers one
    record per input line, so a repeated name is read back repeatedly; a sink
    that accumulates (the tree walker's does) would then see one object's
    bytes twice and parse a phantom second copy of every record in it.
    Repeated names are ORDINARY: two directories with identical content are
    one tree object at two paths, and one blob may be committed at many paths.
    """
    if not oids:
        return
    unique = tuple(dict.fromkeys(oids))
    reader = _BatchReader(open_sink, close_sink)
    _git._p22_git(
        git_executable,
        git_dir=git_dir,
        work_tree=None,
        args=("cat-file", "--batch"),
        deadline=deadline,
        cwd=git_dir,
        stdin_bytes=("\n".join(unique) + "\n").encode("ascii"),
        on_stdout=reader.feed,
    )
    reader.finish(len(unique))


def _closure_oids(
    git_executable: Path,
    *,
    git_dir: Path,
    work_tree: Path | None,
    cwd: Path,
    commit: str,
    deadline: "_git._P22Deadline",
    max_objects: int,
) -> tuple[str, ...]:
    """The complete reachable closure as validated full OIDs.

    ``--no-object-names`` is what keeps this phase free of display paths
    (A-185): the alternative prints ``<oid> SP <path>``, and a path printed
    here would be a second, quoting-dependent spelling of a name the raw
    tree grammar already answers exactly.
    """
    raw = _git._p22_git(
        git_executable,
        git_dir=git_dir,
        work_tree=work_tree,
        args=("rev-list", "--objects", "--no-object-names", commit),
        deadline=deadline,
        cwd=cwd,
    )
    oids: list[str] = []
    for line in _decode(raw).splitlines():
        candidate = line.strip()
        # No blank-line skip: ``rev-list`` never emits one, so a skip would be
        # uncoverable, and letting an empty field fail the OID grammar below
        # is the stricter reading anyway.
        if _OID_RE.fullmatch(candidate) is None:
            raise _git_failed(f"git rev-list emitted {candidate!r}, not a full object name")
        oids.append(candidate)
        if len(oids) > max_objects:
            raise _limit_exceeded(
                f"the reachable closure of {commit} exceeds max_objects ({max_objects})"
            )
    if not oids:
        raise _git_failed(f"the reachable closure of {commit} is empty")
    return tuple(oids)


def _object_metadata(
    git_executable: Path,
    *,
    git_dir: Path,
    work_tree: Path | None,
    cwd: Path,
    oids: tuple[str, ...],
    deadline: "_git._P22Deadline",
) -> dict[str, tuple[str, int]]:
    raw = _git._p22_git(
        git_executable,
        git_dir=git_dir,
        work_tree=work_tree,
        args=("cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"),
        deadline=deadline,
        cwd=cwd,
        stdin_bytes=("\n".join(oids) + "\n").encode("ascii"),
    )
    metadata: dict[str, tuple[str, int]] = {}
    for line in _decode(raw).splitlines():
        fields = line.split(" ")
        if len(fields) != 3:
            raise _git_failed(
                f"git cat-file --batch-check reported {line!r}; an object is "
                f"missing or corrupt in the source repository"
            )
        metadata[fields[0]] = (fields[1], int(fields[2]))
    missing = set(oids) - set(metadata)
    if missing:
        raise _git_failed(f"{len(missing)} inventoried objects were not reported by git")
    return metadata


def _enforce_object_limits(
    metadata: Mapping[str, tuple[str, int]], limits: SnapshotLimits
) -> None:
    if len(metadata) > limits.max_objects:
        raise _limit_exceeded(
            f"the closure holds {len(metadata)} objects, exceeding max_objects "
            f"({limits.max_objects})"
        )
    total = 0
    for oid, (kind, size) in metadata.items():
        if kind == "blob" and size > limits.max_blob_bytes:
            raise _limit_exceeded(
                f"blob {oid} is {size} bytes, exceeding max_blob_bytes "
                f"({limits.max_blob_bytes})"
            )
        total += size
        if total > limits.max_total_object_bytes:
            raise _limit_exceeded(
                f"the closure exceeds max_total_object_bytes "
                f"({limits.max_total_object_bytes})"
            )


def _parse_tree(raw: bytes, oid: str) -> tuple[tuple[str, bytes, str], ...]:
    """Parse one raw SHA-1 tree object.

    The wire grammar is a repetition of
    ``<mode> SP <name-bytes> NUL <20-byte-oid>`` with no count, no padding
    and no terminator, so a truncated record is only detectable by running
    off the end -- which is why every field length is checked rather than
    assumed.
    """
    records: list[tuple[str, bytes, str]] = []
    index = 0
    length = len(raw)
    while index < length:
        space = raw.find(b" ", index)
        if space == -1:
            raise _git_failed(f"tree {oid} has a record with no mode separator")
        mode_bytes = raw[index:space]
        nul = raw.find(b"\x00", space + 1)
        if nul == -1:
            raise _git_failed(f"tree {oid} has a record with no name terminator")
        name = raw[space + 1 : nul]
        oid_start = nul + 1
        oid_end = oid_start + 20
        if oid_end > length:
            raise _git_failed(f"tree {oid} ends inside an object name")
        if not name:
            raise _git_failed(f"tree {oid} has an empty entry name")
        try:
            mode = mode_bytes.decode("ascii")
        except UnicodeDecodeError as exc:
            raise _git_failed(f"tree {oid} has a non-ASCII mode {mode_bytes!r}") from exc
        records.append((mode, name, raw[oid_start:oid_end].hex()))
        index = oid_end
    return tuple(records)


def _check_symlink_target(path: PurePosixPath, target: str) -> None:
    """Refuse any symlink that does not resolve inside the snapshot root.

    Resolution is LEXICAL and never touches the filesystem: asking the
    kernel (``Path.resolve``) would answer about the machine assay happens
    to run on, follow links that already exist, and quietly accept a link
    that only escapes once its target is created. Because every link in the
    tree is checked this way, a chain of links cannot escape either.
    """
    if not target:
        raise _git_failed(f"symlink {path!s} has an empty target")
    if target.startswith("/"):
        raise _git_failed(f"symlink {path!s} targets the absolute path {target!r}")
    parent = path.parent
    parts = [] if str(parent) == "." else list(parent.parts)
    for component in PurePosixPath(target).parts:
        if component == ".":
            continue
        if component == "..":
            if not parts:
                raise _git_failed(
                    f"symlink {path!s} targets {target!r}, which escapes the snapshot root"
                )
            parts.pop()
        else:
            parts.append(component)


def _build_manifest(
    git_executable: Path,
    *,
    seed_git_dir: Path,
    spec: SnapshotSpec,
    metadata: Mapping[str, tuple[str, int]],
    deadline: "_git._P22Deadline",
) -> _Manifest:
    """Traverse the commit's trees from raw objects and freeze what is safe.

    Every structural refusal happens HERE, before a seed is ever exposed:
    an unsafe tree that is validated during materialization would already
    have been packed, copied, and partly written to disk by the time it was
    caught.
    """
    limits = spec.limits
    root_tree = _decode(
        _git._p22_git(
            git_executable,
            git_dir=seed_git_dir,
            work_tree=None,
            args=("rev-parse", f"{spec.commit}^{{tree}}"),
            deadline=deadline,
            cwd=seed_git_dir,
        )
    ).strip()

    entries: list[_Entry] = []
    directories: list[PurePosixPath] = [PurePosixPath(".")]
    files: set[PurePosixPath] = set()
    seen_dirs: set[PurePosixPath] = {PurePosixPath(".")}
    symlinks: list[tuple[PurePosixPath, str]] = []
    counted_entries = 0
    total_path_bytes = 0
    pending: list[tuple[str, PurePosixPath, tuple[str, ...]]] = [
        (root_tree, PurePosixPath("."), (root_tree,))
    ]

    while pending:
        level = pending
        pending = []
        raw_trees: dict[str, bytearray] = {}

        def open_sink(
            oid: str, kind: str, _size: int, _sink: dict[str, bytearray] = raw_trees
        ) -> Callable[[bytes], None] | None:
            if kind != "tree":
                raise _git_failed(f"object {oid} is a {kind} where a tree was required")
            buffer = _sink.setdefault(oid, bytearray())
            return buffer.extend

        _batch_objects(
            git_executable,
            git_dir=seed_git_dir,
            oids=tuple(oid for oid, _prefix, _ancestry in level),
            deadline=deadline,
            open_sink=open_sink,
        )

        for tree_oid, prefix, ancestry in level:
            for mode, name, child_oid in _parse_tree(bytes(raw_trees[tree_oid]), tree_oid):
                counted_entries += 1
                if counted_entries > limits.max_entries:
                    raise _limit_exceeded(
                        f"the tree of {spec.commit} exceeds max_entries "
                        f"({limits.max_entries})"
                    )
                if mode == _MODE_GITLINK:
                    raise _git_failed(
                        f"tree {tree_oid} contains gitlink {name!r}; a submodule "
                        f"is not part of this repository's committed content"
                    )
                if mode not in _SUPPORTED_MODES:
                    raise _git_failed(
                        f"tree {tree_oid} contains entry {name!r} with unsupported "
                        f"mode {mode!r}"
                    )
                try:
                    component = name.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise _git_failed(
                        f"tree {tree_oid} contains a non-UTF-8 name {name!r}"
                    ) from exc
                if component in (".", ".."):
                    raise _git_failed(f"tree {tree_oid} contains the name {component!r}")
                if component == ".git":
                    raise _git_failed(
                        f"tree {tree_oid} contains a '.git' component, which would "
                        f"collide with the snapshot's own Git namespace"
                    )
                path = (
                    PurePosixPath(component)
                    if str(prefix) == "."
                    else prefix / component
                )
                path_bytes = len(str(path).encode("utf-8"))
                if path_bytes > limits.max_path_bytes:
                    raise _limit_exceeded(
                        f"path {path!s} is {path_bytes} bytes, exceeding "
                        f"max_path_bytes ({limits.max_path_bytes})"
                    )
                total_path_bytes += path_bytes
                if total_path_bytes > limits.max_total_path_bytes:
                    raise _limit_exceeded(
                        f"the tree of {spec.commit} exceeds max_total_path_bytes "
                        f"({limits.max_total_path_bytes})"
                    )
                if path in files or path in seen_dirs:
                    raise _git_failed(
                        f"tree {tree_oid} yields the duplicate or colliding path {path!s}"
                    )
                declared = metadata.get(child_oid)
                if mode == _MODE_TREE:
                    # No cycle guard: a tree cycle would require a SHA-1 hash
                    # cycle, since a tree's name IS the hash of its content.
                    # Traversal is instead bounded by ``max_entries`` above,
                    # which is a real ceiling rather than an uncoverable one.
                    # Repeated subtree OIDs at DIFFERENT paths are an ordinary
                    # DAG (identical directories) and are traversed normally --
                    # but only because ``_batch_objects`` deduplicates the
                    # object names it requests. Without that, one level's read
                    # concatenates such a tree with itself and every record in
                    # it is parsed twice, which the duplicate-path check below
                    # then reports as a collision in the CONSUMER's repository.
                    # This sentence was asserted before it was true; see
                    # ``test_reviewer_identical_sibling_directories_are_not_a_duplicate_path``.
                    if declared is not None and declared[0] != "tree":
                        raise _git_failed(
                            f"{child_oid} at {path!s} is declared {declared[0]}, not a tree"
                        )
                    seen_dirs.add(path)
                    directories.append(path)
                    pending.append((child_oid, path, ancestry + (child_oid,)))
                    continue
                if declared is None:
                    raise _git_failed(
                        f"blob {child_oid} at {path!s} is absent from the inventory"
                    )
                if declared[0] != "blob":
                    raise _git_failed(
                        f"{child_oid} at {path!s} is declared {declared[0]}, not a blob"
                    )
                files.add(path)
                entries.append(
                    _Entry(path=path, mode=mode, oid=child_oid, size=declared[1])
                )
                if mode == _MODE_SYMLINK:
                    symlinks.append((path, child_oid))

    # A separate file/directory prefix-collision sweep would be dead code: a
    # leaf's parent is only ever reached by TRAVERSING it as a tree, so it is
    # in ``seen_dirs``, and the duplicate check above already refuses any name
    # that arrives as both. Leaving an unreachable guard here would be a line
    # no test could honestly cover.
    resolved: dict[str, bytes] = {}
    if symlinks:
        def open_target(
            oid: str, kind: str, _size: int, _sink: dict[str, bytes] = resolved
        ) -> Callable[[bytes], None] | None:
            if kind != "blob":
                raise _git_failed(f"symlink object {oid} is a {kind}")
            buffer = bytearray()
            _sink[oid] = buffer  # type: ignore[assignment]
            return buffer.extend

        _batch_objects(
            git_executable,
            git_dir=seed_git_dir,
            oids=tuple(oid for _path, oid in symlinks),
            deadline=deadline,
            open_sink=open_target,
        )

    targets: dict[PurePosixPath, str] = {}
    for path, oid in symlinks:
        raw = bytes(resolved[oid])
        try:
            target = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _git_failed(f"symlink {path!s} has a non-UTF-8 target {raw!r}") from exc
        _check_symlink_target(path, target)
        targets[path] = target

    final = tuple(
        _Entry(
            path=entry.path,
            mode=entry.mode,
            oid=entry.oid,
            size=entry.size,
            target=targets.get(entry.path),
        )
        for entry in entries
    )
    manifest = _Manifest(entries=final, directories=tuple(directories))
    if str(spec.project_prefix) != "." and spec.project_prefix not in seen_dirs:
        raise _git_failed(
            f"project_prefix {spec.project_prefix!s} does not name a tree in {spec.commit}"
        )
    return manifest


def _write_worktree(
    git_executable: Path,
    *,
    git_dir: Path,
    root: Path,
    entries: tuple[_Entry, ...],
    directories: tuple[PurePosixPath, ...],
    deadline: "_git._P22Deadline",
) -> None:
    """Write exact committed bytes with no checkout, filter, or hardlink."""
    handles: dict[str, list[Path]] = {}
    for entry in entries:
        destination = root.joinpath(*entry.path.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if entry.mode == _MODE_SYMLINK:
            assert entry.target is not None
            os.symlink(entry.target, destination)
            os.utime(
                destination,
                (_FIXED_MTIME, _FIXED_MTIME),
                follow_symlinks=False,
            )
        else:
            handles.setdefault(entry.oid, []).append(destination)

    open_files: dict[str, list] = {}

    def open_sink(oid: str, kind: str, _size: int) -> Callable[[bytes], None] | None:
        if kind != "blob":
            raise _git_failed(f"object {oid} is a {kind} where a blob was required")
        # ONE descriptor per object, not one per path: a blob committed at
        # many paths (an empty ``__init__.py`` is the everyday case) would
        # otherwise open them all at once, and holding them for the whole
        # batch makes the peak descriptor count the tracked-file count --
        # a resource no declared limit bounds.
        stream = handles[oid][0].open("wb")
        open_files[oid] = [stream]
        return stream.write

    def close_sink(oid: str) -> None:
        for stream in open_files.pop(oid, ()):
            stream.close()

    try:
        _batch_objects(
            git_executable,
            git_dir=git_dir,
            oids=tuple(handles),
            deadline=deadline,
            open_sink=open_sink,
            close_sink=close_sink,
        )
    finally:
        for streams in open_files.values():
            for stream in streams:
                stream.close()
        open_files.clear()

    for paths in handles.values():
        for duplicate in paths[1:]:
            shutil.copyfile(paths[0], duplicate)

    # Mode belongs to the TREE ENTRY, never to the blob. The same bytes may be
    # committed ``100644`` at one path and ``100755`` at another, so deriving
    # one mode per object silently gives one of the two paths the other's
    # mode -- which the pre-yield clean-status check then reports as a
    # corrupt snapshot rather than as the mode error it is.
    for entry in entries:
        if entry.mode == _MODE_SYMLINK:
            continue
        path = root.joinpath(*entry.path.parts)
        path.chmod(0o755 if entry.mode == _MODE_EXECUTABLE else 0o644)
        os.utime(path, (_FIXED_MTIME, _FIXED_MTIME))

    # EVERY materialized directory, not only the direct parent of a file: an
    # intermediate directory left at its creation defaults carries the
    # ambient umask and the wall clock, so two snapshots of one commit
    # differ in exactly the way the fixed mtime exists to prevent.
    for directory in sorted(
        directories, key=lambda item: len(item.parts), reverse=True
    ):
        if str(directory) == ".":
            continue
        materialized = root.joinpath(*directory.parts)
        if not materialized.is_dir():
            continue
        materialized.chmod(0o755)
        os.utime(materialized, (_FIXED_MTIME, _FIXED_MTIME))


@contextmanager
def prepare_snapshot(
    spec: SnapshotSpec, *, timeout: float
) -> Iterator[SnapshotRepository]:
    """Transfer and validate *spec.commit* once, then yield its private seed."""
    _check_timeout(timeout)
    deadline = _git._P22Deadline(timeout)

    source = _git._p22_open_source(spec.repo_top, deadline)
    if source.repo_top != spec.repo_top:
        raise ValueError(
            f"repo_top {spec.repo_top} is not the resolved repository top "
            f"{source.repo_top}; pass the exact path git.repo_top resolves"
        )
    executable = source.git_executable

    resolved = _decode(
        _git._p22_git(
            executable,
            git_dir=source.git_dir,
            work_tree=source.repo_top,
            args=("rev-parse", "--verify", "--end-of-options", f"{spec.commit}^{{commit}}"),
            deadline=deadline,
            cwd=source.repo_top,
        )
    ).strip()
    if resolved != spec.commit:
        raise _git_failed(
            f"{spec.commit} resolves to {resolved}; only a literal full commit "
            f"is a snapshot identity"
        )

    oids = _closure_oids(
        executable,
        git_dir=source.git_dir,
        work_tree=source.repo_top,
        cwd=source.repo_top,
        commit=spec.commit,
        deadline=deadline,
        max_objects=spec.limits.max_objects,
    )
    metadata = _object_metadata(
        executable,
        git_dir=source.git_dir,
        work_tree=source.repo_top,
        cwd=source.repo_top,
        oids=oids,
        deadline=deadline,
    )
    _enforce_object_limits(metadata, spec.limits)

    child = Path(tempfile.mkdtemp(dir=spec.scratch_root, prefix="assay-p22-seed-"))
    repository: SnapshotRepository | None = None
    try:
        seed_git_dir = child / "seed.git"
        template = child / "template"
        template.mkdir()
        _git._p22_init_private(
            executable,
            path=seed_git_dir,
            template=template,
            bare=True,
            deadline=deadline,
        )
        _git._p22_stream_pack(
            executable,
            source_git_dir=source.git_dir,
            source_work_tree=source.repo_top,
            source_cwd=source.repo_top,
            seed_git_dir=seed_git_dir,
            oid_payload=("\n".join(oids) + "\n").encode("ascii"),
            deadline=deadline,
            max_pack_bytes=spec.limits.max_pack_bytes,
        )

        # From here the source is logically disconnected: every fact below is
        # re-derived from the private seed, so a seed that silently depended
        # on the consumer's store would fail HERE rather than in a consumer's
        # lane after the source moved (O1's source-removal attack).
        seed_oids = set(
            _closure_oids(
                executable,
                git_dir=seed_git_dir,
                work_tree=None,
                cwd=seed_git_dir,
                commit=spec.commit,
                deadline=deadline,
                max_objects=spec.limits.max_objects,
            )
        )
        if seed_oids != set(oids):
            raise _git_failed(
                f"the private seed holds {len(seed_oids)} reachable objects, "
                f"not the {len(oids)} inventoried at the source"
            )
        seed_metadata = _object_metadata(
            executable,
            git_dir=seed_git_dir,
            work_tree=None,
            cwd=seed_git_dir,
            oids=oids,
            deadline=deadline,
        )
        if seed_metadata != metadata:
            raise _git_failed(
                "the private seed's object types/sizes differ from the source inventory"
            )
        if (seed_git_dir / "objects" / "info" / "alternates").exists():
            raise _git_failed("the private seed depends on an alternate object store")

        manifest = _build_manifest(
            executable,
            seed_git_dir=seed_git_dir,
            spec=spec,
            metadata=metadata,
            deadline=deadline,
        )
        repository = SnapshotRepository(
            spec=spec,
            source=source,
            seed_git_dir=seed_git_dir,
            template=template,
            manifest=manifest,
        )
        yield repository
    except BaseException:
        # Already failing: close and discard best-effort. Neither a cleanup
        # failure nor the live-child programmer error may mask the terminal
        # the caller is actually about to see.
        for leaked in _close_repository(repository):
            shutil.rmtree(leaked, ignore_errors=True)
        shutil.rmtree(child, ignore_errors=True)
        raise
    else:
        live = _close_repository(repository)
        # Refusing loudly is right, but a refusal that ABANDONS a snapshot
        # directory inside caller-owned scratch would be the silent state
        # leak the terminal table forbids -- so the misused children are
        # removed first and the programmer error is still raised.
        for leaked in live:
            shutil.rmtree(leaked, ignore_errors=True)
        _remove_owned_tree(child)
        if live:
            raise RuntimeError(
                f"prepare_snapshot's context closed with {len(live)} live "
                f"snapshot context(s); close every materialization first"
            )
