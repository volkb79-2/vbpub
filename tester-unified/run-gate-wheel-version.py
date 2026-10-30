#!/usr/bin/env python3
"""Read version, filename and SHA-256 for one Run-Gate wheel."""
from __future__ import annotations

import hashlib
import json
import stat
import sys
from email.parser import Parser
from pathlib import Path
from zipfile import BadZipFile, ZipFile


def read_wheel_identity(dist_dir: Path) -> dict[str, str]:
    wheels = sorted(dist_dir.glob("run_gate-*.whl"))
    if len(wheels) != 1:
        raise ValueError(f"expected one Run-Gate wheel in {dist_dir}, found {len(wheels)}")
    try:
        mode = wheels[0].lstat().st_mode
    except OSError as exc:
        raise ValueError(f"cannot inspect Run-Gate wheel {wheels[0]}: {exc}") from exc
    if not stat.S_ISREG(mode):
        raise ValueError(f"Run-Gate wheel is not a regular file: {wheels[0]}")
    try:
        with ZipFile(wheels[0]) as archive:
            metadata_paths = [
                name for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ]
            if len(metadata_paths) != 1:
                raise ValueError(
                    f"expected one wheel METADATA file in {wheels[0]}, found {len(metadata_paths)}"
                )
            metadata = Parser().parsestr(
                archive.read(metadata_paths[0]).decode("utf-8")
            )
    except (BadZipFile, UnicodeDecodeError, OSError) as exc:
        raise ValueError(f"cannot read Run-Gate wheel metadata from {wheels[0]}: {exc}") from exc
    name = metadata.get("Name", "").replace("_", "-").lower()
    version = metadata.get("Version", "").strip()
    if name != "run-gate" or not version:
        raise ValueError(
            f"wheel metadata in {wheels[0]} must name run-gate and include a version"
        )
    digest = hashlib.sha256()
    with wheels[0].open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"version": version, "filename": wheels[0].name, "sha256": digest.hexdigest()}


def read_wheel_version(dist_dir: Path) -> str:
    return read_wheel_identity(dist_dir)["version"]


def main(argv: list[str]) -> int:
    json_output = len(argv) == 3 and argv[2] == "--json"
    if len(argv) not in (2, 3) or (len(argv) == 3 and not json_output):
        print(f"usage: {Path(argv[0]).name} DIST_DIR [--json]", file=sys.stderr)
        return 2
    try:
        identity = read_wheel_identity(Path(argv[1]))
    except ValueError as exc:
        print(f"run-gate wheel version: {exc}", file=sys.stderr)
        return 2
    if json_output:
        print(json.dumps(identity, sort_keys=True))
    else:
        print(identity["version"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
