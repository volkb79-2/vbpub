"""Pure-logic path helpers for CIU v2.

Implements the path model from SPEC S1 (project & path model), specifically:
- S1.4: to_physical_path — maps logical paths under REPO_ROOT to their physical
  equivalents under PHYSICAL_REPO_ROOT for paths handed to the Docker daemon.
- S1.9: When PHYSICAL_REPO_ROOT == REPO_ROOT (native host), to_physical_path is
  the identity function.

Logical path:  as seen by the CIU process (REPO_ROOT-based).
Physical path: same location as seen by the Docker daemon (PHYSICAL_REPO_ROOT-based).
"""
from __future__ import annotations

import os
from pathlib import Path


def is_under(path: Path, root: Path) -> bool:
    """Return True if *path* is located under *root* (or equals root).

    Both arguments should already be resolved (absolute, symlinks followed)
    before calling this helper.
    """
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def to_physical_path(
    path: Path | str,
    repo_root: Path | None = None,
    physical_root: Path | None = None,
) -> Path:
    """Translate a logical path to a physical path (SPEC S1.4).

    Parameters
    ----------
    path:
        The logical path to translate. May be a ``Path`` or a string.
    repo_root:
        The logical workspace root (``REPO_ROOT``). When *None*, read from
        ``os.environ['REPO_ROOT']``; raises ``ValueError`` naming the variable
        if absent.
    physical_root:
        The physical workspace root visible to the Docker daemon
        (``PHYSICAL_REPO_ROOT``). When *None*, read from
        ``os.environ['PHYSICAL_REPO_ROOT']``; raises ``ValueError`` naming the
        variable if absent.

    Returns
    -------
    Path
        - If *path* resolves to somewhere under *repo_root*:
          ``physical_root / relpath(path, repo_root)``.
        - If *path* is outside *repo_root* (e.g. ``/etc/letsencrypt/…``):
          the path is returned unchanged (absolute external paths pass through,
          S1.4).
        - When ``repo_root == physical_root`` the function is the identity
          (native host, S1.9).

    Raises
    ------
    ValueError
        When a required environment variable is missing and the corresponding
        argument was not provided.
    """
    if repo_root is None:
        val = os.environ.get("REPO_ROOT")
        if not val:
            raise ValueError(
                "repo_root was not provided and REPO_ROOT is not set in the environment"
            )
        repo_root = Path(val)

    if physical_root is None:
        val = os.environ.get("PHYSICAL_REPO_ROOT")
        if not val:
            raise ValueError(
                "physical_root was not provided and PHYSICAL_REPO_ROOT is not set in the environment"
            )
        physical_root = Path(val)

    path = Path(path)

    # Resolve all three for a reliable containment check (follows symlinks).
    resolved_path = path.resolve()
    resolved_repo = repo_root.resolve()
    resolved_physical = physical_root.resolve()

    # Identity short-circuit: native host where logical == physical (S1.9).
    if resolved_repo == resolved_physical:
        return path if path.is_absolute() else resolved_path

    if is_under(resolved_path, resolved_repo):
        rel = resolved_path.relative_to(resolved_repo)
        return resolved_physical / rel

    # External absolute paths pass through unchanged (S1.4).
    return path
