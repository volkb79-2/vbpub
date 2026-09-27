"""Materialise the complete disposable source closure used by CMRU gates."""

from __future__ import annotations

import shutil
from pathlib import Path


ROOT_CMRU_ARTIFACTS = (
    "cmru.orchestration.sample.toml",
    "cmru.orchestration.toml",
    "cmru.project.sample.toml",
)
SHARED_LIBRARIES = (
    Path("libraries/cli-extended"),
    Path("libraries/worktree"),
)
EXTERNAL_DOC_ARTIFACTS = (
    Path("wheel-builder/Dockerfile"),
    Path("docs/ciu-vs-cmru.md"),
    Path("docs/RELEASE-TOOLING.md"),
    Path("docs/plan-cmru-release-modes.md"),
    Path("run-gate-project/CONSUMERS.md"),
)


def copy_project_fixture(*, repo_root: Path, project_root: Path, workspace: Path) -> Path:
    """Copy CMRU, its root fixtures, and the shared worktree library.

    The project tests deliberately exercise source-checkout imports for CMRU's
    shared libraries. A disposable fixture that copies only ``cmru/`` makes its
    known-good control fail before the intended mutation/canary is applied,
    which turns every later result into false evidence.
    """
    for name in ROOT_CMRU_ARTIFACTS:
        artifact = repo_root / name
        if not artifact.is_file() or artifact.is_symlink():
            raise ValueError(f"required root CMRU artifact is not a real file: {artifact}")
        shutil.copy2(artifact, workspace / artifact.name)

    for relative in EXTERNAL_DOC_ARTIFACTS:
        artifact = repo_root / relative
        if not artifact.is_file() or artifact.is_symlink():
            raise ValueError(f"required linked CMRU document is not a real file: {artifact}")
        destination = workspace / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(artifact, destination)

    for relative in SHARED_LIBRARIES:
        library_source = repo_root / relative
        if not library_source.is_dir() or library_source.is_symlink():
            raise ValueError(
                f"required shared CMRU library is not a real directory: {library_source}"
            )
        destination = workspace / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(library_source, destination, symlinks=True)

    copied = workspace / project_root.name
    shutil.copytree(project_root, copied, symlinks=True)
    return copied
