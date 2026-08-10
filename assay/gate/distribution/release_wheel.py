#!/usr/bin/env python3
"""Create and verify Assay's hash-bound release-wheel manifest.

This file is intentionally standalone and stdlib-only: a consumer runs
``verify`` before Assay is installed.  Its successful output is one PEP 508
requirement line for ``pip --require-hashes``; pip therefore rechecks the same
sha256 while installing rather than trusting a check/use gap.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import stat
import sys
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path
from typing import BinaryIO, NoReturn

MANIFEST_SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 4 * 1024
MAX_WHEEL_BYTES = 64 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MANIFEST_KEYS = frozenset({"schema_version", "filename", "version", "sha256"})
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)

# Canonical PEP 440 spellings emitted by the pinned backend.  The release
# segment is intentionally X.Y.Z-or-longer; Assay does not publish one-part or
# two-part release identities.  Pre/post/dev/local suffixes remain supported.
VERSION_RE = re.compile(
    r"(?:(?:0|[1-9][0-9]*)!)?"
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:\.(?:0|[1-9][0-9]*))*"
    r"(?:(?:a|b|rc)[0-9]+)?"
    r"(?:\.post[0-9]+)?"
    r"(?:\.dev[0-9]+)?"
    r"(?:\+[a-z0-9]+(?:\.[a-z0-9]+)*)?\Z",
    re.ASCII,
)


class ReleaseContractError(ValueError):
    """The supplied artifact does not satisfy the release contract."""


@dataclass(frozen=True, kw_only=True)
class ReleaseManifest:
    schema_version: int
    filename: str
    version: str
    sha256: str

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ReleaseContractError("schema_version must be integer 1")
        if not isinstance(self.version, str) or not VERSION_RE.fullmatch(self.version):
            raise ReleaseContractError("version is not canonical Assay release PEP 440")
        expected = f"assay-{self.version}-py3-none-any.whl"
        if not isinstance(self.filename, str) or self.filename != expected:
            raise ReleaseContractError(f"filename must be exactly {expected}")
        if not isinstance(self.sha256, str) or not SHA256_RE.fullmatch(self.sha256):
            raise ReleaseContractError("sha256 must be 64 lowercase hexadecimal characters")

    def canonical_bytes(self) -> bytes:
        document = {
            "schema_version": self.schema_version,
            "filename": self.filename,
            "version": self.version,
            "sha256": self.sha256,
        }
        return (json.dumps(document, ensure_ascii=True, separators=(",", ":")) + "\n").encode()


def _refuse(message: str) -> NoReturn:
    raise ReleaseContractError(message)


def _open_regular(path: Path, *, limit: int, label: str) -> BinaryIO:
    required = ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK")
    if any(not hasattr(os, name) for name in required):
        _refuse(f"this platform cannot safely open the {label}")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENXIO, errno.ENODEV}:
            _refuse(f"{label} is a symlink or special file")
        _refuse(f"cannot open {label}: {exc.strerror or exc}")
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            _refuse(f"{label} is not a regular file")
        if info.st_size > limit:
            _refuse(f"{label} exceeds {limit} bytes")
        return os.fdopen(fd, "rb", closefd=True)
    except BaseException:
        os.close(fd)
        raise


def _read_all_bounded(stream: BinaryIO, *, limit: int, label: str) -> bytes:
    data = stream.read(limit + 1)
    if len(data) > limit:
        _refuse(f"{label} exceeds {limit} bytes")
    return data


def _closed_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _refuse(f"duplicate manifest member {key!r}")
        result[key] = value
    return result


def _load_manifest(path: Path) -> ReleaseManifest:
    with _open_regular(path, limit=MAX_MANIFEST_BYTES, label="manifest") as stream:
        raw = _read_all_bounded(stream, limit=MAX_MANIFEST_BYTES, label="manifest")
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_closed_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _refuse(f"manifest is not one UTF-8 JSON object: {exc}")
    if not isinstance(document, dict):
        _refuse("manifest root must be an object")
    keys = frozenset(document)
    if keys != MANIFEST_KEYS:
        _refuse(
            "manifest members must be exactly "
            + ", ".join(sorted(MANIFEST_KEYS))
        )
    return ReleaseManifest(
        schema_version=document["schema_version"],
        filename=document["filename"],
        version=document["version"],
        sha256=document["sha256"],
    )


_HASH_CHUNK_BYTES = 1024 * 1024


def _sha256(stream: BinaryIO) -> str:
    """Return the digest of *stream*, rewound for the metadata reader."""
    stream.seek(0)
    digest = hashlib.sha256()
    while True:
        chunk = stream.read(_HASH_CHUNK_BYTES)
        if not chunk:
            break
        digest.update(chunk)
    stream.seek(0)
    return digest.hexdigest()


def _wheel_name_and_version(stream: BinaryIO, expected_version: str) -> tuple[str, str]:
    """Read one bounded Assay METADATA member from the already-open wheel."""
    with zipfile.ZipFile(stream) as archive:
        candidates = [
            info
            for info in archive.infolist()
            if info.filename.endswith(".dist-info/METADATA")
        ]
        if len(candidates) != 1:
            _refuse("wheel must contain exactly one *.dist-info/METADATA member")
        info = candidates[0]
        expected_name = f"assay-{expected_version}.dist-info/METADATA"
        if info.filename != expected_name:
            _refuse(f"METADATA member must be exactly {expected_name!r}")
        if info.file_size > MAX_METADATA_BYTES:
            _refuse(f"METADATA declares more than {MAX_METADATA_BYTES} bytes")
        data = archive.read(info)
    document = BytesParser().parsebytes(data)
    names = document.get_all("Name") or []
    versions = document.get_all("Version") or []
    if len(names) != 1:
        _refuse("METADATA must declare exactly one Name header")
    if len(versions) != 1:
        _refuse("METADATA must declare exactly one Version header")
    return names[0], versions[0]


def _open_wheel(path: Path) -> BinaryIO:
    return _open_regular(path, limit=MAX_WHEEL_BYTES, label="wheel")


def verify_release(wheel_path: Path, manifest_path: Path) -> str:
    """Verify exact bytes and metadata, returning a pip hash requirement."""
    manifest = _load_manifest(manifest_path)
    if wheel_path.name != manifest.filename:
        _refuse("wheel basename does not match manifest filename")
    with _open_wheel(wheel_path) as wheel:
        actual_hash = _sha256(wheel)
        if actual_hash != manifest.sha256:
            _refuse("wheel sha256 does not match manifest")
        name, version = _wheel_name_and_version(wheel, manifest.version)
    if name != "assay" or version != manifest.version:
        _refuse("wheel METADATA name/version does not match manifest")
    uri = wheel_path.absolute().as_uri()
    return f"assay @ {uri} --hash=sha256:{manifest.sha256}"


def _write_new(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags, 0o644)
    except OSError as exc:
        _refuse(f"cannot create manifest: {exc.strerror or exc}")
    try:
        with os.fdopen(fd, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def create_release_manifest(wheel_path: Path, version: str, output_path: Path) -> None:
    """Create a canonical manifest from one already-built release wheel."""
    candidate = ReleaseManifest(
        schema_version=1,
        filename=wheel_path.name,
        version=version,
        sha256="0" * 64,
    )
    with _open_wheel(wheel_path) as wheel:
        name, metadata_version = _wheel_name_and_version(wheel, version)
        digest = _sha256(wheel)
    if name != "assay" or metadata_version != version:
        _refuse("wheel METADATA name/version does not match requested version")
    manifest = ReleaseManifest(
        schema_version=1,
        filename=candidate.filename,
        version=version,
        sha256=digest,
    )
    _write_new(output_path, manifest.canonical_bytes())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="release_wheel.py")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("manifest")
    create.add_argument("--wheel", type=Path, required=True)
    create.add_argument("--version", required=True)
    create.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--wheel", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "manifest":
            create_release_manifest(args.wheel, args.version, args.output)
        else:
            print(verify_release(args.wheel, args.manifest))
    except (ReleaseContractError, OSError, zipfile.BadZipFile) as exc:
        print(f"release-wheel: REFUSED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
