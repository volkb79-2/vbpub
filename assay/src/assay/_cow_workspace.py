"""Lazy cowtree adapter for Assay's copy-on-write snapshot paths."""

from __future__ import annotations

import importlib
import os
import secrets
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .errors import AssayError, Outcome, ReasonCode
from .workspace import ActiveWorkspace, WorkspaceCleanupError, WorkspaceEstimate

# cowtree's own environment names, duplicated so the no-area decision needs
# no import of cowtree (B196).
_ENV_ROOT = "MDT_COW_ROOT"
_ENV_MODE = "COWTREE_MODE"


def _load_cowtree():
    """Load the same-repository cowtree package only when CoW is requested."""
    return importlib.import_module("cowtree")


class _CowRunWorkspace:
    def __init__(self, run, *, per_clone_bytes: int, cowtree) -> None:
        self._run = run
        self._per_clone_bytes = per_clone_bytes
        self._cowtree = cowtree

    @property
    def cheap_clone(self) -> bool:
        return self._run.caps.cheap_clone

    def ingest_tree(self, source: Path, name: str) -> Path:
        return Path(self._run.ingest_tree(source, name))

    def make_tree(self) -> Path:
        return Path(self._run.make_tree(f"assay-snapshot-{secrets.token_hex(12)}"))

    def clone_file(self, source: Path, destination: Path) -> None:
        self._run.clone_file(source, destination)

    def clone_tree(self, source: Path) -> Path:
        name = f"assay-clone-{secrets.token_hex(12)}"
        return Path(self._run.clone_tree(source, name))

    def seal(self, path: Path) -> None:
        self._run.seal(path)

    def delete_tree(self, path: Path) -> None:
        self._run.delete_tree(path)

    def reserve(self, *, max_live_clones: int) -> None:
        """Reserve the consumer's full concurrent clone bound before use."""
        self._run.reserve(
            self._cowtree.SpaceEstimate(
                ingest_bytes=0,
                per_clone_bytes=self._per_clone_bytes,
                max_live_clones=max_live_clones,
            )
        )


def _is_btrfs(caps) -> bool:
    return caps.fs_type == "btrfs" and caps.backend == "btrfs"


def _require_btrfs(cowtree, caps) -> None:
    if not _is_btrfs(caps):
        raise cowtree.RootInvalid(
            "Assay CoW workspaces require a btrfs filesystem with the btrfs backend"
        )


def _load_cowtree_gc():
    """Load cowtree's garbage collector module (same package, lazy)."""
    return importlib.import_module("cowtree.gc")


def _collect_dead_runs(cowtree, area) -> None:
    """B197: remove runs whose owner died (SIGKILL, container kill).

    cowtree.gc deletes only valid, non-external runs whose owner flock is
    free and whose owner identity is provably dead, so a live sibling run is
    never touched. Collection is housekeeping: a failure is an advisory and
    never refuses the lane.
    """
    try:
        items = _load_cowtree_gc().gc(area.runs_dir)
    except (cowtree.CowError, OSError) as exc:
        sys.stderr.write(f"Assay advisory: dead copy-on-write run collection failed: {exc}\n")
        return
    deleted = [item for item in items if item.status == "deleted"]
    if deleted:
        sys.stderr.write(
            "Assay advisory: collected "
            f"{len(deleted)} dead copy-on-write run(s) left by an earlier killed run\n"
        )


def _as_assay_error(exc: Exception) -> AssayError:
    return AssayError(
        f"copy-on-write workspace failed: {exc}",
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.EXEC_FAILED,
    )


@contextmanager
def open_workspace(
    estimate: WorkspaceEstimate,
    *,
    scratch_root: Path,
) -> Iterator[ActiveWorkspace | None]:
    """Yield a btrfs workspace, or no adapter when auto-detection is absent.

    cowtree owns root validation, including the non-root owner check and the
    mount-table proof. Its absence log is emitted once by `detect()`.

    A valid area whose detected backend is not btrfs (an XFS reflink area, or
    btrfs without unprivileged subvolume deletion) is an optimization that
    does not apply here, not a lane failure: Assay uses the plain path and
    says so once. A run whose backend changes after detection still refuses.
    """
    environment = dict(os.environ)
    if (
        _ENV_ROOT not in environment
        and environment.get(_ENV_MODE, "auto") in {"auto", "off"}
    ):
        # B196: no CoW area is configured, so cowtree would answer "none".
        # Do not import it at all: a missing sibling package must not become
        # an import error before any verdict. (An invalid COWTREE_MODE still
        # goes through cowtree so it is refused there.)
        yield None
        return
    cowtree = _load_cowtree()
    body_finished = False
    try:
        area = cowtree.detect(environ=environment)
        if area is None:
            yield None
            return
        if not _is_btrfs(area.caps):
            sys.stderr.write(
                "Assay advisory: copy-on-write area uses the "
                f"{area.caps.backend} backend on {area.caps.fs_type}; "
                "Assay snapshot clones need btrfs, using plain snapshots\n"
            )
            yield None
            return
        _collect_dead_runs(cowtree, area)
        cow_estimate = cowtree.SpaceEstimate(
            ingest_bytes=estimate.ingest_bytes,
            per_clone_bytes=estimate.per_clone_bytes,
            max_live_clones=0,
        )
        run = cowtree.open_run(
            "assay",
            estimate=cow_estimate,
            environ=environment,
            plain_dir=os.fspath(scratch_root),
        )
        with run:
            _require_btrfs(cowtree, run.caps)
            workspace = _CowRunWorkspace(
                run,
                per_clone_bytes=estimate.per_clone_bytes,
                cowtree=cowtree,
            )
            workspace.reserve(max_live_clones=estimate.max_live_clones)
            yield workspace
            body_finished = True
    except cowtree.CowError as exc:
        if body_finished:
            # B199: only Run.close failed, after the measurement completed.
            raise WorkspaceCleanupError(
                f"copy-on-write workspace cleanup failed: {exc}",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.EXEC_FAILED,
            ) from exc
        raise _as_assay_error(exc) from exc
