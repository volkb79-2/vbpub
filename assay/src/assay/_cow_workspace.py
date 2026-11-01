"""Lazy cowtree adapter for Assay's reflink-only Level A workspace path."""

from __future__ import annotations

import importlib
import os
import secrets
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .errors import AssayError, Outcome, ReasonCode
from .workspace import ActiveWorkspace, WorkspaceEstimate


def _load_cowtree():
    """Load the same-repository cowtree package only when CoW is requested."""
    return importlib.import_module("cowtree")


class _CowRunWorkspace:
    def __init__(self, run) -> None:
        self._run = run

    def ingest_tree(self, source: Path, name: str) -> Path:
        return Path(self._run.ingest_tree(source, name))

    def make_tree(self) -> Path:
        return Path(self._run.make_tree(f"assay-snapshot-{secrets.token_hex(12)}"))

    def clone_file(self, source: Path, destination: Path) -> None:
        self._run.clone_file(source, destination)

    def delete_tree(self, path: Path) -> None:
        self._run.delete_tree(path)


def _require_btrfs(cowtree, caps) -> None:
    if caps.fs_type != "btrfs" or caps.backend != "btrfs":
        raise cowtree.RootInvalid(
            "Assay CoW workspaces require a btrfs filesystem with the btrfs backend"
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
    """
    cowtree = _load_cowtree()
    environment = dict(os.environ)
    try:
        area = cowtree.detect(environ=environment)
        if area is None:
            yield None
            return
        _require_btrfs(cowtree, area.caps)
        cow_estimate = cowtree.SpaceEstimate(
            ingest_bytes=estimate.ingest_bytes,
            per_clone_bytes=estimate.per_clone_bytes,
            max_live_clones=estimate.max_live_clones,
        )
        run = cowtree.open_run(
            "assay",
            estimate=cow_estimate,
            environ=environment,
            plain_dir=os.fspath(scratch_root),
        )
        with run:
            _require_btrfs(cowtree, run.caps)
            yield _CowRunWorkspace(run)
    except cowtree.CowError as exc:
        raise _as_assay_error(exc) from exc
