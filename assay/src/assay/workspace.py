"""The storage contract used by Assay's isolated snapshot workspaces.

This module deliberately knows nothing about cowtree. The adapter is loaded
only when a higher-rigor lane opens a workspace, so importing Assay stays a
stdlib-only operation.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol


@dataclass(frozen=True)
class WorkspaceEstimate:
    """Input-derived admission values for one prepared snapshot workspace."""

    ingest_bytes: int
    per_clone_bytes: int
    max_live_clones: int

    def __post_init__(self) -> None:
        for name in ("ingest_bytes", "per_clone_bytes", "max_live_clones"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")


class ActiveWorkspace(Protocol):
    """Run-owned paths and file clones while one workspace lease is open."""

    ingest_tree: Callable[[Path, str], Path]
    make_tree: Callable[[], Path]
    clone_file: Callable[[Path, Path], None]
    delete_tree: Callable[[Path], None]


class WorkspaceFactory:
    """Open the optional CoW adapter with a caller-derived live-clone bound."""

    def __init__(self, *, max_live_clones: int) -> None:
        if (
            isinstance(max_live_clones, bool)
            or not isinstance(max_live_clones, int)
            or max_live_clones < 1
        ):
            raise ValueError("max_live_clones must be a positive integer")
        self.max_live_clones = max_live_clones

    @contextmanager
    def open(
        self,
        estimate: WorkspaceEstimate,
        *,
        scratch_root: Path,
    ) -> Iterator[ActiveWorkspace | None]:
        """Yield an active CoW workspace, or ``None`` for the plain path."""
        opener = _cow_workspace_opener()
        with opener(
            estimate,
            scratch_root=scratch_root,
        ) as active:
            yield active


def _cow_workspace_opener():
    """Import the optional adapter only at the workspace boundary."""
    from ._cow_workspace import open_workspace

    return open_workspace


def factory_for_lane(lane: object) -> WorkspaceFactory:
    """Derive the prepared workspace clone bound from the declared lane.

    CW12 spares are not enabled in this release. The `+ 1` is the additional
    prepared/baseline workspace in the adopted `jobs + spares + 1` formula.
    """
    judge = getattr(lane, "judge", None)
    mutation = getattr(judge, "mutation", None) if judge is not None else None
    jobs = getattr(mutation, "jobs", None) if mutation is not None else None
    if jobs is None:
        jobs = 1
    return WorkspaceFactory(max_live_clones=jobs + 1)
