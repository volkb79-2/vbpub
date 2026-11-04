#!/usr/bin/env python3
"""Write manifest.json into a staged tls-edge bundle directory.

`files` covers every regular file (cmru's schema-1 bundle manifest); each
[[project.installer.wheels]] entry of cmru.toml additionally gets
`manifest[distribution] = {wheel, sha256, size}`, which get.py verifies before it builds
the hash-locked venv. Exactly one wheel must match each entry and no wheel may be unlisted.
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
import tomllib
from pathlib import Path

from cmru.manifest import build_bundle_manifest, write_manifest
from cmru.release import sha256_file

CONFIG = Path(__file__).resolve().parent.parent / "cmru.toml"


def build(tag: str, root: Path, config: Path = CONFIG) -> dict:
    with config.open("rb") as stream:
        specs = tomllib.load(stream).get("project", {}).get("installer", {}).get("wheels", [])
    manifest = build_bundle_manifest(project="tls-edge", tag=tag, bundle_root=root)
    listed: set[Path] = set()
    for spec in specs:
        pattern = spec["path"]
        matches = sorted(
            path for path in root.glob(pattern)
            if path.is_file() and fnmatch.fnmatch(path.name, Path(pattern).name)
        )
        if len(matches) != 1:
            raise RuntimeError(
                f"expected one bundled wheel for {spec['distribution']!r} ({pattern}), "
                f"got {len(matches)}"
            )
        wheel = matches[0]
        listed.add(wheel.resolve())
        manifest[spec["distribution"]] = {
            "wheel": wheel.name,
            "sha256": sha256_file(wheel),
            "size": wheel.stat().st_size,
        }
    extra = sorted(p.name for p in root.glob("wheels/*.whl") if p.resolve() not in listed)
    if extra:
        raise RuntimeError("bundle wheel(s) missing from [project.installer.wheels]: " + ", ".join(extra))
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = build(args.tag, args.root.resolve())
    except (RuntimeError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    out = write_manifest(manifest, args.root.resolve() / "manifest.json")
    print(f"[INFO] Wrote {out} ({len(manifest['files'])} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
