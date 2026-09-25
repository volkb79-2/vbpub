"""Resolve the OCI release version from cgprofile's CMRU contract and tag."""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Mapping

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


def resolve_build_version(
    project_root: Path,
    *,
    require_release_tag: bool,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return the exact release tag at HEAD, or a deliberate local-build value.

    The prefix is read from ``cmru.toml``. A tag at HEAD takes precedence over
    ambient environment state; an explicit version is only a fallback for
    manual builds. Publishing without either source refuses.
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
        tag = candidates[0]
        return _validated_version(tag[len(prefix):], f"release tag {tag!r}")

    env = os.environ if environ is None else environ
    if VERSION_ENV in env:
        return _validated_version(env[VERSION_ENV], VERSION_ENV)
    if require_release_tag:
        raise RuntimeError(
            f"no {prefix}<version> tag points at HEAD; refuse publication without an exact release version"
        )
    return "0.0.0-dev"
