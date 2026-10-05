"""Isolated, source-first release transactions.

``cmru release`` is deliberately launched from an ordinary developer checkout but
never publishes from it.  A transaction pins ``origin/main`` to a commit, creates
an isolated worktree on a private ``cmru-release-<id>`` branch, and executes the
release child there.  The caller's uncommitted files therefore cannot leak into a
wheel, image, tag, or release asset.

The parent process owns a repository-local flock for the lifetime of its child.
The child builds and publishes from the fixed candidate, then pushes that exact
branch tip to ``origin/main``. When ``origin/main`` advanced during the gate the
push is rejected; the candidate is then never rebased or force-pushed. Instead
``origin/main`` is merged INTO the candidate (``--no-ff``, at most three
attempts) provided the released project's own paths are untouched; a conflict,
a touched project path or unknown paths stop with recovery instructions
(REL-04, see :func:`promote_workspace`).
"""
from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, NamedTuple, Sequence

from cmru.git_auth import (
    GitHubGitAuth,
    run_local_git,
    run_remote_git,
    without_publisher_tokens,
)
from cmru.config_names import PROJECT_CONFIG_FILENAME


CHILD_ENV = "CMRU_RELEASE_TRANSACTION_CHILD"
BRANCH_ENV = "CMRU_RELEASE_BRANCH"
BASE_ENV = "CMRU_RELEASE_BASE"
_LEGACY_RESUME_METADATA_KEY = "transaction_scope"
_LEGACY_RESUME_METADATA_VALUE = "legacy-release-resume"
# REL-14: one commit-id grammar for sidecars and abandon -- SHA-1 (40 hex) and
# SHA-256 (64 hex) object formats alike.
COMMIT_ID_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def is_transaction_child(repo_root: Path) -> bool:
    """Return whether this process is running in its exact managed child worktree.

    The environment carries routing facts from :func:`run_child`, but it is not
    ownership evidence by itself: callers can set environment variables. Verify
    the Git worktree, same-family source root, transaction branch, and shared
    CMRU ownership record (or a validated legacy release adoption) before
    allowing the in-place path. A parser switch is not part of the public grammar.
    """
    if os.environ.get(CHILD_ENV) != "1":
        return False
    expected_path = os.environ.get("CMRU_WORKSPACE_PATH", "").strip()
    expected_source_root = os.environ.get("CMRU_SOURCE_GIT_ROOT", "").strip()
    expected_branch = os.environ.get(BRANCH_ENV, "").strip()
    expected_base = os.environ.get(BASE_ENV, "").strip()
    expected_workspace_id = os.environ.get("CMRU_WORKSPACE_ID", "").strip()
    if not all((expected_path, expected_source_root, expected_branch,
                expected_base, expected_workspace_id)):
        raise RuntimeError("incomplete CMRU transaction child context")
    expected_path_obj = Path(expected_path).expanduser().resolve()
    source_root_obj = Path(expected_source_root).expanduser().resolve()
    if expected_path_obj != Path(repo_root).resolve():
        raise RuntimeError(
            "CMRU transaction child context does not match the loaded repository root"
        )

    shared = _shared_worktree()
    try:
        child_top, child_common, actual_branch, child_head = shared.discover_git_context(
            expected_path_obj
        )
        source_top, source_common, _source_branch, _source_head = shared.discover_git_context(
            source_root_obj
        )
        worktrees = shared.list_git_worktrees(source_top)
        record = shared.find_workspace(child_common, child_top)
    except Exception as exc:
        raise RuntimeError(f"invalid CMRU transaction child worktree: {exc}") from exc

    child_top = Path(child_top).resolve()
    source_top = Path(source_top).resolve()
    if child_top != expected_path_obj or source_top != source_root_obj:
        raise RuntimeError("CMRU transaction child paths do not resolve to Git worktree roots")
    if Path(child_common).resolve() != Path(source_common).resolve():
        raise RuntimeError("CMRU transaction child belongs to a different Git family")
    if actual_branch != expected_branch:
        raise RuntimeError(
            f"CMRU transaction child branch mismatch: expected {expected_branch!r}, "
            f"found {actual_branch!r}"
        )

    if _is_release_branch(actual_branch):
        purpose = "release"
    elif _is_build_branch(actual_branch):
        purpose = "build"
    else:
        raise RuntimeError(
            f"CMRU transaction child branch is not managed: {actual_branch!r}"
        )

    matches = [
        entry for entry in worktrees
        if Path(entry.path).resolve() == child_top
    ]
    if len(matches) != 1 or matches[0].is_primary or matches[0].branch != actual_branch:
        raise RuntimeError("CMRU transaction child is not a registered secondary worktree")

    if record is None:
        raise RuntimeError("CMRU transaction child has no shared ownership record")
    _require_cmru_record_purpose(record, purpose, child_top)
    if record.purpose == "cmru-legacy" and purpose != "release":
        raise RuntimeError("recordless legacy compatibility is release-resume only")
    if record.branch != actual_branch or Path(record.worktree_path).resolve() != child_top:
        raise RuntimeError("CMRU shared transaction record does not match its worktree")
    if Path(record.source_git_root).resolve() != source_root_obj:
        raise RuntimeError("CMRU shared transaction record has a different source root")
    if record.workspace_id != expected_workspace_id:
        raise RuntimeError("CMRU transaction child workspace ID does not match its record")
    if record.base_commit != expected_base:
        raise RuntimeError("CMRU transaction child base does not match its record")
    if record.purpose == "cmru-legacy":
        metadata = getattr(record, "metadata", None)
        if (
            not isinstance(metadata, Mapping)
            or metadata.get(_LEGACY_RESUME_METADATA_KEY) != _LEGACY_RESUME_METADATA_VALUE
        ):
            raise RuntimeError(
                "legacy CMRU release child has no validated resume metadata"
            )
        _validate_legacy_release_progress(
            source_root_obj, child_top, actual_branch, child_head,
        )

    return True


def _git(repo_root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_root, text=True, capture_output=True, check=False,
    )
    if check and result.returncode:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def _common_git_dir(repo_root: Path) -> Path:
    return _shared_worktree().discover_git_root(repo_root)[1]


def _shared_workspace_record(shared: Any, common: Path, path: Path) -> Any | None:
    """Find the shared lifecycle record for one literal Git worktree path."""
    return shared.find_workspace(common, path)


def _require_cmru_record_purpose(record: Any, purpose: str, path: Path) -> None:
    """Refuse to treat another product's shared checkout as a CMRU transaction.

    ``cmru-legacy`` records either the compatibility removal bridge or a
    validated adoption of an older release candidate; its branch still has to
    match the requested CMRU operation before that record can be resumed or
    discarded.
    """
    expected = f"cmru-{purpose}"
    if record.purpose not in {expected, "cmru-legacy"}:
        raise RuntimeError(
            f"{path} is recorded as workspace purpose {record.purpose!r}, "
            f"not a CMRU {purpose} transaction"
        )


@dataclass(frozen=True)
class ReleaseWorkspace:
    """The immutable source snapshot and private branch for one release."""

    repo_root: Path
    path: Path
    branch: str
    base: str
    context: object | None = None
    is_prunable: bool = False

    @property
    def workspace_id(self) -> str | None:
        value = getattr(self.context, "workspace_id", None)
        if value:
            return str(value)
        return _shared_worktree().workspace_id_for_path(self.path)


def _validate_legacy_release_progress(
    repo_root: Path, path: Path, branch: str, head: str,
) -> str:
    """Require a valid, committed legacy-release checkpoint at or before HEAD."""
    if not _is_release_branch(branch):
        raise RuntimeError(f"{path} is not a retained cmru release branch (got {branch!r})")
    if not COMMIT_ID_RE.fullmatch(head):
        raise RuntimeError(f"{path} has an invalid Git HEAD; refusing legacy resume")
    progress_workspace = ReleaseWorkspace(
        repo_root=repo_root.resolve(), path=path.resolve(), branch=branch, base=head,
    )
    progress = read_release_progress(repo_root, progress_workspace)
    if progress is None or not COMMIT_ID_RE.fullmatch(progress):
        raise RuntimeError(
            f"{path} has no valid CMRU release progress record; refusing legacy resume"
        )
    progress_check = run_local_git(
        path, "merge-base", "--is-ancestor", progress, head,
        capture_output=True, text=True, check=False,
    )
    if progress_check.returncode == 1:
        raise RuntimeError(
            f"{path} release progress is not an ancestor of the retained candidate; "
            "refusing legacy resume"
        )
    if progress_check.returncode != 0:
        detail = progress_check.stderr.strip() or progress_check.stdout.strip() or "no diagnostic output"
        raise RuntimeError(
            f"cannot validate legacy release progress for {path} "
            f"({progress_check.returncode}): {detail}"
        )
    return progress


def _shared_worktree():
    """Load the internal source dependency in checkout and wheel modes."""
    try:
        import worktree
        return worktree
    except ModuleNotFoundError:
        # Source-tree test runners import cmru/src directly.  Wheels include
        # this same package through pyproject's package-dir mapping; this
        # fallback only makes direct source execution use that canonical source.
        library_src = Path(__file__).resolve().parents[3] / "libraries" / "worktree" / "src"
        if library_src.is_dir():
            sys.path.insert(0, str(library_src))
            import worktree
            return worktree
        raise


def project_git_family_groups(
    repo_root: Path, projects: Sequence[object],
) -> dict[Path, list[object]]:
    """Group selected projects by their actual Git object/ref family.

    The CMRU root is orchestration scope, not automatically a Git root.  A
    central orchestration file may therefore coordinate projects from several
    repositories, but each family gets its own transaction workspace.  The
    insertion order is retained so release ordering remains the configured
    project order within each family.
    """

    shared = _shared_worktree()
    groups: dict[tuple[Path, Path], list[object]] = {}
    for project in projects:
        project_root = getattr(project, "project_root", None)
        if project_root is None:
            raise RuntimeError("CMRU project has no project_root; cannot resolve its Git family")
        selected = Path(project_root)
        if not selected.is_absolute():
            selected = repo_root / selected
        selected = selected.resolve()
        try:
            top, common, _branch, _head = shared.discover_git_context(selected)
        except Exception as exc:
            raise RuntimeError(
                f"CMRU project root {selected} is not inside a usable Git worktree: {exc}"
            ) from exc
        groups.setdefault((common, top), []).append(project)
    if not groups:
        try:
            top, _common, _branch, _head = shared.discover_git_context(repo_root)
        except Exception as exc:
            raise RuntimeError(
                f"CMRU root {repo_root} has no selected project Git family: {exc}"
            ) from exc
        return {top: []}
    by_root: dict[Path, list[object]] = {}
    for (_common, top), members in groups.items():
        by_root[top] = members
    return by_root


def source_git_root_for_projects(repo_root: Path, projects: Sequence[object]) -> Path:
    """Resolve the sole Git family for one transaction child.

    Multi-family callers split the operation with
    :func:`project_git_family_groups`; retaining this strict helper prevents a
    single child from accidentally treating independent repositories as one
    worktree.
    """
    groups = project_git_family_groups(repo_root, projects)
    if len(groups) != 1:
        details = ", ".join(str(root) for root in sorted(groups))
        raise RuntimeError(
            "selected CMRU projects belong to independent Git families; split the "
            f"transaction by family before allocating a worktree ({details})"
        )
    return next(iter(groups))


class _SyncLocalMainResult(NamedTuple):
    """The private outcome of one caller-main synchronization attempt."""

    ok: bool
    reason: str = ""


@contextmanager
def release_lock(repo_root: Path) -> Iterator[None]:
    """Serialize local release transactions without relying on a mutable checkout."""
    lock_path = _common_git_dir(repo_root) / "cmru-release.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another cmru release transaction is already running.") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def fetch_origin_main(
    repo_root: Path, *, git_auth: GitHubGitAuth | None = None,
) -> str:
    """Fetch and return the exact remote commit authoritative for a new release."""
    run_remote_git(
        repo_root, "fetch", "--prune", "origin", "main", auth=git_auth, check=True,
    )
    return _git(repo_root, "rev-parse", "origin/main")


def local_main_divergence(repo_root: Path, *, ref: str = "main") -> tuple[int, int]:
    """Return commits ``(ahead, behind)`` for ``ref`` relative to origin/main.

    KI-20: ``ref`` always reads the SHARED ``refs/heads/main`` unless a caller
    overrides it -- in a repo with many worktrees, ``refs/heads/main`` is one
    object-store-wide ref, unrelated to which worktree/commit the invoking
    checkout actually has checked out. Pass ``ref="origin/main"`` (or any
    git-resolvable ref, including ``"HEAD"`` for "whatever this invocation's
    own checkout has") to evaluate against something other than the literal
    local ``main`` branch.
    """
    try:
        counts = _git(repo_root, "rev-list", "--left-right", "--count", f"{ref}...origin/main")
        ahead, behind = counts.split()
        return int(ahead), int(behind)
    except (RuntimeError, ValueError) as exc:
        label = "local main" if ref == "main" else repr(ref)
        raise RuntimeError(
            f"Cannot compare {label} with origin/main; fetch/repair the ref, or pass a "
            "different --ref, before starting a release."
        ) from exc


def assert_local_main_not_ahead(repo_root: Path, *, ref: str = "main") -> int:
    """Reject commits under ``ref`` that an origin/main snapshot would omit.

    A behind ``ref`` is harmless because ``origin/main`` is deliberately
    authoritative; the caller receives that count so it can be reported.
    See :func:`local_main_divergence` for the ``ref`` override (KI-20).
    """
    ahead, behind = local_main_divergence(repo_root, ref=ref)
    if ahead:
        label = "Local main" if ref == "main" else repr(ref)
        raise RuntimeError(
            f"{label} is {ahead} commit(s) ahead of origin/main. Push those commits (or "
            "explicitly base the intended change on origin/main) before release; an "
            "isolated release snapshots origin/main and would omit them."
        )
    return behind


_SCOPE_INVALID_RE = re.compile(r"[^a-z0-9-]+")


def _sanitize_scope(scope: str | None) -> str:
    """Normalise a target (or the unscoped ``all``) into a branch/path-safe
    token (KI-16): lowercase, ``[a-z0-9-]`` only, collapsed, never empty. A
    project name is already constrained by config validation, but this feeds
    directly into a branch name and a worktree directory and must not assume
    that holds -- an unscoped run, an empty string, or an unexpected character
    must all still produce a safe, non-empty token."""
    if not scope:
        return "all"
    cleaned = _SCOPE_INVALID_RE.sub("-", scope.strip().lower()).strip("-")
    return cleaned or "all"


# Transaction branch prefixes. The FLAT ``cmru-<purpose>-`` scheme is what this
# code now creates (KI-16, aligned to ciu's ``<prefix>-<YYYYMMDD_HHMMSS>-<feat>``
# naming): the branch string and its worktree directory basename are then one and
# the same, with no nested ref path. The OLD nested ``cmru/<purpose>/`` scheme is
# still recognised for discovery/resume/cleanup so retained worktrees created
# before this change stay just as removable -- a transaction created under either
# scheme must remain identifiable as the release/build it is.
_FLAT_RELEASE_PREFIX = "cmru-release-"
_FLAT_BUILD_PREFIX = "cmru-build-"
_NESTED_RELEASE_PREFIX = "cmru/release/"
_NESTED_BUILD_PREFIX = "cmru/build/"


def _is_release_branch(branch: str) -> bool:
    """True for a release transaction branch under EITHER naming scheme."""
    return branch.startswith(_FLAT_RELEASE_PREFIX) or branch.startswith(_NESTED_RELEASE_PREFIX)


def _is_build_branch(branch: str) -> bool:
    """True for a build transaction branch under EITHER naming scheme."""
    return branch.startswith(_FLAT_BUILD_PREFIX) or branch.startswith(_NESTED_BUILD_PREFIX)


def _is_transaction_branch(branch: str) -> bool:
    return _is_release_branch(branch) or _is_build_branch(branch)


