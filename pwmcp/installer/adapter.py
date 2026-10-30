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
INSTALL_OVERRIDE = "standalone/ciu.install.override.toml.j2"
INSTALL_OVERRIDE_NAME = "ciu.toml.j2"


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
    release_paths = [*ROOT_LINKS.values(), INSTALL_OVERRIDE]
    for release_path in release_paths:
        if not (release_root / release_path).is_file():
            raise FileNotFoundError(f"release is missing CIU root input: {release_path}")

    for name, release_path in ROOT_LINKS.items():
        link = instance_root / name
        expected = Path("current") / "tree" / release_path
        if link.is_symlink():
            if os.readlink(link) == expected.as_posix():
                continue
            raise FileExistsError(f"refusing to replace unrelated symlink {link}")
        if os.path.lexists(link):
            raise FileExistsError(f"refusing to replace existing path {link}")
        link.symlink_to(expected)

    # This is a sparse site override, not release content: seed it only once so
    # an update can switch current/tree without a dangling link during the
    # install transaction, and preserve any operator edits on later updates.
    override = instance_root / INSTALL_OVERRIDE_NAME
    if not os.path.lexists(override):
        source = release_root / INSTALL_OVERRIDE
        descriptor = os.open(override, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(source.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            try:
                override.unlink()
            except FileNotFoundError:
                pass
            raise


def _check_links(release_root: Path) -> None:
    instance_root = release_root.parents[2]
    for name, release_path in ROOT_LINKS.items():
        link = instance_root / name
        expected = (Path("current") / "tree" / release_path).as_posix()
        if not link.is_symlink() or os.readlink(link) != expected:
            raise RuntimeError(f"CIU root link is missing or changed: {link}")
        if not (release_root / release_path).is_file():
            raise FileNotFoundError(f"release is missing CIU root input: {release_path}")
    override_source = release_root / INSTALL_OVERRIDE
    if not override_source.is_file():
        raise FileNotFoundError(f"release is missing CIU root input: {INSTALL_OVERRIDE}")
    override = instance_root / INSTALL_OVERRIDE_NAME
    if not override.is_file():
        raise RuntimeError(f"standalone CIU override is missing: {override}")


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
