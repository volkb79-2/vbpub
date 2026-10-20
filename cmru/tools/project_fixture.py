"""Materialise the complete disposable source closure used by CMRU gates."""

from __future__ import annotations

import re
import shutil
import os
import subprocess
from pathlib import Path


ROOT_CMRU_ARTIFACTS = (
    "cmru.orchestration.sample.toml",
    "cmru.orchestration.toml",
    "cmru.project.sample.toml",
)
# cli-extended is deliberately absent: it is a real wheel dependency installed in
# the gate environment, so a fixture must never carry (and tests never import) a
# vendored copy of its source.
SHARED_LIBRARIES = (
    Path("libraries/worktree"),
)
# Tracked repository-root inputs the full suite reads in this fixture. The tests
# that judge tester-unified, nyxloom and the whole estate skip here ("isolated
# canary tree"): their full mode needs a Git checkout, cli-extended source or
# every project. Their skip keys must therefore stay absent:
# tester-unified/gen-requirements.py, tester-unified/build-image.sh,
# libraries/cli-extended, run-gate-project/tools/release_candidate.py, and the
# full set of registered project configs. The suite still reads:
# - tester-unified/Dockerfile: test_tester_image_cli_extended_identity module
#   constants, and test_bootstrap_from_zero_sequence's real-pin check;
# - nyxloom/pyproject.toml plus its setuptools search root nyxloom/src:
#   test_tester_unified_image's _nyxloom_wheel_inputs() parametrize list;
# - .gitignore: test_release_gate's clean-source check uses the estate rules;
# - run-gate-project/cmru.toml: test_release_gate's provider-prefix check;
# - assay/src: run_release_gate's isolated component loader imports Assay there;
# - tester-base/Dockerfile: test_installed_wheel_subprocess's _bootstrap_tree
#   copies it into the throw-away tree it bootstraps (the script hashes it);
# - docs/BOOTSTRAP-FROM-ZERO.md: linked from CMRU's README, DESIGN-GUIDE and
#   CONSUMERS (test_docs_config_examples' cross-document link oracle).
SUITE_COLLECTION_INPUTS = (
    Path("tester-unified/Dockerfile"),
    Path("nyxloom/pyproject.toml"),
    Path("nyxloom/src"),
    Path(".gitignore"),
    Path("run-gate-project/cmru.toml"),
    Path("assay/src"),
    Path("docs/BOOTSTRAP-FROM-ZERO.md"),
    Path("tester-base/Dockerfile"),
)
# mutation_campaign fingerprints and dirty-checks exactly the pathspecs of the
# four fixture constants, so the collection inputs join this one rather than
# forming a fifth constant that its manifest would not see.
EXTERNAL_DOC_ARTIFACTS = (
    Path("wheel-builder/Dockerfile"),
    Path("docs/ciu-vs-cmru.md"),
    Path("docs/RELEASE-TOOLING.md"),
    Path("docs/plan-cmru-release-modes.md"),
    Path("run-gate-project/CONSUMERS.md"),
    *SUITE_COLLECTION_INPUTS,
)
ESTATE_CONFIG_FIXTURES = (
    Path("topos/cmru.toml"),
    Path("nyxloom/cmru.toml"),
)


_FAILURE_SECTION = re.compile(r"^(={3,} (?:FAILURES|ERRORS) ={3,}\n)|^(ERROR collecting)", re.MULTILINE)


def failure_excerpt(output: str, limit: int) -> str:
    """Return up to ``limit`` characters starting at the first failure section.

    A pytest ``-q`` run ends with a short summary of bare ``FAILED node-id``
    lines, so the tail of the output names the failing tests but never the
    reason. Start at the first FAILURES/ERRORS section (or collection error)
    instead; with neither, fall back to the tail.
    """
    match = _FAILURE_SECTION.search(output)
    if match is None:
        return output[-limit:]
    start = match.end(1) if match.group(1) else match.start(2)
    return output[start:start + limit]


