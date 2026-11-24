#!/usr/bin/env python3
"""Bootstrap tls-edge's stable CIU root during an offline bundle install.

The adapter creates only the persistent stack directory and its git identity.
The installed ``tls-edge configure`` command owns site-specific settings after
the release has been installed; updates never rewrite that configuration.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

STACK_DIR = "stack"


def _release_root(raw: str) -> Path:
    release_root = Path(raw).resolve()
    if (
        release_root.name != "tree"
        or release_root.parent.parent.name != "releases"
        or len(release_root.parents) < 3
    ):
        raise ValueError(f"unexpected CMRU release tree path: {release_root}")
    return release_root


def _ensure_git(stack: Path) -> None:
    """Create the empty git identity CIU currently requires (backlog CIU-163)."""
    if (stack / ".git").exists():
        return
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "tls-edge-installer",
        "GIT_AUTHOR_EMAIL": "tls-edge-installer@localhost",
        "GIT_COMMITTER_NAME": "tls-edge-installer",
        "GIT_COMMITTER_EMAIL": "tls-edge-installer@localhost",
    })
    for argv in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "commit", "-q", "--allow-empty", "-m", "tls-edge instance root (installer)"],
    ):
        result = subprocess.run(argv, cwd=stack, env=env, capture_output=True, text=True)
        if result.returncode:
            details = (result.stdout + result.stderr).strip()
            raise RuntimeError(f"{' '.join(argv)} failed: {details}")


def prepare(instance_root: Path) -> None:
    stack = instance_root / STACK_DIR
    stack.mkdir(mode=0o755, exist_ok=True)
    if stack.is_symlink() or not stack.is_dir():
        raise FileExistsError(f"{stack} must be a plain directory")
    _ensure_git(stack)


def bootstrap(
    instance_root: Path,
    *,
    euid: int | None = None,
    chown_fn: Callable | None = None,
    chmod_fn: Callable | None = None,
) -> None:
    """Prepare the first install and root-only persistent ACME hostdir."""
    prepare(instance_root)
    effective_euid = os.geteuid() if euid is None else euid
    if effective_euid != 0:
        return

    hostdir = instance_root / "shared" / "acme"
    if os.path.lexists(hostdir):
        if hostdir.is_symlink() or not hostdir.is_dir():
            raise FileExistsError(f"{hostdir} must be a plain directory")
    else:
        hostdir.mkdir(mode=0o700, parents=True)
    (chown_fn or os.chown)(hostdir, 0, 0)
    (chmod_fn or os.chmod)(hostdir, 0o700)


def check(instance_root: Path) -> None:
    stack = instance_root / STACK_DIR
    required = (
        stack / ".git",
        stack / "ciu",
        stack / ".gitignore",
        instance_root / "bin" / "ciu",
        instance_root / "bin" / "tls-edge",
    )
    missing = [path for path in required if not os.path.lexists(path)]
    if missing:
        raise RuntimeError(
            "instance is incomplete; missing " + ", ".join(str(path) for path in missing)
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("bootstrap", "apply", "health", "rollback"))
    parser.add_argument("--release-root", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args(argv)

    try:
        instance_root = _release_root(args.release_root).parents[2]
        if args.action == "health":
            check(instance_root)
        elif args.action == "bootstrap":
            bootstrap(instance_root)
        else:
            prepare(instance_root)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] tls-edge adapter {args.action}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
