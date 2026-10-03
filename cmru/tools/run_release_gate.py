#!/usr/bin/env python3
"""Run CMRU's registered release lanes without exposing publisher secrets."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
SECRET_ENV_KEYS = ("GITHUB_PUSH_PAT", "GITHUB_TOKEN", "CMRU_GIT_AUTH_TOKEN")
FACTS_ENV_KEY = "CMRU_ASSAY_BASELINE_FACTS"
EXTRA_MOUNTS_ENV_KEY = "RUN_GATE_EXTRA_MOUNTS"


@dataclass(frozen=True)
class _SecretBackup:
    path: Path
    backup: Path
    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    atime_ns: int
    mtime_ns: int


def _load_components():
    """Load the selected worktree's CMRU, Assay and baseline helper sources."""
    for source in (
        PROJECT_ROOT / "src",
        REPOSITORY_ROOT / "assay" / "src",
        REPOSITORY_ROOT / "libraries" / "cli-extended" / "src",
        REPOSITORY_ROOT / "libraries" / "worktree" / "src",
    ):
        sys.path.insert(0, str(source))
    from assay import git as assay_git
    import prepare_assay_baseline

    return assay_git, prepare_assay_baseline


def _common_mount_root(repo_root: Path, assay_git) -> Path:
    raw = assay_git.run(
        repo_root, "rev-parse", "--path-format=absolute", "--git-common-dir",
    ).strip()
    if not raw:
        raise RuntimeError("Assay could not resolve the Git common directory")
    common_dir = Path(raw).resolve()
    # run-gate mounts the repository containing this common directory. This
    # also covers worktrees whose .git file points back to the primary checkout.
    if common_dir.name == ".git":
        return common_dir.parent
    return repo_root.resolve()


