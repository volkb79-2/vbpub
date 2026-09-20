"""CIU's adapter over the shared workspace-instance library.

This module answers CIU-specific questions (which root marker is nearest and
which committed roots exist) while delegating Git facts, path identity and
physical translation to :mod:`worktree`.
"""

from __future__ import annotations

import fcntl
import hashlib
import re
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .config_constants import GLOBAL_CONFIG_DEFAULTS


def _shared():
    try:
        import worktree
        return worktree
    except ModuleNotFoundError:
        library_src = Path(__file__).resolve().parents[3] / "libraries" / "worktree" / "src"
        if not library_src.is_dir():
            raise
        sys.path.insert(0, str(library_src))
        import worktree
        return worktree


class CiuWorkspaceError(ValueError):
    """A CIU root/context selection refused before product work starts."""


@contextmanager
def root_lock(context: "RootContext"):
    """Serialize one CIU-root's generated-facts/runtime admission work.

    The lock lives in the shared Git-family state directory, keyed by the
    root's physical identity, so acquiring it never dirties a checkout.
    Callers touching several roots must acquire contexts in offset order.
    """

    shared = _shared()
    offset_key = hashlib.sha256(
        str(context.ciu_root_offset).encode("utf-8")
    ).hexdigest()[:16]
    lock_path = (
        context.workspace.git_common_dir
        / shared.WORKSPACE_RECORD_DIR
        / f"ciu-root-offset-{offset_key}.lock"
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@dataclass(frozen=True)
class RootContext:
    workspace: object
    ciu_root: Path
    ciu_root_offset: Path
    root_instance_id: str
    physical_ciu_root: Path

    @property
    def workspace_id(self) -> str:
        return str(self.workspace.workspace_id)

    @property
    def root_namespace(self) -> str:
        return f"{self.workspace_id}-{self.root_instance_id}"


def _walk_root(start_dir: Path) -> Path | None:
    current = Path(start_dir).resolve()
    while True:
        marker = current / GLOBAL_CONFIG_DEFAULTS
        if marker.is_file():
            return current
        if current == current.parent:
            return None
        current = current.parent


def resolve_ciu_root(
    start_dir: Path | str,
    *,
    root_folder: Path | str | None = None,
) -> Path:
    """Select the nearest CIU root, never from ambient shell identity."""

    start = Path(start_dir).resolve()
    if root_folder is not None:
        root = Path(root_folder).resolve()
        if not (root / GLOBAL_CONFIG_DEFAULTS).is_file():
            raise CiuWorkspaceError(
                f"[no-ciu-root] --root-folder {root} is not a CIU root: "
                f"missing {GLOBAL_CONFIG_DEFAULTS}"
            )
        return root
    root = _walk_root(start)
    if root is None:
        raise CiuWorkspaceError(
            f"[no-ciu-root] no CIU root above {start}; expected "
            f"{GLOBAL_CONFIG_DEFAULTS}. Use --root-folder PATH for an explicit root."
        )
    return root


def resolve_worktree_git_root(
    start_dir: Path | str,
    *,
    root_folder: Path | str | None = None,
) -> Path:
    """Select the containing Git root for a worktree-family operation.

    Unlike a stack verb, a worktree verb must be able to start at the Git root
    and then discover zero or more nested CIU roots from the selected base.
    Therefore ``--root-folder`` is a Git-family selector here, not a demand
    that the supplied directory itself carry a CIU marker.
    """
    shared = _shared()
    selected = Path(root_folder).resolve() if root_folder is not None else Path(start_dir).resolve()
    try:
        git_root, _common, _branch, _head = shared.discover_git_context(selected)
    except shared.WorkspaceError as exc:
        raise CiuWorkspaceError(
            f"[no-git-root] could not resolve a Git root from {selected}: {exc}"
        ) from exc
    return git_root


def context_for_root(
    ciu_root: Path | str,
    *,
    physical_git_root: Path | str | None = None,
) -> RootContext:
    """Build a typed workspace/root context for one CIU root."""

    shared = _shared()
    root = Path(ciu_root).resolve()
    git_root, common, branch, head = shared.discover_git_context(root)
    try:
        offset = root.relative_to(git_root)
    except ValueError as exc:
        raise CiuWorkspaceError(
            f"CIU root {root} escapes Git root {git_root}"
        ) from exc
    physical_git = Path(physical_git_root).resolve() if physical_git_root else git_root
    physical_root = shared.physical_path(
        root, logical_root=git_root, physical_root=physical_git
    )
    workspace_id = shared.workspace_id_for_path(
        shared.physical_path(git_root, logical_root=git_root, physical_root=physical_git)
    )
    # The shared library has no product record for a primary checkout.  Create
    # the same typed value without writing one; lifecycle records are written
    # only for linked worktrees allocated through the generic API.
    workspace = shared.WorkspaceContext(
        source_git_root=git_root,
        worktree_path=git_root,
        git_common_dir=common,
        physical_worktree_path=shared.physical_path(
            git_root, logical_root=git_root, physical_root=physical_git
        ),
        workspace_id=workspace_id,
        branch=branch,
        base_commit=head,
        record_path=common / shared.WORKSPACE_RECORD_DIR / f"{workspace_id}.json",
        namespace=shared.ResourceNamespace(workspace_id, {"ciu_root": str(offset)}),
    )
    return RootContext(
        workspace=workspace,
        ciu_root=root,
        ciu_root_offset=offset,
        root_instance_id=shared.workspace_id_for_path(physical_root),
        physical_ciu_root=physical_root,
    )


def discover_committed_roots(
    git_root: Path | str,
    *,
    base: str = "HEAD",
) -> tuple[Path, ...]:
    """Find CIU roots from committed marker files at *base*.

    The ignored instance overlay is intentionally absent from this grammar.
    ``git ls-tree`` is used instead of a filesystem walk so a user's ignored
    overlay cannot become a new root during worktree creation.
    """

    root = Path(git_root).resolve()
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", base, "--"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise CiuWorkspaceError(
            f"cannot inspect committed CIU roots at {base!r}: "
            f"{(result.stderr or result.stdout).strip()}"
        )
    roots: set[Path] = set()
    for raw in result.stdout.splitlines():
        relative = Path(raw)
        if relative.name != GLOBAL_CONFIG_DEFAULTS:
            continue
        marker = root / relative
        _validate_committed_marker(root, base, raw)
        roots.add(marker.parent.resolve())
    return tuple(sorted(roots, key=lambda path: (len(path.parts), str(path))))


