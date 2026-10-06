"""Release manifest assembly and canonical serialization (Seam 3 / SPEC B §3).

cmru provides generic mechanics only — the project supplies all specifics
(allowlist, image digest map, schema versions) via config/args.

Canonical serialization rules (so manifest.json is itself deterministic):
  - UTF-8 encoding
  - sort_keys=True
  - separators=(",", ":")   (compact, no spaces)
  - trailing newline

Two builds of the same input MUST produce identical bytes.
"""
from __future__ import annotations

import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from cmru.release import sha256_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _epoch() -> int:
    """Read SOURCE_DATE_EPOCH from env; raise clearly if unset."""
    raw = os.environ.get("SOURCE_DATE_EPOCH")
    if not raw:
        raise RuntimeError(
            "SOURCE_DATE_EPOCH is not set in the environment. "
            "The cmru runner sets it automatically (S3.3); "
            "set it explicitly for standalone use: "
            "export SOURCE_DATE_EPOCH=$(git log -1 --format=%ct)"
        )
    return int(raw)


def _iso8601_from_epoch(epoch: int) -> str:
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Image map validation
# ---------------------------------------------------------------------------

def _validate_images(images: Optional[Dict[str, Any]], project: str) -> Dict[str, Any]:
    """Validate the image digest map supplied by the project.

    cmru never invents or queries image digests — that is the project's job (SPEC F).
    If the project declares images (images is not None) it MUST supply a non-empty map
    where every entry has repository, tag, and digest.  If images is None we treat the
    project as not having any container images.
    """
    if images is None:
        return {}

    if not isinstance(images, dict):
        raise TypeError(
            f"[project.{project}] images must be a dict (service -> {{repository, tag, digest}}), "
            f"got {type(images).__name__}"
        )

    if len(images) == 0:
        raise ValueError(
            f"[project.{project}] images is present but empty — "
            "either omit the key or supply at least one service entry. "
            "cmru never queries a registry to discover images."
        )

    required = {"repository", "tag", "digest"}
    for service, entry in images.items():
        if not isinstance(entry, dict):
            raise TypeError(
                f"[project.{project}] images.{service} must be a dict, "
                f"got {type(entry).__name__}"
            )
        missing = required - set(entry.keys())
        if missing:
            raise ValueError(
                f"[project.{project}] images.{service} is missing required keys: "
                f"{sorted(missing)}"
            )

    return images


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_BUNDLE_TAG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")


def bundle_tag_problem(tag: str) -> Optional[str]:
    """Why `tag` is not a release tag the installer (`get.py`) accepts, else None. Mirrors
    the installer's grammar: ``[A-Za-z0-9][A-Za-z0-9._+-]*`` and no ``..``."""
    if not _BUNDLE_TAG_RE.fullmatch(tag) or ".." in tag:
        return (f"invalid --tag {tag!r}: a release tag is made of [A-Za-z0-9._+-], starts "
                "with a letter or digit and has no '..'")
    return None