def _read_secret(path: Path) -> tuple[bytes, os.stat_result] | None:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError(f"publisher secret must be a regular file: {path}")
    if before.st_uid != os.geteuid():
        raise RuntimeError(f"publisher secret owner cannot be preserved by this process: {path}")
    if before.st_gid != os.getegid() and before.st_gid not in os.getgroups():
        raise RuntimeError(f"publisher secret group cannot be preserved by this process: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise RuntimeError(f"publisher secret changed while being opened: {path}")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read()
    finally:
        os.close(descriptor)
    return data, before


def _write_private_backup(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _secret_overlay_lock(temp_parent: Path) -> Iterator[None]:
    """Serialize CMRU gates that mask shared checkout credentials."""
    lock_path = temp_parent / ".cmru-gate-secret-mask.lock"
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise RuntimeError("publisher-secret gate lock is not a regular file")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _restore(backups: list[_SecretBackup]) -> None:
    errors: list[str] = []
    for item in reversed(backups):
        try:
            try:
                current = item.path.lstat()
            except FileNotFoundError:
                current = None
            if current is not None and (current.st_dev, current.st_ino) == (
                item.device, item.inode,
            ):
                continue
            # A lane may have created a path with this name. Remove that entry
            # itself (including a symlink) before restoring the saved file.
            if current is not None:
                item.path.unlink()
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(item.path, flags, item.mode)
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as stream:
                    with item.backup.open("rb") as source:
                        shutil.copyfileobj(source, stream)
                    stream.flush()
                    os.fchown(descriptor, item.uid, item.gid)
                    os.fchmod(descriptor, item.mode)
                    os.fsync(descriptor)
                os.utime(descriptor, ns=(item.atime_ns, item.mtime_ns))
            finally:
                os.close(descriptor)
        except Exception as exc:  # restore all files before reporting any failure
            errors.append(f"{item.path}: {exc}")
    if errors:
        raise RuntimeError("could not restore publisher secret overlay(s): " + "; ".join(errors))


@contextmanager
def _mask_secret_overlays(
    paths: list[Path],
    *,
    mount_root: Path,
    temp_parent: Path = Path("/tmp"),
) -> Iterator[None]:
    """Point secret paths at private host files outside repo mounts while gated.

    The symlinks keep host-side CMRU consumers able to read the overlays. In a
    tester container, their absolute /tmp targets name the container's private
    tmpfs, not the host backup directory, which is not mounted. If restoration
    fails, the backup directory is retained and named in the raised error.
    """
    mount_root = mount_root.resolve()
    temp_parent = temp_parent.resolve(strict=True)
    if temp_parent == mount_root or temp_parent.is_relative_to(mount_root):
        raise RuntimeError(
            "private publisher-secret backup directory is inside the mounted repository"
        )
    backup_root = Path(tempfile.mkdtemp(prefix="cmru-gate-secrets-", dir=temp_parent))
    if backup_root == mount_root or backup_root.is_relative_to(mount_root):
        shutil.rmtree(backup_root)
        raise RuntimeError(
            "private publisher-secret backup directory is inside the mounted repository"
        )
    backups: list[_SecretBackup] = []
    try:
        for index, path in enumerate(dict.fromkeys(paths)):
            saved = _read_secret(path)
            if saved is None:
                continue
            data, metadata = saved
            backup = backup_root / f"overlay-{index}.bin"
            _write_private_backup(backup, data)
            current = path.lstat()
            if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise RuntimeError(f"publisher secret changed before masking: {path}")
            record = _SecretBackup(
                path=path,
                backup=backup,
                device=metadata.st_dev,
                inode=metadata.st_ino,
                mode=stat.S_IMODE(metadata.st_mode),
                uid=metadata.st_uid,
                gid=metadata.st_gid,
                atime_ns=metadata.st_atime_ns,
                mtime_ns=metadata.st_mtime_ns,
            )
            temporary_link = path.with_name(
                f".{path.name}.mask-{secrets.token_hex(16)}"
            )
            os.symlink(backup, temporary_link)
            backups.append(record)
            try:
                os.replace(temporary_link, path)
            finally:
                temporary_link.unlink(missing_ok=True)
        yield
    finally:
        try:
            _restore(backups)
        except Exception as exc:
            raise RuntimeError(
                f"{exc}; private publisher-secret backups retained at {backup_root}"
            ) from exc
        try:
            shutil.rmtree(backup_root)
        except OSError as exc:
            raise RuntimeError(
                "publisher secret overlays were restored, but private backups "
                f"could not be removed at {backup_root}: {exc}"
            ) from exc


def _lane_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in (*SECRET_ENV_KEYS, FACTS_ENV_KEY, EXTRA_MOUNTS_ENV_KEY):
        environment.pop(key, None)
    return environment


def _secret_overlay_paths(
    mount_root: Path,
    repo_root: Path,
    project_root: Path,
) -> list[Path]:
    """Find CMRU credential overlays visible through the repository mounts."""
    candidates = {
        mount_root / "cmru.secret.toml",
        mount_root / "cmru" / "cmru.secret.toml",
        repo_root / "cmru.secret.toml",
        project_root / "cmru.secret.toml",
    }

    def traversal_failed(error: OSError) -> None:
        raise RuntimeError(f"cannot inventory CMRU secret overlays: {error}") from error

    pruned = {
        ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
        ".venv", "__pycache__", "node_modules", "venv",
    }
    for current, directories, files in os.walk(
        mount_root, topdown=True, followlinks=False, onerror=traversal_failed,
    ):
        current_path = Path(current)
        directories[:] = [
            name for name in directories
            if name not in pruned and not (current_path / name).is_symlink()
        ]
        if "cmru.secret.toml" in files:
            candidates.add(current_path / "cmru.secret.toml")
    return sorted(candidates)


def _invoke_lane(repo_root: Path, lane: str, environment: Mapping[str, str]) -> int:
    result = subprocess.run(
        ["./run-gate.py", "--worktree", str(repo_root), lane],
        cwd=repo_root / "cmru",
        env=dict(environment),
        check=False,
    )
    return result.returncode


def run_release_gate(
    repo_root: Path,
    *,
    temp_parent: Path = Path("/tmp"),
) -> int:
    repo_root = repo_root.resolve(strict=True)
    project_root = repo_root / "cmru"
    if not project_root.is_dir() or project_root.is_symlink():
        raise RuntimeError(f"CMRU project directory is missing or unsafe: {project_root}")
    if PROJECT_ROOT.resolve() != project_root.resolve():
        raise RuntimeError(
            f"release gate helper is not running from the selected worktree: {PROJECT_ROOT}"
        )

    assay_git, baseline = _load_components()
    if assay_git.repo_top(project_root).resolve() != repo_root:
        raise RuntimeError("selected CMRU project does not belong to the requested worktree")
    mount_root = _common_mount_root(repo_root, assay_git)
    temp_parent = temp_parent.resolve(strict=True)
    if temp_parent == mount_root or temp_parent.is_relative_to(mount_root):
        raise RuntimeError("private publisher-secret directory is inside the mounted repository")
    with _secret_overlay_lock(temp_parent):
        return _run_locked_gate(
            repo_root,
            project_root,
            mount_root,
            baseline,
            assay_git,
            temp_parent,
        )


def _run_locked_gate(
    repo_root: Path,
    project_root: Path,
    mount_root: Path,
    baseline,
    assay_git,
    temp_parent: Path,
) -> int:
    auth = baseline._repository_git_auth(repo_root)
    lane_environment = _lane_environment()
    secret_paths = _secret_overlay_paths(mount_root, repo_root, project_root)

    with _mask_secret_overlays(
        secret_paths,
        mount_root=mount_root,
        temp_parent=temp_parent,
    ):
        for lane in ("installed-wheel", "assay", "coverage"):
            result = _invoke_lane(repo_root, lane, lane_environment)
            if result:
                return result

        facts = baseline.build_facts(
            repo_root,
            project_root,
            git_auth=auth,
            assay_git=assay_git,
        )
        facts_json = json.dumps(facts, separators=(",", ":"), sort_keys=True)
        if auth.token and auth.token in facts_json:
            raise RuntimeError("remote baseline facts unexpectedly contain publisher credentials")
        mutation_environment = dict(lane_environment)
        mutation_environment[FACTS_ENV_KEY] = facts_json
        result = _invoke_lane(repo_root, "mutation", mutation_environment)
        if result:
            return result

        for lane in ("canary", "enroll"):
            result = _invoke_lane(repo_root, lane, lane_environment)
            if result:
                return result
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        return run_release_gate(args.worktree)
    except Exception as exc:
        print(f"cmru-release-gate: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
