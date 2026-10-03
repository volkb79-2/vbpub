#!/usr/bin/env python3
"""Run Assay-discovered source-diff mutants against CMRU in disposable copies.

This is a consumer-side evidence runner, not an Assay rigor claim.  Assay's
published R2 runner correctly refuses vbpub's full tree because Topos contains
tracked hostile absolute-symlink fixtures.  The runner reuses the Assay source
selected for this worktree for its Python mutation vocabulary, diff parser, and
byte-exact mutation sites; only the disposable execution directory is scoped
to CMRU.  The evidence records the resolved Assay version and source commit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

try:
    from . import project_fixture
    from .project_fixture import copy_project_fixture
except ImportError:  # direct ``python tools/mutation_campaign.py`` execution
    import project_fixture
    from project_fixture import copy_project_fixture

OPERATORS = (
    "python:compare-swap",
    "python:boolop-swap",
    "python:bool-const-flip",
    "python:falsy-swap",
)

@dataclass(frozen=True)
class Result:
    path: str
    line: int
    operator: str
    description: str
    exit_code: int
    outcome: str
    termination: str
    elapsed_seconds: float


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--assay-source",
        type=Path,
        help="Assay source tree (default: <repo-root>/assay)",
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--max-mutants", type=int, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, required=True)
    parser.add_argument("--resume", action="store_true")
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


def _run(
    argv: Sequence[str], *, cwd: Path, timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), cwd=cwd, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=timeout_seconds,
    )


def _append_progress(path: Path, event: dict[str, Any]) -> None:
    if path.is_symlink() or not path.parent.is_dir() or path.parent.is_symlink():
        raise ValueError(f"progress parent must be a real directory: {path.parent}")
    with path.open("a", encoding="utf-8") as stream:
        json.dump(event, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _job_identity(job: Any) -> tuple[str, int, str, str]:
    return job.path, job.site.lineno, job.site.operator, job.site.description


def _result_identity(result: dict[str, Any]) -> tuple[str, int, str, str]:
    return result["path"], result["line"], result["operator"], result["description"]


def _candidate_result(
    job: Any, *, returncode: int | None, elapsed_seconds: float, timed_out: bool,
) -> Result:
    if timed_out:
        exit_code = 124
        outcome = "killed"
        termination = "timeout"
    else:
        assert returncode is not None
        exit_code = returncode
        outcome = "killed" if returncode == 1 else "survived" if returncode == 0 else "crashed"
        termination = "exit"
    return Result(
        path=job.path,
        line=job.site.lineno,
        operator=job.site.operator,
        description=job.site.description,
        exit_code=exit_code,
        outcome=outcome,
        termination=termination,
        elapsed_seconds=round(elapsed_seconds, 3),
    )


def _changed_since(
    repo_root: Path,
    older: str,
    newer: str,
    paths: Sequence[str],
    *,
    assay_git,
) -> bool:
    """Compare resume inputs through Assay's sanitized Git boundary.

    Assay discovers the campaign's source diff with replacement refs disabled.
    A raw Git resume check can therefore mistake a replaced commit for an
    unchanged tree and reuse killed results for a different candidate set.
    """
    from assay.errors import AssayError

    remaining = lambda: math.inf
    try:
        if not assay_git.is_ancestor(
            repo_root, older, newer, remaining=remaining,
        ):
            return True
        return any(
            not assay_git.path_is_current(
                repo_root, older, newer, path, remaining=remaining,
            )
            for path in paths
        )
    except AssayError as exc:
        raise RuntimeError(
            f"could not verify mutation resume history through Assay's Git boundary: {exc}"
        ) from exc


def _campaign_tree_status(repo_root: Path, project_prefix: Path) -> str:
    prefix = project_prefix.as_posix().rstrip("/")
    paths = [prefix or "."]
    paths.append(f":(exclude){prefix + '/' if prefix else ''}.assay")
    paths.extend(project_fixture.ROOT_CMRU_ARTIFACTS)
    paths.extend(path.as_posix() for path in project_fixture.EXTERNAL_DOC_ARTIFACTS)
    paths.extend(path.as_posix() for path in project_fixture.SHARED_LIBRARIES)
    paths.extend(path.as_posix() for path in project_fixture.ESTATE_CONFIG_FIXTURES)
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", *paths],
        cwd=repo_root, check=False, text=True, capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"could not inspect mutation test-closure state: {result.stderr.strip()}")
    return result.stdout


def _test_manifest(repo_root: Path, project_prefix: Path) -> dict[str, Any]:
    """Fingerprint tests and every tracked file copied into candidate fixtures."""
    project_root = repo_root / project_prefix
    pathspecs = [project_prefix.as_posix() or "."]
    pathspecs.extend(project_fixture.ROOT_CMRU_ARTIFACTS)
    pathspecs.extend(path.as_posix() for path in project_fixture.EXTERNAL_DOC_ARTIFACTS)
    pathspecs.extend(path.as_posix() for path in project_fixture.SHARED_LIBRARIES)
    pathspecs.extend(path.as_posix() for path in project_fixture.ESTATE_CONFIG_FIXTURES)
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", *pathspecs],
        cwd=repo_root, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if listed.returncode != 0:
        raise RuntimeError(f"could not list mutation test-closure files: {listed.stderr.decode(errors='replace').strip()}")
    files: dict[str, str] = {}
    fixture_files: dict[str, str] = {}
    test_prefix = (project_prefix / "tests").as_posix().rstrip("/") + "/"
    pyproject_relative = (project_prefix / "pyproject.toml").as_posix()
    pyproject_sha256 = None
    for raw_path in listed.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = raw_path.decode("utf-8")
        path = repo_root / relative
        if path.is_symlink():
            fingerprint = b"symlink\0" + os.fsencode(os.readlink(path))
        elif path.is_file():
            fingerprint = b"file\0" + path.read_bytes()
        else:
            raise ValueError(
                f"mutation test-closure input must be a regular file or symlink: {relative}"
            )
        digest = hashlib.sha256(fingerprint).hexdigest()
        if relative.startswith(test_prefix):
            files[relative] = digest
        elif relative == pyproject_relative:
            pyproject_sha256 = digest
        else:
            fixture_files[relative] = digest
    if pyproject_sha256 is None:
        pyproject = project_root / "pyproject.toml"
        if pyproject.is_symlink() or not pyproject.is_file():
            raise ValueError(f"mutation pytest configuration must be a regular file: {pyproject}")
        pyproject_sha256 = hashlib.sha256(pyproject.read_bytes()).hexdigest()
    return {
        "files": files,
        "pyproject_sha256": pyproject_sha256,
        "fixture_files": fixture_files,
    }


def _test_manifest_matches(previous: Any, current: dict[str, Any]) -> bool:
    """Reuse mutation outcomes only for the identical tests and copied fixtures."""
    return isinstance(previous, dict) and previous == current


def _resume_results(
    *,
    evidence_path: Path,
    repo_root: Path,
    resume: bool,
    base: str,
    head: str,
    project_prefix: Path,
    assay_source_commit: str,
    assay_source_prefix: Path | None = None,
    assay_git,
    test_argv: Sequence[str],
    jobs: Sequence[Any],
    max_mutants: int,
    timeout_seconds: float,
) -> list[dict[str, Any] | None]:
    if evidence_path.is_symlink():
        raise ValueError(f"mutation evidence must not be a symlink: {evidence_path}")
    if not evidence_path.exists():
        return [None] * len(jobs)
    if not resume:
        raise ValueError(f"mutation evidence already exists; pass --resume to continue: {evidence_path}")
    if not evidence_path.is_file() or evidence_path.is_symlink():
        raise ValueError(f"--resume requires a regular mutation evidence file: {evidence_path}")
    try:
        previous = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read mutation resume evidence: {evidence_path}") from exc
    previous_head = previous.get("head") if isinstance(previous, dict) else None
    source_paths = [(project_prefix / "src").as_posix()]
    test_paths = [
        (project_prefix / "tests").as_posix(),
        (project_prefix / "pyproject.toml").as_posix(),
    ]
    if not isinstance(previous_head, str):
        raise ValueError("mutation resume evidence has no prior head commit")
    if _changed_since(
        repo_root, previous_head, head, source_paths, assay_git=assay_git,
    ):
        raise ValueError("mutation resume evidence predates a product-source change")
    if _changed_since(
        repo_root, previous_head, head, [test_paths[-1]], assay_git=assay_git,
    ):
        raise ValueError("mutation resume evidence predates a pytest configuration change")
    if _campaign_tree_status(repo_root, project_prefix):
        raise ValueError("mutation resume requires committed source, tests, and pytest configuration")
    current_test_manifest = _test_manifest(repo_root, project_prefix)
    previous_test_manifest = previous.get("test_manifest")
    if previous_test_manifest is None and previous_head == head:
        # Legacy evidence is resumable at the identical clean commit only. New
        # runs persist this fingerprint so the exact test closure is bound.
        previous_test_manifest = current_test_manifest
    elif (
        isinstance(previous_test_manifest, dict)
        and "fixture_files" not in previous_test_manifest
        and previous_head == head
    ):
        # Older schema-1 manifests did not bind the copied test fixture closure.
        # At the exact same clean commit, the current closure is the same tree.
        previous_test_manifest = {
            **previous_test_manifest,
            "fixture_files": current_test_manifest["fixture_files"],
        }
    if not _test_manifest_matches(previous_test_manifest, current_test_manifest):
        raise ValueError(
            "mutation resume requires an unchanged test suite and fixture closure; "
            "start a new campaign after any test or fixture change"
        )
    previous_assay_source_commit = previous.get("assay_source_commit")
    if previous_assay_source_commit != assay_source_commit:
        assay_paths = (
            [
                (assay_source_prefix / "src").as_posix(),
                (assay_source_prefix / "pyproject.toml").as_posix(),
            ]
            if assay_source_prefix is not None else []
        )
        if not assay_paths or _changed_since(
            repo_root, previous_assay_source_commit, assay_source_commit, assay_paths,
            assay_git=assay_git,
        ):
            raise ValueError("mutation resume evidence predates an Assay source change")
    if (
        previous.get("base") != base
        or previous.get("candidate_count") != len(jobs)
        or previous.get("max_mutants") != max_mutants
        or previous.get("project_prefix") != (project_prefix.as_posix() or ".")
        or previous.get("operators") != list(OPERATORS)
        or previous.get("test_argv") != list(test_argv)
    ):
        raise ValueError("mutation resume evidence does not match this campaign")
    prior_results = previous.get("results")
    if not isinstance(prior_results, list) or len(prior_results) > len(jobs):
        raise ValueError("mutation resume evidence has an invalid result list")
    reusable: list[dict[str, Any] | None] = [None] * len(jobs)
    for index, prior in enumerate(prior_results):
        if prior is None:
            continue
        if not isinstance(prior, dict) or _result_identity(prior) != _job_identity(jobs[index]):
            raise ValueError(f"mutation resume evidence does not match candidate {index}")
        if (
            prior.get("outcome") == "killed"
            and (
                (prior.get("termination", "exit") == "exit" and prior.get("exit_code") == 1)
                or (
                    prior.get("termination") == "timeout"
                    and previous.get("timeout_seconds") == timeout_seconds
                )
            )
        ):
            reusable[index] = prior
    return reusable


def _relative_project_path(path: str, *, repo_root: Path, project_root: Path) -> Path:
    source = (repo_root / PurePosixPath(path)).resolve()
    try:
        return source.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(f"mutation target {path!r} is outside project root") from exc


def run(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    test_argv = list(args.test_argv)
    if test_argv[:1] == ["--"]:
        test_argv = test_argv[1:]
    if not test_argv:
        raise ValueError("test argv is required after '--'")
    if args.max_mutants < 1:
        raise ValueError("--max-mutants must be positive")
    if args.timeout_seconds <= 0:
        raise ValueError("--timeout-seconds must be positive")

    repo_root = _require_directory(args.repo_root, "--repo-root")
    project_root = _require_directory(args.project_root, "--project-root")
    assay_source = _require_directory(
        args.assay_source or repo_root / "assay", "--assay-source"
    )
    if not (assay_source / "pyproject.toml").is_file():
        raise ValueError(f"--assay-source has no pyproject.toml: {assay_source}")
    if not (assay_source / "src" / "assay").is_dir():
        raise ValueError(f"--assay-source has no src/assay package: {assay_source}")
    try:
        project_prefix = project_root.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError("--project-root must be inside --repo-root") from exc
    try:
        assay_source_prefix = assay_source.relative_to(repo_root)
    except ValueError:
        assay_source_prefix = None
    if _campaign_tree_status(repo_root, project_prefix):
        raise ValueError("mutation campaign requires committed source, tests, and pytest configuration")

    sys.path.insert(0, str(assay_source / "src"))
    from assay import __version__ as assay_version
    from assay import diff, git, mutation
    from assay.adapters.python import PythonAdapter

    assay_source_commit = git.head_rev(assay_source)
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
        raise RuntimeError("the selected Assay Python adapter does not support mutation")
    if len(jobs) > args.max_mutants:
        raise RuntimeError(
            f"observed {len(jobs)} mutation candidates, above declared maximum "
            f"{args.max_mutants}; refusing a partial sample"
        )
    if args.require_candidates and not jobs:
        raise RuntimeError("the declared source diff produced no mutation candidates")

    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="cmru-mutation-baseline-") as temporary:
            baseline_root = copy_project_fixture(
                repo_root=repo_root, project_root=project_root, workspace=Path(temporary)
            )
            baseline = _run(test_argv, cwd=baseline_root, timeout_seconds=args.timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"the known-good mutation control exceeded {args.timeout_seconds:g} seconds"
        ) from exc
    if baseline.returncode != 0:
        raise RuntimeError(
            f"the known-good mutation control failed with exit {baseline.returncode}: "
            f"{baseline.stdout[-1000:]}"
        )

    results = _resume_results(
        evidence_path=args.evidence,
        repo_root=repo_root,
        resume=args.resume,
        base=base,
        head=head,
        project_prefix=project_prefix,
        assay_source_commit=assay_source_commit,
        assay_source_prefix=assay_source_prefix,
        assay_git=git,
        test_argv=test_argv,
        jobs=jobs,
        max_mutants=args.max_mutants,
        timeout_seconds=args.timeout_seconds,
    )
    document: dict[str, Any] = {
        "schema_version": 1,
        "tool": "cmru mutation campaign using worktree Assay mutation sites",
        "assay_version": assay_version,
        "assay_source": str(assay_source),
        "assay_source_commit": assay_source_commit,
        "base": base,
        "head": head,
        "project_prefix": project_prefix.as_posix() or ".",
        "max_mutants": args.max_mutants,
        "timeout_seconds": args.timeout_seconds,
        "operators": list(OPERATORS),
        "test_argv": test_argv,
        "test_manifest": _test_manifest(repo_root, project_prefix),
        "candidate_count": len(jobs),
        "baseline_exit_code": baseline.returncode,
        "status": "running",
        "results": results,
    }
    _write_evidence(args.evidence, document)
    _append_progress(args.progress, {
        "event": "start",
        "base": base,
        "head": head,
        "candidate_total": len(jobs),
        "resumed_total": sum(item is not None for item in results),
        "timeout_seconds": args.timeout_seconds,
    })

    for index, job in enumerate(jobs):
        existing = results[index]
        if existing is not None and existing.get("outcome") == "killed":
            _append_progress(args.progress, {
                "event": "candidate",
                "candidate_index": index,
                "candidate_total": len(jobs),
                "path": job.path,
                "line": job.site.lineno,
                "operator": job.site.operator,
                "outcome": "killed",
                "reused": True,
                "elapsed_seconds": time.monotonic() - started,
            })
            continue
        relative = _relative_project_path(
            job.path, repo_root=repo_root, project_root=project_root
        )
        with tempfile.TemporaryDirectory(prefix="cmru-mutation-") as temporary:
            candidate_root = copy_project_fixture(
                repo_root=repo_root, project_root=project_root, workspace=Path(temporary)
            )
            target = candidate_root / relative
            original = target.read_text(encoding="utf-8")
            if original != job.original_text:
                raise RuntimeError(f"copied mutation target differs from pinned source: {job.path}")
            target.write_bytes(job.site.apply(original.encode("utf-8")))
            candidate_started = time.monotonic()
            try:
                completed = _run(
                    test_argv, cwd=candidate_root, timeout_seconds=args.timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                result = _candidate_result(
                    job, returncode=None,
                    elapsed_seconds=time.monotonic() - candidate_started,
                    timed_out=True,
                )
            else:
                result = _candidate_result(
                    job, returncode=completed.returncode,
                    elapsed_seconds=time.monotonic() - candidate_started,
                    timed_out=False,
                )
        results[index] = asdict(result)
        document["results"] = results
        _write_evidence(args.evidence, document)
        _append_progress(args.progress, {
            "event": "candidate",
            "candidate_index": index,
            "candidate_total": len(jobs),
            "path": result.path,
            "line": result.line,
            "operator": result.operator,
            "description": result.description,
            "outcome": result.outcome,
            "termination": result.termination,
            "elapsed_seconds": result.elapsed_seconds,
            "campaign_elapsed_seconds": round(time.monotonic() - started, 3),
        })

    survivors = [result for result in results if result is not None and result["outcome"] != "killed"]
    document["status"] = "failed" if survivors else "passed"
    _write_evidence(args.evidence, document)
    _append_progress(args.progress, {
        "event": "complete",
        "status": document["status"],
        "candidate_total": len(jobs),
        "survivor_total": len(survivors),
        "campaign_elapsed_seconds": round(time.monotonic() - started, 3),
    })
    if survivors:
        raise RuntimeError(
            "mutation campaign did not kill every candidate: "
            + ", ".join(
                f"{item['path']}:{item['line']}:{item['operator']}:{item['outcome']}"
                for item in survivors
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
