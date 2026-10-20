#!/usr/bin/env python3
"""Run Assay-discovered source-diff mutants against CMRU in disposable copies.

This is a consumer-side evidence runner, not an Assay rigor claim.  Assay's
published R2 runner correctly refuses vbpub's full tree because Topos contains
tracked hostile absolute-symlink fixtures.  The runner reuses the Assay source
selected for this worktree for its Python mutation vocabulary, diff parser, and
byte-exact mutation sites; only the disposable execution directory is scoped
to CMRU. The evidence records the Assay distribution version and source commit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
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
_CANDIDATE_CLEANUP_SECONDS = 10.0
_CANDIDATE_OUTPUT_TAIL_BYTES = 1 << 20
_SETUPTOOLS_SOURCE_KEYS = frozenset({
    "include-package-data",
    "package-data",
    "package-dir",
    "packages",
})
_SETUPTOOLS_FIND_KEYS = frozenset({"where", "include", "exclude", "namespaces"})


class CandidateIsolationError(RuntimeError):
    """The candidate process tree could not be proven empty and reaped."""


@dataclass(frozen=True)
class CommandRun:
    returncode: int
    stdout: str
    descendants_killed: int
    process_tree_evidence: dict[str, Any]


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
    descendants_killed: int = 0
    resource_limit_evidence: dict[str, Any] | None = None
    process_tree_evidence: dict[str, Any] | None = None


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


def _read_output_tail(stream) -> str:
    size = os.fstat(stream.fileno()).st_size
    stream.seek(max(0, size - _CANDIDATE_OUTPUT_TAIL_BYTES))
    return stream.read().decode("utf-8", errors="replace")


def _run(
    argv: Sequence[str], *, cwd: Path, timeout_seconds: float,
    output_dir: Path | None = None,
) -> CommandRun:
    """Run one command and prove its descendants are gone before returning."""
    try:
        from assay.process_tree import ProcessTreeError, ProcessTreeWindow
    except ImportError as exc:
        raise CandidateIsolationError(
            "the selected Assay source does not provide process-tree isolation"
        ) from exc

    command = list(argv)
    if output_dir is not None:
        output_dir = _require_directory(output_dir, "candidate output directory")
    with tempfile.TemporaryDirectory(prefix="cmru-mutation-pycache-") as pycache:
        child_env = os.environ.copy()
        child_env["PYTHONPYCACHEPREFIX"] = pycache
        with tempfile.TemporaryFile(mode="w+b", dir=output_dir) as output:
            window = ProcessTreeWindow(cleanup_seconds=_CANDIDATE_CLEANUP_SECONDS)
            try:
                with window:
                    try:
                        process = window.spawn(
                            command,
                            cwd=cwd,
                            env=child_env,
                            stdin=subprocess.DEVNULL,
                            stdout=output,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                        )
                    except OSError as exc:
                        window.finish()
                        raise CandidateIsolationError(
                            f"cannot start candidate command: {exc}"
                        ) from exc
                    try:
                        returncode = process.wait(timeout=timeout_seconds)
                    except subprocess.TimeoutExpired as exc:
                        evidence = window.finish(timed_out=True, force=True)
                        timeout_error = subprocess.TimeoutExpired(
                            command,
                            timeout_seconds,
                            output=_read_output_tail(output),
                        )
                        timeout_error.process_tree_evidence = evidence.to_dict()
                        timeout_error.descendants_killed = evidence.outliving_descendants
                        raise timeout_error from exc
                    except BaseException:
                        window.finish(force=True)
                        raise
                    evidence = window.finish()
                    stdout = _read_output_tail(output)
                    return CommandRun(
                        returncode=returncode,
                        stdout=stdout,
                        descendants_killed=evidence.outliving_descendants,
                        process_tree_evidence=evidence.to_dict(),
                    )
            except ProcessTreeError as exc:
                raise CandidateIsolationError(str(exc)) from exc


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
    descendants_killed: int = 0,
    resource_limit_evidence: Any | None = None,
    process_tree_evidence: dict[str, Any],
) -> Result:
    evidence = (
        resource_limit_evidence.to_dict()
        if resource_limit_evidence is not None else None
    )
    resource_limited = bool(
        resource_limit_evidence is not None
        and resource_limit_evidence.limit_hit
    )
    if timed_out:
        exit_code = 124
        outcome = "crashed"
        termination = "resource-limit" if resource_limited else "timeout"
    else:
        assert returncode is not None
        exit_code = returncode
        if resource_limited:
            outcome = "crashed"
            termination = "resource-limit"
        elif descendants_killed:
            outcome = "crashed"
            termination = "descendant-leak"
        else:
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
        descendants_killed=descendants_killed,
        resource_limit_evidence=evidence,
        process_tree_evidence=process_tree_evidence,
    )


def _read_resource_limit_counters():
    """Read the current tester cgroup, using the observer for hidden ancestors."""
    from assay.resource_limits import read_candidate_resource_counters

    return read_candidate_resource_counters(
        observer_socket_path=os.environ.get("ASSAY_RESOURCE_OBSERVER_SOCKET")
    )


def _resource_limit_evidence(before: Any, after: Any):
    """Compare exact cgroup-v2 counters; ambiguity is an infrastructure error."""
    from assay.resource_limits import ResourceLimitEvidence

    return ResourceLimitEvidence.between(before, after)


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
    paths.append(f":(exclude){prefix + '/' if prefix else ''}.run-gate")
    paths.extend(project_fixture.ROOT_CMRU_ARTIFACTS)
    paths.extend(path.as_posix() for path in project_fixture.EXTERNAL_DOC_ARTIFACTS)
    paths.extend(path.as_posix() for path in project_fixture.SHARED_LIBRARIES)
    paths.extend(path.as_posix() for path in project_fixture.ESTATE_CONFIG_FIXTURES)
    paths.extend(
        f":(exclude){relative.as_posix()}/{state_dir}"
        for relative in project_fixture.SHARED_LIBRARIES
        for state_dir in (".assay", ".run-gate")
    )
    status = _git_worktree_status(repo_root, paths, include_ignored=True)
    return "\0".join(
        record for record in status.split("\0")
        if record
        and record[3:] not in {
            f"{prefix}/coverage.json", f"{prefix}/junit-coverage.xml",
        }
        and not _is_generated_ignored_path(record, repo_root)
    )


def _git_command_context(worktree: Path) -> tuple[str, Path, Path, dict[str, str]]:
    """Resolve a worktree with Git itself, without importing the Assay boundary."""
    git_executable = shutil.which("git")
    if not git_executable:
        raise RuntimeError("Git is required to verify mutation source cleanliness")
    worktree = worktree.resolve(strict=True)
    env = {
        "PATH": os.path.dirname(git_executable),
        "HOME": "/nonexistent",
        "XDG_CONFIG_HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "LC_ALL": "C",
        "LANG": "C",
    }
    common = [
        git_executable,
        "-c", "core.fsmonitor=false",
        "-c", "core.excludesFile=/dev/null",
        "-c", "core.hooksPath=/dev/null",
        "-c", "core.pager=cat",
        "-c", "maintenance.auto=false",
        "-c", "gc.auto=0",
        "-c", "gc.autoDetach=false",
    ]
    try:
        top_result = subprocess.run(
            [*common, "-C", str(worktree), "rev-parse", "--show-toplevel"],
            check=False, text=True, capture_output=True, env=env, timeout=10,
        )
        git_dir_result = subprocess.run(
            [*common, "-C", str(worktree), "rev-parse", "--absolute-git-dir"],
            check=False, text=True, capture_output=True, env=env, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"could not resolve Git worktree for {worktree}") from exc
    if top_result.returncode or git_dir_result.returncode:
        detail = (top_result.stderr or git_dir_result.stderr).strip()
        raise RuntimeError(f"could not resolve Git worktree for {worktree}: {detail}")
    repo_top = Path(top_result.stdout.rstrip("\n")).resolve(strict=True)
    git_dir = Path(git_dir_result.stdout.rstrip("\n")).resolve(strict=True)
    try:
        worktree.relative_to(repo_top)
    except ValueError as exc:
        raise RuntimeError(f"resolved Git worktree does not contain {worktree}") from exc
    return git_executable, repo_top, git_dir, env


def _git_worktree_status(
    worktree: Path,
    pathspecs: Sequence[str],
    *,
    include_ignored: bool = False,
    include_untracked: bool = True,
) -> str:
    """Read status with a sanitized Git process, independent of imported Assay."""
    git_executable, repo_top, git_dir, env = _git_command_context(worktree)
    args = [
        git_executable,
        "-c", "core.fsmonitor=false",
        "-c", "core.excludesFile=/dev/null",
        "-c", "core.hooksPath=/dev/null",
        "-c", "core.pager=cat",
        "-c", "maintenance.auto=false",
        "-c", "gc.auto=0",
        "-c", "gc.autoDetach=false",
        f"--git-dir={git_dir}",
        f"--work-tree={repo_top}",
        "status", "--porcelain=v1", "-z",
        "--untracked-files=all" if include_untracked else "--untracked-files=no",
    ]
    args.extend(["--", *pathspecs])
    try:
        result = subprocess.run(
            args, cwd=repo_top, check=False, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"could not inspect Git worktree status at {repo_top}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"could not inspect Git worktree status at {repo_top}: {detail}")
    output = result.stdout
    try:
        index_flags = subprocess.run(
            [
                git_executable,
                "-c", "core.fsmonitor=false",
                f"--git-dir={git_dir}",
                f"--work-tree={repo_top}",
                "ls-files", "-v", "-z", "--", *pathspecs,
            ],
            cwd=repo_top, check=False, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"could not inspect Git index flags at {repo_top}") from exc
    if index_flags.returncode != 0:
        detail = index_flags.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"could not inspect Git index flags at {repo_top}: {detail}")
    if any(
        record and (record[:1].islower() or record[:1] == b"S")
        for record in index_flags.stdout.split(b"\0")
    ):
        raise RuntimeError(
            f"mutation inputs have assume-unchanged or skip-worktree Git index flags at {repo_top}"
        )
    if include_ignored:
        try:
            ignored = subprocess.run(
                [
                    git_executable,
                    "-c", "core.fsmonitor=false",
                    "-c", "core.excludesFile=/dev/null",
                    f"--git-dir={git_dir}",
                    f"--work-tree={repo_top}",
                    "ls-files", "--others", "--ignored", "--exclude-standard", "-z",
                    "--", *pathspecs,
                ],
                cwd=repo_top, check=False, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"could not inspect ignored Git worktree files at {repo_top}") from exc
        if ignored.returncode != 0:
            detail = ignored.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"could not inspect ignored Git worktree files at {repo_top}: {detail}")
        output += b"".join(b"!! " + path + b"\0" for path in ignored.stdout.split(b"\0") if path)
    return output.decode("utf-8", errors="replace")


def _git_head(worktree: Path) -> str:
    """Read HEAD with Git, without trusting Assay code from the measured source."""
    git_executable, repo_top, git_dir, env = _git_command_context(worktree)
    try:
        result = subprocess.run(
            [git_executable, f"--git-dir={git_dir}", f"--work-tree={repo_top}", "rev-parse", "HEAD"],
            cwd=repo_top, check=False, text=True, capture_output=True,
            env=env, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"could not read Git HEAD at {repo_top}") from exc
    if result.returncode != 0:
        raise RuntimeError(f"could not read Git HEAD at {repo_top}: {result.stderr.strip()}")
    return result.stdout.strip()


def _assay_source_package_paths(assay_source: Path) -> list[str]:
    """Derive every supported Assay source root from its build configuration."""
    source_root = assay_source.resolve()
    pyproject = source_root / "pyproject.toml"
    if pyproject.is_symlink() or not pyproject.is_file():
        raise RuntimeError(f"Assay's build configuration must be a regular file: {pyproject}")
    try:
        config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        build_system = config["build-system"]
        setuptools = config["tool"]["setuptools"]
        packages = setuptools["packages"]
        package_finder = packages["find"]
        where = package_finder["where"]
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError(
            f"could not derive Assay's packaged source roots from {pyproject}"
        ) from exc
    if not isinstance(build_system, dict) or build_system.get("build-backend") != "setuptools.build_meta":
        raise RuntimeError(
            f"Assay must use the supported setuptools.build_meta backend: {pyproject}"
        )
    if not isinstance(setuptools, dict) or not isinstance(packages, dict) or not isinstance(package_finder, dict):
        raise RuntimeError(f"Assay's setuptools package configuration is invalid: {pyproject}")
    unsupported = set(setuptools) - _SETUPTOOLS_SOURCE_KEYS
    if unsupported:
        names = ", ".join(sorted(str(name) for name in unsupported))
        raise RuntimeError(f"Assay uses unsupported setuptools settings: {names}")
    unsupported_find = set(package_finder) - _SETUPTOOLS_FIND_KEYS
    if unsupported_find:
        names = ", ".join(sorted(str(name) for name in unsupported_find))
        raise RuntimeError(f"Assay uses unsupported setuptools package finder settings: {names}")
    if set(packages) != {"find"}:
        raise RuntimeError(f"Assay must use the supported setuptools package finder: {pyproject}")
    package_data = setuptools.get("package-data", {})
    if not isinstance(package_data, dict):
        raise RuntimeError(f"Assay's setuptools package-data must be a mapping: {pyproject}")
    include_package_data = setuptools.get("include-package-data", True)
    if not isinstance(include_package_data, bool):
        raise RuntimeError(f"Assay's setuptools include-package-data must be boolean: {pyproject}")
    if not isinstance(where, list) or not where:
        raise RuntimeError(
            f"Assay's setuptools package finder must declare non-empty `where` roots: {pyproject}"
        )

    roots: list[str] = []

    def validate_root(raw: object) -> tuple[str, Path]:
        if not isinstance(raw, str) or not raw:
            raise RuntimeError(f"Assay declares an invalid package source root: {raw!r}")
        if any(character in raw for character in "*?[]:\\"):
            raise RuntimeError(f"Assay declares a non-literal package source root: {raw!r}")
        path = PurePosixPath(raw)
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"Assay declares an unsafe package source root: {raw!r}")
        normalized = path.as_posix()
        package_root = source_root / Path(*path.parts)
        try:
            resolved_root = package_root.resolve(strict=True)
            resolved_relative = resolved_root.relative_to(source_root).as_posix()
        except (OSError, ValueError) as exc:
            raise RuntimeError(
                f"Assay's declared package source root escapes or is missing: {package_root}"
            ) from exc
        if package_root.is_symlink():
            raise RuntimeError(f"Assay's declared package source root must not be a symlink: {package_root}")
        if resolved_relative != normalized:
            raise RuntimeError(
                f"Assay's declared package source root contains a symlinked path component: {package_root}"
            )
        if not package_root.is_dir():
            raise RuntimeError(f"Assay's declared package source root is missing: {package_root}")
        for directory, child_directories, files in os.walk(package_root, followlinks=False):
            if any((Path(directory) / name).is_symlink() for name in [*child_directories, *files]):
                raise RuntimeError(
                    f"Assay's declared package source root contains a symlink entry: {package_root}"
                )
        return normalized, resolved_root

    def add_root(raw: object, *, allow_existing: bool = False) -> None:
        normalized, _ = validate_root(raw)
        if normalized in roots:
            if allow_existing:
                return
            raise RuntimeError(f"Assay declares a duplicate package source root: {raw!r}")
        roots.append(normalized)

    for raw in where:
        add_root(raw)

    package_dir = setuptools.get("package-dir", {})
    if not isinstance(package_dir, dict):
        raise RuntimeError(f"Assay's setuptools `package-dir` must be a mapping: {pyproject}")
    for package_name, raw in package_dir.items():
        if not isinstance(package_name, str):
            raise RuntimeError(f"Assay declares an invalid package-dir key: {package_name!r}")
        if package_name:
            raise RuntimeError(
                f"Assay uses an unsupported named package-dir mapping: {package_name!r}"
            )
        add_root(raw, allow_existing=True)

    return [*roots, "pyproject.toml"]


def _is_generated_ignored_path(status_record: str, worktree: Path) -> bool:
    """Ignore known generated caches only when each ignored entry is a regular file."""
    if status_record[:2] != "!!":
        return False
    relative = status_record[3:].rstrip("/")
    path = PurePosixPath(relative)
    parts = path.parts
    candidate = worktree / Path(*parts)
    if candidate.is_symlink() or not candidate.is_file():
        return False
    if any(part.endswith(".egg-info") for part in parts):
        return True
    if "__pycache__" in parts:
        cache_index = parts.index("__pycache__")
        entries = parts[cache_index + 1:]
        return len(entries) == 1 and entries[0].endswith(".pyc")
    if ".pytest_cache" in parts or ".hypothesis" in parts:
        return True
    return (
        path.name in {".coverage", "coverage.json", "coverage.xml", "junit-coverage.xml"}
        or path.name.startswith(".coverage.")
    )


def _assay_import_is_bound_to_source(assay_source: Path, module_file: str | None) -> bool:
    """Require the imported Assay package to come from a declared source root."""
    if not isinstance(module_file, str) or not module_file:
        return False
    source_root = assay_source.resolve()
    imported_file = Path(module_file).resolve()
    if imported_file.name != "__init__.py" or imported_file.parent.name != "assay":
        return False
    package_dir = imported_file.parent
    for relative in _assay_source_package_paths(source_root):
        if relative == "pyproject.toml":
            continue
        configured_root = (source_root / relative).resolve()
        if package_dir.parent == configured_root or package_dir == configured_root:
            return True
    return False


def _assay_source_tree_status(assay_source: Path) -> str:
    """Report edits to Assay's packaged sources or package configuration."""
    _, repo_top, _, _ = _git_command_context(assay_source)
    source_root = assay_source.resolve()
    try:
        prefix = source_root.relative_to(repo_top)
    except ValueError as exc:
        raise RuntimeError("Assay source is outside its resolved Git worktree") from exc
    relative = prefix.as_posix()
    roots = _assay_source_package_paths(source_root)
    paths = [f"{relative}/{path}" if relative != "." else path for path in roots]
    paths.append(f":(exclude){relative + '/' if relative != '.' else ''}.assay")
    status = _git_worktree_status(repo_top, paths, include_ignored=True)
    return "\0".join(
        record for record in status.split("\0")
        if record and not _is_generated_ignored_path(record, repo_top)
    )


