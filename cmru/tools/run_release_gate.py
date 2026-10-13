#!/usr/bin/env python3
"""Run CMRU's registered release lanes without exposing publisher secrets."""
from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from xml.etree import ElementTree
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
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
    parent_device: int
    parent_inode: int
    backup: Path
    device: int
    inode: int
    mode: int
    uid: int
    gid: int
    atime_ns: int
    mtime_ns: int
    overlay_device: int
    overlay_inode: int


def _load_components():
    """Load the selected worktree's CMRU, Assay and baseline helper sources."""
    for source in (
        PROJECT_ROOT / "src",
        REPOSITORY_ROOT / "assay" / "src",
        # cli_extended is the INSTALLED released wheel (CX-D1), never a source root.
        REPOSITORY_ROOT / "libraries" / "worktree" / "src",
    ):
        sys.path.insert(0, str(source))
    from assay import git as assay_git
    import prepare_assay_baseline

    return assay_git, prepare_assay_baseline


def _preflight_assay_source(repo_root: Path) -> None:
    """Check the source tree before any registered lane can install it."""
    script = PROJECT_ROOT / "tools" / "check_assay_source.py"
    assay_source = repo_root / "assay"
    if script.is_symlink() or not script.is_file():
        raise RuntimeError(f"Assay source preflight is missing or unsafe: {script}")
    try:
        result = subprocess.run(
            [sys.executable, str(script), "--assay-source", str(assay_source)],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("could not run the Assay editable-install source preflight") from exc
    if result.returncode != 0 or "CMRU_ASSAY_SOURCE_PREFLIGHT=clean" not in result.stdout.splitlines():
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"Assay editable-install source preflight refused: {detail}")


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


