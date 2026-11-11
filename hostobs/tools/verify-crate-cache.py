#!/usr/bin/env python3
"""Fail closed unless this Cargo.lock matches the image's exact crate cache."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tomllib
from pathlib import Path


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CRATES_IO_SOURCE = "registry+https://github.com/rust-lang/crates.io-index"


class CacheMismatch(ValueError):
    """The checked-out lock and the baked Cargo registry cache disagree."""


def _lock_packages(lock_path: Path) -> dict[str, str]:
    try:
        document = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise CacheMismatch(f"cannot read Cargo.lock {lock_path}: {exc}") from exc

    packages: dict[str, str] = {}
    for package in document.get("package", []):
        source = package.get("source")
        if source is None:
            continue
        name = package.get("name")
        version = package.get("version")
        checksum = package.get("checksum")
        if not isinstance(name, str) or not isinstance(version, str):
            raise CacheMismatch(f"Cargo.lock has a registry package without name/version: {package!r}")
        if source != _CRATES_IO_SOURCE:
            raise CacheMismatch(
                f"Cargo.lock package {name}-{version} uses unsupported source {source!r}; "
                "the Rust image cache is crates.io only"
            )
        if not isinstance(checksum, str) or not _SHA256.fullmatch(checksum):
            raise CacheMismatch(f"Cargo.lock package {name}-{version} has no valid archive checksum")
        archive = f"{name}-{version}.crate"
        if archive in packages:
            raise CacheMismatch(f"Cargo.lock maps more than one package to archive {archive}")
        packages[archive] = checksum
    return packages


def _manifest_entries(manifest_path: Path, cargo_home: Path) -> dict[str, tuple[str, Path]]:
    try:
        text = manifest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CacheMismatch(f"cannot read crate cache manifest {manifest_path}: {exc}") from exc
    if not text.strip():
        raise CacheMismatch(f"crate cache manifest is empty: {manifest_path}")

    entries: dict[str, tuple[str, Path]] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        fields = line.split("  ", 1)
        if len(fields) != 2 or not _SHA256.fullmatch(fields[0]) or not fields[1]:
            raise CacheMismatch(f"malformed crate cache manifest entry at {manifest_path}:{number}")
        digest, raw_path = fields
        archive_path = Path(raw_path)
        if not archive_path.is_absolute():
            raise CacheMismatch(f"crate cache manifest path is not absolute at line {number}: {raw_path}")
        try:
            archive_path.resolve().relative_to(cargo_home.resolve())
        except ValueError as exc:
            raise CacheMismatch(
                f"crate cache manifest path escapes CARGO_HOME at line {number}: {raw_path}"
            ) from exc
        if archive_path.suffix != ".crate":
            raise CacheMismatch(f"manifest entry is not a .crate archive at line {number}: {raw_path}")
        archive = archive_path.name
        if archive in entries:
            raise CacheMismatch(f"crate cache manifest repeats archive {archive}")
        entries[archive] = (digest, archive_path)
    return entries


def verify(
    lock_path: Path,
    manifest_path: Path,
    baked_lock_sha256_path: Path,
    cargo_home: Path,
) -> list[str]:
    locked = _lock_packages(lock_path)
    cached = _manifest_entries(manifest_path, cargo_home)

    missing = sorted(set(locked) - set(cached))
    if missing:
        raise CacheMismatch(f"crate cache is missing locked archive: {missing[0]}")
    extra = sorted(set(cached) - set(locked))
    if extra:
        raise CacheMismatch(f"crate cache contains archive absent from Cargo.lock: {extra[0]}")

    for archive in sorted(locked):
        locked_digest = locked[archive]
        manifest_digest, archive_path = cached[archive]
        if manifest_digest != locked_digest:
            raise CacheMismatch(
                f"Cargo.lock checksum differs from baked cache manifest for {archive}: "
                f"lock {locked_digest}, cache {manifest_digest}"
            )
        try:
            actual_digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise CacheMismatch(f"cached crate is missing or unreadable: {archive}: {exc}") from exc
        if actual_digest != manifest_digest:
            raise CacheMismatch(
                f"cached crate checksum mismatch for {archive}: "
                f"expected {manifest_digest}, got {actual_digest}"
            )

    try:
        expected_lock_digest = baked_lock_sha256_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise CacheMismatch(f"baked Cargo.lock identity is missing or unreadable: {exc}") from exc
    if not _SHA256.fullmatch(expected_lock_digest):
        raise CacheMismatch(f"baked Cargo.lock identity is malformed: {baked_lock_sha256_path}")
    actual_lock_digest = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    if actual_lock_digest != expected_lock_digest:
        raise CacheMismatch(
            "Cargo.lock differs from the lock baked into tester-unified-rust; "
            "rebuild the image from this checkout's reviewed lock"
        )
    return sorted(locked)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--baked-lock-sha256", required=True, type=Path)
    parser.add_argument("--cargo-home", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    try:
        packages = verify(args.lock, args.manifest, args.baked_lock_sha256, args.cargo_home)
    except CacheMismatch as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"crate cache verified: {len(packages)} locked crates match {args.lock}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