def _assay_install_tree_status(assay_source: Path) -> str:
    """Check all Assay build inputs before pip can execute its build backend."""
    _, repo_top, _, _ = _git_command_context(assay_source)
    source_root = assay_source.resolve()
    try:
        prefix = source_root.relative_to(repo_top).as_posix()
    except ValueError as exc:
        raise RuntimeError("Assay source is outside its resolved Git worktree") from exc
    prefix = "" if prefix == "." else prefix
    paths = [prefix or "."]
    for state_dir in (".assay", ".run-gate"):
        paths.append(f":(exclude){prefix + '/' if prefix else ''}{state_dir}")
    status = _git_worktree_status(repo_top, paths, include_ignored=True)
    return "\0".join(
        record for record in status.split("\0")
        if record and not _is_generated_ignored_path(record, repo_top)
    )


def _test_manifest(repo_root: Path, project_prefix: Path) -> dict[str, Any]:
    """Fingerprint tests and every tracked file copied into candidate fixtures."""
    project_root = repo_root / project_prefix
    pathspecs = [project_prefix.as_posix() or "."]
    pathspecs.extend(project_fixture.ROOT_CMRU_ARTIFACTS)
    pathspecs.extend(path.as_posix() for path in project_fixture.EXTERNAL_DOC_ARTIFACTS)
    pathspecs.extend(path.as_posix() for path in project_fixture.SHARED_LIBRARIES)
    pathspecs.extend(path.as_posix() for path in project_fixture.ESTATE_CONFIG_FIXTURES)
    listed_paths = project_fixture.tracked_paths(repo_root, pathspecs)
    files: dict[str, str] = {}
    fixture_files: dict[str, str] = {}
    test_prefix = (project_prefix / "tests").as_posix().rstrip("/") + "/"
    pyproject_relative = (project_prefix / "pyproject.toml").as_posix()
    pyproject_sha256 = None
    for relative in listed_paths:
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