def _tracked_blob(root: Path, base: str, relative: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-t", f"{base}:{relative}"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "blob"


_TOML_TABLE_RE = re.compile(r"^\s*\[[A-Za-z_][A-Za-z0-9_.-]*(?:\.[^\]]+)?\]\s*$")


def _validate_committed_marker(root: Path, base: str, relative: str) -> None:
    """Validate the committed marker enough to distinguish a real root.

    Markers are Jinja TOML templates, so a full TOML parse would reject valid
    ``$VAR`` and template expressions. The closed marker grammar nevertheless
    requires UTF-8 text, no NUL bytes, and at least one syntactically valid TOML
    table header. The actual config loader performs the complete render/parse
    later, after allocation; discovery must refuse a malformed committed blob
    before creating any linked checkout.
    """
    result = subprocess.run(
        ["git", "show", f"{base}:{relative}"],
        cwd=root,
        text=False,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise CiuWorkspaceError(
            f"invalid committed CIU root marker {relative!r}: cannot read marker"
        )
    payload = result.stdout
    if b"\x00" in payload:
        raise CiuWorkspaceError(
            f"invalid committed CIU root marker {relative!r}: NUL byte"
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CiuWorkspaceError(
            f"invalid committed CIU root marker {relative!r}: not UTF-8"
        ) from exc
    if not any(_TOML_TABLE_RE.match(line) for line in text.splitlines()):
        raise CiuWorkspaceError(
            f"invalid committed CIU root marker {relative!r}: no TOML table header"
        )


def root_runtime_names(context: RootContext, *, stack: str) -> dict[str, str]:
    """Return deterministic adapter-owned names carrying both identities."""

    safe_stack = stack.replace("/", "-").replace("_", "-").lower()
    identity = (
        context.workspace_id
        if context.workspace_id == context.root_instance_id
        else f"{context.workspace_id}-{context.root_instance_id}"
    )
    return {
        "network": f"ciu-{identity}-network",
        "project": f"ciu-{identity}-{safe_stack}",
    }


def assert_root_identity_distinct(contexts: list[RootContext]) -> None:
    """Refuse a short root-identity collision with both physical paths."""

    seen: dict[str, RootContext] = {}
    for context in contexts:
        previous = seen.get(context.root_instance_id)
        if previous is not None and previous.physical_ciu_root != context.physical_ciu_root:
            raise CiuWorkspaceError(
                f"root identity collision for {context.root_instance_id!r}: "
                f"{previous.physical_ciu_root} and {context.physical_ciu_root}; refusing"
            )
        seen[context.root_instance_id] = context
