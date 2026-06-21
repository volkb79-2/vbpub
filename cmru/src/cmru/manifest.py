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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
