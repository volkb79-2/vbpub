"""Isolated, source-first release transactions.

``cmru release`` is deliberately launched from an ordinary developer checkout but
never publishes from it.  A transaction pins ``origin/main`` to a commit, creates
an isolated worktree on a private ``cmru/release/<id>`` branch, and executes the
release child there.  The caller's uncommitted files therefore cannot leak into a
wheel, image, tag, or release asset.

The parent process owns a repository-local flock for the lifetime of its child.
The child may fast-forward ``origin/main`` from the prepared release branch; a
concurrent remote update fails closed before tags or publication.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Mapping, Sequence


CHILD_ENV = "CMRU_RELEASE_TRANSACTION_CHILD"
BRANCH_ENV = "CMRU_RELEASE_BRANCH"
BASE_ENV = "CMRU_RELEASE_BASE"


def _git(repo_root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_root, text=True, capture_output=True, check=False,
    )
    if check and result.returncode:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def _common_git_dir(repo_root: Path) -> Path:
    raw = _git(repo_root, "rev-parse", "--git-common-dir")
    path = Path(raw)
    return path if path.is_absolute() else (repo_root / path).resolve()


@dataclass(frozen=True)
class ReleaseWorkspace:
    """The immutable source snapshot and private branch for one release."""

    repo_root: Path
    path: Path
    branch: str
    base: str


@contextmanager
def release_lock(repo_root: Path) -> Iterator[None]:
    """Serialize local release transactions without relying on a mutable checkout."""
    lock_path = _common_git_dir(repo_root) / "cmru-release.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another cmru release transaction is already running.") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def fetch_origin_main(repo_root: Path) -> str:
    """Fetch and return the exact remote commit authoritative for a new release."""
    subprocess.run(["git", "fetch", "--prune", "origin", "main"], cwd=repo_root, check=True)
    return _git(repo_root, "rev-parse", "origin/main")


def local_main_divergence(repo_root: Path) -> tuple[int, int]:
    """Return commits ``(ahead, behind)`` for local main relative to origin/main."""
    try:
        counts = _git(repo_root, "rev-list", "--left-right", "--count", "main...origin/main")
        ahead, behind = counts.split()
        return int(ahead), int(behind)
    except (RuntimeError, ValueError) as exc:
        raise RuntimeError(
            "Cannot compare local main with origin/main; fetch/repair the local main ref "
            "before starting a release."
        ) from exc


def assert_local_main_not_ahead(repo_root: Path) -> int:
    """Reject commits local-only commits that an origin/main snapshot would omit.

    A behind local checkout is harmless because ``origin/main`` is deliberately
    authoritative; the caller receives that count so it can be reported.
    """
    ahead, behind = local_main_divergence(repo_root)
    if ahead:
        raise RuntimeError(
            f"Local main is {ahead} commit(s) ahead of origin/main. "
            "Push those commits (or explicitly base the intended change on origin/main) "
            "before release; an isolated release snapshots origin/main and would omit them."
        )
    return behind


def create_workspace(
    repo_root: Path, *, base: str | None = None, purpose: str = "release",
) -> ReleaseWorkspace:
    """Create a worktree at one already-fetched authoritative remote commit.

    ``purpose`` is intentionally visible in the branch/path. A successful release
    is ephemeral; a normal build is retained for inspection and must therefore
    never be mistaken for a failed, resumable release attempt.
    """
    if purpose not in {"release", "build"}:
        raise ValueError(f"unknown CMRU workspace purpose: {purpose}")
    if base is None:
        base = fetch_origin_main(repo_root)
    token = uuid.uuid4().hex[:12]
    branch = f"cmru/{purpose}/{token}"
    parent = repo_root / ".worktrees"
    parent.mkdir(exist_ok=True)
    path = Path(tempfile.mkdtemp(prefix=f"cmru-{purpose}-{token}-", dir=parent))
    path.rmdir()  # git worktree requires a path that does not exist yet.
    subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(path), base], cwd=repo_root, check=True,
    )
    return ReleaseWorkspace(repo_root=repo_root, path=path, branch=branch, base=base)


def resume_workspace(repo_root: Path, path: Path) -> ReleaseWorkspace:
    """Validate and reopen a retained ``cmru/release/*`` worktree."""
    path = path.resolve()
    if not path.is_dir():
        raise RuntimeError(f"release worktree does not exist: {path}")
    if _common_git_dir(path) != _common_git_dir(repo_root):
        raise RuntimeError(f"{path} is not a worktree of {repo_root}")
    branch = _git(path, "branch", "--show-current")
    if not branch.startswith("cmru/release/"):
        raise RuntimeError(f"{path} is not a retained cmru release branch (got {branch!r})")
    subprocess.run(["git", "fetch", "--prune", "origin", "main"], cwd=repo_root, check=True)
    return ReleaseWorkspace(repo_root=repo_root, path=path, branch=branch, base=_git(path, "rev-parse", "HEAD"))


def copy_secret_overlays(
    repo_root: Path, workspace: ReleaseWorkspace, project_config_paths: Sequence[Path],
) -> None:
    """Copy the root credential and explicit project overlays into a child worktree."""
    source = repo_root.resolve() / "cmru.secret.toml"
    if source.exists() and not source.is_file():
        raise RuntimeError(f"repository credential path is not a regular file: {source}")
    if source.is_file():
        target = workspace.path / "cmru.secret.toml"
        shutil.copyfile(source, target)
        target.chmod(0o600)
    for config_path in project_config_paths:
        config_path = config_path.resolve()
        try:
            relative = config_path.parent.relative_to(repo_root.resolve())
        except ValueError as exc:
            raise RuntimeError(f"project config is outside repository: {config_path}") from exc
        source = config_path.with_name("cmru.secret.toml")
        if source.exists() and not source.is_file():
            raise RuntimeError(f"project credential path is not a regular file: {source}")
        if not source.is_file():
            continue
        target = workspace.path / relative / "cmru.secret.toml"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o600)


def remove_workspace(workspace: ReleaseWorkspace) -> None:
    """Remove a successful ephemeral worktree and its private branch."""
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(workspace.path)],
        cwd=workspace.repo_root, check=True,
    )
    subprocess.run(
        ["git", "branch", "-D", workspace.branch], cwd=workspace.repo_root, check=True,
    )


def _release_token(workspace: ReleaseWorkspace) -> str:
    return workspace.branch.rsplit("/", 1)[-1]


def _scope_dir(repo_root: Path) -> Path:
    return _common_git_dir(repo_root) / "cmru-release-scopes"


def write_release_scope(repo_root: Path, workspace: ReleaseWorkspace, project_names: Sequence[str]) -> None:
    """Record which projects a release attempt targets, in the shared common git
    dir (never inside the worktree — S-REL.4a's undeclared-write guard must never
    see it). A later ``--abandon all-previous`` scopes cleanup by this record."""
    scope_dir = _scope_dir(repo_root)
    scope_dir.mkdir(parents=True, exist_ok=True)
    (scope_dir / f"{_release_token(workspace)}.json").write_text(
        json.dumps(sorted(project_names)), encoding="utf-8",
    )


def read_release_scope(repo_root: Path, workspace: ReleaseWorkspace) -> list[str] | None:
    """The recorded project scope for a retained worktree, or None if it predates
    this feature (an older retained worktree) — callers should treat None
    conservatively (not auto-abandon it)."""
    path = _scope_dir(repo_root) / f"{_release_token(workspace)}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def forget_release_scope(repo_root: Path, workspace: ReleaseWorkspace) -> None:
    """Remove this workspace's scope + progress-checkpoint marker files. Callers:
    abandon_workspace() (a discarded attempt) and a successful release (its scope
    marker is otherwise never cleaned up — see remove_workspace())."""
    (_scope_dir(repo_root) / f"{_release_token(workspace)}.json").unlink(missing_ok=True)
    _forget_release_progress(repo_root, workspace)
    (_scope_dir(repo_root) / f"{_release_token(workspace)}.results.json").unlink(missing_ok=True)


def write_release_result(
    repo_root: Path, workspace: ReleaseWorkspace, project: str, immutable_id: str,
) -> None:
    """Record a completed project’s immutable release coordinate for retention.

    Stored alongside the scope, outside a worktree, so the parent can safely move
    selected outputs only after the child reports whole-transaction success.
    """
    path = _scope_dir(repo_root) / f"{_release_token(workspace)}.results.json"
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot read release result record {path}") from exc
    if not isinstance(current, dict):
        raise RuntimeError(f"invalid release result record {path}")
    current[project] = immutable_id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_release_results(repo_root: Path, workspace: ReleaseWorkspace) -> dict[str, str]:
    """Return project → immutable tag/source coordinate recorded by the child."""
    path = _scope_dir(repo_root) / f"{_release_token(workspace)}.results.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot read release result record {path}") from exc
    if not isinstance(raw, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in raw.items()):
        raise RuntimeError(f"invalid release result record {path}")
    return raw


def _digest_tree(path: Path) -> list[dict[str, str]]:
    """Describe regular artifact files deterministically for retained release.json."""
    entries: list[dict[str, str]] = []
    for item in sorted(path.rglob("*")):
        if not item.is_file():
            continue
        digest = hashlib.sha256()
        with item.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        entries.append({
            "path": item.relative_to(path).as_posix(),
            "sha256": digest.hexdigest(),
            "bytes": str(item.stat().st_size),
        })
    return entries


_BUILD_OUTPUT_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z_[0-9a-f]{40}$")


def build_output_id(workspace: ReleaseWorkspace) -> tuple[str, str, str]:
    """Return the immutable local-output coordinate for the built source tree.

    The coordinate is deliberately source-derived rather than wall-clock-derived:
    rebuilding an unchanged commit addresses the same local evidence record and
    therefore refuses to overwrite it.  ``cmru cleanup --delete-build-output``
    is the explicit way to discard that record before another build of the same
    source snapshot.
    """
    source_commit = _git(workspace.path, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise RuntimeError(f"invalid build source commit: {source_commit!r}")
    raw_epoch = _git(workspace.path, "show", "-s", "--format=%ct", "HEAD")
    if not raw_epoch.isdecimal():
        raise RuntimeError(f"invalid build source commit timestamp: {raw_epoch!r}")
    source_date = _git(workspace.path, "show", "-s", "--format=%cI", "HEAD")
    output_id = (
        datetime.fromtimestamp(int(raw_epoch), timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + f"_{source_commit}"
    )
    return output_id, source_commit, source_date


def _project_roots_for_retention(
    repo_root: Path, workspace: ReleaseWorkspace, project: object, name: str,
) -> tuple[Path, Path]:
    """Resolve one project in the caller checkout and its transaction copy."""
    project_root = getattr(project, "project_root", None)
    if project_root is None:
        raise RuntimeError(f"{name}: cannot retain outputs without project_root")
    main_project_root = Path(project_root).resolve()
    try:
        relative = main_project_root.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise RuntimeError(f"{name}: project_root is outside repository: {main_project_root}") from exc
    return main_project_root, workspace.path / relative


def retain_successful_build_outputs(
    repo_root: Path,
    workspace: ReleaseWorkspace,
    projects: Mapping[str, object],
    project_names: Sequence[str],
) -> list[Path]:
    """Copy a successful non-release build's evidence into the caller checkout.

    A normal ``cmru build`` is useful precisely because its artifacts can be
    consumed locally.  After a successful child build this function copies the
    declared artifact directories and project-local logs into immutable,
    commit-addressed records, then lets the caller remove the isolated worktree.
    It never treats those records as publishable release candidates: ``build.json``
    says so explicitly and records any tracked source changes left by ``prepare``.

    Every destination is first staged and validated.  Existing coordinates are a
    hard error; overwriting an earlier build would make its provenance mutable.
    Sources remain in the worktree until all copying succeeds, so a retention
    error leaves the worktree intact for diagnosis.
    """
    output_id, source_commit, source_date = build_output_id(workspace)
    retained: list[Path] = []
    source_changes = _git(
        workspace.path, "status", "--porcelain=v1", "--untracked-files=all",
    ).splitlines()

    for name in project_names:
        project = projects.get(name)
        if project is None:
            raise RuntimeError(f"build result names unknown project: {name}")
        artifact_dirs = tuple(getattr(project, "artifact_dirs", ()) or ())
        if not artifact_dirs:
            raise RuntimeError(
                f"{name}: cmru build requires project.release.artifact_dirs so it can retain "
                "the successful local output"
            )
        main_project_root, child_project_root = _project_roots_for_retention(
            repo_root, workspace, project, name,
        )
        source_logs = child_project_root / "logs"
        if not source_logs.is_dir() or source_logs.is_symlink():
            raise RuntimeError(f"{name}: build logs are missing or unsafe: {source_logs}")

        source_artifacts: list[tuple[str, Path]] = []
        for raw_dir in artifact_dirs:
            source = child_project_root / raw_dir
            if not source.is_dir() or source.is_symlink():
                raise RuntimeError(f"{name}: declared artifact directory is missing or unsafe: {source}")
            source_artifacts.append((raw_dir, source))

        target_logs = main_project_root / "logs" / output_id
        target_artifacts = main_project_root / "artifacts" / output_id
        if target_logs.exists() or target_artifacts.exists():
            raise RuntimeError(
                f"{name}: build output {output_id} already exists; inspect it or remove it with "
                f"cmru cleanup --project {name} --delete-build-output {output_id} --yes"
            )

        stage = Path(tempfile.mkdtemp(prefix=".cmru-build-retain-", dir=main_project_root))
        staged_logs = stage / "logs"
        staged_artifacts = stage / "artifacts"
        moved_logs = False
        try:
            shutil.copytree(source_logs, staged_logs, symlinks=True)
            staged_artifacts.mkdir()
            copied_artifacts: list[dict[str, object]] = []
            for raw_dir, source in source_artifacts:
                target = staged_artifacts / Path(raw_dir).name
                if target.exists():
                    raise RuntimeError(f"{name}: artifact directory name collision: {target.name}")
                shutil.copytree(source, target, symlinks=True)
                files = _digest_tree(target)
                if not files:
                    raise RuntimeError(f"{name}: declared artifact directory is empty: {source}")
                copied_artifacts.append({"directory": target.name, "files": files})

            manifest = {
                "schema_version": 1,
                "kind": "cmru-local-build",
                "publication": "forbidden",
                "project": name,
                "build_id": output_id,
                "source_commit": source_commit,
                "source_commit_date": source_date,
                "source_tree_changes": source_changes,
                "logs": _digest_tree(staged_logs),
                "artifacts": copied_artifacts,
            }
            (staged_artifacts / "build.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
            )

            # Recheck immediately before the irreversible rename; another shell
            # must not turn an immutable output record into an overwrite.
            if target_logs.exists() or target_artifacts.exists():
                raise RuntimeError(f"{name}: build output destination appeared during retention: {output_id}")
            target_logs.parent.mkdir(parents=True, exist_ok=True)
            target_artifacts.parent.mkdir(parents=True, exist_ok=True)
            staged_logs.replace(target_logs)
            moved_logs = True
            try:
                staged_artifacts.replace(target_artifacts)
            except Exception:
                # The sources are still safe in the retained worktree.  Restore
                # the first rename so the caller checkout remains all-or-nothing.
                target_logs.replace(staged_logs)
                moved_logs = False
                raise
            retained.extend((target_logs, target_artifacts))
        finally:
            if moved_logs:
                # A later unexpected exception must not leave a partial record.
                if target_logs.exists() and not target_artifacts.exists():
                    target_logs.replace(staged_logs)
                    retained[:] = [
                        path for path in retained
                        if path not in {target_logs, target_artifacts}
                    ]
                    shutil.rmtree(stage, ignore_errors=True)
                    raise RuntimeError(
                        f"{name}: retained artifact destination disappeared during retention"
                    )
            shutil.rmtree(stage, ignore_errors=True)
    return retained


def delete_retained_build_output(
    repo_root: Path,
    project: object,
    project_name: str,
    output_id: str,
    *,
    dry_run: bool,
) -> list[Path]:
    """Delete one verified local build record, never a glob or age range."""
    if not _BUILD_OUTPUT_ID_RE.fullmatch(output_id):
        raise RuntimeError(
            "--delete-build-output must be the exact <commit-date>_<40-hex-commit> "
            "coordinate printed by cmru build"
        )
    main_project_root, _child_project_root = _project_roots_for_retention(
        repo_root,
        ReleaseWorkspace(repo_root=repo_root, path=repo_root, branch="", base=""),
        project,
        project_name,
    )
    artifact_root = main_project_root / "artifacts" / output_id
    logs_root = main_project_root / "logs" / output_id
    manifest_path = artifact_root / "build.json"
    if (
        not artifact_root.is_dir() or artifact_root.is_symlink()
        or not logs_root.is_dir() or logs_root.is_symlink()
        or not manifest_path.is_file() or manifest_path.is_symlink()
    ):
        raise RuntimeError(
            f"{project_name}: retained build record is incomplete or unsafe for {output_id}; "
            "remove it manually after inspection"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"{project_name}: invalid retained build manifest: {manifest_path}") from exc
    if not isinstance(manifest, dict) or (
        manifest.get("schema_version") != 1
        or manifest.get("kind") != "cmru-local-build"
        or manifest.get("publication") != "forbidden"
        or manifest.get("project") != project_name
        or manifest.get("build_id") != output_id
    ):
        raise RuntimeError(f"{project_name}: retained build manifest does not authorize cleanup: {manifest_path}")

    targets = [logs_root, artifact_root]
    if dry_run:
        return targets
    # Both paths were derived from a validated project root and a strict output
    # ID, then authenticated by build.json.  They are therefore safe exact
    # deletion targets; no user-supplied directory tree is ever recursed.
    for target in targets:
        shutil.rmtree(target)
    return targets


def discard_build_workspace(repo_root: Path, path: Path, *, dry_run: bool) -> ReleaseWorkspace:
    """Discard one inspected failed ``cmru/build/*`` worktree by exact path."""
    path = path.resolve()
    expected_parent = (repo_root / ".worktrees").resolve()
    if path.parent != expected_parent:
        raise RuntimeError(f"{path} is outside this repository's managed .worktrees directory")
    if not path.is_dir() or _common_git_dir(path) != _common_git_dir(repo_root):
        raise RuntimeError(f"{path} is not a worktree of {repo_root}")
    branch = _git(path, "branch", "--show-current")
    if not branch.startswith("cmru/build/"):
        raise RuntimeError(f"{path} is not a retained cmru build worktree (got {branch!r})")
    workspace = ReleaseWorkspace(
        repo_root=repo_root,
        path=path,
        branch=branch,
        base=_git(path, "rev-parse", "HEAD"),
    )
    if not dry_run:
        remove_workspace(workspace)
    return workspace


def retain_success_outputs(
    repo_root: Path,
    workspace: ReleaseWorkspace,
    projects: dict[str, object],
    results: dict[str, str],
    *,
    retain_logs: bool,
    retain_artifacts: bool,
) -> list[Path]:
    """Move selected completed-release evidence out before deleting the worktree.

    Destinations are project-local and immutable by coordinate.  Existing targets
    are an error: overwriting a retained record would turn provenance into a
    mutable cache.  A missing declared artifact directory is also an error when
    retention was requested; configuration must describe a real build output.
    """
    retained: list[Path] = []
    for name, immutable_id in results.items():
        project = projects.get(name)
        if project is None:
            raise RuntimeError(f"release result names unknown project: {name}")
        project_root = getattr(project, "project_root", None)
        if project_root is None:
            raise RuntimeError(f"{name}: cannot retain outputs without project_root")
        project_root = Path(project_root).resolve()
        relative = project_root.relative_to(repo_root.resolve())
        child_root = workspace.path / relative
        source_logs = child_root / "logs"
        target_logs = project_root / "logs" / "cmru-release" / immutable_id
        if retain_logs and source_logs.exists() and target_logs.exists():
            raise RuntimeError(f"{name}: retained log destination already exists: {target_logs}")

        artifact_dirs: tuple[str, ...] = ()
        target_root = project_root / "artifacts" / immutable_id
        artifact_sources: list[tuple[str, Path, Path]] = []
        if retain_artifacts:
            artifact_dirs = tuple(getattr(project, "artifact_dirs", ()) or ())
            if not artifact_dirs:
                raise RuntimeError(
                    f"{name}: --retain-artifacts-on-release requires project.release.artifact_dirs"
                )
            if target_root.exists():
                raise RuntimeError(f"{name}: retained artifact destination already exists: {target_root}")
            seen_names: set[str] = set()
            for raw_dir in artifact_dirs:
                source = child_root / raw_dir
                if not source.is_dir():
                    raise RuntimeError(f"{name}: declared artifact directory is missing: {source}")
                target_name = Path(raw_dir).name
                if target_name in seen_names:
                    raise RuntimeError(f"{name}: artifact directory name collision: {target_name}")
                seen_names.add(target_name)
                target = target_root / target_name
                if target.exists():
                    raise RuntimeError(f"{name}: artifact directory destination already exists: {target}")
                artifact_sources.append((target_name, source, target))

        log_parent = target_logs.parent
        log_parent_existed = log_parent.exists()
        artifact_parent = target_root.parent
        artifact_parent_existed = artifact_parent.exists()
        moved_sources: list[tuple[Path, Path]] = []
        created_target_root = False
        moved_logs = False
        try:
            # All destination creation is deliberately before the first move.
            if retain_logs and source_logs.exists():
                log_parent.mkdir(parents=True, exist_ok=True)
            if retain_artifacts:
                artifact_parent.mkdir(parents=True, exist_ok=True)
                target_root.mkdir()
                created_target_root = True

            if retain_logs and source_logs.exists():
                shutil.move(str(source_logs), str(target_logs))
                moved_sources.append((source_logs, target_logs))
                moved_logs = True
            moved: list[dict[str, object]] = []
            if retain_artifacts:
                for target_name, source, target in artifact_sources:
                    shutil.move(str(source), str(target))
                    moved_sources.append((source, target))
                    moved.append({"directory": target_name, "files": _digest_tree(target)})
                manifest = {
                    "schema_version": 1,
                    "project": name,
                    "immutable_id": immutable_id,
                    "source_commit": _git(workspace.path, "rev-parse", "HEAD"),
                    "artifacts": moved,
                }
                (target_root / "release.json").write_text(
                    json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
            if moved_logs:
                retained.append(target_logs)
            if retain_artifacts:
                retained.append(target_root)
        except Exception:
            rollback_errors: list[Exception] = []
            for source, target in reversed(moved_sources):
                try:
                    shutil.move(str(target), str(source))
                except Exception as rollback_exc:
                    rollback_errors.append(rollback_exc)
            if created_target_root and target_root.exists():
                try:
                    shutil.rmtree(target_root)
                except Exception as rollback_exc:
                    rollback_errors.append(rollback_exc)
            if not artifact_parent_existed and artifact_parent.exists():
                try:
                    artifact_parent.rmdir()
                except OSError:
                    pass
            if not log_parent_existed and log_parent.exists():
                try:
                    log_parent.rmdir()
                except OSError:
                    pass
            if rollback_errors:
                raise RuntimeError(f"{name}: retention rollback failed") from rollback_errors[0]
            raise
    return retained


def write_release_progress(repo_root: Path, workspace: ReleaseWorkspace, sha: str) -> None:
    """Record the commit SHA as of the last *fully completed* project in a
    per-project release run (build-all-projects-after-another: S-REL — each
    project's prepare/gate/promote/tag/build/publish cycle finishes before the
    next project's starts). On a later failure, the parent uses this instead of
    the transaction's original base commit so it only reverts the in-flight
    project's promoted changes, never an earlier project's already-published
    release."""
    scope_dir = _scope_dir(repo_root)
    scope_dir.mkdir(parents=True, exist_ok=True)
    (scope_dir / f"{_release_token(workspace)}.progress").write_text(sha, encoding="utf-8")


def read_release_progress(repo_root: Path, workspace: ReleaseWorkspace) -> str | None:
    """The last-fully-completed-project checkpoint for a workspace, or None if no
    project has completed yet (or this predates the feature) — callers should
    fall back to ``workspace.base`` (revert everything) in that case."""
    path = _scope_dir(repo_root) / f"{_release_token(workspace)}.progress"
    if not path.exists():
        return None
    try:
        sha = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return sha or None


def _forget_release_progress(repo_root: Path, workspace: ReleaseWorkspace) -> None:
    (_scope_dir(repo_root) / f"{_release_token(workspace)}.progress").unlink(missing_ok=True)


def list_cmru_workspaces(repo_root: Path) -> list[ReleaseWorkspace]:
    """Every retained ``cmru/release/*`` or ``cmru/build/*`` worktree.

    ``git worktree add`` records the absolute path it was given at creation
    time, and ``git worktree list`` always reports that same literal path
    back — it never re-resolves it from the *current* process's vantage
    point. This repo is bind-mounted at two different absolute paths (the
    devcontainer's ``/workspaces/vbpub`` and the host's own checkout path),
    so a worktree created from one side is invisible — its recorded path
    genuinely does not exist — when this runs from the other side.  Keep it
    in this discovery listing with an empty ``base`` so an operator still sees
    the path; callers that need to mutate a worktree must require visibility.
    """
    raw = _git(repo_root, "worktree", "list", "--porcelain")
    workspaces: list[ReleaseWorkspace] = []
    for block in raw.split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            if value:
                fields[key] = value
        wt_path, branch_ref = fields.get("worktree"), fields.get("branch")
        if not wt_path or not branch_ref:
            continue
        branch = branch_ref[len("refs/heads/"):] if branch_ref.startswith("refs/heads/") else branch_ref
        if not (branch.startswith("cmru/release/") or branch.startswith("cmru/build/")):
            continue
        path = Path(wt_path)
        if not path.is_dir():
            workspaces.append(ReleaseWorkspace(repo_root, path, branch, ""))
            continue
        base = _git(path, "rev-parse", "HEAD", check=False) or ""
        workspaces.append(ReleaseWorkspace(repo_root, path, branch, base))
    return workspaces


def list_retained_workspaces(repo_root: Path) -> list[ReleaseWorkspace]:
    """Visible retained release worktrees, for release-resume machinery only."""
    return [
        workspace
        for workspace in list_cmru_workspaces(repo_root)
        if workspace.branch.startswith("cmru/release/") and workspace.path.is_dir()
    ]


def abandon_workspace(repo_root: Path, workspace: ReleaseWorkspace) -> None:
    """Fully discard a retained, never-promoted release attempt: its origin backup
    branch, local worktree/branch, and scope marker. Unlike remove_workspace() (the
    success path) this never touches origin/main — a failed release's gates ran
    before promote, so there is nothing there to undo."""
    remove_backup_branch(workspace)
    remove_workspace(workspace)
    forget_release_scope(repo_root, workspace)


def abandon_previous(repo_root: Path, current_projects: Sequence[str]) -> list[str]:
    """Abandon every retained release worktree whose recorded scope overlaps
    ``current_projects`` (S-CLI.1: releases always start fresh, never resume by
    default). Worktrees with no recorded scope are left alone — the caller can
    still target them explicitly via ``--abandon <path>``. Returns the branch
    names abandoned."""
    current = set(current_projects)
    abandoned: list[str] = []
    for workspace in list_retained_workspaces(repo_root):
        scope = read_release_scope(repo_root, workspace)
        if scope is None or not (current & set(scope)):
            continue
        abandon_workspace(repo_root, workspace)
        abandoned.append(workspace.branch)
    return abandoned


_PROMOTE_MAX_RETRIES = 5


def promote_workspace(workspace: ReleaseWorkspace) -> None:
    """Fast-forward remote main from the prepared branch, or fail before publication.

    This repo has other concurrent committers (see AGENTS.md) — a project's own
    prepare/gate cycle can run long enough (an OCI image build especially) that
    ``origin/main`` moves before this push lands. A plain, unretried push would
    fail the WHOLE release over a race that a bare ``git pull --rebase && push``
    would have resolved by hand. So: on a non-fast-forward rejection, fetch and
    rebase this branch's own commits onto the new ``origin/main`` tip and retry,
    bounded to :data:`_PROMOTE_MAX_RETRIES` attempts (a busy-fought repo should
    fail loud, not spin forever). A rebase that hits a REAL conflict is never
    auto-resolved — abort back to a clean state and raise immediately, exactly
    as an unretriable failure would, so the retained worktree stays inspectable.

    Rebasing rewrites every commit SHA in the branch, including any earlier
    project's already-recorded :func:`write_release_progress` checkpoint from
    EARLIER in this same multi-project run — left stale, a later revert would
    compute its undo range against commits that no longer exist on this branch.
    Each successful rebase re-anchors that checkpoint to its rebased equivalent
    by commit COUNT (a plain, non-interactive rebase preserves commit order and
    count 1:1), not by content-matching, which would be fragile.
    """
    checkpoint = read_release_progress(workspace.repo_root, workspace)
    checkpoint_depth = (
        int(_git(workspace.path, "rev-list", "--count", f"{checkpoint}..HEAD"))
        if checkpoint else None
    )
    # Every iteration either succeeds or raises.  An explicit unbounded loop
    # makes that contract structural: there is no phantom fall-through after
    # the bounded retry policy for coverage (or callers) to mistake for a
    # successful promotion.
    attempt = 0
    while True:
        result = subprocess.run(
            ["git", "push", "origin", "HEAD:refs/heads/main"],
            cwd=workspace.path, capture_output=True, text=True,
        )
        if result.returncode == 0:
            return
        stderr = result.stderr or ""
        # Git uses "(non-fast-forward)" when it can tell locally that the
        # remote is ahead, and the more generic "(fetch first)" when it can't
        # (e.g. no recent fetch) — both mean the same thing: the remote moved,
        # rebase and retry. Anything else "[rejected]" (a protected-branch
        # hook, say) would just keep failing every retry too, surfacing as
        # the same "lost the race" error once attempts are exhausted below.
        if "[rejected]" not in stderr:
            raise RuntimeError(f"git push origin HEAD:refs/heads/main failed:\n{stderr}")
        if attempt == _PROMOTE_MAX_RETRIES:
            raise RuntimeError(
                f"git push origin HEAD:refs/heads/main lost the race to a concurrent "
                f"push {_PROMOTE_MAX_RETRIES} time(s) in a row — resolve manually "
                "(this repo has other concurrent committers; see AGENTS.md)."
            )
        subprocess.run(["git", "fetch", "--prune", "origin", "main"], cwd=workspace.path, check=True)
        rebase = subprocess.run(
            ["git", "rebase", "origin/main"], cwd=workspace.path, capture_output=True, text=True,
        )
        if rebase.returncode != 0:
            subprocess.run(["git", "rebase", "--abort"], cwd=workspace.path, check=False)
            raise RuntimeError(
                "A concurrent push landed on origin/main while this release was "
                "running, and rebasing this release's own commits onto it hit a "
                "real conflict — not auto-resolvable. Resolve manually in the "
                f"retained worktree:\n{rebase.stdout}\n{rebase.stderr}"
            )
        if checkpoint_depth is not None:
            checkpoint = _git(workspace.path, "rev-parse", f"HEAD~{checkpoint_depth}")
            write_release_progress(workspace.repo_root, workspace, checkpoint)
        attempt += 1


def push_backup_branch(workspace: ReleaseWorkspace) -> None:
    """Push the validated, gated branch to origin under its own name, before promotion.

    Purely additive durability: a crashed machine or lost worktree after this point
    still leaves an inspectable, resumable copy of the prepared release on origin —
    ``main`` itself is untouched by this push.

    ``--force`` is safe here specifically because ``workspace.branch`` is a
    uuid-scoped name this ONE transaction created and exclusively owns (never a
    shared or human-authored branch) — required, not just permissive, once
    :func:`promote_workspace` has rebased: origin's own copy of this same branch
    name (pushed by an EARLIER call in this same run, before the rebase) then
    diverges from the local rewritten history, and a plain push would itself
    hit the identical non-fast-forward rejection this function exists to avoid.
    """
    subprocess.run(
        ["git", "push", "--force", "origin", f"HEAD:refs/heads/{workspace.branch}"],
        cwd=workspace.path, check=True,
    )


def remove_backup_branch(workspace: ReleaseWorkspace) -> None:
    """Delete the durability backup branch from origin after a fully successful release.

    Best-effort: a release that already succeeded should not fail cleanup over a
    missing/already-gone remote branch.
    """
    subprocess.run(
        ["git", "push", "origin", "--delete", workspace.branch],
        cwd=workspace.path, check=False,
    )


def promotion_landed(repo_root: Path, workspace: ReleaseWorkspace) -> bool:
    """True if ``origin/main`` still sits exactly at this workspace's branch tip.

    Used after a failed release to distinguish "promote_workspace() ran, then a
    later step (tag/build/publish) failed" from "the failure happened before
    promotion" or "origin/main has since moved past this release entirely" — the
    latter two are not safe to auto-revert.
    """
    subprocess.run(["git", "fetch", "--prune", "origin", "main"], cwd=repo_root, check=True)
    origin_main = _git(repo_root, "rev-parse", "origin/main")
    branch_tip = _git(workspace.path, "rev-parse", workspace.branch)
    return origin_main == branch_tip


@dataclass(frozen=True)
class RevertResult:
    ok: bool          # False ⇒ needs manual cleanup (didn't apply cleanly / push rejected)
    reverted: bool    # True ⇒ a revert commit was actually pushed; False ⇒ nothing needed it


def revert_promotion(workspace: ReleaseWorkspace, *, from_sha: str | None = None) -> RevertResult:
    """Best-effort: undo this release's commits on ``origin/main`` by pushing a revert.

    ``from_sha`` scopes the revert to ``(from_sha, branch tip]`` instead of the whole
    transaction (``workspace.base``) — pass the last-fully-completed-project checkpoint
    (:func:`read_release_progress`) in a per-project release run so a later project's
    failure only undoes its own promoted changes, never an earlier project's already
    -published release. Defaults to ``workspace.base`` (revert everything promoted this
    transaction) when omitted, preserving the whole-transaction behavior.

    Caller MUST have already confirmed :func:`promotion_landed` — this never rewrites
    history (no force-push), it only adds a new revert commit on top, so it is safe
    to attempt even if the precondition was checked slightly earlier.

    ``RevertResult.ok`` is False (manual cleanup required) when the revert does not
    apply cleanly or the push is rejected (e.g. someone pushed to main meanwhile).
    ``RevertResult.reverted`` distinguishes "there was nothing to revert" (ok=True,
    reverted=False — e.g. the failing project never got as far as its own promote)
    from "a revert commit was actually pushed" (ok=True, reverted=True) — callers
    that log "reverted" should check ``.reverted``, not just ``.ok``, or they'll
    claim a revert happened when nothing was there to undo.
    """
    base = from_sha if from_sha is not None else workspace.base
    branch_tip = _git(workspace.path, "rev-parse", workspace.branch)
    if base == branch_tip:
        return RevertResult(ok=True, reverted=False)
    result = subprocess.run(
        ["git", "revert", "--no-edit", "--no-commit", f"{base}..{branch_tip}"],
        cwd=workspace.path,
    )
    if result.returncode != 0:
        subprocess.run(["git", "revert", "--abort"], cwd=workspace.path, check=False)
        return RevertResult(ok=False, reverted=False)
    subprocess.run(
        ["git", "commit", "-m", f"revert: undo failed release {workspace.branch}"],
        cwd=workspace.path, check=True,
    )
    push = subprocess.run(["git", "push", "origin", "HEAD:refs/heads/main"], cwd=workspace.path)
    return RevertResult(ok=push.returncode == 0, reverted=push.returncode == 0)


def sync_local_main(repo_root: Path) -> bool:
    """Bring the caller's local ``main`` up to date with ``origin/main``.

    Rebase, not merge — consistent with the rest of this pipeline, which is
    fast-forward-only end to end (promote_workspace's push, revert_promotion's
    push, assert_local_main_not_ahead's precondition): no other step here ever
    produces a merge commit, so local main shouldn't be the one exception. A
    release only ever commits declared, mechanical generated paths (S-REL.4a) —
    never hand-edited source — so local commits added while the release built
    (e.g. ongoing work in another terminal) essentially never touch the same
    files, and replay conflict-free. ``git rebase`` degenerates to a plain
    fast-forward when local main hasn't moved at all (the common case). It is
    only aborted (returning False, leaving local main untouched) on a genuine
    content conflict, which is a signal of unusual overlap worth a human's
    attention. Rewrites local main's own commits onto the new base — safe here
    because they are, by construction, commits the release process never saw
    and the developer has not necessarily pushed anywhere yet.
    """
    subprocess.run(["git", "fetch", "--prune", "origin", "main"], cwd=repo_root, check=True)
    current = _git(repo_root, "branch", "--show-current", check=False)
    if current == "main":
        result = subprocess.run(["git", "rebase", "origin/main"], cwd=repo_root)
        if result.returncode != 0:
            subprocess.run(["git", "rebase", "--abort"], cwd=repo_root, check=False)
        return result.returncode == 0
    local_main = _git(repo_root, "rev-parse", "main", check=False)
    if local_main:
        merge_base = _git(repo_root, "merge-base", "main", "origin/main", check=False)
        if merge_base != local_main:
            return False  # local main has commits of its own — do not force-move it
    result = subprocess.run(["git", "branch", "-f", "main", "origin/main"], cwd=repo_root)
    return result.returncode == 0


def run_child(
    workspace: ReleaseWorkspace, child_args: Sequence[str], *, verb: str = "release",
) -> int:
    """Run a CMRU verb from the snapshot, preserving terminal output.

    Release children use the installed ``cmru`` executable. The root checkout no
    longer carries a Python shim, so a release must be launched after CMRU has
    been bootstrapped and placed on PATH.
    """
    env = os.environ.copy()
    env[CHILD_ENV] = "1"
    env[BRANCH_ENV] = workspace.branch
    env[BASE_ENV] = workspace.base
    launcher = [os.environ.get("CMRU_BIN") or shutil.which("cmru") or "cmru"]
    command = [*launcher, verb, "--_transaction-child", *child_args]
    return subprocess.run(command, cwd=workspace.path, env=env).returncode