def _copy_frozen_fixture(
    *, template_root: Path, project_prefix: Path, workspace: Path,
) -> Path:
    """Make a disposable baseline/candidate copy of the one tracked snapshot."""
    copied_root = workspace / "fixture"
    shutil.copytree(template_root, copied_root, symlinks=True)
    return copied_root / project_prefix


def _fixture_matches_manifest(
    *, template_root: Path, project_prefix: Path, manifest: dict[str, Any],
) -> bool:
    """Verify the shared template still contains exactly its pinned file closure."""
    expected = {
        **manifest.get("files", {}),
        **manifest.get("fixture_files", {}),
    }
    pyproject = (project_prefix / "pyproject.toml").as_posix()
    expected[pyproject] = manifest.get("pyproject_sha256")
    actual_paths: set[str] = set()
    for directory, child_directories, files in os.walk(template_root, followlinks=False):
        base = Path(directory)
        for name in list(child_directories):
            path = base / name
            if path.is_symlink():
                actual_paths.add(path.relative_to(template_root).as_posix())
                child_directories.remove(name)
        for name in files:
            actual_paths.add((base / name).relative_to(template_root).as_posix())
    if actual_paths != set(expected):
        return False
    for relative, expected_digest in expected.items():
        path = template_root / relative
        if path.is_symlink():
            content = b"symlink\0" + os.fsencode(os.readlink(path))
        elif path.is_file():
            content = b"file\0" + path.read_bytes()
        else:
            return False
        if hashlib.sha256(content).hexdigest() != expected_digest:
            return False
    return True