def workspace_purpose(branch: str) -> str:
    """The transaction purpose ("release" or "build") ``branch`` belongs to,
    under EITHER naming scheme (KI-21). A flat ``cmru-<purpose>-...`` branch
    has no ``/`` at all -- callers must use this instead of index-splitting
    the branch string, which crashes on exactly that shape. This is a display
    helper, not a validity check: an unrecognized legacy nested
    ``cmru/<kind>/<token>`` branch still has a middle segment worth showing
    (preserving the old ``split("/", 2)[1]`` behavior for exactly that
    shape); an unrecognized flat branch has no separator to extract from and
    falls back to the raw branch name."""
    if _is_release_branch(branch):
        return "release"
    if _is_build_branch(branch):
        return "build"
    parts = branch.split("/", 2)
    return parts[1] if len(parts) >= 2 else branch


def _new_transaction_branch(
    purpose: str,
    scope: str | None,
    *,
    identity: str,
    suffix: int = 1,
) -> str:
    """Return a readable transaction branch without a second UUID identity.

    Flat single-token name -- no nested ref path -- so the branch string IS the
    worktree directory basename (see :func:`_worktree_dirname`). The ``_``
    between date and time matches ciu's ``<prefix>-<YYYYMMDD_HHMMSS>-<feature>``
    scheme and keeps the date/time boundary visually distinct from the ``-``
    field separators. UTC timestamp for chronological sort and a numeric suffix
    for the rare same-second/same-scope allocation collision. The shared
    allocator's six-character path identity is the ownership identity and is
    persisted in its record; CMRU does not invent a second UUID namespace.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix_text = "" if suffix == 1 else f"-{suffix}"
    return f"cmru-{purpose}-{timestamp}-{_sanitize_scope(scope)}-{identity}{suffix_text}"


def _worktree_dirname(branch: str) -> str:
    """The worktree directory basename for ``branch`` (KI-16).

    For a flat ``cmru-<purpose>-...`` branch this is the identity: branch string
    and directory basename are exactly equal -- true 1:1 naming, nothing to
    reconstruct in either direction. A legacy nested ``cmru/<purpose>/...``
    branch (never created here any more) still maps its ``/`` to ``-`` so an
    older retained worktree's on-disk basename remains derivable.
    """
    return branch.replace("/", "-")


def create_workspace(
    repo_root: Path, *, base: str | None = None, purpose: str = "release", scope: str | None = None,
    source_git_root: Path | None = None, git_auth: GitHubGitAuth | None = None,
) -> ReleaseWorkspace:
    """Create a worktree at one already-fetched authoritative remote commit.

    ``purpose`` is intentionally visible in the branch/path. A successful release
    is ephemeral; a normal build is retained for inspection and must therefore
    never be mistaken for a failed, resumable release attempt. ``scope`` is the
    target value when the run is scoped, else ``None`` (recorded as
    ``all``) -- see :func:`_new_transaction_branch`.

    Discovery, resume, and cleanup recognise a transaction branch through
    :func:`_is_release_branch` / :func:`_is_build_branch`, which accept BOTH the
    flat ``cmru-<purpose>-`` names created here and the legacy nested
    ``cmru/<purpose>/`` names -- so retained worktrees from either the current
    scheme or the OLD ``cmru/<purpose>/<12-hex>`` one remain equally
    discoverable, resumable, and removable.
    """
    if purpose not in {"release", "build"}:
        raise ValueError(f"unknown CMRU workspace purpose: {purpose}")
    source_root = (source_git_root or repo_root).resolve()
    if base is None:
        base = fetch_origin_main(source_root, git_auth=git_auth)
    shared = _shared_worktree()
    parent = source_root / ".worktrees"
    parent.mkdir(exist_ok=True)
    # The visible allocation identity is derived before the final branch/path
    # is named. This avoids the impossible self-reference of hashing a path
    # whose basename contains the hash itself; the durable shared record stores
    # this explicit canonical seed path, so ownership admission and resume use
    # the same identity input.
    seed_branch = _new_transaction_branch(
        purpose, scope, identity=_shared_worktree().workspace_id_for_path(parent / "allocation-seed")
    )
    visible_identity = _shared_worktree().workspace_id_for_path(parent / seed_branch)
    branch = _new_transaction_branch(purpose, scope, identity=visible_identity)
    path = parent / _worktree_dirname(branch)
    # Uniqueness is admitted by the shared family allocator and its path-derived
    # record identity, so there is nothing to mkdtemp+rmdir for. Explicitly
    # refuse an existing path rather than
    # trusting `git worktree add` to: git only fails closed on a NON-EMPTY
    # existing directory -- it silently ADOPTS an empty pre-existing one,
    # which would not be "this ONE transaction exclusively owns the name it
    # created" if that ever happened (an allocation collision or stale leftover).
    if path.exists():
        raise RuntimeError(
            f"worktree path already exists: {path}; refusing to reuse an occupied transaction name"
        )
    try:
        with without_publisher_tokens():
            context = shared.create_workspace(
                source_root,
                # The workspace allocator must operate on the selected Git
                # family, never on an orchestration directory above it.
                path,
                branch=branch,
                base=base,
                purpose=f"cmru-{purpose}",
                labels={"cmru.purpose": purpose, "cmru.scope": _sanitize_scope(scope)},
                metadata={"transaction_scope": _sanitize_scope(scope)},
                identity_path=parent / seed_branch,
            )
        # The neutral allocator deliberately admits a checkout with no
        # files so CIU can write its adapter record before its own reset.
        # CMRU has no such staged allocation phase: its child must see the
        # committed project documents before it starts, so materialize the
        # exact requested snapshot before returning the workspace.
        checkout = run_local_git(
            path, "reset", "--hard", base,
            text=True,
            capture_output=True,
            check=False,
        )
        if checkout.returncode:
            try:
                with without_publisher_tokens():
                    shared.remove_workspace(context, force=True)
            except Exception:
                pass
            raise RuntimeError(
                f"git reset --hard {base} failed ({checkout.returncode}): "
                f"{(checkout.stderr or checkout.stdout).strip()}"
            )
        return ReleaseWorkspace(
            repo_root=source_root, path=path, branch=branch, base=base, context=context,
        )
    except shared.WorkspaceError as exc:
        raise RuntimeError(str(exc)) from exc


def resume_workspace(
    repo_root: Path, path: Path, *, git_auth: GitHubGitAuth | None = None,
) -> ReleaseWorkspace:
    """Validate and reopen a retained release worktree (flat ``cmru-release-*``
    or legacy nested ``cmru/release/*``)."""
    path = path.resolve()
    if not path.is_dir():
        raise RuntimeError(f"release worktree does not exist: {path}")
    shared = _shared_worktree()
    try:
        path_top, path_common, _path_branch, _path_head = shared.discover_git_context(path)
    except Exception as exc:
        raise RuntimeError(f"{path} is not a worktree: {exc}") from exc
    try:
        expected_common = _common_git_dir(repo_root)
    except RuntimeError:
        expected_common = None
    if expected_common is not None and path_common != expected_common:
        raise RuntimeError(f"{path} is not a worktree of {repo_root}")
    # New transactions resume directly from their CMRU record. Legacy
    # candidates, including a removal-bridge record, must revalidate progress
    # and refresh origin/main before returning or completing adoption.
    try:
        _top, common, _branch, _head = shared.discover_git_context(path)
        record = _shared_workspace_record(shared, common, path)
        if record is not None:
            _require_cmru_record_purpose(record, "release", path)
            context = shared.ensure_workspace(record)
            if not _is_release_branch(context.branch):
                raise RuntimeError(
                    f"{path} is not a retained cmru release branch (got {context.branch!r})"
                )
            if record.purpose == "cmru-legacy":
                metadata = getattr(record, "metadata", None)
                if not isinstance(metadata, Mapping):
                    raise RuntimeError(
                        f"{path} has invalid legacy CMRU workspace metadata; refusing resume"
                    )
                scope = metadata.get(_LEGACY_RESUME_METADATA_KEY)
                if scope not in (None, _LEGACY_RESUME_METADATA_VALUE):
                    raise RuntimeError(
                        f"{path} has an unrecognized legacy CMRU transaction scope; "
                        "refusing resume"
                    )
                _top, _common, retained_branch, retained_head = shared.discover_git_context(path)
                _validate_legacy_release_progress(
                    repo_root, path, retained_branch, retained_head,
                )
                run_remote_git(
                    path, "fetch", "--prune", "origin", "main",
                    auth=git_auth, check=True,
                )
                if scope is None:
                    metadata = dict(metadata)
                    metadata[_LEGACY_RESUME_METADATA_KEY] = _LEGACY_RESUME_METADATA_VALUE
                    context = shared.ensure_workspace(record, metadata=metadata)
            return ReleaseWorkspace(
                repo_root=repo_root.resolve(),
                path=path,
                branch=context.branch,
                base=context.base_commit,
                context=context,
            )
    except subprocess.CalledProcessError:
        raise
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc
    branch = _git(path, "branch", "--show-current")
    if not _is_release_branch(branch):
        raise RuntimeError(f"{path} is not a retained cmru release branch (got {branch!r})")
    if expected_common is None:
        raise RuntimeError(
            f"cannot validate legacy release worktree {path}: source Git family is unknown"
        )
    head = _git(path, "rev-parse", "HEAD")
    _validate_legacy_release_progress(repo_root, path, branch, head)
    try:
        source_top, source_common, _source_branch, _source_head = shared.discover_git_context(
            repo_root
        )
        if (
            source_common != path_common
            or Path(source_top).resolve() != repo_root.resolve()
            or Path(path_top).resolve() != path
        ):
            raise RuntimeError("legacy release worktree and source root do not share the exact Git family")
    except Exception as exc:
        raise RuntimeError(f"cannot adopt validated legacy release worktree {path}: {exc}") from exc
    run_remote_git(
        path_top, "fetch", "--prune", "origin", "main", auth=git_auth, check=True,
    )
    try:
        context = shared.adopt_workspace(
            source_top,
            path,
            purpose="cmru-legacy",
            labels={"cmru.purpose": "release"},
            metadata={_LEGACY_RESUME_METADATA_KEY: _LEGACY_RESUME_METADATA_VALUE},
            identity_path=path,
        )
    except Exception as exc:
        raise RuntimeError(f"cannot adopt validated legacy release worktree {path}: {exc}") from exc
    return ReleaseWorkspace(
        repo_root=repo_root.resolve(), path=path, branch=branch,
        base=context.base_commit, context=context,
    )


def assert_resume_workspace_committed(path: Path) -> None:
    """Refuse to resume while operator fixes are still outside the candidate commit.

    The resumed prepare and gate run against this exact branch tip. Requiring a
    clean worktree prevents a successful tag/promotion from silently omitting
    an uncommitted correction that the operator expected to ship.
    """
    changes = _git(path, "status", "--porcelain=v1", "--untracked-files=normal")
    if changes:
        raise RuntimeError(
            "retained release worktree has uncommitted changes. Commit the fixes on "
            "that release branch, then rerun `cmru release --resume`; the resumed "
            "prepare and required gate will run against and ship that commit."
        )


def _copy_secret_overlay(
    source: Path, workspace_root: Path, relative_target: Path,
) -> None:
    """Install one mode-0600 secret copy without following source or target links."""
    try:
        source_metadata = source.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(source_metadata.st_mode):
        raise RuntimeError(f"publisher credential path is not a regular file: {source}")
    if relative_target.is_absolute() or ".." in relative_target.parts or not relative_target.parts:
        raise RuntimeError(f"publisher credential target escapes its worktree: {relative_target}")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(source, flags)
    parent_flags = (
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    root_fd = parent_fd = -1
    destination_fd = -1
    temporary_name: str | None = None
    try:
        opened_source = os.fstat(source_fd)
        if (
            not stat.S_ISREG(opened_source.st_mode)
            or (opened_source.st_dev, opened_source.st_ino)
            != (source_metadata.st_dev, source_metadata.st_ino)
        ):
            raise RuntimeError(f"publisher credential changed while being opened: {source}")

        root_fd = os.open(workspace_root, parent_flags)
        parent_fd = root_fd
        for component in relative_target.parts[:-1]:
            try:
                os.mkdir(component, 0o755, dir_fd=parent_fd)
            except FileExistsError:
                pass
            child_fd = os.open(component, parent_flags, dir_fd=parent_fd)
            if parent_fd != root_fd:
                os.close(parent_fd)
            parent_fd = child_fd

        target_name = relative_target.parts[-1]

        def target_metadata():
            try:
                return os.stat(target_name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return None

        original_target = target_metadata()
        if original_target is not None and not stat.S_ISREG(original_target.st_mode):
            raise RuntimeError(
                f"publisher credential destination is not a regular file: "
                f"{workspace_root / relative_target}"
            )

        temporary_name = f".{target_name}.cmru-secret-{secrets.token_hex(16)}"
        destination_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent_fd,
        )
        with os.fdopen(source_fd, "rb", closefd=False) as source_stream:
            with os.fdopen(destination_fd, "wb", closefd=False) as destination_stream:
                shutil.copyfileobj(source_stream, destination_stream)
                destination_stream.flush()
                os.fchmod(destination_fd, 0o600)
                os.fsync(destination_fd)
        os.close(destination_fd)
        destination_fd = -1
        after_source = source.lstat()
        if (
            not stat.S_ISREG(after_source.st_mode)
            or (after_source.st_dev, after_source.st_ino)
            != (source_metadata.st_dev, source_metadata.st_ino)
        ):
            raise RuntimeError(f"publisher credential changed while being copied: {source}")

        current_target = target_metadata()
        if original_target is None:
            target_changed = current_target is not None
        else:
            target_changed = (
                current_target is None
                or not stat.S_ISREG(current_target.st_mode)
                or (current_target.st_dev, current_target.st_ino)
                != (original_target.st_dev, original_target.st_ino)
            )
        if target_changed:
            raise RuntimeError(
                f"publisher credential destination changed while being copied: "
                f"{workspace_root / relative_target}"
            )

        # The temp file and final name share a directory. Replacing the path is
        # atomic and never follows a symlink installed at the destination.
        os.replace(
            temporary_name, target_name,
            src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
        )
        temporary_name = None
    finally:
        os.close(source_fd)
        if destination_fd >= 0:
            os.close(destination_fd)
        if temporary_name is not None and parent_fd >= 0:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
        if parent_fd >= 0 and parent_fd != root_fd:
            os.close(parent_fd)
        if root_fd >= 0:
            os.close(root_fd)


def copy_secret_overlays(
    repo_root: Path, workspace: ReleaseWorkspace, project_config_paths: Sequence[Path],
    *, candidate_config_paths: Sequence[Path] | None = None,
) -> None:
    """Copy the root credential and explicit project overlays into a child worktree."""
    if candidate_config_paths is not None and len(candidate_config_paths) != len(project_config_paths):
        raise RuntimeError(
            "candidate project config paths must match the source project config paths"
        )
    source_root = workspace.repo_root.resolve()
    workspace_root = workspace.path
    source = repo_root.resolve() / "cmru.secret.toml"
    if repo_root.resolve() == source_root:
        _copy_secret_overlay(source, workspace_root, Path("cmru.secret.toml"))
    # A central CMRU root can be outside the selected Git family. In that
    # layout the child receives the absolute orchestration config and reads the
    # central root secret directly; copying it into an unrelated worktree would
    # make ownership ambiguous.
    for index, config_path in enumerate(project_config_paths):
        config_path = config_path.resolve()
        if candidate_config_paths is None:
            try:
                relative = config_path.parent.relative_to(source_root)
            except ValueError as exc:
                raise RuntimeError(
                    f"project config is outside selected Git workspace {source_root}: {config_path}"
                ) from exc
        else:
            candidate_config = Path(candidate_config_paths[index])
            if (
                candidate_config.is_absolute()
                or ".." in candidate_config.parts
                or candidate_config.name != PROJECT_CONFIG_FILENAME
            ):
                raise RuntimeError(
                    f"candidate project config path is unsafe: {candidate_config}"
                )
            relative = candidate_config.parent
        source = config_path.with_name("cmru.secret.toml")
        _copy_secret_overlay(
            source, workspace_root, relative / "cmru.secret.toml",
        )


def remove_workspace(workspace: ReleaseWorkspace) -> None:
    """Remove a successful ephemeral worktree and its private branch."""
    if workspace.context is not None:
        try:
            with without_publisher_tokens():
                _shared_worktree().remove_workspace(workspace.context)
            return
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc
    try:
        with without_publisher_tokens():
            _shared_worktree().remove_unrecorded_workspace(
                workspace.repo_root,
                workspace.path,
                expected_branch=workspace.branch,
                purpose="cmru-legacy",
                force=True,
            )
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc


def _release_token(workspace: ReleaseWorkspace) -> str:
    """The ``<timestamp>_<scope>`` token that names this transaction's
    sidecar state files, stripped of its scheme prefix so the token is stable
    and prefix-free under both naming schemes: a flat ``cmru-<purpose>-<token>``
    branch drops the ``cmru-release-``/``cmru-build-`` head; a legacy nested
    ``cmru/<purpose>/<token>`` branch takes its last path segment."""
    branch = workspace.branch
    for prefix in (_FLAT_RELEASE_PREFIX, _FLAT_BUILD_PREFIX):
        if branch.startswith(prefix):
            return branch[len(prefix):]
    return branch.rsplit("/", 1)[-1]


def _scope_dir(repo_root: Path) -> Path:
    return _common_git_dir(repo_root) / "cmru-release-scopes"


def _ensure_scope_dir(repo_root: Path) -> Path:
    """Create the transaction metadata directory beneath Git's existing common dir."""
    scope_dir = _scope_dir(repo_root)
    scope_dir.mkdir(exist_ok=True)
    return scope_dir


def write_release_scope(repo_root: Path, workspace: ReleaseWorkspace, project_names: Sequence[str]) -> None:
    """Record which projects a release attempt targets, in the shared common git
    dir (never inside the worktree — S-REL.4a's undeclared-write guard must never
    see it). The first-class ``cmru abandon`` command reports this exact scope."""
    scope_dir = _ensure_scope_dir(repo_root)
    (scope_dir / f"{_release_token(workspace)}.json").write_text(
        json.dumps(sorted(project_names)), encoding="utf-8",
    )


def write_release_tag_snapshot(
    repo_root: Path, workspace: ReleaseWorkspace, tag_refs: Mapping[str, str],
) -> None:
    """Record origin's exact tag refs before this release attempt can create any.

    The snapshot is a separate shared-Git sidecar so old project-scope records
    remain readable. A resume must preserve the original snapshot rather than
    replacing it with the post-failure remote state.
    """
    path = _ensure_scope_dir(repo_root) / f"{_release_token(workspace)}.tags.json"
    for ref, oid in tag_refs.items():
        if (
            not isinstance(ref, str)
            or not _valid_ls_remote_ref(ref, "refs/tags/")
            or not isinstance(oid, str)
            or not COMMIT_ID_RE.fullmatch(oid)
        ):
            raise RuntimeError("origin tag snapshot contains a malformed ref record")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"release tag snapshot already exists: {path}") from exc
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(sorted(tag_refs.items())), indent=2) + "\n")


def read_release_tag_snapshot(
    repo_root: Path, workspace: ReleaseWorkspace,
) -> dict[str, str] | None:
    """Return the immutable pre-attempt remote tag set, or None for legacy workspaces."""
    path = _scope_dir(repo_root) / f"{_release_token(workspace)}.tags.json"
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError(f"cannot inspect release tag snapshot {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"release tag snapshot is not a regular file: {path}")
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError(f"cannot read release tag snapshot {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"release tag snapshot is malformed: {path}")
    snapshot: dict[str, str] = {}
    for ref, oid in raw.items():
        if (
            not isinstance(ref, str)
            or not _valid_ls_remote_ref(ref, "refs/tags/")
            or not isinstance(oid, str)
            or not COMMIT_ID_RE.fullmatch(oid)
        ):
            raise RuntimeError(f"release tag snapshot is malformed: {path}")
        snapshot[ref] = oid
    return snapshot


def list_local_tag_refs(repo_root: Path) -> dict[str, str]:
    """Return local tag refs and their exact object IDs without folding errors into absence."""
    result = run_local_git(
        repo_root, "for-each-ref", "--format=%(refname)%09%(objectname)", "refs/tags/",
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise RuntimeError(f"cannot inspect local release tags ({result.returncode}): {detail}")
    refs: dict[str, str] = {}
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if (
            len(fields) != 2
            or not _valid_ls_remote_ref(fields[0], "refs/tags/")
            or not COMMIT_ID_RE.fullmatch(fields[1])
        ):
            raise RuntimeError(f"local tag listing returned a malformed ref record: {line!r}")
        ref, oid = fields
        if ref in refs:
            raise RuntimeError(f"local tag listing returned a duplicate ref record: {ref}")
        refs[ref] = oid
    return refs


def write_release_tag_attempts(
    repo_root: Path,
    workspace: ReleaseWorkspace,
    tag_refs: Mapping[str, str],
) -> None:
    """Record exact local release tags before CMRU attempts to push them."""
    path = _ensure_scope_dir(repo_root) / f"{_release_token(workspace)}.tag-attempts.json"
    incoming: dict[str, str] = {}
    for ref, oid in tag_refs.items():
        if (
            not isinstance(ref, str)
            or not _valid_ls_remote_ref(ref, "refs/tags/")
            or not isinstance(oid, str)
            or not COMMIT_ID_RE.fullmatch(oid)
        ):
            raise RuntimeError("local release tag attempt contains a malformed ref record")
        incoming[ref] = oid
    try:
        existing = read_release_tag_attempts(repo_root, workspace) or {}
    except RuntimeError:
        raise
    absence_proofs: dict[str, str] | None = None
    for ref, oid in incoming.items():
        previous = existing.get(ref)
        if previous is not None and previous != oid:
            if absence_proofs is None:
                absence_proofs = read_confirmed_absent_release_tag_attempts(
                    repo_root, workspace,
                )
            if absence_proofs.get(ref) != previous:
                raise RuntimeError(
                    f"release tag {ref} changed after a prior push attempt without "
                    "an exact origin-absence confirmation"
                )
        existing[ref] = oid
    # Clear proofs before replacing their matching attempt OIDs. If the later
    # attempts-file write fails, the candidate remains fail-closed: its older
    # proof cannot accidentally authorize the newly generated local tag.
    clear_release_tag_absence(repo_root, workspace, incoming)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(12)}")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(sorted(existing.items())), indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_release_tag_attempts(
    repo_root: Path, workspace: ReleaseWorkspace,
) -> dict[str, str] | None:
    """Return exact local tags attempted by CMRU, or None for older transactions."""
    path = _scope_dir(repo_root) / f"{_release_token(workspace)}.tag-attempts.json"
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError(f"cannot inspect release tag attempt record {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"release tag attempt record is not a regular file: {path}")
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError(f"cannot read release tag attempt record {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"release tag attempt record is malformed: {path}")
    attempts: dict[str, str] = {}
    for ref, oid in raw.items():
        if (
            not isinstance(ref, str)
            or not _valid_ls_remote_ref(ref, "refs/tags/")
            or not isinstance(oid, str)
            or not COMMIT_ID_RE.fullmatch(oid)
        ):
            raise RuntimeError(f"release tag attempt record is malformed: {path}")
        attempts[ref] = oid
    return attempts


def write_confirmed_absent_release_tag_attempts(
    repo_root: Path,
    workspace: ReleaseWorkspace,
    tag_refs: Mapping[str, str],
) -> None:
    """Record exact tag attempts CMRU confirmed absent remotely and removed locally."""
    attempts = read_release_tag_attempts(repo_root, workspace) or {}
    local_tags = list_local_tag_refs(repo_root)
    path = _scope_dir(repo_root) / f"{_release_token(workspace)}.tag-absent.json"
    existing = read_confirmed_absent_release_tag_attempts(repo_root, workspace)
    incoming: dict[str, str] = {}
    for ref, oid in tag_refs.items():
        if (
            not isinstance(ref, str)
            or not _valid_ls_remote_ref(ref, "refs/tags/")
            or not isinstance(oid, str)
            or not COMMIT_ID_RE.fullmatch(oid)
        ):
            raise RuntimeError("confirmed absent release tag record contains a malformed ref")
        if attempts.get(ref) != oid:
            raise RuntimeError(
                f"confirmed absent release tag {ref} does not match its recorded push attempt"
            )
        if ref in local_tags:
            raise RuntimeError(
                f"confirmed absent release tag {ref} still exists in the local repository"
            )
        incoming[ref] = oid
    existing.update(incoming)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(12)}")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(sorted(existing.items())), indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_confirmed_absent_release_tag_attempts(
    repo_root: Path, workspace: ReleaseWorkspace,
) -> dict[str, str]:
    """Return CMRU's exact remote-absence confirmations for prior tag attempts."""
    path = _scope_dir(repo_root) / f"{_release_token(workspace)}.tag-absent.json"
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise RuntimeError(f"cannot inspect release tag absence record {path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"release tag absence record is not a regular file: {path}")
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError(f"cannot read release tag absence record {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"release tag absence record is malformed: {path}")
    proofs: dict[str, str] = {}
    attempts = read_release_tag_attempts(repo_root, workspace) or {}
    for ref, oid in raw.items():
        if (
            not isinstance(ref, str)
            or not _valid_ls_remote_ref(ref, "refs/tags/")
            or not isinstance(oid, str)
            or not COMMIT_ID_RE.fullmatch(oid)
            or attempts.get(ref) != oid
        ):
            raise RuntimeError(f"release tag absence record is malformed: {path}")
        proofs[ref] = oid
    return proofs


def clear_release_tag_absence(
    repo_root: Path, workspace: ReleaseWorkspace, tag_refs: Mapping[str, str],
) -> None:
    """Invalidate absence proofs whenever CMRU makes another attempt for those refs."""
    if not tag_refs:
        return
    existing = read_confirmed_absent_release_tag_attempts(repo_root, workspace)
    changed = False
    for ref in tag_refs:
        changed = existing.pop(ref, None) is not None or changed
    if not changed:
        return
    path = _scope_dir(repo_root) / f"{_release_token(workspace)}.tag-absent.json"
    if not existing:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(12)}")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(sorted(existing.items())), indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate object keys instead of accepting the last sidecar value."""
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON object key: {key}")
        value[key] = item
    return value


def _valid_ls_remote_ref(ref: str, namespace: str) -> bool:
    """Apply Git's ref-name restrictions to a remote advertisement row."""
    if namespace == "refs/tags/" and ref.endswith("^{}"):
        name = ref[:-3]
    else:
        name = ref
    if not name.startswith(namespace) or name == namespace:
        return False
    if (
        name.startswith("/") or name.endswith(("/", "."))
        or "//" in name or ".." in name or "@{" in name
        or "\\" in name
        or any(ord(char) <= 32 or ord(char) == 127 or char in "~^:?*[" for char in name)
    ):
        return False
    return all(
        component and not component.startswith(".")
        and not component.endswith(".lock")
        for component in name.split("/")
    )


def parse_ls_remote_refs(
    output: str, *, namespace: str, description: str,
) -> dict[str, str]:
    """Parse successful ``git ls-remote`` output without folding bad rows into absence."""
    refs: dict[str, str] = {}
    for line in output.splitlines():
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) != 2:
            raise RuntimeError(f"{description} returned a malformed ref record: {line!r}")
        oid, ref = fields
        valid_ref = _valid_ls_remote_ref(ref, namespace)
        if not COMMIT_ID_RE.fullmatch(oid) or not valid_ref:
            raise RuntimeError(f"{description} returned a malformed ref record: {line!r}")
        if ref in refs:
            raise RuntimeError(f"{description} returned a duplicate ref record: {ref}")
        refs[ref] = oid
    return refs


def read_release_scope(repo_root: Path, workspace: ReleaseWorkspace) -> list[str] | None:
    """The recorded project scope for a retained worktree, or None if it predates
    this feature (an older retained worktree) — callers should treat None
    conservatively (not auto-abandon it)."""
    path = _scope_dir(repo_root) / f"{_release_token(workspace)}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_release_scope_for_workspace(
    repo_root: Path, workspace: ReleaseWorkspace,
) -> list[str] | None:
    """Read release scope using Git-family metadata, without inspecting the path.

    ``list_cmru_workspaces`` may return a literal path from another filesystem
    namespace. The scope sidecar lives under the shared Git directory, so its
    contents can be read from ``repo_root`` without statting that worktree path.
    Missing legacy metadata returns ``None``; malformed or unreadable metadata
    raises so callers cannot mistake uncertainty for absence.
    """
    if not _is_release_branch(workspace.branch):
        raise RuntimeError(
            f"{workspace.branch!r} is not a retained CMRU release branch"
        )
    metadata = _scope_dir(repo_root) / f"{_release_token(workspace)}.json"
    try:
        metadata.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError(f"cannot inspect recorded release scope {metadata}: {exc}") from exc
    try:
        scope = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError(f"cannot read recorded release scope {metadata}: {exc}") from exc
    if (
        not isinstance(scope, list)
        or not scope
        or any(not isinstance(name, str) or not name or name.strip() != name for name in scope)
        or len(scope) != len(set(scope))
    ):
        raise RuntimeError(f"recorded release scope is malformed: {metadata}")
    return scope


def read_release_scope_for_path(path: Path) -> list[str] | None:
    """Read the exact saved scope for a retained release worktree.

    Unlike :func:`read_release_scope`, this resolves the owning Git family from
    the candidate itself. It is used before project selection during resume,
    when the project scope is the fact needed to choose that Git family.
    Missing metadata is represented by ``None`` for legacy candidates; malformed
    or unreadable metadata is an error so it can never be mistaken for absence.
    """
    path = Path(path).expanduser().resolve()
    if not path.is_dir():
        raise RuntimeError(f"release worktree does not exist: {path}")
    shared = _shared_worktree()
    try:
        top, _common, branch, head = shared.discover_git_context(path)
    except Exception as exc:
        raise RuntimeError(f"{path} is not a readable Git worktree: {exc}") from exc
    top = Path(top).resolve()
    if top != path:
        raise RuntimeError(f"release resume path must name the worktree root: {path}")
    if not _is_release_branch(branch):
        raise RuntimeError(f"{path} is not a retained CMRU release branch (got {branch!r})")
    workspace = ReleaseWorkspace(repo_root=top, path=path, branch=branch, base=head)
    return read_release_scope_for_workspace(top, workspace)


def forget_release_scope(repo_root: Path, workspace: ReleaseWorkspace) -> None:
    """Remove this workspace's scope + progress-checkpoint marker files. Callers:
    abandon_workspace() (a discarded attempt) and a successful release (its scope
    marker is otherwise never cleaned up — see remove_workspace())."""
    (_scope_dir(repo_root) / f"{_release_token(workspace)}.json").unlink(missing_ok=True)
    (_scope_dir(repo_root) / f"{_release_token(workspace)}.tags.json").unlink(missing_ok=True)
    (_scope_dir(repo_root) / f"{_release_token(workspace)}.tag-attempts.json").unlink(missing_ok=True)
    (_scope_dir(repo_root) / f"{_release_token(workspace)}.tag-absent.json").unlink(missing_ok=True)
    _forget_release_progress(repo_root, workspace)
    (_scope_dir(repo_root) / f"{_release_token(workspace)}.results.json").unlink(missing_ok=True)
    _forget_plan_refused(repo_root, workspace)
    _forget_backup_pushed(repo_root, workspace)


def mark_backup_pushed(repo_root: Path, workspace: ReleaseWorkspace) -> None:
    """Record that :func:`push_backup_branch` actually pushed this exact
    transaction's durability backup to origin (KI-15).

    :func:`remove_backup_branch` checks :func:`backup_was_pushed` before
    attempting any delete, instead of special-casing every caller known
    (today) not to have pushed one -- a dry run, a "nothing to release" run,
    and a refused release plan (S12.2a/S12.2b) all reach a successful exit
    without ever calling :func:`push_backup_branch`, and an unconditional
    best-effort delete on those paths is exactly what made a genuinely
    successful, side-effect-free run print `error: unable to delete '...':
    remote ref does not exist` / `error: failed to push some refs to '...'`.
    Tracking this transaction's OWN observed push state -- rather than
    enumerating which call sites are "known safe" -- also guards the opposite,
    worse failure: a push that genuinely happened must still be reliably
    recognised as pushed, or its backup branch is orphaned on origin forever.
    """
    scope_dir = _ensure_scope_dir(repo_root)
    (scope_dir / f"{_release_token(workspace)}.backup-pushed").write_text("1", encoding="utf-8")


def backup_was_pushed(repo_root: Path, workspace: ReleaseWorkspace) -> bool:
    """True if :func:`mark_backup_pushed` was called for this exact workspace."""
    return (_scope_dir(repo_root) / f"{_release_token(workspace)}.backup-pushed").exists()


def mark_backup_removed(repo_root: Path, workspace: ReleaseWorkspace) -> None:
    """Record that explicit abandonment verified deletion of the origin candidate ref."""
    scope_dir = _ensure_scope_dir(repo_root)
    (scope_dir / f"{_release_token(workspace)}.backup-removed").write_text("1", encoding="utf-8")


def backup_was_removed(repo_root: Path, workspace: ReleaseWorkspace) -> bool:
    """True after abandonment has verified its remote candidate-ref deletion."""
    return (_scope_dir(repo_root) / f"{_release_token(workspace)}.backup-removed").exists()


def _forget_backup_pushed(repo_root: Path, workspace: ReleaseWorkspace) -> None:
    (_scope_dir(repo_root) / f"{_release_token(workspace)}.backup-pushed").unlink(missing_ok=True)
    (_scope_dir(repo_root) / f"{_release_token(workspace)}.backup-removed").unlink(missing_ok=True)


def mark_plan_refused(repo_root: Path, workspace: ReleaseWorkspace) -> None:
    """Record that this transaction's release-plan integrity check
    (S12.2a/S12.2b) refused before any project's cycle started. Nothing was
    gated, promoted, or tagged, so the worktree is safe to discard exactly
    like a success would be -- the parent process checks
    :func:`plan_was_refused` (its child exited non-zero, same as any other
    failure) to tell that apart from a genuine mid-release failure, which
    MUST be retained for inspection instead."""
    scope_dir = _ensure_scope_dir(repo_root)
    (scope_dir / f"{_release_token(workspace)}.plan-refused").write_text("1", encoding="utf-8")


def plan_was_refused(repo_root: Path, workspace: ReleaseWorkspace) -> bool:
    """True if :func:`mark_plan_refused` was called for this exact workspace."""
    return (_scope_dir(repo_root) / f"{_release_token(workspace)}.plan-refused").exists()


def clear_plan_refused(repo_root: Path, workspace: ReleaseWorkspace) -> None:
    """Clear the child-to-parent refusal marker before another transaction attempt."""
    _forget_plan_refused(repo_root, workspace)


def _forget_plan_refused(repo_root: Path, workspace: ReleaseWorkspace) -> None:
    (_scope_dir(repo_root) / f"{_release_token(workspace)}.plan-refused").unlink(missing_ok=True)


def write_release_result(
    repo_root: Path, workspace: ReleaseWorkspace, project: str, immutable_id: str,
) -> None:
    """Record a completed project’s immutable release coordinate for retention.

    Stored alongside the scope, outside a worktree, so the parent can safely move
    selected outputs only after the child reports whole-transaction success.
    """
    path = _scope_dir(repo_root) / f"{_release_token(workspace)}.results.json"
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot read release result record {path}") from exc
    if not isinstance(current, dict):
        raise RuntimeError(f"invalid release result record {path}")
    current[project] = immutable_id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_release_results(repo_root: Path, workspace: ReleaseWorkspace) -> dict[str, str]:
    """Return project → immutable tag/source coordinate recorded by the child."""
    path = _scope_dir(repo_root) / f"{_release_token(workspace)}.results.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot read release result record {path}") from exc
    if not isinstance(raw, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in raw.items()):
        raise RuntimeError(f"invalid release result record {path}")
    return raw


def _digest_tree(path: Path) -> list[dict[str, str]]:
    """Describe regular artifact files deterministically for retained release.json."""
    entries: list[dict[str, str]] = []
    for item in sorted(path.rglob("*")):
        if not item.is_file():
            continue
        digest = hashlib.sha256()
        with item.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        entries.append({
            "path": item.relative_to(path).as_posix(),
            "sha256": digest.hexdigest(),
            "bytes": str(item.stat().st_size),
        })
    return entries


def _safe_digest_tree(path: Path, *, exclude_root: frozenset[str] = frozenset()) -> list[dict[str, str]]:
    """Inventory regular files without following any symlink or special path."""
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"build output path is missing or unsafe: {path}")
    entries: list[dict[str, str]] = []
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise RuntimeError(f"build output contains a symlink: {item}")
        if item.is_dir():
            continue
        if not item.is_file():
            raise RuntimeError(f"build output contains a non-regular path: {item}")
        relative = item.relative_to(path).as_posix()
        if "/" not in relative and relative in exclude_root:
            continue
        entries.append({
            "path": relative,
            "sha256": _sha256_file(item),
            "bytes": str(item.stat().st_size),
        })
    return entries


def validate_build_output_tree(
    artifact_root: Path, project_name: str, output_id: str,
) -> dict[str, Any]:
    """Validate one retained artifact directory against its immutable build manifest.

    This shared validator is used by `cmru publish` and the built-in publisher
    adapters, so the path and digest rules have one implementation.
    """
    if not is_build_output_id(output_id):
        raise RuntimeError(f"invalid retained build output ID: {output_id!r}")
    if artifact_root.name != output_id or artifact_root.is_symlink() or not artifact_root.is_dir():
        raise RuntimeError(f"{project_name}: retained build output is missing or unsafe: {artifact_root}")
    manifest_path = artifact_root / "build.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError(f"{project_name}: retained build manifest is missing or unsafe: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{project_name}: invalid retained build manifest: {manifest_path}") from exc
    if not isinstance(manifest, dict) or (
        set(manifest) != {
            "schema_version", "kind", "publication", "project", "build_id",
            "source_commit", "source_commit_date", "source_tree_changes", "logs", "artifacts",
        }
        or manifest.get("schema_version") != 1
        or manifest.get("kind") != "cmru-local-build"
        or manifest.get("publication") not in {"eligible", "forbidden"}
        or manifest.get("project") != project_name
        or manifest.get("build_id") != output_id
    ):
        raise RuntimeError(
            f"{project_name}: retained build manifest does not authorize publication: {manifest_path}"
        )
    source_commit = manifest.get("source_commit")
    if (
        not isinstance(source_commit, str)
        or not re.fullmatch(r"[0-9a-f]{40}", source_commit)
        or not output_id.endswith("_" + source_commit)
    ):
        raise RuntimeError(f"{project_name}: build manifest source commit does not match {output_id}")
    raw_source_date = manifest.get("source_commit_date")
    if not isinstance(raw_source_date, str) or not raw_source_date:
        raise RuntimeError(f"{project_name}: build manifest has no source commit date")
    try:
        source_date = datetime.fromisoformat(raw_source_date)
    except ValueError as exc:
        raise RuntimeError(f"{project_name}: build manifest source commit date is invalid") from exc
    if (
        source_date.tzinfo is None
        or source_date.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        != output_id.split("_", 1)[0]
    ):
        raise RuntimeError(f"{project_name}: build manifest source date does not match {output_id}")
    if not isinstance(manifest.get("source_tree_changes"), list) or any(
        not isinstance(item, str) for item in manifest["source_tree_changes"]
    ):
        raise RuntimeError(f"{project_name}: build manifest source tree changes are invalid")
    if manifest["source_tree_changes"]:
        raise RuntimeError(
            f"{project_name}: retained build output has source tree changes; "
            "only a clean source tree can be published"
        )
    if manifest["publication"] != "eligible":
        raise RuntimeError(
            f"{project_name}: retained build manifest does not authorize publication: {manifest_path}"
        )

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise RuntimeError(f"{project_name}: build manifest has no publishable artifacts")
    expected: dict[str, tuple[str, int]] = {}
    artifact_dirs: list[Path] = []
    seen_dirs: set[str] = set()
    malformed_directory = f"{project_name}: malformed artifact directory in build manifest"
    unsafe_coordinate = f"{project_name}: unsafe file coordinate in build manifest"
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"directory", "files"}:
            raise RuntimeError(f"{project_name}: malformed artifact inventory in build manifest")
        directory = artifact["directory"]
        files = artifact["files"]
        if not isinstance(directory, str):
            raise RuntimeError(malformed_directory)
        if not directory:
            raise RuntimeError(malformed_directory)
        directory_path = PurePosixPath(directory)
        if "\\" in directory:
            raise RuntimeError(malformed_directory)
        if directory_path.is_absolute():
            raise RuntimeError(malformed_directory)
        if len(directory_path.parts) != 1:
            raise RuntimeError(malformed_directory)
        if directory_path.as_posix() != directory:
            raise RuntimeError(malformed_directory)
        if directory in {".", "..", "build.json"}:
            raise RuntimeError(malformed_directory)
        if directory in seen_dirs:
            raise RuntimeError(malformed_directory)
        if not isinstance(files, list):
            raise RuntimeError(malformed_directory)
        if not files:
            raise RuntimeError(malformed_directory)
        seen_dirs.add(directory)
        artifact_dir = artifact_root / directory
        if artifact_dir.is_symlink() or not artifact_dir.is_dir():
            raise RuntimeError(f"{project_name}: declared artifact directory is missing or unsafe: {artifact_dir}")
        artifact_dirs.append(artifact_dir)
        for entry in files:
            if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "bytes"}:
                raise RuntimeError(f"{project_name}: malformed file inventory in build manifest")
            relative = entry["path"]
            digest = entry["sha256"]
            byte_count = entry["bytes"]
            if not isinstance(relative, str):
                raise RuntimeError(unsafe_coordinate)
            if not relative:
                raise RuntimeError(unsafe_coordinate)
            relative_path = PurePosixPath(relative)
            if "\\" in relative:
                raise RuntimeError(unsafe_coordinate)
            if relative_path.is_absolute():
                raise RuntimeError(unsafe_coordinate)
            if not relative_path.parts:
                raise RuntimeError(unsafe_coordinate)
            if relative_path.as_posix() != relative:
                raise RuntimeError(unsafe_coordinate)
            if ".." in relative_path.parts:
                raise RuntimeError(unsafe_coordinate)
            if not isinstance(digest, str):
                raise RuntimeError(unsafe_coordinate)
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise RuntimeError(unsafe_coordinate)
            if not isinstance(byte_count, str):
                raise RuntimeError(unsafe_coordinate)
            if not byte_count.isdecimal():
                raise RuntimeError(unsafe_coordinate)
            if str(int(byte_count)) != byte_count:
                raise RuntimeError(unsafe_coordinate)
            coordinate = f"{directory}/{relative_path.as_posix()}"
            if coordinate in expected:
                raise RuntimeError(f"{project_name}: duplicate file coordinate in build manifest: {coordinate}")
            expected[coordinate] = (digest, int(byte_count))

    actual = _safe_digest_tree(artifact_root, exclude_root=frozenset({"build.json"}))
    actual_map = {
        item["path"]: (item["sha256"], int(item["bytes"]))
        for item in actual
    }
    if actual_map != expected:
        missing = sorted(set(expected) - set(actual_map))
        extra = sorted(set(actual_map) - set(expected))
        changed = sorted(
            path for path in set(actual_map) & set(expected)
            if actual_map[path] != expected[path]
        )
        raise RuntimeError(
            f"{project_name}: retained artifact bytes differ from build.json "
            f"(missing={missing}, extra={extra}, changed={changed})"
        )
    return {"manifest": manifest, "artifact_root": artifact_root, "artifact_dirs": artifact_dirs}


def validate_retained_build_output(
    project: object, project_name: str, output_id: str,
) -> dict[str, Any]:
    """Validate the complete project build record before any publisher runs."""
    raw_root = getattr(project, "project_root", None)
    if raw_root is None:
        raise RuntimeError(f"{project_name}: cannot resolve a retained build output without project_root")
    main_project_root = Path(raw_root)
    if main_project_root.is_symlink() or not main_project_root.is_dir():
        raise RuntimeError(f"{project_name}: project root is missing or unsafe: {main_project_root}")
    main_project_root = main_project_root.resolve()
    artifact_root = main_project_root / "artifacts" / output_id
    logs_root = main_project_root / "logs" / output_id
    _assert_no_symlink_components(main_project_root, artifact_root, project_name)
    _assert_no_symlink_components(main_project_root, logs_root, project_name)
    result = validate_build_output_tree(artifact_root, project_name, output_id)
    if logs_root.is_symlink() or not logs_root.is_dir():
        raise RuntimeError(f"{project_name}: retained build logs are missing or unsafe: {logs_root}")
    recorded_logs = result["manifest"].get("logs")
    actual_logs = _safe_digest_tree(logs_root)
    if not isinstance(recorded_logs, list) or actual_logs != recorded_logs:
        raise RuntimeError(f"{project_name}: retained build logs differ from build.json: {logs_root}")
    declared = tuple(getattr(project, "artifact_dirs", ()) or ())
    expected_dirs = {Path(item).name for item in declared}
    actual_dirs = {path.name for path in result["artifact_dirs"]}
    if not declared or expected_dirs != actual_dirs:
        raise RuntimeError(
            f"{project_name}: current artifact_dirs do not match retained build output "
            f"(declared={sorted(expected_dirs)}, retained={sorted(actual_dirs)})"
        )
    return {**result, "logs_root": logs_root}


_BUILD_OUTPUT_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z_[0-9a-f]{40}$")


def is_build_output_id(value: str) -> bool:
    """Return whether a value is the exact public ID format emitted by build."""
    if not isinstance(value, str) or _BUILD_OUTPUT_ID_RE.fullmatch(value) is None:
        return False
    try:
        datetime.strptime(value.split("_", 1)[0], "%Y%m%dT%H%M%SZ")
    except ValueError:
        return False
    return True


def _require_build_output_id(output_id: str) -> None:
    if not is_build_output_id(output_id):
        raise RuntimeError(
            "--delete-build-output must be the exact <commit-date>_<40-hex-commit> "
            "coordinate printed by cmru build"
        )


def build_output_id(workspace: ReleaseWorkspace) -> tuple[str, str, str]:
    """Return the immutable local-output coordinate for the built source tree.

    The coordinate is deliberately source-derived rather than wall-clock-derived:
    rebuilding an unchanged commit addresses the same local evidence record and
    therefore refuses to overwrite it.  ``cmru cleanup --delete-build-output``
    is the explicit way to discard that record before another build of the same
    source snapshot.
    """
    source_commit = _git(workspace.path, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise RuntimeError(f"invalid build source commit: {source_commit!r}")
    raw_epoch = _git(workspace.path, "show", "-s", "--format=%ct", "HEAD")
    if not raw_epoch.isdecimal():
        raise RuntimeError(f"invalid build source commit timestamp: {raw_epoch!r}")
    source_date = _git(workspace.path, "show", "-s", "--format=%cI", "HEAD")
    output_id = (
        datetime.fromtimestamp(int(raw_epoch), timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + f"_{source_commit}"
    )
    return output_id, source_commit, source_date


def _project_roots_for_retention(
    repo_root: Path, workspace: ReleaseWorkspace, project: object, name: str,
) -> tuple[Path, Path]:
    """Resolve one project in the caller checkout and its transaction copy."""
    project_root = getattr(project, "project_root", None)
    if project_root is None:
        raise RuntimeError(f"{name}: cannot retain outputs without project_root")
    main_project_root = Path(project_root).resolve()
    try:
        relative = main_project_root.relative_to(workspace.repo_root.resolve())
    except ValueError as exc:
        raise RuntimeError(
            f"{name}: project_root is outside selected Git workspace "
            f"{workspace.repo_root}: {main_project_root}"
        ) from exc
    return main_project_root, workspace.path / relative


def _declared_evidence_path(name: str, raw_path: object) -> Path:
    """Validate one runtime evidence declaration and return its safe path."""
    if not isinstance(raw_path, str) or not raw_path:
        raise RuntimeError(f"{name}: evidence_paths must contain non-empty strings")
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts or relative == Path("."):
        raise RuntimeError(
            f"{name}: evidence path must be project-relative and may not contain '..' or '.'"
        )
    return relative


def _assert_no_symlink_components(
    root: Path, path: Path, name: str, *, subject: str = "evidence",
) -> None:
    """Refuse a path that reaches its source or destination through a symlink."""
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"{name}: {subject} path escaped its project root: {path}") from exc
    if root.is_symlink():
        raise RuntimeError(f"{name}: {subject} project root is a symlink: {root}")
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise RuntimeError(f"{name}: {subject} path is or crosses a symlink: {current}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _evidence_hashes(path: Path, coordinate: Path, name: str) -> list[dict[str, str]]:
    """Validate a retained evidence path and return hashes relative to its record."""
    if path.is_symlink():
        raise RuntimeError(f"{name}: declared evidence path is a symlink: {path}")
    if path.is_file():
        return [{
            "path": coordinate.as_posix(),
            "sha256": _sha256_file(path),
            "bytes": str(path.stat().st_size),
        }]
    if not path.is_dir():
        raise RuntimeError(f"{name}: declared evidence path is missing or not a file/directory: {path}")

    entries: list[dict[str, str]] = []
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise RuntimeError(f"{name}: declared evidence contains a symlink: {item}")
        if item.is_dir():
            continue
        if not item.is_file():
            raise RuntimeError(f"{name}: declared evidence contains a non-regular path: {item}")
        entries.append({
            "path": (coordinate / item.relative_to(path)).as_posix(),
            "sha256": _sha256_file(item),
            "bytes": str(item.stat().st_size),
        })
    return entries


def retain_successful_build_outputs(
    repo_root: Path,
    workspace: ReleaseWorkspace,
    projects: Mapping[str, object],
    project_names: Sequence[str],
) -> list[Path]:
    """Copy a successful non-release build's evidence into the caller checkout.

    A normal ``cmru build`` is useful precisely because its artifacts can be
    consumed locally.  After a successful child build this function copies the
    declared artifact directories and project-local logs into immutable,
    commit-addressed records, then lets the caller remove the isolated worktree.
    A record can feed explicit artifact-only publication only when the recorded
    source tree is clean and its complete digest inventory revalidates; it is
    not a source release candidate. The manifest records tracked and untracked
    source changes left by ``prepare`` or later build steps.

    Every destination is first staged and validated.  Existing coordinates are a
    hard error; overwriting an earlier build would make its provenance mutable.
    Sources remain in the worktree until all copying succeeds, so a retention
    error leaves the worktree intact for diagnosis.
    """
    output_id, source_commit, source_date = build_output_id(workspace)
    retained: list[Path] = []
    source_changes = _git(
        workspace.path, "status", "--porcelain=v1", "--untracked-files=all",
    ).splitlines()

    for name in project_names:
        project = projects.get(name)
        if project is None:
            raise RuntimeError(f"build result names unknown project: {name}")
        artifact_dirs = tuple(getattr(project, "artifact_dirs", ()) or ())
        if not artifact_dirs:
            raise RuntimeError(
                f"{name}: cmru build requires project.release.artifact_dirs so it can retain "
                "the successful local output"
            )
        main_project_root, child_project_root = _project_roots_for_retention(
            repo_root, workspace, project, name,
        )
        source_logs = child_project_root / "logs"
        if not source_logs.is_dir() or source_logs.is_symlink():
            raise RuntimeError(f"{name}: build logs are missing or unsafe: {source_logs}")

        source_artifacts: list[tuple[str, Path]] = []
        for raw_dir in artifact_dirs:
            source = child_project_root / raw_dir
            if not source.is_dir() or source.is_symlink():
                raise RuntimeError(f"{name}: declared artifact directory is missing or unsafe: {source}")
            source_artifacts.append((raw_dir, source))

        target_logs = main_project_root / "logs" / output_id
        target_artifacts = main_project_root / "artifacts" / output_id
        if target_logs.exists() or target_artifacts.exists():
            raise RuntimeError(
                f"{name}: build output {output_id} already exists; inspect it or remove it with "
                f"cmru cleanup {name} --delete-build-output {output_id} --yes"
            )

        stage = Path(tempfile.mkdtemp(prefix=".cmru-build-retain-", dir=main_project_root))
        staged_logs = stage / "logs"
        staged_artifacts = stage / "artifacts"
        moved_logs = False
        try:
            shutil.copytree(source_logs, staged_logs, symlinks=True)
            staged_artifacts.mkdir()
            copied_artifacts: list[dict[str, object]] = []
            for raw_dir, source in source_artifacts:
                target = staged_artifacts / Path(raw_dir).name
                if target.exists():
                    raise RuntimeError(f"{name}: artifact directory name collision: {target.name}")
                shutil.copytree(source, target, symlinks=True)
                files = _safe_digest_tree(target)
                if not files:
                    raise RuntimeError(f"{name}: declared artifact directory is empty: {source}")
                copied_artifacts.append({"directory": target.name, "files": files})

            manifest = {
                "schema_version": 1,
                "kind": "cmru-local-build",
                "publication": "eligible" if not source_changes else "forbidden",
                "project": name,
                "build_id": output_id,
                "source_commit": source_commit,
                "source_commit_date": source_date,
                "source_tree_changes": source_changes,
                "logs": _safe_digest_tree(staged_logs),
                "artifacts": copied_artifacts,
            }
            (staged_artifacts / "build.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
            )

            # Recheck immediately before the irreversible rename; another shell
            # must not turn an immutable output record into an overwrite.
            if target_logs.exists() or target_artifacts.exists():
                raise RuntimeError(f"{name}: build output destination appeared during retention: {output_id}")
            target_logs.parent.mkdir(parents=True, exist_ok=True)
            target_artifacts.parent.mkdir(parents=True, exist_ok=True)
            staged_logs.replace(target_logs)
            moved_logs = True
            try:
                staged_artifacts.replace(target_artifacts)
            except Exception:
                # The sources are still safe in the retained worktree.  Restore
                # the first rename so the caller checkout remains all-or-nothing.
                target_logs.replace(staged_logs)
                moved_logs = False
                raise
            retained.extend((target_logs, target_artifacts))
        finally:
            if moved_logs:
                # A later unexpected exception must not leave a partial record.
                if target_logs.exists() and not target_artifacts.exists():
                    target_logs.replace(staged_logs)
                    retained[:] = [
                        path for path in retained
                        if path not in {target_logs, target_artifacts}
                    ]
                    shutil.rmtree(stage, ignore_errors=True)
                    raise RuntimeError(
                        f"{name}: retained artifact destination disappeared during retention"
                    )
            shutil.rmtree(stage, ignore_errors=True)
    return retained


@dataclass(frozen=True)
class RetainedBuildOutputIdentity:
    """Filesystem identity captured by a cleanup preview for one build output."""

    project_name: str
    output_id: str
    artifact_root: Path
    artifact_root_stat: tuple[int, int, int, int, int, int]
    artifact_tree_stat: tuple[tuple[str, tuple[int, int, int, int, int, int]], ...]
    manifest_stat: tuple[int, int, int, int, int, int]
    manifest_sha256: str
    logs_root: Path
    logs_root_stat: tuple[int, int, int, int, int, int]
    logs_tree_stat: tuple[tuple[str, tuple[int, int, int, int, int, int]], ...]


def _filesystem_stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns,
    )


def _directory_open_flags() -> int:
    required = ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")
    if any(not hasattr(os, name) for name in required):
        raise RuntimeError("safe descriptor-relative build-output cleanup is unavailable")
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _open_directory_path_nofollow(path: Path) -> int:
    """Open an absolute directory one component at a time without following symlinks."""
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"cannot safely open non-canonical directory path: {path}")
    flags = _directory_open_flags()
    current_fd = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


@contextmanager
def _retained_build_output_parent_fds(
    repo_root: Path, project: object, project_name: str, output_id: str,
) -> Iterator[tuple[Path, int, int]]:
    """Hold no-follow descriptors for the project and both cleanup parents."""
    _require_build_output_id(output_id)
    main_project_root, _child_project_root = _project_roots_for_retention(
        repo_root,
        ReleaseWorkspace(repo_root=repo_root, path=repo_root, branch="", base=""),
        project,
        project_name,
    )
    artifact_root = main_project_root / "artifacts" / output_id
    logs_root = main_project_root / "logs" / output_id
    _assert_no_symlink_components(
        main_project_root, artifact_root, project_name, subject="retained build output",
    )
    _assert_no_symlink_components(
        main_project_root, logs_root, project_name, subject="retained build output",
    )
    root_fd = artifact_parent_fd = logs_parent_fd = None
    try:
        root_fd = _open_directory_path_nofollow(main_project_root)
        flags = _directory_open_flags()
        artifact_parent_fd = os.open("artifacts", flags, dir_fd=root_fd)
        logs_parent_fd = os.open("logs", flags, dir_fd=root_fd)
    except OSError as exc:
        for descriptor in (logs_parent_fd, artifact_parent_fd, root_fd):
            if descriptor is not None:
                os.close(descriptor)
        if exc.errno == errno.ELOOP:
            raise RuntimeError(
                f"{project_name}: retained build output path is or crosses a symlink"
            ) from exc
        raise RuntimeError(
            f"{project_name}: retained build record is incomplete or unsafe for {output_id}; "
            "remove it manually after inspection"
        ) from exc
    except BaseException:
        for descriptor in (logs_parent_fd, artifact_parent_fd, root_fd):
            if descriptor is not None:
                os.close(descriptor)
        raise
    assert artifact_parent_fd is not None and logs_parent_fd is not None
    assert root_fd is not None
    try:
        yield main_project_root, artifact_parent_fd, logs_parent_fd
    finally:
        os.close(logs_parent_fd)
        os.close(artifact_parent_fd)
        os.close(root_fd)


def _filesystem_tree_identity_fd(
    root_fd: int,
) -> tuple[tuple[str, tuple[int, int, int, int, int, int]], ...]:
    """Snapshot a tree through directory descriptors without following symlinks."""
    entries: list[tuple[str, tuple[int, int, int, int, int, int]]] = []
    pending: list[tuple[str, tuple[int, int, int, int, int, int]]] = [
        ("", _filesystem_stat_identity(os.fstat(root_fd))),
    ]
    flags = _directory_open_flags()
    while pending:
        prefix, expected_identity = pending.pop()
        parent_fd = os.dup(root_fd)
        try:
            if prefix:
                for part in Path(prefix).parts:
                    child_fd = os.open(part, flags, dir_fd=parent_fd)
                    os.close(parent_fd)
                    parent_fd = child_fd
            if _filesystem_stat_identity(os.fstat(parent_fd)) != expected_identity:
                raise RuntimeError("retained build output changed during inspection")
            for name in os.listdir(parent_fd):
                info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                relative = f"{prefix}/{name}" if prefix else name
                identity = _filesystem_stat_identity(info)
                entries.append((relative, identity))
                if stat.S_ISDIR(info.st_mode):
                    pending.append((relative, identity))
        finally:
            os.close(parent_fd)
    return tuple(sorted(entries))


def _read_regular_file_at(
    parent_fd: int, name: str, project_name: str, path: Path,
) -> tuple[bytes, tuple[int, int, int, int, int, int]]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open(name, flags, dir_fd=parent_fd)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(
                f"{project_name}: retained build record is incomplete or unsafe: {path}"
            )
        chunks = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if _filesystem_stat_identity(before) != _filesystem_stat_identity(after):
            raise RuntimeError(f"{project_name}: retained build manifest changed during inspection")
        return b"".join(chunks), _filesystem_stat_identity(after)
    finally:
        os.close(descriptor)


def _retained_build_output_cleanup_facts(
    project_name: str,
    output_id: str,
    main_project_root: Path,
    artifact_parent_fd: int,
    logs_parent_fd: int,
) -> tuple[list[Path], RetainedBuildOutputIdentity]:
    _require_build_output_id(output_id)
    artifact_root = main_project_root / "artifacts" / output_id
    logs_root = main_project_root / "logs" / output_id
    artifact_fd = logs_fd = None
    open_flags = _directory_open_flags()
    try:
        artifact_fd = os.open(output_id, open_flags, dir_fd=artifact_parent_fd)
        logs_fd = os.open(output_id, open_flags, dir_fd=logs_parent_fd)
    except OSError as exc:
        for descriptor in (logs_fd, artifact_fd):
            if descriptor is not None:
                os.close(descriptor)
        if exc.errno == errno.ELOOP:
            raise RuntimeError(
                f"{project_name}: retained build output path is or crosses a symlink"
            ) from exc
        raise RuntimeError(
            f"{project_name}: retained build record is incomplete or unsafe for {output_id}; "
            "remove it manually after inspection"
        ) from exc
    except BaseException:
        for descriptor in (logs_fd, artifact_fd):
            if descriptor is not None:
                os.close(descriptor)
        raise
    assert artifact_fd is not None and logs_fd is not None
    try:
        identity = _retained_build_output_identity_from_fds(
            project_name, output_id, main_project_root, artifact_fd, logs_fd,
        )
        return [logs_root, artifact_root], identity
    finally:
        os.close(logs_fd)
        os.close(artifact_fd)


def _retained_build_output_identity_from_fds(
    project_name: str,
    output_id: str,
    main_project_root: Path,
    artifact_fd: int,
    logs_fd: int,
) -> RetainedBuildOutputIdentity:
    _require_build_output_id(output_id)
    artifact_root = main_project_root / "artifacts" / output_id
    logs_root = main_project_root / "logs" / output_id
    manifest_path = artifact_root / "build.json"
    try:
        manifest_bytes, manifest_stat = _read_regular_file_at(
            artifact_fd, "build.json", project_name, manifest_path,
        )
    except OSError as exc:
        raise RuntimeError(
            f"{project_name}: retained build record is incomplete or unsafe for {output_id}; "
            "remove it manually after inspection"
        ) from exc
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError(
            f"{project_name}: invalid retained build manifest: {manifest_path}"
        ) from exc
    if not isinstance(manifest, dict) or (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "cmru-local-build"
        or manifest.get("publication") not in {"forbidden", "eligible"}
        or manifest.get("project") != project_name
        or manifest.get("build_id") != output_id
    ):
        raise RuntimeError(
            f"{project_name}: retained build manifest does not authorize cleanup: {manifest_path}"
        )

    artifact_root_stat = _filesystem_stat_identity(os.fstat(artifact_fd))
    logs_root_stat = _filesystem_stat_identity(os.fstat(logs_fd))
    artifact_tree_stat = _filesystem_tree_identity_fd(artifact_fd)
    logs_tree_stat = _filesystem_tree_identity_fd(logs_fd)
    if (
        _filesystem_stat_identity(os.fstat(artifact_fd)) != artifact_root_stat
        or _filesystem_stat_identity(os.fstat(logs_fd)) != logs_root_stat
    ):
        raise RuntimeError("retained build output changed during inspection")
    return RetainedBuildOutputIdentity(
        project_name=project_name,
        output_id=output_id,
        artifact_root=artifact_root,
        artifact_root_stat=artifact_root_stat,
        artifact_tree_stat=artifact_tree_stat,
        manifest_stat=manifest_stat,
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        logs_root=logs_root,
        logs_root_stat=logs_root_stat,
        logs_tree_stat=logs_tree_stat,
    )


def retained_build_output_identity(
    repo_root: Path, project: object, project_name: str, output_id: str,
) -> RetainedBuildOutputIdentity:
    """Validate and capture the exact local record selected for cleanup."""
    with _retained_build_output_parent_fds(
        repo_root, project, project_name, output_id,
    ) as (main_project_root, artifact_parent_fd, logs_parent_fd):
        return _retained_build_output_cleanup_facts(
            project_name, output_id, main_project_root,
            artifact_parent_fd, logs_parent_fd,
        )[1]


def _create_private_cleanup_stage(parent_fd: int, output_id: str) -> tuple[str, int]:
    flags = _directory_open_flags()
    for _attempt in range(8):
        stage_name = f".cmru-cleanup-{output_id}-{secrets.token_hex(12)}"
        try:
            os.mkdir(stage_name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        try:
            return stage_name, os.open(stage_name, flags, dir_fd=parent_fd)
        except BaseException:
            os.rmdir(stage_name, dir_fd=parent_fd)
            raise
    raise RuntimeError("could not allocate a private build-output cleanup directory")


def _restore_cleanup_stage_record(
    parent_fd: int, stage_fd: int, output_id: str,
) -> bool:
    """Restore a staged record only when its original name remains vacant."""
    try:
        os.stat("record", dir_fd=stage_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    try:
        os.stat(output_id, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return _rename_noreplace_at(
            "record", stage_fd, output_id, parent_fd,
        )
    return False


def _rename_noreplace_at(
    source: str, source_fd: int, destination: str, destination_fd: int,
) -> bool:
    """Use Linux renameat2(RENAME_NOREPLACE); refuse a racy fallback."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError):
        return False
    renameat2.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    rename_noreplace = 1  # RENAME_NOREPLACE from <linux/fs.h>.
    result = renameat2(
        source_fd, os.fsencode(source), destination_fd, os.fsencode(destination),
        rename_noreplace,
    )
    if result == 0:
        return True
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
        return False
    raise OSError(error_number, os.strerror(error_number), destination)


def _staged_cleanup_identity_matches(
    expected: RetainedBuildOutputIdentity,
    staged: RetainedBuildOutputIdentity,
) -> bool:
    """Compare a moved record while allowing rename to update root timestamps."""
    # Moving a directory into its private staging folder may update the root's
    # timestamps. The pre-move identity was already checked; this comparison
    # still binds the inode and every entry to that same reviewed record.
    return (
        expected.project_name == staged.project_name
        and expected.output_id == staged.output_id
        and expected.artifact_root == staged.artifact_root
        and expected.artifact_root_stat[:4] == staged.artifact_root_stat[:4]
        and expected.artifact_tree_stat == staged.artifact_tree_stat
        and expected.manifest_stat == staged.manifest_stat
        and expected.manifest_sha256 == staged.manifest_sha256
        and expected.logs_root == staged.logs_root
        and expected.logs_root_stat[:4] == staged.logs_root_stat[:4]
        and expected.logs_tree_stat == staged.logs_tree_stat
    )


def delete_retained_build_output(
    repo_root: Path,
    project: object,
    project_name: str,
    output_id: str,
    *,
    dry_run: bool,
    expected_identity: RetainedBuildOutputIdentity | None = None,
) -> list[Path]:
    """Delete one verified local build record, never a glob or age range."""
    with _retained_build_output_parent_fds(
        repo_root, project, project_name, output_id,
    ) as (main_project_root, artifact_parent_fd, logs_parent_fd):
        targets, current_identity = _retained_build_output_cleanup_facts(
            project_name, output_id, main_project_root,
            artifact_parent_fd, logs_parent_fd,
        )
        if expected_identity is not None and current_identity != expected_identity:
            raise RuntimeError(
                f"{project_name}: retained build record changed after cleanup preview for "
                f"{output_id}; inspect it and retry"
            )
        if dry_run:
            return targets
        if not shutil.rmtree.avoids_symlink_attacks:
            raise RuntimeError("safe descriptor-relative build-output cleanup is unavailable")

        # Move both entries under private, descriptor-pinned names. Recheck the
        # moved records before deleting so a last-moment replacement at the
        # public ID cannot be mistaken for the previewed output.
        stages: list[tuple[int, str, int]] = []
        moved: list[tuple[int, str, int]] = []
        try:
            logs_stage_name, logs_stage_fd = _create_private_cleanup_stage(
                logs_parent_fd, output_id,
            )
            stages.append((logs_parent_fd, logs_stage_name, logs_stage_fd))
            artifacts_stage_name, artifacts_stage_fd = _create_private_cleanup_stage(
                artifact_parent_fd, output_id,
            )
            stages.append((artifact_parent_fd, artifacts_stage_name, artifacts_stage_fd))

            os.rename(
                output_id, "record",
                src_dir_fd=logs_parent_fd, dst_dir_fd=logs_stage_fd,
            )
            moved.append((logs_parent_fd, logs_stage_name, logs_stage_fd))
            os.rename(
                output_id, "record",
                src_dir_fd=artifact_parent_fd, dst_dir_fd=artifacts_stage_fd,
            )
            moved.append((artifact_parent_fd, artifacts_stage_name, artifacts_stage_fd))

            open_flags = _directory_open_flags()
            staged_artifact_fd = os.open("record", open_flags, dir_fd=artifacts_stage_fd)
            try:
                staged_logs_fd = os.open("record", open_flags, dir_fd=logs_stage_fd)
                try:
                    staged_identity = _retained_build_output_identity_from_fds(
                        project_name, output_id, main_project_root,
                        staged_artifact_fd, staged_logs_fd,
                    )
                finally:
                    os.close(staged_logs_fd)
            finally:
                os.close(staged_artifact_fd)
            if not _staged_cleanup_identity_matches(current_identity, staged_identity):
                raise RuntimeError(
                    f"{project_name}: retained build record changed during cleanup for "
                    f"{output_id}; inspect it and retry"
                )

            # These names are private to directories created with mode 0700,
            # and the validated parent descriptors remain open until deletion.
            shutil.rmtree("record", dir_fd=logs_stage_fd)
            shutil.rmtree("record", dir_fd=artifacts_stage_fd)
            for parent_fd, stage_name, _stage_fd in stages:
                os.rmdir(stage_name, dir_fd=parent_fd)
            return targets
        except BaseException as exc:
            restore_failures: list[Path] = []
            for parent_fd, stage_name, stage_fd in reversed(moved):
                try:
                    restored = _restore_cleanup_stage_record(
                        parent_fd, stage_fd, output_id,
                    )
                except OSError:
                    restored = False
                if not restored:
                    restore_failures.append(
                        main_project_root / (
                            "logs" if parent_fd == logs_parent_fd else "artifacts"
                        ) / stage_name / "record"
                    )
            if restore_failures:
                raise RuntimeError(
                    f"{project_name}: cleanup stopped and retained records need inspection at "
                    + ", ".join(map(str, restore_failures))
                ) from exc
            for parent_fd, stage_name, _stage_fd in stages:
                try:
                    os.rmdir(stage_name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
            raise
        finally:
            for _parent_fd, _stage_name, stage_fd in reversed(stages):
                os.close(stage_fd)


def discard_build_workspace(
    repo_root: Path,
    path: Path,
    *,
    dry_run: bool,
    expected_workspace: ReleaseWorkspace | None = None,
) -> ReleaseWorkspace:
    """Discard one inspected failed build worktree (flat ``cmru-build-*`` or
    legacy nested ``cmru/build/*``) by exact path."""
    path = path.resolve()
    expected_parent = (repo_root / ".worktrees").resolve()
    if path.parent != expected_parent:
        raise RuntimeError(f"{path} is outside this repository's managed .worktrees directory")
    if not path.is_dir() or _common_git_dir(path) != _common_git_dir(repo_root):
        raise RuntimeError(f"{path} is not a worktree of {repo_root}")
    branch = _git(path, "branch", "--show-current")
    if not _is_build_branch(branch):
        raise RuntimeError(f"{path} is not a retained cmru build worktree (got {branch!r})")
    context = None
    shared = _shared_worktree()
    try:
        _top, common, _branch, _head = shared.discover_git_context(path)
        record = _shared_workspace_record(shared, common, path)
        if record is not None:
            _require_cmru_record_purpose(record, "build", path)
            context = shared.ensure_workspace(record)
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc
    workspace = ReleaseWorkspace(
        repo_root=repo_root,
        path=path,
        branch=branch,
        base=_git(path, "rev-parse", "HEAD"),
        context=context,
    )
    if expected_workspace is not None:
        same_git_identity = (
            workspace.path.resolve() == expected_workspace.path.resolve()
            and workspace.branch == expected_workspace.branch
            and workspace.base == expected_workspace.base
        )
        if expected_workspace.context is None:
            same_recorded_identity = workspace.context is None
        else:
            same_recorded_identity = (
                workspace.context is not None
                and workspace.workspace_id == expected_workspace.workspace_id
            )
        if not same_git_identity or not same_recorded_identity:
            raise RuntimeError(
                "retained build worktree identity changed after cleanup preview; "
                "inspect it and request a new preview"
            )
    if not dry_run:
        remove_workspace(workspace)
    return workspace


def retain_success_outputs(
    repo_root: Path,
    workspace: ReleaseWorkspace,
    projects: dict[str, object],
    results: dict[str, str],
    *,
    retain_logs: bool,
    retain_artifacts: bool,
    retain_evidence: bool = True,
) -> list[Path]:
    """Move selected completed-release evidence out before deleting the worktree.

    Destinations are project-local and immutable by coordinate.  Existing targets
    are an error: overwriting a retained record would turn provenance into a
    mutable cache.  Artifact retention is best-effort per project: a project that
    declares no ``project.release.artifact_dirs`` simply has nothing to retain and
    is skipped without error (retention is now the release default, applied across
    every orchestrated project, not all of which build a local artifact). A
    project that DOES declare ``artifact_dirs`` and then fails to produce one is a
    real build defect and still raises. Declared gate evidence follows the same
    all-or-nothing transaction, but lives under a separate ``evidence`` coordinate
    and has its own integrity manifest; it is never treated as a publishable artifact.
    """
    retained: list[Path] = []
    for name, immutable_id in results.items():
        project = projects.get(name)
        if project is None:
            raise RuntimeError(f"release result names unknown project: {name}")
        project_root = getattr(project, "project_root", None)
        if project_root is None:
            raise RuntimeError(f"{name}: cannot retain outputs without project_root")
        project_root = Path(project_root).resolve()
        relative = project_root.relative_to(workspace.repo_root.resolve())
        child_root = workspace.path / relative
        source_logs = child_root / "logs"
        target_logs = project_root / "logs" / "cmru-release" / immutable_id
        if retain_logs and source_logs.exists() and target_logs.exists():
            raise RuntimeError(f"{name}: retained log destination already exists: {target_logs}")

        artifact_dirs: tuple[str, ...] = ()
        target_root = project_root / "artifacts" / immutable_id
        artifact_sources: list[tuple[str, Path, Path]] = []
        if retain_artifacts:
            artifact_dirs = tuple(getattr(project, "artifact_dirs", ()) or ())
        # Retention is the release default now, applied uniformly across every
        # orchestrated project -- a project that declares no artifact_dirs has
        # nothing to retain and is silently skipped, not an error (unlike a
        # declared directory that fails to actually appear, below).
        attempt_artifacts = retain_artifacts and bool(artifact_dirs)
        if attempt_artifacts:
            if target_root.exists():
                raise RuntimeError(f"{name}: retained artifact destination already exists: {target_root}")
            seen_names: set[str] = set()
            for raw_dir in artifact_dirs:
                source = child_root / raw_dir
                if not source.is_dir():
                    raise RuntimeError(f"{name}: declared artifact directory is missing: {source}")
                target_name = Path(raw_dir).name
                if target_name in seen_names:
                    raise RuntimeError(f"{name}: artifact directory name collision: {target_name}")
                seen_names.add(target_name)
                target = target_root / target_name
                if target.exists():
                    raise RuntimeError(f"{name}: artifact directory destination already exists: {target}")
                artifact_sources.append((target_name, source, target))

        evidence_paths = tuple(getattr(project, "evidence_paths", ()) or ())
        evidence_root = project_root / "evidence" / "cmru-release" / immutable_id
        evidence_sources: list[tuple[str, Path, Path, str]] = []
        attempt_evidence = retain_evidence and bool(evidence_paths)
        if attempt_evidence:
            _assert_no_symlink_components(project_root, evidence_root, name)
            if evidence_root.exists() or evidence_root.is_symlink():
                raise RuntimeError(f"{name}: retained evidence destination already exists: {evidence_root}")
            seen_paths: list[tuple[Path, str]] = []
            for raw_path in evidence_paths:
                relative = _declared_evidence_path(name, raw_path)
                if relative == Path("evidence.json"):
                    raise RuntimeError(
                        f"{name}: evidence path collides with the retention manifest: {raw_path}"
                    )
                for previous, previous_raw in seen_paths:
                    if relative == previous or previous in relative.parents or relative in previous.parents:
                        raise RuntimeError(
                            f"{name}: evidence path collision/overlap: {previous_raw} and {raw_path}"
                        )
                seen_paths.append((relative, raw_path))
                source = child_root / relative
                _assert_no_symlink_components(child_root, source, name)
                if source.is_symlink():
                    raise RuntimeError(f"{name}: declared evidence path is a symlink: {source}")
                if not source.exists():
                    raise RuntimeError(f"{name}: declared evidence path is missing: {source}")
                kind = "file" if source.is_file() else "directory" if source.is_dir() else "other"
                if kind == "other":
                    raise RuntimeError(
                        f"{name}: declared evidence path is not a file/directory: {source}"
                    )
                _evidence_hashes(source, relative, name)
                target = evidence_root / relative
                _assert_no_symlink_components(project_root, target, name)
                if target.exists() or target.is_symlink():
                    raise RuntimeError(f"{name}: evidence destination already exists: {target}")
                evidence_sources.append((relative.as_posix(), source, target, kind))

        log_parent = target_logs.parent
        log_parent_existed = log_parent.exists()
        artifact_parent = target_root.parent
        artifact_parent_existed = artifact_parent.exists()
        evidence_parent = evidence_root.parent
        evidence_parent_existed = evidence_parent.exists()
        moved_sources: list[tuple[Path, Path]] = []
        created_target_root = False
        created_evidence_root = False
        moved_logs = False
        try:
            # All destination creation is deliberately before the first move.
            if retain_logs and source_logs.exists():
                log_parent.mkdir(parents=True, exist_ok=True)
            if attempt_artifacts:
                artifact_parent.mkdir(parents=True, exist_ok=True)
                target_root.mkdir()
                created_target_root = True
            if attempt_evidence:
                evidence_root.parent.mkdir(parents=True, exist_ok=True)
                evidence_root.mkdir()
                created_evidence_root = True
                for _raw_path, _source, target, _kind in evidence_sources:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _assert_no_symlink_components(project_root, target, name)

            if retain_logs and source_logs.exists():
                shutil.move(str(source_logs), str(target_logs))
                moved_sources.append((source_logs, target_logs))
                moved_logs = True
            moved: list[dict[str, object]] = []
            if attempt_artifacts:
                for target_name, source, target in artifact_sources:
                    shutil.move(str(source), str(target))
                    moved_sources.append((source, target))
                    moved.append({"directory": target_name, "files": _digest_tree(target)})
                manifest = {
                    "schema_version": 1,
                    "project": name,
                    "immutable_id": immutable_id,
                    "source_commit": _git(workspace.path, "rev-parse", "HEAD"),
                    "artifacts": moved,
                }
                (target_root / "release.json").write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
            if attempt_evidence:
                for _raw_path, source, target, _kind in evidence_sources:
                    shutil.move(str(source), str(target))
                    moved_sources.append((source, target))
                evidence_manifest = {
                    "schema_version": 1,
                    "kind": "cmru-release-evidence",
                    "project": name,
                    "immutable_id": immutable_id,
                    "source_commit": _git(workspace.path, "rev-parse", "HEAD"),
                    "paths": [
                        {
                            "path": raw_path,
                            "type": kind,
                            "files": _evidence_hashes(target, Path(raw_path), name),
                        }
                        for raw_path, _source, target, kind in evidence_sources
                    ],
                }
                (evidence_root / "evidence.json").write_text(
                    json.dumps(evidence_manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            if moved_logs:
                retained.append(target_logs)
            if attempt_artifacts:
                retained.append(target_root)
            if attempt_evidence:
                retained.append(evidence_root)
        except Exception:
            rollback_errors: list[Exception] = []
            for source, target in reversed(moved_sources):
                try:
                    shutil.move(str(target), str(source))
                except Exception as rollback_exc:
                    rollback_errors.append(rollback_exc)
            if created_target_root and target_root.exists():
                try:
                    shutil.rmtree(target_root)
                except Exception as rollback_exc:
                    rollback_errors.append(rollback_exc)
            if created_evidence_root and evidence_root.exists():
                try:
                    shutil.rmtree(evidence_root)
                except Exception as rollback_exc:
                    rollback_errors.append(rollback_exc)
            if not artifact_parent_existed and artifact_parent.exists():
                try:
                    artifact_parent.rmdir()
                except OSError:
                    pass
            if not log_parent_existed and log_parent.exists():
                try:
                    log_parent.rmdir()
                except OSError:
                    pass
            if not evidence_parent_existed:
                current = evidence_parent
                while current != project_root and current.exists():
                    try:
                        current.rmdir()
                    except OSError:
                        break
                    current = current.parent
            if rollback_errors:
                raise RuntimeError(f"{name}: retention rollback failed") from rollback_errors[0]
            raise
    return retained


def write_release_progress(repo_root: Path, workspace: ReleaseWorkspace, sha: str) -> None:
    """Record the commit SHA as of the last *fully completed* project in a
    per-project release run (build-all-projects-after-another: S-REL — each
    project's prepare/gate/tag/build/publish/promote cycle finishes before the
    next project's starts). It identifies the last complete source candidate
    for inspection and resume reporting; current releases never use it to
    manufacture a source-tree revert."""
    scope_dir = _ensure_scope_dir(repo_root)
    (scope_dir / f"{_release_token(workspace)}.progress").write_text(sha, encoding="utf-8")


def read_release_progress(repo_root: Path, workspace: ReleaseWorkspace) -> str | None:
    """The last-fully-completed-project checkpoint for a workspace, or None if no
    project has completed yet (or this predates the feature)."""
    path = _scope_dir(repo_root) / f"{_release_token(workspace)}.progress"
    if not path.exists():
        return None
    try:
        sha = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return sha or None


def _forget_release_progress(repo_root: Path, workspace: ReleaseWorkspace) -> None:
    (_scope_dir(repo_root) / f"{_release_token(workspace)}.progress").unlink(missing_ok=True)


def list_cmru_workspaces(repo_root: Path) -> list[ReleaseWorkspace]:
    """Every retained release or build worktree, under either the flat
    ``cmru-release-*``/``cmru-build-*`` scheme or the legacy nested
    ``cmru/release/*``/``cmru/build/*`` one.

    ``git worktree add`` records the absolute path it was given at creation
    time, and ``git worktree list --porcelain -z`` reports that literal path
    without re-resolving it from the current process's vantage point. This
    repository is bind-mounted at different absolute paths in the cockpit and
    on the Docker host. The shared Git inventory carries Git's ``prunable``
    fact, so this adapter need not stat a path belonging to another namespace.
    That marker describes Git's registration state, not path visibility;
    preserve the reported HEAD for prunable worktrees too. Callers that need to
    mutate a worktree must withhold actions for prunable entries and let
    lifecycle preflight validate every non-prunable target.
    """
    shared = _shared_worktree()
    entries = shared.list_git_worktrees(repo_root)
    records = {
        record.worktree_path: record
        for record in shared.list_workspaces(_common_git_dir(repo_root))
    }
    workspaces: list[ReleaseWorkspace] = []
    cmru_purposes = {"cmru-release", "cmru-build", "cmru-legacy"}
    for record in records.values():
        purpose_matches_branch = (
            record.purpose == "cmru-release" and _is_release_branch(record.branch)
        ) or (
            record.purpose == "cmru-build" and _is_build_branch(record.branch)
        ) or (
            record.purpose == "cmru-legacy" and _is_transaction_branch(record.branch)
        )
        if record.purpose in cmru_purposes and not purpose_matches_branch:
            raise RuntimeError(
                f"shared CMRU record {record.record_path} has purpose "
                f"{record.purpose!r} but branch {record.branch!r}; refusing to "
                "classify it as a retained transaction"
            )
    for entry in entries:
        branch = entry.branch
        if not branch:
            continue
        if not _is_transaction_branch(branch):
            continue
        purpose = workspace_purpose(branch)
        record = records.get(entry.path)
        context = None
        if record is not None:
            # A branch-name collision with another product is not CMRU
            # ownership. Legacy CMRU worktrees have no shared record; those
            # remain discoverable by their historical branch convention.
            if record.purpose not in {f"cmru-{purpose}", "cmru-legacy"}:
                continue
            if record.branch != branch:
                raise RuntimeError(
                    f"Git reports branch {branch!r} at {entry.path}, but its "
                    f"shared record says {record.branch!r}"
                )
            context = record.context()
        base = entry.head or ""
        workspaces.append(
            ReleaseWorkspace(
                repo_root,
                entry.path,
                branch,
                base,
                context=context,
                is_prunable=entry.is_prunable,
            )
        )
    return workspaces


def list_retained_workspaces(repo_root: Path) -> list[ReleaseWorkspace]:
    """Non-prunable retained release worktrees, for release-resume machinery only."""
    return [
        workspace
        for workspace in list_cmru_workspaces(repo_root)
        if _is_release_branch(workspace.branch) and not workspace.is_prunable
    ]


def abandon_workspace(
    repo_root: Path, workspace: ReleaseWorkspace, *,
    git_auth: GitHubGitAuth | None = None,
    expected_remote_candidate_oid: str | None = None,
    expected_remote_tag_refs: Mapping[str, str] | None = None,
    release_tag_prefixes: Sequence[str] = (),
    expected_local_tag_refs: Mapping[str, str] | None = None,
    local_tags_to_remove: Mapping[str, str] | None = None,
) -> None:
    """Fully discard a retained release attempt and its origin candidate branch.

    Unlike ``remove_workspace`` (the success path), this never touches
    ``origin/main``. The current release order leaves a failed candidate out of
    main, so deleting the candidate is the only source cleanup required here.
    """
    ref = "refs/heads/" + workspace.branch
    remote = run_remote_git(
        repo_root, "ls-remote", "--heads", "origin", ref,
        auth=git_auth, capture_output=True, text=True, check=False,
    )
    if remote.returncode != 0:
        raise RuntimeError("cannot determine origin candidate state; retained transaction was not removed")
    remote_refs = parse_ls_remote_refs(
        remote.stdout, namespace="refs/heads/", description="origin branch lookup",
    )
    if any(remote_ref != ref for remote_ref in remote_refs):
        raise RuntimeError("origin branch lookup returned an unexpected ref; retained transaction was not removed")
    remote_candidate_oid = remote_refs.get(ref)
    if (
        expected_remote_candidate_oid is not None
        and remote_candidate_oid != expected_remote_candidate_oid
    ):
        raise RuntimeError("origin candidate ref changed after abandonment inspection; retained transaction was not removed")
    present = remote_candidate_oid is not None
    pushed = backup_was_pushed(repo_root, workspace)
    removed = backup_was_removed(repo_root, workspace)
    if pushed and not removed and not present:
        raise RuntimeError("origin candidate ref is missing but its removal was not recorded; refusing stale transaction metadata")
    if (not pushed or removed) and present:
        raise RuntimeError("origin candidate ref exists without matching active transaction state")
    if expected_remote_tag_refs is not None:
        tags = run_remote_git(
            repo_root, "ls-remote", "--tags", "origin",
            auth=git_auth, capture_output=True, text=True, check=False,
        )
        if tags.returncode != 0:
            raise RuntimeError("cannot recheck origin release tags; retained transaction was not removed")
        current_tag_refs = parse_ls_remote_refs(
            tags.stdout, namespace="refs/tags/", description="origin release tag lookup",
        )
        current_tag_refs = _tag_refs_for_prefixes(current_tag_refs, release_tag_prefixes)
        if current_tag_refs != dict(expected_remote_tag_refs):
            raise RuntimeError("origin release tags changed after abandonment inspection; retained transaction was not removed")
    if expected_local_tag_refs is not None:
        current_local_tag_refs = _tag_refs_for_prefixes(
            list_local_tag_refs(repo_root), release_tag_prefixes,
        )
        if current_local_tag_refs != dict(expected_local_tag_refs):
            raise RuntimeError("local release tags changed after abandonment inspection; retained transaction was not removed")
    if pushed and not removed:
        if remote_candidate_oid is None or not COMMIT_ID_RE.fullmatch(remote_candidate_oid):
            raise RuntimeError("origin candidate object ID is unavailable; retained transaction was not removed")
        result = run_remote_git(
            repo_root, "push",
            f"--force-with-lease={ref}:{remote_candidate_oid}",
            "origin", f":{ref}",
            auth=git_auth, capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"could not delete origin candidate ref {ref}; local worktree and transaction metadata were retained\n"
                f"{result.stderr.strip()}"
            )
        verify = run_remote_git(
            repo_root, "ls-remote", "--heads", "origin", ref,
            auth=git_auth, capture_output=True, text=True, check=False,
        )
        if verify.returncode != 0:
            raise RuntimeError(f"origin candidate ref {ref} still exists or could not be verified; local transaction was retained")
        remaining_refs = parse_ls_remote_refs(
            verify.stdout, namespace="refs/heads/", description="origin candidate deletion verification",
        )
        if remaining_refs:
            raise RuntimeError(f"origin candidate ref {ref} still exists or could not be verified; local transaction was retained")
        mark_backup_removed(repo_root, workspace)
    for tag_ref, expected_oid in sorted((local_tags_to_remove or {}).items()):
        if (
            not _valid_ls_remote_ref(tag_ref, "refs/tags/")
            or not COMMIT_ID_RE.fullmatch(expected_oid)
        ):
            raise RuntimeError("abandonment has a malformed local release-tag cleanup target")
        current_oid = list_local_tag_refs(repo_root).get(tag_ref)
        if current_oid is None:
            continue
        if current_oid != expected_oid:
            raise RuntimeError(
                f"local release tag {tag_ref} changed after abandonment inspection; "
                "the retained worktree was not removed"
            )
        result = run_local_git(
            repo_root, "update-ref", "-d", tag_ref, expected_oid,
            capture_output=True, text=True, check=False,
        )
        remaining_oid = list_local_tag_refs(repo_root).get(tag_ref)
        if remaining_oid is not None:
            if remaining_oid != expected_oid:
                raise RuntimeError(
                    f"local release tag {tag_ref} changed during abandonment; "
                    "the retained worktree was not removed"
                )
            detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
            raise RuntimeError(
                f"could not remove local release tag {tag_ref} ({result.returncode}); "
                f"the retained worktree was not removed: {detail}"
            )
    remove_workspace(workspace)
    forget_release_scope(repo_root, workspace)


def _tag_refs_for_prefixes(
    tag_refs: Mapping[str, str], prefixes: Sequence[str],
) -> dict[str, str]:
    """Select complete tag-ref records in the named project release namespaces."""
    if not prefixes:
        return dict(tag_refs)
    selected: dict[str, str] = {}
    for ref, oid in tag_refs.items():
        name = ref.removeprefix("refs/tags/")
        if name.endswith("^{}"):
            name = name[:-3]
        if any(name.startswith(prefix) for prefix in prefixes):
            selected[ref] = oid
    return selected


PROMOTE_MERGE_ATTEMPTS = 3
# Git's stderr for a push that lost a fast-forward race.
_NON_FAST_FORWARD_MARKERS = ("non-fast-forward", "fetch first", "[rejected]")


def _promotion_recovery(workspace: ReleaseWorkspace) -> str:
    return (
        "The release itself is complete and must NOT be repeated: its tag and published "
        "artifacts stay as they are. To land the candidate by hand, run in the retained "
        f"worktree:\n  cd {workspace.path}\n  git fetch origin main\n"
        "  git merge --no-ff origin/main     # resolve any conflict, then commit\n"
        "  git push origin HEAD:refs/heads/main\n"
        "Never force-push main."
    )


def _merge_origin_main_into_candidate(
    workspace: ReleaseWorkspace,
    *,
    git_auth: GitHubGitAuth | None,
    project_paths: Sequence[str],
    release_label: str,
) -> bool:
    """Fetch origin/main and merge it into the candidate (``--no-ff``).

    Returns False when origin/main is already contained in the candidate (the
    rejection then had another cause). Raises, leaving the candidate unchanged,
    when the merge would conflict or when origin/main changed the released
    project's own paths (the gated and published content would then differ from
    what lands on main).
    """
    path = workspace.path
    fetched = run_remote_git(
        path, "fetch", "--prune", "origin", "main",
        auth=git_auth, capture_output=True, text=True, check=False,
    )
    if fetched.returncode != 0:
        raise RuntimeError(
            "release candidate was not promoted: fetching origin/main failed "
            f"({(fetched.stderr or '').strip()}). {_promotion_recovery(workspace)}"
        )
    origin_main = _git(path, "rev-parse", "origin/main")
    contained = run_local_git(
        path, "merge-base", "--is-ancestor", origin_main, "HEAD",
        capture_output=True, text=True, check=False,
    )
    if contained.returncode == 0:
        return False
    if contained.returncode != 1:
        raise RuntimeError(
            "release candidate was not promoted: could not compare the candidate with "
            f"origin/main. {_promotion_recovery(workspace)}"
        )
    merge_base = _git(path, "merge-base", "HEAD", origin_main)
    if not project_paths:
        raise RuntimeError(
            "release candidate was not promoted: origin/main advanced and the released "
            "project's paths are unknown, so CMRU cannot prove the merge leaves the "
            f"gated content unchanged. {_promotion_recovery(workspace)}"
        )
    touched = _git(
        path, "diff", "--name-only", merge_base, origin_main, "--", *project_paths,
    )
    if touched:
        raise RuntimeError(
            "release candidate was not promoted: origin/main advanced AND changed the "
            "released project's own paths, so merging would land content that differs "
            f"from what was gated and published ({', '.join(touched.splitlines()[:5])}"
            f"{'...' if len(touched.splitlines()) > 5 else ''}). Review those changes "
            f"first; if they are acceptable, merge by hand. {_promotion_recovery(workspace)}"
        )
    label = release_label or workspace.branch
    merged = run_local_git(
        path, "merge", "--no-ff", "-m",
        f"Merge origin/main into release candidate {label}\n\n"
        "origin/main advanced while the release ran; none of the released "
        "project's paths changed.",
        origin_main,
        capture_output=True, text=True, check=False,
    )
    if merged.returncode != 0:
        run_local_git(path, "merge", "--abort", capture_output=True, text=True, check=False)
        detail = (merged.stdout or merged.stderr or "").strip()
        raise RuntimeError(
            "release candidate was not promoted: merging origin/main into the candidate "
            f"conflicted ({detail}); the merge was aborted and the candidate is unchanged. "
            f"{_promotion_recovery(workspace)}"
        )
    return True


def promote_workspace(
    workspace: ReleaseWorkspace,
    *,
    git_auth: GitHubGitAuth | None = None,
    project_paths: Sequence[str] = (),
    release_label: str = "",
    max_attempts: int = PROMOTE_MERGE_ATTEMPTS,
) -> None:
    """Land the release candidate on ``origin/main`` without ever force-pushing.

    The candidate is built and published before this function is called, so the
    gated commit must stay an ancestor of what lands: the candidate is never
    rebased. When origin/main advanced during the (long) gate, the push is
    rejected as non-fast-forward; REL-04: fetch, merge origin/main into the
    candidate (``--no-ff``, naming the release), and push again, at most
    ``max_attempts`` times. A conflict, or a merge that would touch the released
    project's own paths, stops with recovery instructions; the tag and published
    state are kept.
    """
    attempts = 0
    while True:
        result = run_remote_git(
            workspace.path, "push", "origin", "HEAD:refs/heads/main",
            auth=git_auth, capture_output=True, text=True,
        )
        if result.returncode == 0:
            return
        stderr = result.stderr or ""
        failure = (
            "release candidate was not promoted to origin/main; the candidate may "
            "have lost a fast-forward race or the remote rejected the push. The "
            f"candidate branch {getattr(workspace, 'branch', '<unknown>')} was retained "
            f"for inspection.\n{stderr}"
        )
        if not any(marker in stderr for marker in _NON_FAST_FORWARD_MARKERS):
            # Authentication, hook or network failures are not a lost race:
            # merging main into the candidate cannot help, so fail immediately.
            raise RuntimeError(failure)
        if attempts >= max_attempts:
            raise RuntimeError(
                f"{failure}\nGave up after {attempts} merge attempt(s) because origin/main "
                f"kept advancing. {_promotion_recovery(workspace)}"
            )
        attempts += 1
        if not _merge_origin_main_into_candidate(
            workspace, git_auth=git_auth, project_paths=project_paths,
            release_label=release_label,
        ):
            raise RuntimeError(failure)


def push_backup_branch(
    workspace: ReleaseWorkspace, *, git_auth: GitHubGitAuth | None = None,
) -> None:
    """Push the current release candidate to its durable origin branch.

    This is called initially, after each prepare/tag commit, and before each
    public build or publish step. A crashed machine or lost worktree after any
    such call still leaves an inspectable copy of the exact candidate on origin;
    ``main`` itself is untouched by this push.

    ``--force`` is safe here because ``workspace.branch`` is an allocator-scoped name
    this ONE transaction created and exclusively owns. Each refresh deliberately
    replaces that branch's prior candidate tip as the local transaction advances.

    Records that THIS transaction actually pushed the backup
    (:func:`mark_backup_pushed`, KI-15) — the state :func:`remove_backup_branch`
    later checks before attempting any cleanup delete.
    """
    run_remote_git(
        workspace.path, "push", "--force", "origin", f"HEAD:refs/heads/{workspace.branch}",
        auth=git_auth, check=True,
    )
    mark_backup_pushed(workspace.repo_root, workspace)


def remove_backup_branch(
    workspace: ReleaseWorkspace, *, git_auth: GitHubGitAuth | None = None,
) -> None:
    """Delete the durability backup branch from origin after a fully successful release.

    Only attempted when THIS transaction actually pushed one
    (:func:`backup_was_pushed`, KI-15) — a dry run, a "nothing to release" run,
    and a refused release plan (S12.2a/S12.2b) all reach a successful exit
    without ever calling :func:`push_backup_branch`; unconditionally attempting
    a delete on those paths is exactly what made a genuinely successful,
    side-effect-free run print `error: unable to delete '...': remote ref does
    not exist` / `error: failed to push some refs to '...'` — training operators
    to read this release tool's `error:` output as noise.

    When a push DID happen, the delete still always runs — this transaction
    state is tracked precisely so that case is never mistaken for "nothing to
    clean up" either, which would orphan a real backup branch on origin
    forever. Best-effort: output is captured rather than left to reach the
    real stderr, so even a genuine failure here (branch already gone,
    network blip) stays silent rather than alarming — a release that already
    succeeded should not fail, or look like it failed, over cleanup of a
    branch whose job is already done.
    """
    if not backup_was_pushed(workspace.repo_root, workspace):
        return
    run_remote_git(
        workspace.path, "push", "origin", "--delete", workspace.branch,
        auth=git_auth, check=False, capture_output=True, text=True,
    )


def promotion_landed(
    repo_root: Path, workspace: ReleaseWorkspace, *,
    git_auth: GitHubGitAuth | None = None,
) -> bool:
    """Legacy inspector for transactions created by the pre-candidate-order flow.

    The current release path promotes only after publication and does not call
    this function. It remains available to inspect an older retained attempt
    without making that historical state part of the normal failure path.
    """
    run_remote_git(
        repo_root, "fetch", "--prune", "origin", "main", auth=git_auth, check=True,
    )
    origin_main = _git(repo_root, "rev-parse", "origin/main")
    branch_tip = _git(workspace.path, "rev-parse", workspace.branch)
    return origin_main == branch_tip


@dataclass(frozen=True)
class RevertResult:
    ok: bool          # False ⇒ needs manual cleanup (didn't apply cleanly / push rejected)
    reverted: bool    # True ⇒ a revert commit was actually pushed; False ⇒ nothing needed it


def revert_promotion(
    workspace: ReleaseWorkspace, *, from_sha: str | None = None,
    git_auth: GitHubGitAuth | None = None,
) -> RevertResult:
    """Legacy recovery helper for a transaction created by the old release order.

    The current release path never calls this function: failed candidates are
    withheld from ``origin/main`` and retained on their own branch. For an older
    retained attempt, this still best-effort undoes source commits on
    ``origin/main`` by pushing a revert.

    ``from_sha`` scopes the revert to ``(from_sha, branch tip]`` instead of the whole
    transaction (``workspace.base``) — pass the last-fully-completed-project checkpoint
    (:func:`read_release_progress`) in a per-project release run so a later project's
    failure only undoes its own promoted changes, never an earlier project's already
    -published release. Defaults to ``workspace.base`` (revert everything promoted this
    transaction) when omitted, preserving the whole-transaction behavior.

    Caller MUST have already confirmed :func:`promotion_landed` — this never rewrites
    history (no force-push), it only adds a new revert commit on top, so it is safe
    to attempt even if the precondition was checked slightly earlier.

    ``RevertResult.ok`` is False (manual cleanup required) when the revert does not
    apply cleanly or the push is rejected (e.g. someone pushed to main meanwhile).
    ``RevertResult.reverted`` distinguishes "there was nothing to revert" (ok=True,
    reverted=False — e.g. the failing project never got as far as its own promote)
    from "a revert commit was actually pushed" (ok=True, reverted=True) — callers
    that log "reverted" should check ``.reverted``, not just ``.ok``, or they'll
    claim a revert happened when nothing was there to undo.
    """
    base = from_sha if from_sha is not None else workspace.base
    branch_tip = _git(workspace.path, "rev-parse", workspace.branch)
    if base == branch_tip:
        return RevertResult(ok=True, reverted=False)
    result = run_local_git(
        workspace.path, "revert", "--no-edit", "--no-commit", f"{base}..{branch_tip}",
    )
    if result.returncode != 0:
        run_local_git(workspace.path, "revert", "--abort", check=False)
        return RevertResult(ok=False, reverted=False)
    run_local_git(
        workspace.path, "commit", "-m", f"revert: undo failed release {workspace.branch}",
        check=True,
    )
    push = run_remote_git(
        workspace.path, "push", "origin", "HEAD:refs/heads/main", auth=git_auth,
    )
    return RevertResult(ok=push.returncode == 0, reverted=push.returncode == 0)


_SYNC_DIRTY_REASON = (
    "Could not sync local main automatically: the caller checkout is dirty "
    "(tracked or untracked changes), "
    "so no rebase or rebase-abort was attempted and local main plus those files "
    "were left untouched. Commit or stash all changes, then run "
    "`git rebase origin/main` from the clean checkout."
)


_SYNC_IGNORED_COLLISION_REASON = (
    "Could not sync local main automatically: origin/main adds file(s) at path(s) that "
    "are ignored (and present) in the caller checkout, which a checkout would silently "
    "overwrite (or they could not be inspected). Local main and those files were left "
    "untouched. Move them away (or `git stash -a`), then run `git rebase origin/main`."
)


def _ignored_paths_origin_would_overwrite(repo_root: Path) -> list[str] | None:
    """Ignored local files that updating to origin/main would create-over (None: unknown)."""
    try:
        added = _git(
            repo_root, "diff", "--name-only", "--diff-filter=ACR", "-z",
            "HEAD", "origin/main",
        )
        ignored = _git(
            repo_root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z",
        )
    except RuntimeError:
        return None
    incoming = {name for name in added.split("\0") if name}
    return sorted(name for name in ignored.split("\0") if name in incoming)


def _git_path_exists(repo_root: Path, name: str) -> bool | None:
    """Return whether a Git state path exists, or ``None`` if it is unreadable."""
    try:
        raw = _git(repo_root, "rev-parse", "--git-path", name)
    except RuntimeError:
        return None
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = repo_root / path
    try:
        return path.exists()
    except OSError:
        return None


def _rebase_in_progress(repo_root: Path) -> bool | None:
    """Inspect both Git rebase state layouts without guessing on read failure."""
    merge = _git_path_exists(repo_root, "rebase-merge")
    apply = _git_path_exists(repo_root, "rebase-apply")
    if merge is None or apply is None:
        return None
    return merge or apply


def _sync_local_main_result(
    repo_root: Path, *, git_auth: GitHubGitAuth | None = None,
) -> _SyncLocalMainResult:
    """Perform caller-main synchronization and retain its exact per-call outcome."""
    try:
        run_remote_git(
            repo_root, "fetch", "--prune", "origin", "main", auth=git_auth, check=True,
        )
    except (subprocess.CalledProcessError, OSError, RuntimeError) as exc:
        # REL-06: this runs after the release already completed (or failed and was
        # reported). A transient fetch failure must not change that outcome.
        return _SyncLocalMainResult(
            False,
            "Could not sync local main automatically: fetching origin/main failed "
            f"({exc}). Local main was left untouched; run `git fetch origin main` and "
            "`git rebase origin/main` from the caller checkout when origin is reachable.",
        )
    current = _git(repo_root, "branch", "--show-current", check=False)
    if current == "main":
        # ``git rebase`` refuses a dirty checkout itself, but calling it first
        # would produce a misleading secondary ``rebase --abort`` error.
        # Tracked and untracked (non-ignored) changes always block. Ignored files
        # (REL-13) block only where origin/main would actually create a file at
        # an ignored path: a checkout silently overwrites those, but ordinary
        # ignored build output must not make every release warn.
        status = _git(
            repo_root,
            "status",
            "--porcelain",
            "--untracked-files=all",
        )
        if status:
            return _SyncLocalMainResult(False, _SYNC_DIRTY_REASON)
        collisions = _ignored_paths_origin_would_overwrite(repo_root)
        if collisions is None or collisions:
            return _SyncLocalMainResult(False, _SYNC_IGNORED_COLLISION_REASON)
        if _rebase_in_progress(repo_root) is True:
            return _SyncLocalMainResult(
                False,
                "Could not sync local main automatically: a rebase is already in progress "
                "in the clean-looking caller checkout, so no new rebase or abort was "
                "attempted. Finish or abort that existing rebase, then retry cleanup.",
            )
        result = run_local_git(repo_root, "rebase", "origin/main")
        if result.returncode == 0:
            return _SyncLocalMainResult(True)

        # A non-zero rebase is not synonymous with a content conflict. Inspect
        # the index and rebase state while this invocation still owns the
        # outcome, before deciding whether an abort is both needed and safe.
        try:
            unmerged = _git(repo_root, "ls-files", "-u")
            conflict_known = True
        except RuntimeError:
            unmerged = ""
            conflict_known = False
        rebase_active = _rebase_in_progress(repo_root)
        abort_result = None
        if rebase_active is True:
            abort_result = run_local_git(repo_root, "rebase", "--abort", check=False)

        if conflict_known and unmerged:
            if abort_result is not None and abort_result.returncode == 0:
                return _SyncLocalMainResult(
                    False,
                    "Could not sync local main automatically: a clean checkout's rebase "
                    "established a genuine conflict (content conflict); the rebase was aborted and "
                    "local main was left untouched. Inspect the overlap, then rerun "
                    "`git rebase origin/main` from a clean checkout and resolve it manually.",
                )
            return _SyncLocalMainResult(
                False,
                "Could not sync local main automatically: the rebase established a genuine "
                "conflict (content conflict), but automatic rebase cleanup failed; local main or the "
                "caller checkout may still require manual inspection before retrying.",
            )

        # ``abort_result`` is assigned only after observing
        # ``rebase_active is True``. Its presence therefore records the state
        # transition that required an abort; keep the return-code check nested
        # so an absent result cannot be confused with a failed abort.
        if abort_result is not None:
            if abort_result.returncode != 0:
                return _SyncLocalMainResult(
                    False,
                    "Could not sync local main automatically: the rebase failed without an "
                    "established content conflict, and its in-progress state could not be "
                    "aborted. The cause is undetermined; inspect the caller checkout before "
                    "retrying.",
                )
            if conflict_known:
                return _SyncLocalMainResult(
                    False,
                    "Could not sync local main automatically: the rebase failed without an "
                    "established content conflict, and its in-progress state was aborted "
                    "successfully. The cause is undetermined; local main was not claimed to be "
                    "synchronized. Inspect the caller checkout before retrying.",
                )
        if rebase_active is None or not conflict_known:
            return _SyncLocalMainResult(
                False,
                "Could not sync local main automatically: the rebase failed, but its "
                "conflict state could not be determined. The cause is undetermined; "
                "local main was not claimed to be synchronized. Inspect the caller "
                "checkout before retrying.",
            )
        return _SyncLocalMainResult(
            False,
            "Could not sync local main automatically: the rebase failed without "
            "establishing a content conflict (for example, a hook or other Git failure); "
            "no rebase-abort was needed. The cause is undetermined, and local main was "
            "not claimed to be synchronized. Inspect the caller checkout before retrying.",
        )
    local_main = _git(repo_root, "rev-parse", "main", check=False)
    if local_main:
        merge_base = _git(repo_root, "merge-base", "main", "origin/main", check=False)
        if merge_base != local_main:
            return _SyncLocalMainResult(
                False,
                "Could not sync local main automatically: local main has commits of its own "
                "and is not checked out, so it was not force-moved. Reconcile that ref "
                "manually; no caller checkout synchronization is claimed.",
            )
    result = run_local_git(repo_root, "branch", "-f", "main", "origin/main")
    if result.returncode == 0:
        return _SyncLocalMainResult(True)
    return _SyncLocalMainResult(
        False,
        "Could not sync local main automatically: updating the non-current local main ref "
        "failed; local main was left untouched. Inspect the ref and synchronize it manually.",
    )


def sync_local_main(
    repo_root: Path, *, git_auth: GitHubGitAuth | None = None,
) -> bool:
    """Bring the caller's local ``main`` up to date with ``origin/main``.

    The historical public API remains a boolean. The CLI uses the private
    per-call result helper so a false result is reported from the operation
    that produced it, rather than classified by a later checkout inspection.
    """
    return _sync_local_main_result(repo_root, git_auth=git_auth).ok


def run_child(
    workspace: ReleaseWorkspace, child_args: Sequence[str], *, verb: str = "release",
    project_names: Sequence[str] | None = None,
) -> int:
    """Run a CMRU verb from the snapshot, preserving terminal output.

    Release children use the installed ``cmru`` executable. For CMRU's own
    release, prepend the candidate's source roots so that the code being shipped
    also owns its transaction, including Git transport authentication.
    """
    env = os.environ.copy()
    env[CHILD_ENV] = "1"
    env[BRANCH_ENV] = workspace.branch
    env[BASE_ENV] = workspace.base
    if workspace.workspace_id:
        env["CMRU_WORKSPACE_ID"] = workspace.workspace_id
    env["CMRU_WORKSPACE_PATH"] = str(workspace.path)
    env["CMRU_SOURCE_GIT_ROOT"] = str(workspace.repo_root)
    if project_names is not None:
        env["CMRU_TRANSACTION_PROJECTS"] = ",".join(project_names)
        candidate_cmru = workspace.path / "cmru" / "src"
        if "cmru" in project_names and (candidate_cmru / "cmru" / "cli.py").is_file():
            source_roots = [
                candidate_cmru,
                workspace.path / "libraries" / "worktree" / "src",
                workspace.path / "libraries" / "cli-extended" / "src",
            ]
            source_paths = [str(path) for path in source_roots if path.is_dir()]
            inherited = env.get("PYTHONPATH", "")
            if inherited:
                source_paths.extend(inherited.split(os.pathsep))
            env["PYTHONPATH"] = os.pathsep.join(source_paths)
    launcher = [os.environ.get("CMRU_BIN") or shutil.which("cmru") or "cmru"]
    command = [*launcher, verb, *child_args]
    return subprocess.run(command, cwd=workspace.path, env=env).returncode
