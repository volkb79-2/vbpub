#!/usr/bin/env python3
"""Expose the verified PWMCP release as a stable standalone CIU root."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


ROOT_LINKS = {
    "ciu.global.defaults.toml.j2": "standalone/ciu.global.defaults.toml.j2",
    "ciu.compose.yml.j2": "ciu.compose.yml.j2",
    "ciu.defaults.toml.j2": "ciu.defaults.toml.j2",
}


def _release_root(raw: str) -> Path:
    release_root = Path(raw).resolve()
    if (
        release_root.name != "tree"
        or release_root.parent.parent.name != "releases"
        or len(release_root.parents) < 3
    ):
        raise ValueError(f"unexpected CMRU release tree path: {release_root}")
    return release_root


def _install_links(release_root: Path) -> None:
    instance_root = release_root.parents[2]
    for name, release_path in ROOT_LINKS.items():
        if not (release_root / release_path).is_file():
            raise FileNotFoundError(f"release is missing CIU root input: {release_path}")
        link = instance_root / name
        expected = Path("current") / "tree" / release_path
        if link.is_symlink():
            if os.readlink(link) == expected.as_posix():
                continue
            raise FileExistsError(f"refusing to replace unrelated symlink {link}")
        if os.path.lexists(link):
            raise FileExistsError(f"refusing to replace existing path {link}")
        link.symlink_to(expected)


def _check_links(release_root: Path) -> None:
    instance_root = release_root.parents[2]
    for name, release_path in ROOT_LINKS.items():
        link = instance_root / name
        expected = (Path("current") / "tree" / release_path).as_posix()
        if not link.is_symlink() or os.readlink(link) != expected:
            raise RuntimeError(f"CIU root link is missing or changed: {link}")
        if not (release_root / release_path).is_file():
            raise FileNotFoundError(f"release is missing CIU root input: {release_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("bootstrap", "apply", "health", "rollback"))
    parser.add_argument("--release-root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()

    release_root = _release_root(args.release_root)
    if args.action == "health":
        _check_links(release_root)
    else:
        _install_links(release_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
