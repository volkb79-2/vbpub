"""Resolve the OCI release version from cgprofile's CMRU contract and tag."""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path

VERSION_ENV = "CGPROFILE_VERSION"
_PRERELEASE_ID = r"(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
_SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    rf"(?:-(?:{_PRERELEASE_ID})(?:\.{_PRERELEASE_ID})*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


def runtime_version(default: str) -> str:
    """Use the version embedded by the image build, or the source fallback."""
    value = os.environ.get(VERSION_ENV)
    if value is None:
        return default
    return _validated_version(value, VERSION_ENV)


def _validated_version(value: str, source: str) -> str:
    version = value.strip()
    if not _SEMVER.fullmatch(version):
        raise RuntimeError(f"{source} must be a semantic version, got {value!r}")
    return version


def _require_clean_tagged_tree(project_root: Path) -> None:
    top_level = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=str(project_root), capture_output=True, text=True, check=False,
    )
    if top_level.returncode or not top_level.stdout.strip():
        detail = top_level.stderr.strip() or f"exit {top_level.returncode}"
        raise RuntimeError(f"cannot verify tagged build tree cleanliness: {detail}")

    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=top_level.stdout.strip(), capture_output=True, text=True, check=False,
    )
    if status.returncode:
        detail = status.stderr.strip() or f"exit {status.returncode}"
        raise RuntimeError(f"cannot verify tagged build tree cleanliness: {detail}")
    if status.stdout:
        raise RuntimeError(
            "tagged build tree has tracked or untracked changes; refuse to use "
            "the release identity for source that differs from the tag"
        )


def resolve_build_version(
    project_root: Path,
    *,
    require_release_tag: bool,
) -> str:
    """Return the exact release tag at HEAD, or the local development identity.

    The prefix is read from ``cmru.toml``. A tag at HEAD takes precedence over
    ambient environment state. Environment variables are never a source of
    release identity: an untagged local build is ``0.0.0-dev``, and publishing
    without an exact tag refuses.
    """
    root = Path(project_root)
    config = tomllib.loads((root / "cmru.toml").read_text(encoding="utf-8"))
    project = config.get("project")
    prefix = project.get("prefix") if isinstance(project, dict) else None
    if not isinstance(prefix, str) or not prefix:
        raise RuntimeError("cmru.toml must declare a non-empty project.prefix")

    tagged = subprocess.run(
        ["git", "tag", "--points-at", "HEAD"],
        cwd=str(root), capture_output=True, text=True, check=False,
    )
    if tagged.returncode:
        detail = tagged.stderr.strip() or f"exit {tagged.returncode}"
        raise RuntimeError(f"cannot resolve release tag at HEAD: {detail}")

    candidates = [
        line.strip() for line in tagged.stdout.splitlines()
        if line.strip().startswith(prefix)
    ]
    if len(candidates) > 1:
        raise RuntimeError(
            f"multiple {prefix!r} release tags point at HEAD: {', '.join(sorted(candidates))}"
        )
    if candidates:
        _require_clean_tagged_tree(root)
        tag = candidates[0]
        return _validated_version(tag[len(prefix):], f"release tag {tag!r}")

    if require_release_tag:
        raise RuntimeError(
            f"no {prefix}<version> tag points at HEAD; refuse publication without an exact release tag"
        )
    return "0.0.0-dev"
