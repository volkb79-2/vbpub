#!/usr/bin/env python3
"""Recheck B131 pilot artifacts on the host after the judge container exits."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _identity(info: os.stat_result) -> dict[str, int]:
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "links": info.st_nlink,
        "mode": stat.S_IFMT(info.st_mode) | stat.S_IMODE(info.st_mode),
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
    }


def _read_regular(path: Path, *, maximum: int) -> tuple[bytes, dict[str, Any]]:
    requested = Path(os.path.normpath(os.path.abspath(os.fspath(path))))
    parent = requested.parent.resolve(strict=True)
    parent_fd = os.open(
        parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    )
    descriptor: int | None = None
    try:
        parent_before = os.fstat(parent_fd)
        before = os.stat(requested.name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
            raise ValueError(f"evidence path is not a regular non-symlink file: {path}")
        if before.st_nlink != 1:
            raise ValueError(f"evidence file has {before.st_nlink} hard links: {path}")
        if before.st_size > maximum:
            raise ValueError(f"evidence file exceeds its size limit: {path}")
        descriptor = os.open(
            requested.name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(before):
            raise ValueError(f"evidence file changed while opening: {path}")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > maximum:
                raise ValueError(f"evidence file exceeds its size limit: {path}")
        after = os.fstat(descriptor)
        entry_after = os.stat(requested.name, dir_fd=parent_fd, follow_symlinks=False)
        parent_after = os.fstat(parent_fd)
        if (
            _identity(opened) != _identity(after)
            or _identity(after) != _identity(entry_after)
            or _identity(parent_before) != _identity(parent_after)
            or size != after.st_size
        ):
            raise ValueError(f"evidence file changed while reading: {path}")
        return b"".join(chunks), {
            "path": str(parent / requested.name),
            "parent": {"device": parent_before.st_dev, "inode": parent_before.st_ino},
            "file": _identity(after),
        }
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_fd)


def _stat_regular(path: Path) -> dict[str, Any]:
    requested = Path(os.path.normpath(os.path.abspath(os.fspath(path))))
    parent = requested.parent.resolve(strict=True)
    parent_fd = os.open(
        parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    )
    descriptor: int | None = None
    try:
        parent_before = os.fstat(parent_fd)
        before = os.stat(requested.name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode) or before.st_nlink != 1:
            raise ValueError(f"evidence path is not a single-link regular file: {path}")
        descriptor = os.open(
            requested.name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
        opened = os.fstat(descriptor)
        after = os.stat(requested.name, dir_fd=parent_fd, follow_symlinks=False)
        parent_after = os.fstat(parent_fd)
        if (
            _identity(before) != _identity(opened)
            or _identity(opened) != _identity(after)
            or _identity(parent_before) != _identity(parent_after)
        ):
            raise ValueError(f"evidence path changed during metadata check: {path}")
        return {
            "path": str(parent / requested.name),
            "parent": {"device": parent_before.st_dev, "inode": parent_before.st_ino},
            "file": _identity(opened),
        }
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_fd)


def _directory(path: Path) -> dict[str, Any]:
    requested = Path(os.path.normpath(os.path.abspath(os.fspath(path))))
    parent = requested.parent.resolve(strict=True)
    descriptor = os.open(
        requested, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    )
    try:
        before = os.fstat(descriptor)
        path_info = os.stat(requested, follow_symlinks=False)
        if stat.S_ISLNK(path_info.st_mode) or not stat.S_ISDIR(path_info.st_mode):
            raise ValueError(f"evidence directory is not a real directory: {path}")
        if (before.st_dev, before.st_ino) != (path_info.st_dev, path_info.st_ino):
            raise ValueError(f"evidence directory changed while opening: {path}")
        names: list[str] = []
        for entry in os.scandir(descriptor):
            info = os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise ValueError(f"evidence directory has a non-regular entry: {entry.name}")
            if info.st_nlink != 1:
                raise ValueError(f"evidence directory entry has multiple links: {entry.name}")
            names.append(entry.name)
        after = os.fstat(descriptor)
        if _identity(before) != _identity(after):
            raise ValueError(f"evidence directory changed while listing: {path}")
        return {
            "path": str(parent / requested.name),
            "directory": _identity(after),
            "names": sorted(names),
        }
    finally:
        os.close(descriptor)


def _require_absent(path: Path) -> dict[str, Any]:
    requested = Path(os.path.normpath(os.path.abspath(os.fspath(path))))
    parent = requested.parent.resolve(strict=True)
    descriptor = os.open(
        parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    )
    try:
        before = os.fstat(descriptor)
        try:
            os.stat(requested.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError(f"pilot verdict must remain absent: {path}")
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError(f"pilot verdict parent changed while checked: {path}")
        return {
            "path": str(parent / requested.name),
            "parent": {"device": before.st_dev, "inode": before.st_ino},
            "absent": True,
        }
    finally:
        os.close(descriptor)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate attestation field: {key}")
        result[key] = value
    return result


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _check_active_deadline(
    raw: bytes, *, expected_commit: str, expected_tree: str
) -> None:
    try:
        document = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_object
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"campaign deadline is malformed at final verification: {exc}") from exc
    expected_fields = {
        "schema", "campaign", "commit", "git_tree", "lanes", "assay_version",
        "wheel_sha256", "plan_sha256", "created_at_utc", "expires_at_utc",
    }
    if not isinstance(document, dict) or set(document) != expected_fields:
        raise ValueError("campaign deadline has missing or unknown fields at final verification")
    if (
        document.get("schema") != "assay-campaign-deadline/1"
        or document.get("campaign") != f"analysis-r2-pilot-{expected_commit[:12]}"
        or document.get("commit") != expected_commit
        or document.get("git_tree") != expected_tree
        or document.get("lanes") != ["analysis-r2"]
        or not isinstance(document.get("assay_version"), str)
        or not document["assay_version"]
        or not isinstance(document.get("wheel_sha256"), str)
        or len(document["wheel_sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in document["wheel_sha256"])
        or not isinstance(document.get("plan_sha256"), dict)
        or set(document["plan_sha256"]) != {"analysis-r2"}
        or not isinstance(document["plan_sha256"]["analysis-r2"], str)
        or len(document["plan_sha256"]["analysis-r2"]) != 64
        or any(
            character not in "0123456789abcdef"
            for character in document["plan_sha256"]["analysis-r2"]
        )
    ):
        raise ValueError("campaign deadline binding is invalid at final verification")
    try:
        created = datetime.strptime(
            document["created_at_utc"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
        expires = datetime.strptime(
            document["expires_at_utc"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ValueError("campaign deadline timestamps are malformed at final verification") from exc
    now = _utc_now()
    if (
        created > now
        or expires - created not in {timedelta(seconds=7200), timedelta(seconds=7201)}
        or expires <= now
    ):
        raise ValueError("campaign deadline expired during final host verification")


def _git(project: Path, *arguments: str) -> str:
    environment = os.environ.copy()
    for key in tuple(environment):
        if key.startswith("GIT_"):
            environment.pop(key, None)
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(
        ["git", "-C", str(project.parent), *arguments],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"cannot recheck judged Git source: {result.stderr.strip()}")
    return result.stdout.strip()


def _check_worktree_source(
    project: Path, *, expected_commit: str, expected_tree: str
) -> None:
    repository = project.parent.resolve(strict=True)
    top_level = Path(_git(project, "rev-parse", "--show-toplevel")).resolve(strict=True)
    if top_level != repository:
        raise ValueError("project root no longer belongs to the admitted worktree")
    if _git(project, "rev-parse", "HEAD") != expected_commit:
        raise ValueError("judged worktree HEAD changed during host evidence verification")
    if _git(project, "rev-parse", "HEAD^{tree}") != expected_tree:
        raise ValueError("judged worktree tree changed during host evidence verification")
    if _git(project, "status", "--porcelain=1", "--untracked-files=all"):
        raise ValueError("judged worktree became dirty during host evidence verification")


def _check(args: argparse.Namespace) -> str:
    attestation_raw, attestation_identity = _read_regular(
        args.attestation, maximum=1024 * 1024
    )
    attestation_sha256 = hashlib.sha256(attestation_raw).hexdigest()
    if attestation_sha256 != args.expected_sha256:
        raise ValueError("container attestation digest differs from its completion marker")
    try:
        document = json.loads(
            attestation_raw.decode("utf-8"), object_pairs_hook=_unique_object
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"container attestation is malformed: {exc}") from exc
    if not isinstance(document, dict) or set(document) != {
        "schema", "commit", "files", "state_directory", "verdict"
    }:
        raise ValueError("container attestation has missing or unknown fields")
    if (
        document["schema"] != "assay-analysis-r2-pilot-evidence-attestation/2"
        or document["commit"] != args.expected_commit
    ):
        raise ValueError("container attestation schema or commit differs from the gate")

    project = Path(args.project_root).resolve(strict=True)
    state_dir = project / ".assay/analysis-r2-pilot-state"
    expected_paths = {
        "plan": project / ".assay/analysis-r2-pilot-plan.json",
        "selection": project / ".assay/analysis-r2-pilot-selection.json",
        "candidate file": project / ".assay/analysis-r2-pilot-candidates.txt",
        "summary": project / ".assay/analysis-r2-pilot-summary.json",
        "run log": project / ".assay/analysis-r2-pilot-run.log",
        "progress": project / ".assay/progress-analysis-r2-pilot.jsonl",
        "deadline": Path(os.path.normpath(os.path.abspath(os.fspath(args.deadline)))),
        "manifest": project / ".assay/r2-manifest-analysis-r2-pilot.txt",
    }
    limits = {
        "plan": 64 * 1024 * 1024,
        "selection": 64 * 1024 * 1024,
        "candidate file": 64 * 1024 * 1024,
        "summary": 64 * 1024 * 1024,
        "run log": 64 * 1024 * 1024,
        "progress": 16 * 1024 * 1024,
        "deadline": 64 * 1024,
        "manifest": 64 * 1024 * 1024,
    }
    files = document["files"]
    state_directory = document["state_directory"]
    if not isinstance(files, dict) or not isinstance(state_directory, dict):
        raise ValueError("container attestation file inventory is malformed")
    names = state_directory.get("names")
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise ValueError("container attestation state inventory is malformed")
    if names != sorted(set(names)) or "PILOT-STATE" not in names:
        raise ValueError("container attestation state inventory is not canonical")
    expected_labels = {**expected_paths}
    expected_labels.update({f"state/{name}": state_dir / name for name in names})
    if set(files) != set(expected_labels):
        raise ValueError("container attestation does not name every expected evidence file")

    observed: dict[str, dict[str, Any]] = {}
    deadline_raw: bytes | None = None
    for label, path in expected_labels.items():
        entry = files[label]
        if not isinstance(entry, dict) or set(entry) != {"identity", "sha256"}:
            raise ValueError(f"container attestation entry {label!r} is malformed")
        identity = entry["identity"]
        if not isinstance(identity, dict) or identity.get("path") != str(path):
            raise ValueError(f"container attestation path for {label!r} is incorrect")
        maximum = limits.get(label, 16 * 1024 * 1024)
        raw, current_identity = _read_regular(path, maximum=maximum)
        if (
            current_identity != identity
            or hashlib.sha256(raw).hexdigest() != entry["sha256"]
        ):
            raise ValueError(f"host evidence differs from the container attestation: {label}")
        if label == "deadline":
            deadline_raw = raw
        observed[label] = identity

    def recheck_inventory(*, phase: str) -> None:
        for label, path in expected_labels.items():
            raw, current_identity = _read_regular(
                path, maximum=limits.get(label, 16 * 1024 * 1024)
            )
            if (
                current_identity != observed[label]
                or hashlib.sha256(raw).hexdigest() != files[label]["sha256"]
            ):
                raise ValueError(f"host evidence changed {phase}: {label}")

    expected_directory = _directory(state_dir)
    if expected_directory != state_directory:
        raise ValueError("host state directory differs from the container attestation")
    verdict_path = project / ".assay/verdict-analysis-r2.json"
    if _require_absent(verdict_path) != document["verdict"]:
        raise ValueError("host verdict absence differs from the container attestation")

    # Recheck the whole inventory after all content reads. This catches a file
    # replaced while a later artifact was being hashed.
    recheck_inventory(phase="before final verification")
    if _directory(state_dir) != state_directory:
        raise ValueError("host state directory changed before final verification")
    if _require_absent(verdict_path) != document["verdict"]:
        raise ValueError("host verdict appeared before final verification")
    if _read_regular(args.attestation, maximum=1024 * 1024)[0] != attestation_raw:
        raise ValueError("container attestation changed during host verification")
    if _stat_regular(args.attestation) != attestation_identity:
        raise ValueError("container attestation identity changed during host verification")
    if deadline_raw is None:
        raise ValueError("campaign deadline is absent from the attested inventory")
    _check_worktree_source(
        project,
        expected_commit=args.expected_commit,
        expected_tree=args.expected_tree,
    )
    # The source check can take long enough for ignored pilot outputs to be
    # changed in place. Rehash the whole attested inventory after that check,
    # not just the state directory and deadline.
    recheck_inventory(phase="during final source verification")
    if _directory(state_dir) != state_directory:
        raise ValueError("host state directory changed during final source verification")
    if _require_absent(verdict_path) != document["verdict"]:
        raise ValueError("host verdict appeared during final source verification")
    final_attestation_raw, final_attestation_identity = _read_regular(
        args.attestation, maximum=1024 * 1024
    )
    if (
        final_attestation_raw != attestation_raw
        or final_attestation_identity != attestation_identity
    ):
        raise ValueError("container attestation changed during final source verification")
    final_deadline_raw, final_deadline_identity = _read_regular(
        expected_paths["deadline"], maximum=limits["deadline"]
    )
    if (
        final_deadline_raw != deadline_raw
        or final_deadline_identity != observed["deadline"]
        or hashlib.sha256(final_deadline_raw).hexdigest()
        != files["deadline"]["sha256"]
    ):
        raise ValueError("campaign deadline changed during final host verification")
    _check_active_deadline(
        final_deadline_raw,
        expected_commit=args.expected_commit,
        expected_tree=args.expected_tree,
    )
    return attestation_sha256


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--deadline", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-tree", required=True)
    parser.add_argument("--expected-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        digest = _check(args)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"analysis_r2_pilot_host_check: {exc}", file=sys.stderr)
        return 2
    print(f"ANALYSIS_R2_PILOT_HOST_EVIDENCE_VERIFIED={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
