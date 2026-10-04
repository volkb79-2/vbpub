#!/usr/bin/env python3
"""Select CMRU's latest published release tag reachable from a candidate."""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = str(PROJECT_ROOT / "src")
if SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)

from cmru.release import _semver_key


def latest_published_release_tag(remote_tag_commits: Mapping[str, str]) -> str | None:
    """Return the highest-version CMRU release tag in authenticated origin facts."""
    tags = [tag for tag in remote_tag_commits if tag.startswith("cmru-v")]
    if not tags:
        return None
    return max(
        tags,
        key=lambda tag: (_semver_key(tag[len("cmru-v"):]), tag),
    )


def latest_ancestor_release_tag(
    assay_git,
    project_root: Path,
    head_commit: str,
    remote_tag_commits: Mapping[str, str],
    *,
    exclude_tags: set[str] | frozenset[str] = frozenset(),
) -> str | None:
    """Return the highest-version published release tag in HEAD's ancestry.

    Remote tag commits are the host-authenticated facts carried into the
    tester. Assay's sanitized Git boundary determines reachability, including
    merge second parents. Tags at HEAD are excluded when checking a tagged-HEAD
    rerun, and a remote tag whose commit object is unavailable locally fails
    closed through Assay rather than being treated as unreachable.
    """
    candidates = sorted(
        (
            tag for tag, commit in remote_tag_commits.items()
            if tag.startswith("cmru-v")
            and tag not in exclude_tags
            and commit != head_commit
        ),
        key=lambda tag: (_semver_key(tag[len("cmru-v"):]), tag),
        reverse=True,
    )
    remaining = lambda: math.inf
    for tag in candidates:
        if assay_git.is_ancestor(
            project_root,
            remote_tag_commits[tag],
            head_commit,
            remaining=remaining,
        ):
            return tag
    return None
