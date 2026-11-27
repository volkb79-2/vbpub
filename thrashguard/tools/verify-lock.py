#!/usr/bin/env python3
"""Fail closed unless thrashguard's Cargo.lock needs no crate the image does not carry.

thrashguard declares no third-party dependency (design §5), so its registry set
is empty and every package in the lock is a workspace member (no ``source``).
The Rust gate image caches exactly the registry set of ``hostobs/Cargo.lock``;
a lock here that names a registry or git package would need that image rebuilt
before an offline ``--locked`` build could work, so it is refused here loudly
instead of failing later inside cargo with a network error.

Usage: verify-lock.py CARGO.LOCK
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path


class LockRefusal(ValueError):
    """The lock names a package this offline build cannot obtain."""


def verify(lock_path: Path) -> list[str]:
    """Return the workspace-member package names, or raise :class:`LockRefusal`."""
    try:
        document = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise LockRefusal(f"cannot read Cargo.lock {lock_path}: {exc}") from exc
    lock_version = document.get("version")
    if not isinstance(lock_version, int) or isinstance(lock_version, bool):
        raise LockRefusal("Cargo.lock has no integer top-level version")
    members: list[str] = []
    for package in document.get("package", []):
        name = package.get("name")
        version = package.get("version")
        if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
            raise LockRefusal(f"Cargo.lock package without name/version: {package!r}")
        source = package.get("source")
        if source is not None:
            raise LockRefusal(
                f"Cargo.lock names {name}-{version} from {source!r}; thrashguard declares no "
                "third-party dependency and the Rust gate image caches only hostobs's registry "
                "set, so this lock needs the image rebuilt before an offline build can pass"
            )
        members.append(f"{name}-{version}")
    if not members:
        raise LockRefusal("Cargo.lock declares no package at all")
    return members


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: verify-lock.py CARGO.LOCK", file=sys.stderr)
        return 2
    try:
        members = verify(Path(argv[1]))
    except LockRefusal as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"lock verified: {len(members)} workspace package(s), no registry crate: {', '.join(members)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
