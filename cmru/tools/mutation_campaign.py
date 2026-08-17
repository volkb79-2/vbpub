#!/usr/bin/env python3
"""Run Assay-discovered source-diff mutants against CMRU in disposable copies.

This is a consumer-side evidence runner, not an Assay rigor claim.  Assay's
published R2 runner correctly refuses vbpub's full tree because Topos contains
tracked hostile absolute-symlink fixtures.  The runner still reuses the
consumer-pinned Assay release for its Python mutation vocabulary, diff parser,
and byte-exact mutation sites; only the disposable execution directory is
scoped to CMRU.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Sequence


OPERATORS = (
    "python:compare-swap",
    "python:boolop-swap",
    "python:bool-const-flip",
    "python:falsy-swap",
)

# These tracked repository-root artifacts are part of CMRU's checked-in test
# contract.  Keep the list explicit: copying a whole monorepo would reintroduce
# the unrelated hostile Topos fixtures that this consumer-scoped runner avoids.
ROOT_CMRU_ARTIFACTS = (
    "cmru.orchestration.sample.toml",
    "cmru.orchestration.toml",
    "cmru.project.sample.toml",
    "cmru.release.sh",
)


@dataclass(frozen=True)
class Result:
    path: str
    line: int
    operator: str
    description: str
    exit_code: int
    outcome: str


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assay-zipapp", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--max-mutants", type=int, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--require-candidates", action="store_true")
    parser.add_argument("test_argv", nargs=argparse.REMAINDER)
    return parser


def _require_directory(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_dir() or resolved.is_symlink():
        raise ValueError(f"{label} must be a real directory: {path}")
    return resolved


def _write_evidence(path: Path, document: dict[str, Any]) -> None:
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError(f"evidence parent must be a real directory: {path.parent}")
    parent = parent.resolve()
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=parent, prefix=".mutation-", delete=False
    ) as stream:
        temporary = Path(stream.name)
        json.dump(document, stream, indent=2, sort_keys=True)
        stream.write("\n")
    try:
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _run(argv: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def _relative_project_path(path: str, *, repo_root: Path, project_root: Path) -> Path:
    source = (repo_root / PurePosixPath(path)).resolve()
    try:
        return source.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(f"mutation target {path!r} is outside project root") from exc


def _copy_project_fixture(*, repo_root: Path, project_root: Path, workspace: Path) -> Path:
    """Copy the tested project plus its checked-in root CMRU artifacts.

    CMRU's own tests intentionally locate these files relative to the project
    directory's parent.  Copying just ``cmru/`` changes that contract and can
    make every mutation appear killed by a broken control instead.
    """
    for name in ROOT_CMRU_ARTIFACTS:
        artifact = repo_root / name
        if not artifact.is_file() or artifact.is_symlink():
            raise ValueError(f"required root CMRU artifact is not a real file: {artifact}")
        shutil.copy2(artifact, workspace / artifact.name)
    copied = workspace / project_root.name
    shutil.copytree(project_root, copied, symlinks=True)
    return copied


def run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    test_argv = list(args.test_argv)
    if test_argv[:1] == ["--"]:
        test_argv = test_argv[1:]
    if not test_argv:
        raise ValueError("test argv is required after '--'")
    if args.max_mutants < 1:
        raise ValueError("--max-mutants must be positive")

    zipapp = args.assay_zipapp.resolve()
    if not zipapp.is_file() or zipapp.is_symlink():
        raise ValueError(f"--assay-zipapp must be a real file: {args.assay_zipapp}")
    repo_root = _require_directory(args.repo_root, "--repo-root")
    project_root = _require_directory(args.project_root, "--project-root")
    try:
        project_prefix = project_root.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError("--project-root must be inside --repo-root") from exc

    sys.path.insert(0, str(zipapp))
    from assay import diff, git, mutation
    from assay.adapters.python import PythonAdapter

    base = git.resolve_base(repo_root, args.base)
    head = git.head_rev(repo_root)
    diff_text = git.run(repo_root, "diff", "--unified=0", base, head)
    added = diff.parse_added_lines(diff_text)
    adapter = PythonAdapter()
    targets = mutation.resolve_mutation_targets(
        added,
        repo_top=repo_root,
        source_root_paths=(project_root / "src" / "cmru",),
        adapter=adapter,
        read_source_text=lambda path: (repo_root / path).read_text(encoding="utf-8"),
    )
    jobs = mutation.collect_mutation_sites(
        targets, adapter=adapter, operators=OPERATORS, limit=args.max_mutants + 1
    )
    if jobs == "UNSUPPORTED":
        raise RuntimeError("the pinned Assay Python adapter does not support mutation")
    if len(jobs) > args.max_mutants:
        raise RuntimeError(
            f"observed {len(jobs)} mutation candidates, above declared maximum "
            f"{args.max_mutants}; refusing a partial sample"
        )
    if args.require_candidates and not jobs:
        raise RuntimeError("the declared source diff produced no mutation candidates")

    with tempfile.TemporaryDirectory(prefix="cmru-mutation-baseline-") as temporary:
        baseline_root = _copy_project_fixture(
            repo_root=repo_root, project_root=project_root, workspace=Path(temporary)
        )
        baseline = _run(test_argv, cwd=baseline_root)
    if baseline.returncode != 0:
        raise RuntimeError(
            f"the known-good mutation control failed with exit {baseline.returncode}: "
            f"{baseline.stdout[-1000:]}"
        )

    results: list[Result] = []
    for job in jobs:
        relative = _relative_project_path(
            job.path, repo_root=repo_root, project_root=project_root
        )
        with tempfile.TemporaryDirectory(prefix="cmru-mutation-") as temporary:
            candidate_root = _copy_project_fixture(
                repo_root=repo_root, project_root=project_root, workspace=Path(temporary)
            )
            target = candidate_root / relative
            original = target.read_text(encoding="utf-8")
            if original != job.original_text:
                raise RuntimeError(f"copied mutation target differs from pinned source: {job.path}")
            target.write_bytes(job.site.apply(original.encode("utf-8")))
            completed = _run(test_argv, cwd=candidate_root)
        outcome = "killed" if completed.returncode == 1 else "survived" if completed.returncode == 0 else "crashed"
        results.append(
            Result(
                path=job.path,
                line=job.site.lineno,
                operator=job.site.operator,
                description=job.site.description,
                exit_code=completed.returncode,
                outcome=outcome,
            )
        )

    document = {
        "schema_version": 1,
        "tool": "cmru mutation campaign using pinned Assay mutation sites",
        "base": base,
        "head": head,
        "project_prefix": project_prefix.as_posix() or ".",
        "max_mutants": args.max_mutants,
        "operators": list(OPERATORS),
        "test_argv": test_argv,
        "candidate_count": len(jobs),
        "baseline_exit_code": baseline.returncode,
        "results": [asdict(result) for result in results],
    }
    _write_evidence(args.evidence, document)
    survivors = [result for result in results if result.outcome != "killed"]
    if survivors:
        raise RuntimeError(
            "mutation campaign did not kill every candidate: "
            + ", ".join(f"{item.path}:{item.line}:{item.operator}:{item.outcome}" for item in survivors)
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
