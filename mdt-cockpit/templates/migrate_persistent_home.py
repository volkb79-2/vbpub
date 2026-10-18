#!/usr/bin/env python3
"""Migrate an old MDT container home into the full-home host mount.

Run on the host while the source devcontainer is stopped. The default is a
read-only dry run. ``--apply`` streams ``docker cp`` archives through a
temporary sibling directory, skips image-owned runtime payloads, writes a
completion marker, then atomically installs the new home source. Existing
legacy mounts are never modified.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import BinaryIO, Iterator


PARENT_NAME = "mdt--mounted-folders"
HOME_SOURCE_NAME = "home-vscode"
MARKER_NAME = ".mdt-home-migration.json"
SCHEMA_VERSION = 1
CONTAINER_HOME = "/home/vscode/."
IMAGE_VENV_NAMES = {".venv"}
IMAGE_VENV_RE = re.compile(r"\.venv-py[0-9]+\Z")


class MigrationError(RuntimeError):
    """The home cannot be migrated safely."""


def _normalized_member_name(name: str) -> PurePosixPath | None:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise MigrationError(f"unsafe path in docker archive: {name!r}")
    parts = tuple(part for part in path.parts if part not in ("", "."))
    if not parts:
        return None
    return PurePosixPath(*parts)


def _mapped_path(relative: PurePosixPath) -> tuple[PurePosixPath | None, str | None]:
    """Return the destination path and an exclusion reason, if any."""
    parts = relative.parts
    first = parts[0]
    if first == ".ssh-host":
        return None, "read-only native host SSH bind"
    if first in IMAGE_VENV_NAMES or IMAGE_VENV_RE.fullmatch(first):
        return None, "image-owned runtime"
    if first == ".oh-my-zsh":
        if len(parts) >= 2 and parts[1] == "custom":
            suffix = parts[2:]
            return PurePosixPath(".oh-my-zsh-custom", *suffix), None
        return None, "image-owned Oh My Zsh distribution"
    if parts[:3] == (".codex", "packages", "standalone"):
        return None, "standalone Codex runtime payload"
    return relative, None


class ArchivePlan:
    def __init__(self) -> None:
        self.files = 0
        self.directories = 0
        self.symlinks = 0
        self.bytes = 0
        self.excluded: dict[str, tuple[str, int]] = {}
        self.unsupported: list[str] = []
        self.seen: set[PurePosixPath] = set()
        self.directory_modes: list[tuple[PurePosixPath, int]] = []
        self.file_hashes: dict[PurePosixPath, tuple[int, str]] = {}

    def exclude(self, reason: str, name: str) -> None:
        count, size = self.excluded.get(reason, (0, 0))
        self.excluded[reason] = (count + 1, size)


def _safe_parent(root: Path, relative: PurePosixPath) -> Path:
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise MigrationError(f"archive path traverses a symlink: {relative}")
        if current.exists() and not current.is_dir():
            raise MigrationError(f"archive path traverses a non-directory: {relative}")
        current.mkdir(mode=0o700, exist_ok=True)
    return current / relative.name


def _member_sha256(archive: tarfile.TarFile, member: tarfile.TarInfo) -> str:
    source = archive.extractfile(member)
    if source is None:
        raise MigrationError(f"archive file has no payload: {member.name}")
    digest = hashlib.sha256()
    size = 0
    with source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    if size != member.size:
        raise MigrationError(
            f"archive file size changed while reading {member.name}: expected {member.size}, read {size}"
        )
    return digest.hexdigest()


def _copy_member(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    destination: Path,
    relative: PurePosixPath,
    plan: ArchivePlan,
) -> None:
    target = _safe_parent(destination, relative)
    if member.isdir():
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise MigrationError(f"destination type conflict at {relative}")
        target.mkdir(mode=0o700, exist_ok=True)
        plan.directories += 1
        plan.directory_modes.append((relative, member.mode & 0o777))
        return
    if target.exists() or target.is_symlink():
        raise MigrationError(f"duplicate or conflicting archive path: {relative}")
    if member.isreg():
        source = archive.extractfile(member)
        if source is None:
            raise MigrationError(f"archive file has no payload: {member.name}")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(target, flags, member.mode & 0o777)
        with os.fdopen(descriptor, "wb") as output, source:
            digest = hashlib.sha256()
            size = 0
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if size != member.size:
            raise MigrationError(
                f"archive file size changed while copying {member.name}: expected {member.size}, wrote {size}"
            )
        os.chmod(target, member.mode & 0o777)
        plan.files += 1
        plan.bytes += member.size
        plan.file_hashes[relative] = (member.size, digest.hexdigest())
        return
    if member.issym():
        target.symlink_to(member.linkname)
        plan.symlinks += 1
        return
    plan.unsupported.append(member.name)


def scan_archive(stream: BinaryIO, destination: Path | None = None) -> ArchivePlan:
    """Inspect the docker cp tar stream and optionally copy selected entries."""
    plan = ArchivePlan()
    with tarfile.open(fileobj=stream, mode="r|*") as archive:
        for member in archive:
            relative = _normalized_member_name(member.name)
            if relative is None:
                continue
            mapped, reason = _mapped_path(relative)
            if reason:
                plan.exclude(reason, member.name)
                continue
            if mapped is None:
                continue
            if mapped in plan.seen:
                if member.isdir() and mapped not in plan.seen:
                    pass
                else:
                    raise MigrationError(f"duplicate path in docker archive: {mapped}")
            plan.seen.add(mapped)
            if destination is None:
                if member.isdir():
                    plan.directories += 1
                    plan.directory_modes.append((mapped, member.mode & 0o777))
                elif member.isreg():
                    plan.files += 1
                    plan.bytes += member.size
                    plan.file_hashes[mapped] = (member.size, _member_sha256(archive, member))
                elif member.issym():
                    plan.symlinks += 1
                else:
                    plan.unsupported.append(member.name)
            else:
                _copy_member(archive, member, destination, mapped, plan)
    if destination is not None:
        for relative, mode in sorted(plan.directory_modes, key=lambda row: len(row[0].parts), reverse=True):
            (destination / Path(*relative.parts)).chmod(mode)
    return plan


def _plan_signature(plan: ArchivePlan) -> tuple[object, ...]:
    """Summarize all archive classes that must remain stable between passes."""
    return (
        plan.files,
        plan.directories,
        plan.symlinks,
        plan.bytes,
        tuple(sorted(plan.excluded.items())),
        tuple(sorted(plan.unsupported)),
        tuple(sorted(plan.seen)),
        tuple(sorted(plan.directory_modes, key=lambda row: (row[0], row[1]))),
        tuple(sorted(plan.file_hashes.items())),
    )


@contextmanager
def docker_home_archive(docker: str, container: str) -> Iterator[BinaryIO]:
    command = [docker, "cp", f"{container}:{CONTAINER_HOME}", "-"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.stdout is None or process.stderr is None:
        process.kill()
        raise MigrationError("could not open docker cp output")
    try:
        yield process.stdout
    finally:
        process.stdout.close()
        stderr = process.stderr.read().decode("utf-8", errors="replace")
        process.stderr.close()
        status = process.wait()
        if status != 0:
            raise MigrationError(f"docker cp failed ({status}): {stderr.strip()}")


def require_stopped_container(docker: str, container: str) -> None:
    try:
        result = subprocess.run(
            [docker, "inspect", "--format", "{{.State.Running}}", container],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise MigrationError(f"could not run docker inspect: {exc}") from exc
    if result.returncode != 0:
        raise MigrationError(f"docker inspect failed for {container}: {result.stderr.strip()}")
    state = result.stdout.strip().lower()
    if state != "false":
        raise MigrationError(f"container {container!r} must be stopped; inspect reported {state!r}")


def home_source_path() -> Path:
    return Path.home() / PARENT_NAME / HOME_SOURCE_NAME


def _report(plan: ArchivePlan, home_source: Path) -> None:
    print(f"Home source: {home_source}")
    print(f"Files: {plan.files}; directories: {plan.directories}; symlinks: {plan.symlinks}")
    print(f"Regular file payload: {plan.bytes:,} bytes ({plan.bytes / (1024**3):.2f} GiB)")
    for reason, (count, _size) in sorted(plan.excluded.items()):
        print(f"Excluded ({reason}): {count} archive entries")
    if plan.unsupported:
        print(f"Skipped unsupported special entries: {len(plan.unsupported)}")
        for name in plan.unsupported[:10]:
            print(f"  {name}")


def _check_destination(home_source: Path) -> None:
    if home_source.is_symlink():
        raise MigrationError(f"home source must not be a symlink: {home_source}")
    if home_source.exists():
        if not home_source.is_dir():
            raise MigrationError(f"home source is not a directory: {home_source}")
        if any(home_source.iterdir()):
            raise MigrationError(
                f"home source is not empty: {home_source}; move or review its contents before migration"
            )


def _write_marker(staging: Path, container: str) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "complete": True,
        "source_container": container,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "excluded_image_payloads": [
            ".venv", ".venv-pyNN", ".oh-my-zsh", ".ssh-host",
            ".codex/packages/standalone",
        ],
    }
    marker = staging / MARKER_NAME
    descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def migrate(container: str, *, docker: str = "docker", apply: bool = False) -> int:
    require_stopped_container(docker, container)
    home_source = home_source_path()
    _check_destination(home_source)
    with docker_home_archive(docker, container) as stream:
        plan = scan_archive(stream)
    _report(plan, home_source)
    if not apply:
        free_bytes = shutil.disk_usage(home_source.parent if home_source.parent.exists() else Path.home()).free
        print(f"Available space: {free_bytes:,} bytes ({free_bytes / (1024**3):.2f} GiB)")
        print("Dry run only. Review the exclusions, then rerun with --apply to migrate.")
        return 0

    if plan.unsupported:
        examples = ", ".join(plan.unsupported[:5])
        remaining = len(plan.unsupported) - 5
        if remaining > 0:
            examples += f", and {remaining} more"
        raise MigrationError(
            "cannot apply a partial home migration because the archive contains "
            f"unsupported special entries: {examples}"
        )

    parent = home_source.parent
    parent.mkdir(parents=True, exist_ok=True)
    available = shutil.disk_usage(parent).free
    if available < plan.bytes:
        raise MigrationError(
            f"insufficient free space at {parent}: need at least {plan.bytes:,} bytes, "
            f"have {available:,} bytes"
        )
    require_stopped_container(docker, container)
    _check_destination(home_source)
    staging = Path(tempfile.mkdtemp(prefix=".home-vscode-migration-", dir=parent))
    try:
        os.chmod(staging, 0o700)
        with docker_home_archive(docker, container) as stream:
            copied = scan_archive(stream, staging)
        if _plan_signature(copied) != _plan_signature(plan):
            raise MigrationError("docker home contents changed between scan and copy; no home was installed")
        _write_marker(staging, container)
        os.chmod(staging, 0o700)
        if home_source.exists():
            home_source.rmdir()
        os.replace(staging, home_source)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    print(f"Migration complete: {home_source}")
    print(f"Marker written: {home_source / MARKER_NAME}")
    print("Keep the old split sources as rollback copies until the new container is verified.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("container", help="name or ID of the stopped source devcontainer")
    parser.add_argument("--apply", action="store_true", help="copy into the persistent home")
    parser.add_argument("--docker", default="docker", help="Docker-compatible CLI (default: docker)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return migrate(args.container, docker=args.docker, apply=args.apply)
    except (MigrationError, OSError, tarfile.TarError) as exc:
        print(f"[mdt-home-migration] ERROR {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
