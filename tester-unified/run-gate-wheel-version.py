#!/usr/bin/env python3
"""Print the version from the single Run-Gate wheel in a dist directory."""
from __future__ import annotations

import sys
from email.parser import Parser
from pathlib import Path
from zipfile import BadZipFile, ZipFile


def read_wheel_version(dist_dir: Path) -> str:
    wheels = sorted(dist_dir.glob("run_gate-*.whl"))
    if len(wheels) != 1:
        raise ValueError(f"expected one Run-Gate wheel in {dist_dir}, found {len(wheels)}")
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
    return version


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {Path(argv[0]).name} DIST_DIR", file=sys.stderr)
        return 2
    try:
        print(read_wheel_version(Path(argv[1])))
    except ValueError as exc:
        print(f"run-gate wheel version: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
