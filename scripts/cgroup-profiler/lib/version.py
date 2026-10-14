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
_GIT_OBJECT_ID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


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


def _git_output(project_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(project_root), capture_output=True, text=True,
        check=False,
    )
    if result.returncode or not result.stdout.strip():
        detail = result.stderr.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"cannot resolve release source with git {args[0]}: {detail}")
    return result.stdout.strip()


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


def resolve_cmru_release_identity(project_root: Path) -> tuple[str, str, str]:
    """Validate CMRU's protected tag/commit handoff against this checkout.

    CMRU supplies these values only to the tagged release's artifact and push
    steps. The local checkout must still be the exact candidate, the named tag
    must peel to that same commit, and the tree must be clean. The returned
    tuple is ``(version, commit, tag)`` and can safely identify an immutable
    build context.
    """
    commit = os.environ.get("CMRU_RELEASE_COMMIT")
    tag = os.environ.get("CMRU_RELEASE_TAG")
    if commit is None and tag is None:
        raise RuntimeError(
            "CMRU_RELEASE_COMMIT and CMRU_RELEASE_TAG are required; "
            "run the tagged publication through cmru release"
        )
    if not commit or not tag:
        raise RuntimeError(
            "incomplete CMRU release identity; both CMRU_RELEASE_COMMIT and "
            "CMRU_RELEASE_TAG are required"
        )
    if not _GIT_OBJECT_ID.fullmatch(commit):
        raise RuntimeError("CMRU_RELEASE_COMMIT must be a full Git object id")

    root = Path(project_root)
    config = tomllib.loads((root / "cmru.toml").read_text(encoding="utf-8"))
    project = config.get("project")
    prefix = project.get("prefix") if isinstance(project, dict) else None
    if not isinstance(prefix, str) or not prefix:
        raise RuntimeError("cmru.toml must declare a non-empty project.prefix")
    if not tag.startswith(prefix):
        raise RuntimeError(
            f"CMRU_RELEASE_TAG {tag!r} does not use configured prefix {prefix!r}"
        )
    version = _validated_version(tag[len(prefix):], f"CMRU_RELEASE_TAG {tag!r}")

    head = _git_output(root, "rev-parse", "--verify", "HEAD^{commit}")
    if head != commit:
        raise RuntimeError(
            f"CMRU release commit {commit} does not match checkout HEAD {head}"
        )
    tagged_commit = _git_output(
        root, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}",
    )
    if tagged_commit != commit:
        raise RuntimeError(
            f"CMRU release tag {tag!r} points at {tagged_commit}, expected {commit}"
        )
    _require_clean_tagged_tree(root)
    return version, commit, tag


def resolve_build_identity(
    project_root: Path,
    *,
    require_release_tag: bool,
) -> tuple[str, str]:
    """Return one checked ``(version, commit)`` identity for a build.

    The version and commit are resolved from the same HEAD read. A release tag
    must peel back to that commit and the tagged checkout must be clean.
    Environment variables never create release authority.
    """
    root = Path(project_root)
    config = tomllib.loads((root / "cmru.toml").read_text(encoding="utf-8"))
    project = config.get("project")
    prefix = project.get("prefix") if isinstance(project, dict) else None
    if not isinstance(prefix, str) or not prefix:
        raise RuntimeError("cmru.toml must declare a non-empty project.prefix")

    head = _git_output(root, "rev-parse", "--verify", "HEAD^{commit}")
    tagged = subprocess.run(
        ["git", "tag", "--points-at", head, "--list", f"{prefix}*"],
        cwd=str(root), capture_output=True, text=True, check=False,
    )
    if tagged.returncode:
        detail = tagged.stderr.strip() or f"exit {tagged.returncode}"
        raise RuntimeError(f"cannot resolve release tag at HEAD: {detail}")

    candidates = [
        line.strip() for line in tagged.stdout.splitlines()
        if line.strip().startswith(prefix) and not line.strip().endswith("-latest")
    ]
    if len(candidates) > 1:
        raise RuntimeError(
            f"multiple {prefix!r} release tags point at HEAD: {', '.join(sorted(candidates))}"
        )
    if candidates:
        _require_clean_tagged_tree(root)
        tag = candidates[0]
        tagged_commit = _git_output(
            root, "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}",
        )
        if tagged_commit != head:
            raise RuntimeError(
                f"release tag {tag!r} points at {tagged_commit}, not checkout HEAD {head}"
            )
        version = _validated_version(tag[len(prefix):], f"release tag {tag!r}")
        return version, head

    if require_release_tag:
        raise RuntimeError(
            f"no {prefix}<version> tag points at HEAD; refuse publication without an exact release tag"
        )
    return "0.0.0-dev", head


def resolve_build_version(
    project_root: Path,
    *,
    require_release_tag: bool,
) -> str:
    """Compatibility wrapper returning the version from a checked build identity."""
    return resolve_build_identity(
        project_root, require_release_tag=require_release_tag,
    )[0]