def tracked_paths(repo_root: Path, pathspecs: tuple[str, ...] | list[str]) -> list[str]:
    """List committed inputs with Git configuration and index paths sanitized."""
    git_executable = shutil.which("git")
    if not git_executable:
        raise RuntimeError("Git is required to assemble the CMRU test fixture")
    repo_root = repo_root.resolve(strict=True)
    env = {
        "PATH": os.path.dirname(git_executable),
        "HOME": "/nonexistent",
        "XDG_CONFIG_HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "LANG": "C",
    }
    common = [
        git_executable,
        "-c", "core.fsmonitor=false",
        "-c", "core.excludesFile=/dev/null",
        "-c", "maintenance.auto=false",
        "-c", "gc.auto=0",
        "-c", "gc.autoDetach=false",
    ]
    try:
        top = subprocess.run(
            [*common, "-C", str(repo_root), "rev-parse", "--show-toplevel"],
            check=False, capture_output=True, env=env, timeout=10,
        )
        git_dir_result = subprocess.run(
            [*common, "-C", str(repo_root), "rev-parse", "--absolute-git-dir"],
            check=False, capture_output=True, env=env, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"could not resolve the fixture Git worktree: {repo_root}") from exc
    if top.returncode or git_dir_result.returncode:
        detail = (top.stderr or git_dir_result.stderr).decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"could not resolve the fixture Git worktree: {detail}")
    resolved_top = Path(os.fsdecode(top.stdout.rstrip(b"\n"))).resolve(strict=True)
    git_dir = Path(os.fsdecode(git_dir_result.stdout.rstrip(b"\n"))).resolve(strict=True)
    if resolved_top != repo_root:
        raise RuntimeError(f"fixture root is not the Git worktree root: {repo_root}")
    try:
        listed = subprocess.run(
            [
                *common, f"--git-dir={git_dir}", f"--work-tree={resolved_top}",
                "ls-files", "-z", "--", *pathspecs,
            ],
            cwd=resolved_top, check=False, capture_output=True, env=env, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("could not list committed mutation fixture inputs") from exc
    if listed.returncode:
        detail = listed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"could not list committed mutation fixture inputs: {detail}")
    return [os.fsdecode(raw) for raw in listed.stdout.split(b"\0") if raw]


def copy_project_fixture(*, repo_root: Path, project_root: Path, workspace: Path) -> Path:
    """Copy CMRU and the external files its full test suite reads.

    The project tests exercise source-checkout imports for CMRU's shared
    libraries. Estate configs are also read by adoption contract tests, and
    SUITE_COLLECTION_INPUTS by the tests named beside it, so all must be present
    in the known-good control and every mutated candidate.
    """
    repo_root = repo_root.resolve(strict=True)
    project_root = project_root.resolve(strict=True)
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        project_prefix = project_root.relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise ValueError("CMRU fixture project must be inside the selected Git worktree") from exc
    pathspecs = [project_prefix]
    pathspecs.extend(ROOT_CMRU_ARTIFACTS)
    pathspecs.extend(path.as_posix() for path in EXTERNAL_DOC_ARTIFACTS)
    pathspecs.extend(path.as_posix() for path in SHARED_LIBRARIES)
    pathspecs.extend(path.as_posix() for path in ESTATE_CONFIG_FIXTURES)
    files = tracked_paths(repo_root, pathspecs)
    if not any(path == project_prefix or path.startswith(project_prefix + "/") for path in files):
        raise ValueError(f"CMRU fixture has no tracked project files: {project_root}")

    required = {
        *ROOT_CMRU_ARTIFACTS,
        *(path.as_posix() for path in EXTERNAL_DOC_ARTIFACTS),
        *(path.as_posix() for path in ESTATE_CONFIG_FIXTURES),
    }
    # A file pathspec must be tracked itself; a tree pathspec must select a file.
    missing = sorted(
        spec for spec in required
        if not any(path == spec or path.startswith(spec + "/") for path in files)
    )
    if missing:
        raise ValueError(f"required tracked CMRU fixture inputs are missing: {', '.join(missing)}")
    for relative in SHARED_LIBRARIES:
        if not any(path == relative.as_posix() or path.startswith(relative.as_posix() + "/") for path in files):
            raise ValueError(f"required shared CMRU library has no tracked files: {relative}")

    for relative in files:
        source = repo_root / relative
        destination = workspace / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            destination.symlink_to(os.readlink(source))
        elif source.is_file():
            shutil.copy2(source, destination)
        else:
            raise ValueError(f"tracked CMRU fixture input is not a file or symlink: {source}")
    return workspace / project_prefix