def _restore(backups: list[_SecretBackup]) -> list[_SecretBackup]:
    """Restore through the original directory handle, retaining uncertain backups."""
    errors: list[str] = []
    retained: list[_SecretBackup] = []
    for item in reversed(backups):
        parent_fd: int | None = None
        try:
            flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            try:
                parent_fd = os.open(item.path.parent, flags)
            except OSError as exc:
                if exc.errno in {
                    errno.EACCES, errno.ELOOP, errno.ENOENT, errno.ENOTDIR,
                }:
                    # Do not inspect or mutate the path through a replacement
                    # parent. The private backup is the recovery copy.
                    retained.append(item)
                    continue
                raise
            parent_metadata = os.fstat(parent_fd)
            if (
                not stat.S_ISDIR(parent_metadata.st_mode)
                or (parent_metadata.st_dev, parent_metadata.st_ino)
                != (item.parent_device, item.parent_inode)
                or not _secret_parent_path_matches(item)
            ):
                retained.append(item)
                continue

            basename = item.path.name
            try:
                current = os.stat(basename, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                current = None
            if current is not None and (current.st_dev, current.st_ino) == (
                item.device, item.inode,
            ):
                if _secret_parent_path_matches(item):
                    continue
                retained.append(item)
                continue
            if current is not None:
                displaced = f".{basename}.restore-{secrets.token_hex(16)}"
                # Move the current directory entry atomically before deciding
                # whether it is our overlay. A prior lstat followed by unlink
                # could erase a credential atomically rotated into this path
                # between those operations. The directory FD pins this move
                # to the parent that was masked, even if its pathname changes.
                os.replace(
                    basename, displaced,
                    src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
                )
                displaced_metadata = os.stat(
                    displaced, dir_fd=parent_fd, follow_symlinks=False,
                )
                displaced_identity = (displaced_metadata.st_dev, displaced_metadata.st_ino)
                if displaced_identity == (item.device, item.inode):
                    try:
                        os.link(
                            displaced, basename,
                            src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
                            follow_symlinks=False,
                        )
                    except OSError as exc:
                        raise RuntimeError(
                            f"the original publisher secret was moved during restoration; "
                            f"it is preserved at {item.path.parent / displaced}: {exc}"
                        ) from exc
                    os.unlink(displaced, dir_fd=parent_fd)
                    if _secret_parent_path_matches(item):
                        continue
                    retained.append(item)
                    continue
                if displaced_identity != (item.overlay_device, item.overlay_inode):
                    try:
                        # link() is atomic and fails if a newer entry has
                        # appeared, preserving both versions instead of
                        # replacing either one.
                        os.link(
                            displaced, basename,
                            src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
                            follow_symlinks=False,
                        )
                    except OSError as exc:
                        raise RuntimeError(
                            "publisher secret changed while the gate was running; "
                            f"preserving the replacement at {item.path.parent / displaced} "
                            f"and any newer path entry: {exc}"
                        ) from exc
                    os.unlink(displaced, dir_fd=parent_fd)
                    raise RuntimeError(
                        "publisher secret changed while the gate was running; "
                        "preserving the replacement"
                    )
                os.unlink(displaced, dir_fd=parent_fd)
            file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            file_flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(basename, file_flags, item.mode, dir_fd=parent_fd)
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
            if not _secret_parent_path_matches(item):
                # All writes above used the opened parent, never the possibly
                # replaced pathname. Keep the backup if that parent moved.
                retained.append(item)
        except Exception as exc:  # restore all files before reporting any failure
            if not _secret_parent_path_matches(item):
                retained.append(item)
                continue
            errors.append(f"{item.path}: {exc}")
        finally:
            if parent_fd is not None:
                os.close(parent_fd)
    if errors:
        raise RuntimeError("could not restore publisher secret overlay(s): " + "; ".join(errors))
    return retained


def _secret_parent_path_matches(item: _SecretBackup) -> bool:
    """Return whether the pathname still names the directory masked earlier."""
    try:
        metadata = item.path.parent.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(metadata.st_mode)
        and (metadata.st_dev, metadata.st_ino)
        == (item.parent_device, item.parent_inode)
    )


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
    restoring = False
    previous_signal_handlers = {}

    def _restore_on_termination_signal(signum, _frame) -> None:
        if restoring:
            return
        raise SystemExit(128 + signum)

    try:
        for signum in (signal.SIGTERM, signal.SIGHUP):
            previous_signal_handlers[signum] = signal.signal(
                signum, _restore_on_termination_signal,
            )
        for index, path in enumerate(dict.fromkeys(paths)):
            try:
                parent_metadata = path.parent.lstat()
            except (FileNotFoundError, NotADirectoryError):
                # Inventory may have found an overlay in a nested worktree
                # that was removed before masking began. There is no file to
                # expose or restore at that path now.
                continue
            if not stat.S_ISDIR(parent_metadata.st_mode):
                raise RuntimeError(f"publisher secret parent is not a real directory: {path.parent}")
            saved = _read_secret(path)
            if saved is None:
                continue
            data, metadata = saved
            try:
                parent_recheck = path.parent.lstat()
            except (FileNotFoundError, NotADirectoryError) as exc:
                raise RuntimeError(
                    f"publisher secret parent changed before masking: {path.parent}"
                ) from exc
            if (parent_metadata.st_dev, parent_metadata.st_ino) != (
                parent_recheck.st_dev, parent_recheck.st_ino,
            ):
                raise RuntimeError(f"publisher secret parent changed before masking: {path.parent}")
            backup = backup_root / f"overlay-{index}.bin"
            _write_private_backup(backup, data)
            current = path.lstat()
            if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise RuntimeError(f"publisher secret changed before masking: {path}")
            temporary_link = path.with_name(
                f".{path.name}.mask-{secrets.token_hex(16)}"
            )
            displaced = path.with_name(
                f".{path.name}.mask-displaced-{secrets.token_hex(16)}"
            )
            os.symlink(backup, temporary_link)
            try:
                overlay_metadata = temporary_link.lstat()
                record = _SecretBackup(
                    path=path,
                    parent_device=parent_metadata.st_dev,
                    parent_inode=parent_metadata.st_ino,
                    backup=backup,
                    device=metadata.st_dev,
                    inode=metadata.st_ino,
                    mode=stat.S_IMODE(metadata.st_mode),
                    uid=metadata.st_uid,
                    gid=metadata.st_gid,
                    atime_ns=metadata.st_atime_ns,
                    mtime_ns=metadata.st_mtime_ns,
                    overlay_device=overlay_metadata.st_dev,
                    overlay_inode=overlay_metadata.st_ino,
                )
                backups.append(record)
                # Move the visible entry first, then inspect the inode that was
                # actually moved. Replacing the path with the overlay directly
                # could erase a credential rotated in after the lstat above.
                os.replace(path, displaced)
                moved = displaced.lstat()
                if (moved.st_dev, moved.st_ino) != (metadata.st_dev, metadata.st_ino):
                    backups.pop()
                    try:
                        os.link(displaced, path, follow_symlinks=False)
                    except OSError as exc:
                        raise RuntimeError(
                            "publisher secret changed while masking; "
                            f"preserving the moved entry at {displaced}: {exc}"
                        ) from exc
                    displaced.unlink()
                    raise RuntimeError(f"publisher secret changed before masking: {path}")
                try:
                    # Hard-linking the symlink itself is an exclusive install:
                    # it fails if another writer has created a new path entry.
                    os.link(temporary_link, path, follow_symlinks=False)
                except OSError as exc:
                    backups.pop()
                    try:
                        path.lstat()
                    except FileNotFoundError:
                        try:
                            os.link(displaced, path, follow_symlinks=False)
                        except OSError as restore_exc:
                            raise RuntimeError(
                                "could not install the publisher-secret overlay; "
                                f"the original is preserved at {displaced}: {restore_exc}"
                            ) from exc
                        displaced.unlink()
                    else:
                        raise RuntimeError(
                            "could not install the publisher-secret overlay without "
                            "replacing a concurrent path entry; "
                            f"the original is preserved at {displaced}: {exc}"
                        ) from exc
                    raise RuntimeError(
                        f"could not install the publisher-secret overlay at {path}: {exc}"
                    ) from exc
                displaced.unlink()
            finally:
                temporary_link.unlink(missing_ok=True)
        yield
    finally:
        restoring = True
        try:
            try:
                retained = _restore(backups)
            except Exception as exc:
                raise RuntimeError(
                    f"{exc}; private publisher-secret backups retained at {backup_root}"
                ) from exc
            if retained:
                details = "; ".join(
                    f"{item.path} (original retained at {item.backup})"
                    for item in retained
                )
                raise RuntimeError(
                    "publisher-secret restoration incomplete because a secret parent "
                    f"disappeared or changed: {details}; private backups retained at "
                    f"{backup_root}"
                )
            else:
                try:
                    shutil.rmtree(backup_root)
                except OSError as exc:
                    raise RuntimeError(
                        "publisher secret overlays were restored, but private backups "
                        f"could not be removed at {backup_root}: {exc}"
                    ) from exc
        finally:
            for signum, previous_handler in previous_signal_handlers.items():
                signal.signal(signum, previous_handler)


