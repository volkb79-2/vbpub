#!/usr/bin/env python3
"""Write the version marker used by source-tree installations of iogovmon."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path


_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.!_-]*$")
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def read_source_version(project_root: Path) -> str:
    """Read the package version used by source installs from pyproject.toml."""
    pyproject = project_root / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"cannot read {pyproject}: {exc}") from exc

    version = data.get("project", {}).get("version")
    if version is None:
        version = data.get("tool", {}).get("iogovmon", {}).get("install", {}).get("version")
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise ValueError(f"{pyproject} must declare a valid static project version or tool.iogovmon.install.version")
    return version


def git_short_sha(project_root: Path) -> str | None:
    """Return the checkout's short HEAD when this source is in Git and Git is available."""
    if not any((parent / ".git").exists() for parent in (project_root, *project_root.parents)):
        return None
    git = shutil.which("git")
    if git is None:
        return None

    result = subprocess.run(
        [git, "-C", str(project_root), "rev-parse", "--short=7", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"Git could not resolve {project_root} HEAD: {result.stderr.strip()}")
    sha = result.stdout.strip()
    if not _SHA_RE.fullmatch(sha):
        raise ValueError(f"Git returned an invalid short SHA for {project_root}: {sha!r}")
    return sha


def render_installed_version(project_root: Path) -> str:
    version = read_source_version(project_root)
    sha = git_short_sha(project_root)
    if sha is not None:
        version = f"{version}+g{sha}"
    return f"__version__ = {json.dumps(version)}\n"


def write_installed_version(project_root: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_installed_version(project_root), encoding="utf-8")
    output.chmod(0o644)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        write_installed_version(args.project_root, args.output)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
