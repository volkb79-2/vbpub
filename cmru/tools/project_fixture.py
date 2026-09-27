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
ESTATE_CONFIG_FIXTURES = (
    Path("topos/cmru.toml"),
    Path("nyxloom/cmru.toml"),
)


def copy_project_fixture(*, repo_root: Path, project_root: Path, workspace: Path) -> Path:
    """Copy CMRU and the external files its full test suite reads.

    The project tests exercise source-checkout imports for CMRU's shared
    libraries. Estate configs are also read by adoption contract tests, so both
    the libraries and configs must be present in the known-good control and
    every mutated candidate.
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

    for relative in ESTATE_CONFIG_FIXTURES:
        artifact = repo_root / relative
        if not artifact.is_file() or artifact.is_symlink():
            raise ValueError(f"required estate config is not a real file: {artifact}")
        destination = workspace / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(artifact, destination)

    copied = workspace / project_root.name
    shutil.copytree(project_root, copied, symlinks=True)
    return copied