def _lane_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in (*SECRET_ENV_KEYS, FACTS_ENV_KEY, EXTRA_MOUNTS_ENV_KEY):
        environment.pop(key, None)
    return environment


def _secret_overlay_paths(
    repo_root: Path,
    project_root: Path,
) -> list[Path]:
    """Find secret overlays inside the linked worktree mounted by run-gate.

    The Git common directory can be shared by many worktrees, but its sibling
    checkouts are not mounted into a linked-worktree lane. Walking that common
    root makes an unrelated worktree appearing or disappearing during a long
    release gate affect the result. The release gate refuses a main checkout:
    Run-Gate mounts that whole path, including sibling checkouts under
    ``.worktrees``.
    """
    candidates = {
        repo_root / "cmru.secret.toml",
        project_root / "cmru.secret.toml",
    }

    def tester_unified_uid() -> int:
        """Derive the lane UID from the image's final Docker USER declaration."""
        dockerfile = repo_root / "tester-unified" / "Dockerfile"
        try:
            lines = dockerfile.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise RuntimeError(
                f"cannot derive tester-unified's run UID from {dockerfile}"
            ) from exc
        users = [
            match.group(1)
            for line in lines
            if (match := re.fullmatch(r"USER\s+([^\s#]+)\s*(?:#.*)?", line.strip()))
        ]
        if not users:
            raise RuntimeError(f"tester-unified has no final USER declaration: {dockerfile}")
        uid_text = users[-1].split(":", 1)[0]
        if not uid_text.isdecimal():
            raise RuntimeError(
                f"cannot derive tester-unified's numeric run UID from {dockerfile}: {users[-1]}"
            )
        return int(uid_text)

    def traversal_failed(error: OSError) -> None:
        # The gate's UID is the final USER in tester-unified, not the host
        # caller's EUID. Conservatively account for any group execute bit,
        # because observer lanes add a supplementary group. We skip only a
        # directory that the lane credentials cannot traverse at all.
        if isinstance(error, (PermissionError, FileNotFoundError)) and error.filename is not None:
            try:
                metadata = os.lstat(error.filename)
            except FileNotFoundError:
                print(
                    "cmru-release-gate: WARN: skipping vanished directory during "
                    f"secret-overlay inventory: {error.filename}",
                    file=sys.stderr,
                )
                return
            except OSError:
                metadata = None
            if metadata is not None and isinstance(error, PermissionError):
                lane_uid = tester_unified_uid()
                lane_can_traverse = (
                    (
                        metadata.st_uid == lane_uid
                        and bool(metadata.st_mode & stat.S_IXUSR)
                    )
                    or bool(metadata.st_mode & (stat.S_IXGRP | stat.S_IXOTH))
                )
                if stat.S_ISDIR(metadata.st_mode) and not lane_can_traverse:
                    print(
                        "cmru-release-gate: WARN: skipping directory not traversable "
                        f"by tester-unified UID {lane_uid}: {error.filename}",
                        file=sys.stderr,
                    )
                    return
        raise RuntimeError(f"cannot inventory CMRU secret overlays: {error}") from error

    # The tester mounts the selected worktree as a whole. Virtual environments,
    # bytecode caches and dependency directories are still visible to the lane,
    # so a cmru.secret.toml hidden there must be masked too. Only Git metadata
    # is excluded from this working-tree inventory.
    pruned = {".git"}
    for current, directories, files in os.walk(
        repo_root, topdown=True, followlinks=False, onerror=traversal_failed,
    ):
        current_path = Path(current)
        directories[:] = [
            name for name in directories
            if name not in pruned and not (current_path / name).is_symlink()
        ]
        if "cmru.secret.toml" in files:
            candidates.add(current_path / "cmru.secret.toml")
    return sorted(candidates)