def bundle_files(root: Path, exclude: Iterable[str] = ()) -> Dict[str, Dict[str, Any]]:
    """The manifest ``files`` map of a bundle tree: ``{relpath: {sha256, size, mode}}`` for
    every regular file below ``root`` (posix relative paths, sorted by canonical
    serialization). ``exclude`` names root-level files that are not part of the content
    (the manifest and its signature). A symlink or any non-regular entry is an error: the
    installer (`get.py`) only installs hashed regular files, so a bundle that needs
    anything else cannot be described.

    ``mode`` is the permission bits (``0o755`` -> 493); informational for the installer,
    which takes modes from the archive."""
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f"bundle root is not a directory: {root}")
    skip = set(exclude)
    files: Dict[str, Dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel in skip:
            continue
        if path.is_symlink() or (not path.is_dir() and not path.is_file()):
            raise ValueError(f"bundle entry {rel!r} is a symlink or special file; the "
                             "manifest can only describe regular files")
        if path.is_dir():
            continue
        st = path.stat()
        files[rel] = {"sha256": sha256_file(path), "size": st.st_size,
                      "mode": stat.S_IMODE(st.st_mode)}
    return files


def build_bundle_manifest(
    *,
    project: str,
    tag: str,
    bundle_root: Path,
    exclude: Iterable[str] = ("manifest.json", "manifest.json.minisig"),
) -> Dict[str, Any]:
    """The schema-1 manifest for a plain bundle (no wheels): what a project whose release
    is a tarball of files (tls-edge) needs for the hardened ``get.py``.

    ``{"schema_version": 1, "project", "tag", "created", "files"}`` -- ``files`` covers
    every regular file in ``bundle_root`` (see ``bundle_files``), which is exactly what
    the installer demands: it refuses any bundle member the manifest does not list.
    Deterministic given SOURCE_DATE_EPOCH and the tree."""
    return {
        "schema_version": 1,
        "project": project,
        "tag": tag,
        "created": _iso8601_from_epoch(_epoch()),
        "files": bundle_files(bundle_root, exclude),
    }


def build_manifest(
    *,
    project: str,
    tag: str,
    source_commit: str,
    cmru_wheel: Path,
    ciu_wheel: Path,
    images: Optional[Dict[str, Any]],
    installer_schema_version: int,
    host_config_schema_version: int,
    platform: Dict[str, Any],
    upgrade: Dict[str, Any],
    bundle_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Assemble the §3 manifest dict.

    All project-specific inputs are supplied by the caller (SPEC F / cmru.toml config);
    cmru hardcodes nothing about the consuming project.

    Args:
        project:                     Project name (e.g. "dstdns").
        tag:                         Full release tag (e.g. "dstdns-v1.2.3").
        source_commit:               HEAD commit SHA.
        cmru_wheel:                  Path to the bundled cmru wheel (.whl).
        ciu_wheel:                   Path to the bundled ciu wheel (.whl).
        images:                      Image digest map {service: {repository, tag, digest}};
                                     None if the project has no images.
        installer_schema_version:    Schema version integer for the installer config.
        host_config_schema_version:  Schema version integer for the host config.
        platform:                    {min_python, arch} dict.
        upgrade:                     {min_from, rollback_to} dict.
        bundle_root:                 when given, the manifest also carries ``files``: the
                                     sha256/size/mode of every regular file in that tree
                                     (see ``bundle_files``), which the hardened get.py
                                     requires for everything it installs.

    Returns:
        The assembled manifest dict (not yet serialized).

    Raises:
        RuntimeError:  SOURCE_DATE_EPOCH not set.
        TypeError/ValueError: images map has wrong shape.
    """
    import importlib.metadata

    epoch = _epoch()
    created = _iso8601_from_epoch(epoch)

    # Wheel checksums via release.sha256_file (do NOT reimplement).
    cmru_sha256 = sha256_file(cmru_wheel)
    ciu_sha256 = sha256_file(ciu_wheel)

    # cmru version from installed package metadata (stdlib importlib.metadata).
    try:
        cmru_version = importlib.metadata.version("cmru")
    except importlib.metadata.PackageNotFoundError:
        cmru_version = "0.0.0"

    # ciu version: read from wheel filename or metadata if installed.
    ciu_version = _version_from_wheel_name(ciu_wheel)

    validated_images = _validate_images(images, project)

    manifest: Dict[str, Any] = {
        "schema_version": 1,
        "project": project,
        "tag": tag,
        "source_commit": source_commit,
        "created": created,
        "cmru": {
            "version": cmru_version,
            "wheel": str(cmru_wheel.name),
            "sha256": cmru_sha256,
        },
        "ciu": {
            "version": ciu_version,
            "wheel": str(ciu_wheel.name),
            "sha256": ciu_sha256,
        },
        "installer_schema_version": installer_schema_version,
        "host_config_schema_version": host_config_schema_version,
        "images": validated_images,
        "platform": platform,
        "upgrade": upgrade,
    }
    if bundle_root is not None:
        manifest["files"] = bundle_files(bundle_root)
    return manifest


def _version_from_wheel_name(wheel_path: Path) -> str:
    """Extract version from wheel filename (PEP 427: <name>-<ver>-<tag>.whl)."""
    stem = wheel_path.stem  # strip .whl
    parts = stem.split("-")
    if len(parts) >= 2:
        return parts[1]
    return "0.0.0"


def write_manifest(manifest: Dict[str, Any], out_path: Path) -> Path:
    """Write manifest to out_path with canonical serialization (§3 rules).

    Canonical = UTF-8, sort_keys=True, compact separators, trailing newline.
    Two calls with the same input produce identical bytes.

    Returns out_path for convenience.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    out_path.write_text(content, encoding="utf-8")
    return out_path


def manifest_sha256(path: Path) -> str:
    """Return the hex SHA-256 of the manifest file at path."""
    return sha256_file(path)


def build_trusted_comment(*, project: str, tag: str, manifest_path: Path) -> str:
    """Build the minisign trusted comment for a manifest.

    Format:  project=<name> tag=<tag> manifest_sha256=<hex>

    This binds the signature to the exact manifest bytes, so an attacker cannot
    swap the manifest for a different file and reuse the signature.
    """
    hexdigest = manifest_sha256(manifest_path)
    return f"project={project} tag={tag} manifest_sha256={hexdigest}"
