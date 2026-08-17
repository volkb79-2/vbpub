#!/usr/bin/env python3
"""Prove CMRU's real coverage gate rejects an intentionally uncovered line."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Sequence


ROOT_CMRU_ARTIFACTS = (
    "cmru.orchestration.sample.toml",
    "cmru.orchestration.toml",
    "cmru.project.sample.toml",
    "cmru.release.sh",
)
EXPECTED_REASON = "Required test coverage of 100% not reached"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("test_argv", nargs=argparse.REMAINDER)
    return parser


def _directory(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_dir() or resolved.is_symlink():
        raise ValueError(f"{label} must be a real directory: {path}")
    return resolved


def _copy_project(*, repo_root: Path, project_root: Path, workspace: Path) -> Path:
    for name in ROOT_CMRU_ARTIFACTS:
        artifact = repo_root / name
        if not artifact.is_file() or artifact.is_symlink():
            raise ValueError(f"required root CMRU artifact is not a real file: {artifact}")
        shutil.copy2(artifact, workspace / name)
    copied = workspace / project_root.name
    shutil.copytree(project_root, copied, symlinks=True)
    return copied


def _run(argv: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def _write(path: Path, value: dict[str, Any]) -> None:
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError(f"evidence parent must be a real directory: {path.parent}")
    parent = parent.resolve()
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=parent, prefix=".canary-", delete=False
    ) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    try:
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    test_argv = list(args.test_argv)
    if test_argv[:1] == ["--"]:
        test_argv = test_argv[1:]
    if not test_argv:
        raise ValueError("test argv is required after '--'")
    repo_root = _directory(args.repo_root, "--repo-root")
    project_root = _directory(args.project_root, "--project-root")
    target = args.target
    if target.is_absolute() or ".." in target.parts:
        raise ValueError("--target must be a normalized project-relative path")
    source = project_root / target
    if not source.is_file() or source.is_symlink():
        raise ValueError(f"--target must be a real source file: {target}")

    with tempfile.TemporaryDirectory(prefix="cmru-coverage-canary-") as temporary:
        copied = _copy_project(repo_root=repo_root, project_root=project_root, workspace=Path(temporary))
        control = _run(test_argv, cwd=copied)
        if control.returncode != 0:
            raise RuntimeError(f"known-good canary control failed with exit {control.returncode}")
        target_copy = copied / target
        target_copy.write_text(
            target_copy.read_text(encoding="utf-8")
            + "\n\ndef _cmru_coverage_canary_unreached() -> None:\n    return None\n",
            encoding="utf-8",
        )
        transformed = _run(test_argv, cwd=copied)

    reason_observed = EXPECTED_REASON in transformed.stdout
    document = {
        "schema_version": 1,
        "mechanism": "uncovered-line",
        "target": target.as_posix(),
        "test_argv": test_argv,
        "control_exit_code": control.returncode,
        "transformed_exit_code": transformed.returncode,
        "expected_reason": EXPECTED_REASON,
        "observed_expected_reason": reason_observed,
    }
    _write(args.evidence, document)
    if transformed.returncode != 1 or not reason_observed:
        raise RuntimeError(
            "coverage canary did not fail for the expected coverage reason: "
            f"exit={transformed.returncode}, expected_reason_seen={reason_observed}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