def _is_failure_line(line: str) -> bool:
    return line.startswith(("FAILED ", "ERROR "))


def _report_lane_failure(project_root: Path, lane: str, since_ns: int) -> None:
    """Name the failing tests of a failed lane (BG-03).

    A lane's own console output can hide pytest's short-summary lines (assay
    keeps them only in the verdict's ``result_stdout_tail``). Print every
    ``FAILED``/``ERROR`` line from verdicts and junit files written by this
    lane run so the release log always names the test.
    """
    lines: list[str] = []
    for verdict in sorted((project_root / ".assay").glob("verdict-*.json")):
        try:
            if verdict.stat().st_mtime_ns < since_ns:
                continue
            tail = json.loads(verdict.read_text(encoding="utf-8")).get("result_stdout_tail")
        except (OSError, ValueError):
            continue
        if isinstance(tail, str):
            lines.extend(line for line in tail.splitlines() if _is_failure_line(line))
    junit = project_root / "junit-coverage.xml"
    try:
        if junit.stat().st_mtime_ns >= since_ns:
            for case in ElementTree.parse(junit).iter("testcase"):
                if case.find("failure") is not None or case.find("error") is not None:
                    lines.append(f"FAILED {case.get('classname')}::{case.get('name')} (junit)")
    except (OSError, ElementTree.ParseError):
        pass
    if lines:
        print(f"cmru-release-gate: lane {lane!r} failed; failing tests:", file=sys.stderr)
        for line in dict.fromkeys(lines):
            print(f"  {line}", file=sys.stderr)


POSTPONED_MARKER = Path(".assay") / "mutation-postponed-cmru.json"
POSTPONE_REASON = (
    "operator-approved provisional release: the R2 mutation lane was skipped and "
    "must be run afterwards against the released tag"
)


def _tracking_id(value: str) -> str:
    """Return a stripped, non-empty postponement tracking id or refuse."""
    tracking_id = value.strip()
    if not tracking_id:
        raise argparse.ArgumentTypeError("the postponement tracking id must be non-empty")
    return tracking_id