def _copy_frozen_fixture(
    *, template_root: Path, project_prefix: Path, workspace: Path,
) -> Path:
    """Make a disposable baseline/candidate copy of the one tracked snapshot."""
    copied_root = workspace / "fixture"
    shutil.copytree(template_root, copied_root, symlinks=True)
    return copied_root / project_prefix


def _resume_results(
    *,
    evidence_path: Path,
    repo_root: Path,
    resume: bool,
    base: str,
    head: str,
    project_prefix: Path,
    assay_source_commit: str,
    assay_source_root: Path,
    assay_git,
    test_argv: Sequence[str],
    jobs: Sequence[Any],
    max_mutants: int,
    timeout_seconds: float,
) -> list[dict[str, Any] | None]:
    if assay_source_root is None:
        raise ValueError("mutation resume requires an Assay source root")
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
    if not isinstance(previous, dict) \
            or previous.get("schema_version") != 5 \
            or previous.get("candidate_isolation") != "process-tree/1":
        raise ValueError(
            "mutation resume evidence predates the current process-tree evidence schema; "
            f"archive or remove {evidence_path} and start a fresh campaign"
        )
    from assay.process_tree import ProcessTreeEvidence

    try:
        baseline_tree = ProcessTreeEvidence.from_dict(
            previous["baseline_process_tree_evidence"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            "mutation resume evidence has invalid baseline process-tree proof"
        ) from exc
    if (
        baseline_tree.leader_status != "exited"
        or baseline_tree.outliving_descendants != 0
    ):
        raise ValueError("mutation resume evidence has an invalid baseline process-tree proof")
    source_paths = [(project_prefix / "src").as_posix()]
    test_paths = [
        (project_prefix / "tests").as_posix(),
        (project_prefix / "pyproject.toml").as_posix(),
    ]
    if not isinstance(previous_head, str):
        raise ValueError("mutation resume evidence has no prior head commit")
    from assay.errors import AssayError

    try:
        previous_head_is_ancestor = assay_git.is_ancestor(
            repo_root, previous_head, head, remaining=lambda: math.inf,
        )
    except AssayError as exc:
        raise RuntimeError(
            "could not verify whether mutation resume evidence is an ancestor of the current HEAD"
        ) from exc
    if not previous_head_is_ancestor:
        raise ValueError(
            "mutation resume evidence is from a non-ancestor HEAD; "
            "start a fresh campaign after rebasing or changing branches"
        )
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
    if _assay_source_tree_status(assay_source_root):
        raise ValueError(
            "mutation resume requires a clean Assay source tree across every packaged root and pyproject.toml"
        )
    current_test_manifest = _test_manifest(repo_root, project_prefix)
    previous_test_manifest = previous.get("test_manifest")
    if not isinstance(previous_test_manifest, dict):
        raise ValueError("mutation resume evidence has no exact test and fixture manifest")
    if not _test_manifest_matches(previous_test_manifest, current_test_manifest):
        raise ValueError(
            "mutation resume requires an unchanged test suite and fixture closure; "
            "start a new campaign after any test or fixture change"
        )
    previous_assay_source_commit = previous.get("assay_source_commit")
    if previous_assay_source_commit != assay_source_commit:
        assay_repo_root = assay_git.repo_top(assay_source_root).resolve()
        source_root = assay_source_root.resolve()
        try:
            source_prefix = source_root.relative_to(assay_repo_root)
        except ValueError as exc:
            raise ValueError("Assay source is outside its resolved Git worktree") from exc
        prefix = source_prefix.as_posix()
        assay_paths = [
            f"{prefix}/{path}" if prefix != "." else path
            for path in _assay_source_package_paths(source_root)
        ]
        if _changed_since(
            assay_repo_root, previous_assay_source_commit, assay_source_commit, assay_paths,
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
        or previous.get("timeout_seconds") != timeout_seconds
    ):
        raise ValueError("mutation resume evidence does not match this campaign")
    prior_results = previous.get("results")
    if not isinstance(prior_results, list) or len(prior_results) > len(jobs):
        raise ValueError("mutation resume evidence has an invalid result list")
    reusable: list[dict[str, Any] | None] = [None] * len(jobs)
    from assay.resource_limits import ResourceLimitEvidence

    for index, prior in enumerate(prior_results):
        if prior is None:
            continue
        if not isinstance(prior, dict) or _result_identity(prior) != _job_identity(jobs[index]):
            raise ValueError(f"mutation resume evidence does not match candidate {index}")
        try:
            resource_evidence = ResourceLimitEvidence.from_dict(
                prior["resource_limit_evidence"]
            )
            process_tree_evidence = ProcessTreeEvidence.from_dict(
                prior["process_tree_evidence"]
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"mutation resume evidence has invalid resource or process-tree "
                f"evidence for candidate {index}"
            ) from exc
        if prior.get("outcome") == "killed":
            if (
                prior.get("termination") != "exit"
                or prior.get("exit_code") != 1
                or resource_evidence.limit_hit
                or process_tree_evidence.leader_status != "exited"
                or process_tree_evidence.outliving_descendants != 0
            ):
                raise ValueError(
                    f"mutation resume evidence contains an unsupported kill claim for candidate {index}"
                )
            reusable[index] = prior
    return reusable


def _relative_project_path(path: str, *, repo_root: Path, project_root: Path) -> Path:
    source = (repo_root / PurePosixPath(path)).resolve()
    try:
        return source.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(f"mutation target {path!r} is outside project root") from exc


def run(argv: Sequence[str] | None = None) -> int:
    """Run with private fixture and bytecode roots that outlive each candidate."""
    with tempfile.TemporaryDirectory(prefix="cmru-mutation-campaign-") as temporary:
        old_pycache_prefix = sys.pycache_prefix
        sys.pycache_prefix = str(Path(temporary) / "controller-pycache")
        try:
            return _run_campaign(argv, template_root=Path(temporary) / "fixture-template")
        finally:
            sys.pycache_prefix = old_pycache_prefix


def _run_campaign(
    argv: Sequence[str] | None = None, *, template_root: Path,
) -> int:
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
    assay_source_paths_relative = _assay_source_package_paths(assay_source)
    if _assay_source_tree_status(assay_source):
        raise ValueError(
            "mutation campaign requires a clean Assay source tree across every packaged root and pyproject.toml"
        )
    try:
        project_prefix = project_root.relative_to(repo_root)
    except ValueError as exc:
        raise ValueError("--project-root must be inside --repo-root") from exc
    if _campaign_tree_status(repo_root, project_prefix):
        raise ValueError("mutation campaign requires committed source, tests, and pytest configuration")

    assay_source_paths = [
        assay_source / relative
        for relative in assay_source_paths_relative
        if relative != "pyproject.toml"
    ]
    sys.path[:0] = [str(path) for path in assay_source_paths]
    import assay

    from assay import diff, git, mutation
    from assay.adapters.python import PythonAdapter

    if not _assay_import_is_bound_to_source(assay_source, assay.__file__):
        raise ValueError(
            "mutation campaign imported Assay outside its configured package source roots"
        )
    if _assay_source_tree_status(assay_source):
        raise ValueError(
            "mutation campaign requires a clean Assay source tree across every packaged root and pyproject.toml"
        )
    assay_source_commit = _git_head(assay_source)
    base = git.resolve_base(repo_root, args.base)
    head = _git_head(repo_root)
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

    results = _resume_results(
        evidence_path=args.evidence,
        repo_root=repo_root,
        resume=args.resume,
        base=base,
        head=head,
        project_prefix=project_prefix,
        assay_source_commit=assay_source_commit,
        assay_source_root=assay_source,
        assay_git=git,
        test_argv=test_argv,
        jobs=jobs,
        max_mutants=args.max_mutants,
        timeout_seconds=args.timeout_seconds,
    )

    test_manifest = _test_manifest(repo_root, project_prefix)
    copy_project_fixture(
        repo_root=repo_root,
        project_root=project_root,
        workspace=template_root,
    )
    if not _fixture_matches_manifest(
        template_root=template_root,
        project_prefix=project_prefix,
        manifest=test_manifest,
    ):
        raise RuntimeError("frozen test fixture does not match its tracked manifest")
    if _test_manifest(repo_root, project_prefix) != test_manifest:
        raise RuntimeError("campaign inputs changed while creating the frozen test fixture")
    if _campaign_tree_status(repo_root, project_prefix):
        raise RuntimeError("mutation campaign inputs changed while creating the frozen test fixture")
    if _git_head(repo_root) != head or _git_head(assay_source) != assay_source_commit:
        raise RuntimeError("campaign or Assay HEAD changed while creating the frozen test fixture")

    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="cmru-mutation-baseline-") as temporary:
            baseline_root = _copy_frozen_fixture(
                template_root=template_root,
                project_prefix=project_prefix,
                workspace=Path(temporary),
            )
            # Refuse before the campaign if exact cgroup-v2 counters or contract-2
            # observer access are unavailable. A mutation result without this
            # evidence cannot support a kill claim.
            baseline_resource_before = _read_resource_limit_counters()
            baseline = _run(
                test_argv, cwd=baseline_root,
                timeout_seconds=args.timeout_seconds,
                output_dir=Path(temporary),
            )
            baseline_resource_after = _read_resource_limit_counters()
            baseline_resource_evidence = _resource_limit_evidence(
                baseline_resource_before, baseline_resource_after
            )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"the known-good mutation control exceeded {args.timeout_seconds:g} seconds"
        ) from exc
    except CandidateIsolationError as exc:
        raise RuntimeError(
            f"could not isolate the known-good mutation control: {exc}"
        ) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise RuntimeError(
            f"could not verify cgroup resource counters for the known-good control: {exc}"
        ) from exc
    if baseline_resource_evidence.limit_hit:
        raise RuntimeError(
            "a cgroup resource-limit event occurred during the known-good mutation control"
        )
    if baseline.descendants_killed:
        raise RuntimeError(
            "the known-good mutation control left candidate descendants; "
            "refusing to start the mutation campaign"
        )
    if baseline.returncode != 0:
        raise RuntimeError(
            f"the known-good mutation control failed with exit {baseline.returncode}: "
            f"{project_fixture.failure_excerpt(baseline.stdout, 1000)}"
        )
    if _campaign_tree_status(repo_root, project_prefix):
        raise RuntimeError("mutation campaign inputs changed while the known-good control ran")
    if _assay_source_tree_status(assay_source):
        raise RuntimeError("Assay source changed while the known-good control ran")
    if _git_head(repo_root) != head or _git_head(assay_source) != assay_source_commit:
        raise RuntimeError("campaign or Assay HEAD changed while the known-good control ran")
    if _test_manifest(repo_root, project_prefix) != test_manifest:
        raise RuntimeError("campaign test suite or fixture closure changed while the known-good control ran")
    if not _fixture_matches_manifest(
        template_root=template_root,
        project_prefix=project_prefix,
        manifest=test_manifest,
    ):
        raise RuntimeError("frozen test fixture changed while the known-good control ran")

    document: dict[str, Any] = {
        "schema_version": 5,
        "candidate_isolation": "process-tree/1",
        "tool": "cmru mutation campaign using worktree Assay mutation sites",
        "assay_distribution_version": assay.__version__,
        "assay_source": str(assay_source),
        "assay_source_commit": assay_source_commit,
        "base": base,
        "head": head,
        "project_prefix": project_prefix.as_posix() or ".",
        "max_mutants": args.max_mutants,
        "timeout_seconds": args.timeout_seconds,
        "operators": list(OPERATORS),
        "test_argv": test_argv,
        "test_manifest": test_manifest,
        "candidate_count": len(jobs),
        "baseline_exit_code": baseline.returncode,
        "baseline_resource_limit_evidence": baseline_resource_evidence.to_dict(),
        "baseline_process_tree_evidence": baseline.process_tree_evidence,
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

    def fail_if_assay_source_changed(context: str) -> None:
        try:
            source_status = _assay_source_tree_status(assay_source)
            source_head = _git_head(assay_source)
        except (OSError, RuntimeError, ValueError) as exc:
            message = f"could not verify Assay source {context}: {exc}"
        else:
            message = ""
            if source_status or source_head != assay_source_commit:
                message = (
                    f"Assay source changed {context}; mutation evidence is bound to "
                    f"{assay_source_commit}"
                )
        if message:
            document["status"] = "infrastructure-error"
            document["infrastructure_error"] = message
            _write_evidence(args.evidence, document)
            _append_progress(args.progress, {
                "event": "complete",
                "status": document["status"],
                "infrastructure_error": message,
            })
            raise RuntimeError(message)

    for index, job in enumerate(jobs):
        fail_if_assay_source_changed(f"before candidate {index}")
        if not _fixture_matches_manifest(
            template_root=template_root,
            project_prefix=project_prefix,
            manifest=test_manifest,
        ):
            document["status"] = "infrastructure-error"
            document["infrastructure_error"] = (
                f"frozen test fixture changed before candidate {index}"
            )
            _write_evidence(args.evidence, document)
            _append_progress(args.progress, {
                "event": "complete",
                "status": document["status"],
                "infrastructure_error": document["infrastructure_error"],
            })
            raise RuntimeError(document["infrastructure_error"])
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
            candidate_root = _copy_frozen_fixture(
                template_root=template_root,
                project_prefix=project_prefix,
                workspace=Path(temporary),
            )
            target = candidate_root / relative
            original = target.read_text(encoding="utf-8")
            if original != job.original_text:
                raise RuntimeError(f"copied mutation target differs from pinned source: {job.path}")
            target.write_bytes(job.site.apply(original.encode("utf-8")))
            candidate_started = time.monotonic()
            try:
                resource_before = _read_resource_limit_counters()
            except (OSError, RuntimeError, ValueError) as exc:
                document["status"] = "infrastructure-error"
                document["infrastructure_error"] = (
                    f"could not read cgroup resource counters before candidate {index}: {exc}"
                )
                _write_evidence(args.evidence, document)
                _append_progress(args.progress, {
                    "event": "complete",
                    "status": document["status"],
                    "infrastructure_error": document["infrastructure_error"],
                })
                raise RuntimeError(document["infrastructure_error"]) from exc
            try:
                completed = _run(
                    test_argv, cwd=candidate_root,
                    timeout_seconds=args.timeout_seconds,
                    output_dir=Path(temporary),
                )
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                returncode = None
                descendants_killed = getattr(exc, "descendants_killed", 0)
                process_tree_evidence = getattr(exc, "process_tree_evidence", None)
            except (CandidateIsolationError, OSError) as exc:
                document["status"] = "infrastructure-error"
                document["infrastructure_error"] = (
                    f"could not isolate candidate {index}: {exc}"
                )
                _write_evidence(args.evidence, document)
                _append_progress(args.progress, {
                    "event": "complete",
                    "status": document["status"],
                    "infrastructure_error": document["infrastructure_error"],
                })
                raise RuntimeError(document["infrastructure_error"]) from exc
            else:
                timed_out = False
                returncode = completed.returncode
                descendants_killed = completed.descendants_killed
                process_tree_evidence = completed.process_tree_evidence
            try:
                resource_after = _read_resource_limit_counters()
                limit_evidence = _resource_limit_evidence(resource_before, resource_after)
            except (OSError, RuntimeError, ValueError) as exc:
                document["status"] = "infrastructure-error"
                document["infrastructure_error"] = (
                    f"could not attribute cgroup resource events around candidate {index}: {exc}"
                )
                _write_evidence(args.evidence, document)
                _append_progress(args.progress, {
                    "event": "complete",
                    "status": document["status"],
                    "infrastructure_error": document["infrastructure_error"],
                })
                raise RuntimeError(document["infrastructure_error"]) from exc
            if timed_out:
                result = _candidate_result(
                    job, returncode=None,
                    elapsed_seconds=time.monotonic() - candidate_started,
                    timed_out=True,
                    descendants_killed=descendants_killed,
                    resource_limit_evidence=limit_evidence,
                    process_tree_evidence=process_tree_evidence,
                )
            else:
                result = _candidate_result(
                    job, returncode=completed.returncode,
                    elapsed_seconds=time.monotonic() - candidate_started,
                    timed_out=False,
                    descendants_killed=descendants_killed,
                    resource_limit_evidence=limit_evidence,
                    process_tree_evidence=process_tree_evidence,
                )
        fail_if_assay_source_changed(f"during candidate {index}")
        if not _fixture_matches_manifest(
            template_root=template_root,
            project_prefix=project_prefix,
            manifest=test_manifest,
        ):
            document["status"] = "infrastructure-error"
            document["infrastructure_error"] = (
                f"frozen test fixture changed during candidate {index}"
            )
            _write_evidence(args.evidence, document)
            _append_progress(args.progress, {
                "event": "complete",
                "status": document["status"],
                "infrastructure_error": document["infrastructure_error"],
            })
            raise RuntimeError(document["infrastructure_error"])
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
            "descendants_killed": result.descendants_killed,
            "resource_limit_evidence": result.resource_limit_evidence,
            "process_tree_evidence": result.process_tree_evidence,
            "elapsed_seconds": result.elapsed_seconds,
            "campaign_elapsed_seconds": round(time.monotonic() - started, 3),
        })
        if limit_evidence.limit_hit:
            document["status"] = "infrastructure-error"
            document["infrastructure_error"] = (
                f"cgroup resource-limit event occurred during candidate {index}; "
                "the candidate cannot count as killed"
            )
            _write_evidence(args.evidence, document)
            _append_progress(args.progress, {
                "event": "complete",
                "status": document["status"],
                "infrastructure_error": document["infrastructure_error"],
            })
            raise RuntimeError(document["infrastructure_error"])

    fail_if_assay_source_changed("before campaign completion")
    if not _fixture_matches_manifest(
        template_root=template_root,
        project_prefix=project_prefix,
        manifest=test_manifest,
    ):
        document["status"] = "infrastructure-error"
        document["infrastructure_error"] = "frozen test fixture changed before campaign completion"
        _write_evidence(args.evidence, document)
        _append_progress(args.progress, {
            "event": "complete",
            "status": document["status"],
            "infrastructure_error": document["infrastructure_error"],
        })
        raise RuntimeError(document["infrastructure_error"])
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