def _record_postponement(project_root: Path, tracking_id: str) -> Path:
    """Write the retained marker saying the mutation lane was skipped, and WARN.

    The marker lives under ``.assay`` so cmru's ``evidence_paths`` retain it
    with the rest of the gate evidence; it is never written by a full gate.
    """
    marker = project_root / POSTPONED_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": 1,
        "lane": "mutation",
        "status": "postponed",
        "tracking_id": tracking_id,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reason": POSTPONE_REASON,
    }
    marker.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"cmru-release-gate: WARN: mutation lane POSTPONED ({tracking_id}); "
        f"this gate is PROVISIONAL; marker {marker}",
        file=sys.stderr,
    )
    return marker


def _invoke_lane(repo_root: Path, lane: str, environment: Mapping[str, str]) -> int:
    started = time.time_ns()
    result = subprocess.run(
        ["./run-gate.py", "--worktree", str(repo_root), lane],
        cwd=repo_root / "cmru",
        env=dict(environment),
        check=False,
    )
    if result.returncode:
        _report_lane_failure(repo_root / "cmru", lane, started)
    return result.returncode


def run_release_gate(
    repo_root: Path,
    *,
    temp_parent: Path = Path("/tmp"),
    postpone_mutation: str | None = None,
) -> int:
    if postpone_mutation is not None:
        postpone_mutation = _tracking_id(postpone_mutation)
    repo_root = repo_root.resolve(strict=True)
    project_root = repo_root / "cmru"
    if not project_root.is_dir() or project_root.is_symlink():
        raise RuntimeError(f"CMRU project directory is missing or unsafe: {project_root}")
    if PROJECT_ROOT.resolve() != project_root.resolve():
        raise RuntimeError(
            f"release gate helper is not running from the selected worktree: {PROJECT_ROOT}"
        )
    git_marker = repo_root / ".git"
    try:
        git_marker_metadata = git_marker.lstat()
    except OSError as exc:
        raise RuntimeError(
            "CMRU release gate requires a linked Git worktree; a main checkout "
            "can expose publisher secrets from sibling worktrees"
        ) from exc
    if not stat.S_ISREG(git_marker_metadata.st_mode):
        raise RuntimeError(
            "CMRU release gate requires a linked Git worktree; a main checkout "
            "can expose publisher secrets from sibling worktrees"
        )

    _preflight_assay_source(repo_root)
    assay_git, baseline = _load_components()
    if assay_git.repo_top(project_root).resolve() != repo_root:
        raise RuntimeError("selected CMRU project does not belong to the requested worktree")
    temp_parent = temp_parent.resolve(strict=True)
    if temp_parent == repo_root or temp_parent.is_relative_to(repo_root):
        raise RuntimeError("private publisher-secret directory is inside the judged worktree")
    with _secret_overlay_lock(temp_parent):
        return _run_locked_gate(
            repo_root,
            project_root,
            baseline,
            assay_git,
            temp_parent,
            postpone_mutation,
        )


def _run_locked_gate(
    repo_root: Path,
    project_root: Path,
    baseline,
    assay_git,
    temp_parent: Path,
    postpone_mutation: str | None = None,
) -> int:
    auth = baseline._repository_git_auth(repo_root)
    lane_environment = _lane_environment()
    secret_paths = _secret_overlay_paths(repo_root, project_root)

    with _mask_secret_overlays(
        secret_paths,
        mount_root=repo_root,
        temp_parent=temp_parent,
    ):
        for lane in ("installed-wheel", "assay", "coverage"):
            result = _invoke_lane(repo_root, lane, lane_environment)
            if result:
                return result

        if postpone_mutation is not None:
            # Provisional gate: ONLY the mutation lane (and its remote-facts
            # preparation) is skipped; the postponement is recorded as evidence.
            _record_postponement(project_root, postpone_mutation)
        else:
            # A full gate never leaves an earlier provisional marker behind.
            (project_root / POSTPONED_MARKER).unlink(missing_ok=True)
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

        result = _invoke_lane(repo_root, "canary", lane_environment)
        if result:
            return result
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument(
        "--postpone-mutation",
        metavar="TRACKING-ID",
        type=_tracking_id,
        default=None,
        help=(
            "skip ONLY the mutation lane (PROVISIONAL gate) and record the "
            "postponement under TRACKING-ID in .assay/mutation-postponed-cmru.json"
        ),
    )
    args = parser.parse_args(argv)
    try:
        return run_release_gate(args.worktree, postpone_mutation=args.postpone_mutation)
    except Exception as exc:
        print(f"cmru-release-gate: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
