#!/usr/bin/env python3
"""Resolve devcontainers release labels for stable/dev base images.

Outputs KEY=VALUE lines for step_runner env_command consumption.

This script ALWAYS pulls fresh to ensure latest base image labels are used,
not stale cached local copies.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request


REGISTRY_URL = "https://mcr.microsoft.com/v2/devcontainers/python/tags/list"
STABLE_TAG_RE = re.compile(r"^(?:1-)?(?P<python>\d+\.\d+)-(?P<debian>[a-z0-9][a-z0-9.-]*)$")


def get_image_label(image: str, label: str) -> str:
    """Inspect label from image config."""
    result = subprocess.run(
        [
            "docker",
            "image",
            "inspect",
            image,
            "--format",
            f"{{{{ index .Config.Labels \"{label}\" }}}}",
        ],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def pull_fresh(image: str) -> None:
    """Pull image fresh from registry.
    
    Always pull to ensure we don't use cached labels from stale local copies.
    """
    result = subprocess.run(
        ["docker", "pull", image],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to pull base image: {image}")


def fetch_registry_tags() -> set[str]:
    """Fetch available devcontainers/python tags from MCR."""
    with urllib.request.urlopen(REGISTRY_URL, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))

    tags = payload.get("tags")
    if not isinstance(tags, list):
        raise RuntimeError("Unexpected MCR response: missing 'tags' list")
    return {str(tag) for tag in tags}


def parse_image_tag(image: str) -> str:
    """Extract the tag part from an image reference."""
    if ":" not in image:
        return ""
    return image.rsplit(":", maxsplit=1)[1].strip()


def parse_stable_tag_components(tag: str) -> tuple[str, str] | None:
    """Parse stable tag like '3.14-trixie' or '1-3.14-trixie'."""
    match = STABLE_TAG_RE.match(tag)
    if not match:
        return None
    return match.group("python"), match.group("debian")


def to_version_tuple(version: str) -> tuple[int, ...]:
    """Convert dotted version into an integer tuple for comparison."""
    return tuple(int(part) for part in version.split("."))


def find_newer_stable_tags(current_python: str, current_debian: str, tags: set[str]) -> list[str]:
    """Return sorted stable tags with newer Python for the same Debian flavor."""
    current_version = to_version_tuple(current_python)
    newer: list[tuple[tuple[int, ...], str]] = []

    for tag in tags:
        components = parse_stable_tag_components(tag)
        if not components:
            continue
        python_version, debian = components
        if debian != current_debian:
            continue
        if to_version_tuple(python_version) > current_version:
            newer.append((to_version_tuple(python_version), tag))

    newer.sort(key=lambda item: item[0])
    return [tag for _, tag in newer]


def normalize_stable_variants(tags: set[str]) -> dict[tuple[str, str], set[str]]:
    """Group stable tags by (python, debian), preserving raw tag variants.

    Example key: ("3.14", "trixie")
    Values may include both "3.14-trixie" and "1-3.14-trixie".
    """
    grouped: dict[tuple[str, str], set[str]] = {}
    for tag in tags:
        components = parse_stable_tag_components(tag)
        if not components:
            continue
        key = (components[0], components[1])
        grouped.setdefault(key, set()).add(tag)
    return grouped


def preferred_tag(tags_for_key: set[str], python_version: str, debian: str) -> str:
    """Return preferred canonical tag for a python/debian tuple.

    Prefer plain tag over 1- prefixed tag when both exist.
    """
    plain = f"{python_version}-{debian}"
    prefixed = f"1-{python_version}-{debian}"
    if plain in tags_for_key:
        return plain
    if prefixed in tags_for_key:
        return prefixed
    return sorted(tags_for_key)[0]


def select_latest_stable_tag(tags: set[str]) -> tuple[str, str, str] | None:
    """Select newest stable tag dynamically from live registry tags.

    Selection strategy:
    1. Highest Python semantic version wins.
    2. For Python ties, lexicographically highest Debian codename wins.
    3. Prefer plain tag over 1- tag for output.
    """
    grouped = normalize_stable_variants(tags)
    if not grouped:
        return None

    ordered = sorted(
        grouped.keys(),
        key=lambda item: (to_version_tuple(item[0]), item[1]),
    )
    latest_python, latest_debian = ordered[-1]
    latest_tag = preferred_tag(grouped[(latest_python, latest_debian)], latest_python, latest_debian)
    return latest_tag, latest_python, latest_debian


def find_other_debian_variants_for_python(
    current_python: str,
    current_debian: str,
    tags: set[str],
) -> list[str]:
    """Return stable tags for same Python on other Debian codenames."""
    candidates: set[str] = set()
    for tag in tags:
        components = parse_stable_tag_components(tag)
        if not components:
            continue
        python_version, debian = components
        if python_version != current_python:
            continue
        if debian == current_debian:
            continue
        candidates.add(tag)
    return sorted(candidates)


def find_newer_python_other_debian(
    current_python: str,
    current_debian: str,
    tags: set[str],
) -> list[str]:
    """Return stable tags where Python is newer but Debian differs.

    This can indicate a newer Python stream already available on a different
    Debian codename.
    """
    current_version = to_version_tuple(current_python)
    newer: list[tuple[tuple[int, ...], str]] = []

    for tag in tags:
        components = parse_stable_tag_components(tag)
        if not components:
            continue
        python_version, debian = components
        if debian == current_debian:
            continue
        version_tuple = to_version_tuple(python_version)
        if version_tuple > current_version:
            newer.append((version_tuple, tag))

    newer.sort(key=lambda item: item[0])
    return [tag for _, tag in newer]


def emit_newer_stable_advisory(stable_image: str) -> tuple[bool, str | None, str | None, str | None]:
    """Emit release advisories and return whether newer streams were found.

    Returns tuple:
    - has_newer_release: bool
    - latest_tag: str | None
    - latest_python: str | None
    - latest_debian: str | None
    """
    image_tag = parse_image_tag(stable_image)
    if not image_tag:
        print(
            f"[WARN] Could not parse tag from DEVCONTAINERS_BASE_STABLE={stable_image}; "
            "skipping newer-version check.",
            file=sys.stderr,
        )
        return False, None, None, None

    current = parse_stable_tag_components(image_tag)
    if not current:
        print(
            f"[INFO] Base tag '{image_tag}' is not a stable python/debian tag; "
            "skipping newer-version check.",
            file=sys.stderr,
        )
        return False, None, None, None

    current_python, current_debian = current

    try:
        tags = fetch_registry_tags()
        newer = find_newer_stable_tags(current_python, current_debian, tags)
        other_debian_same_python = find_other_debian_variants_for_python(
            current_python,
            current_debian,
            tags,
        )
        newer_python_other_debian = find_newer_python_other_debian(
            current_python,
            current_debian,
            tags,
        )
        latest = select_latest_stable_tag(tags)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[WARN] Failed to check newer stable devcontainer tags dynamically: {exc}",
            file=sys.stderr,
        )
        return False, None, None, None

    latest_tag: str | None = None
    latest_python: str | None = None
    latest_debian: str | None = None
    if latest:
        latest_tag, latest_python, latest_debian = latest
        print(
            "[INFO] Latest stable devcontainers/python detected from live registry: "
            f"{latest_tag}.",
            file=sys.stderr,
        )

    if not newer and not other_debian_same_python and not newer_python_other_debian:
        print(
            f"[INFO] No newer stable devcontainers/python tag detected for {current_debian} "
            f"(current: {image_tag}).",
            file=sys.stderr,
        )
        return False, latest_tag, latest_python, latest_debian

    if newer:
        newest = newer[-1]
        print(
            "[WARN] Newer stable devcontainers/python tag(s) detected for "
            f"{current_debian}: {', '.join(newer)}. "
            f"Current base: {image_tag}. Recommended newest stable: {newest}.",
            file=sys.stderr,
        )

    if other_debian_same_python:
        print(
            "[INFO] Additional Debian variant(s) detected for the same Python "
            f"{current_python}: {', '.join(other_debian_same_python)}. "
            f"Current Debian: {current_debian}.",
            file=sys.stderr,
        )

    if newer_python_other_debian:
        print(
            "[INFO] Newer Python stream(s) detected on other Debian variant(s): "
            f"{', '.join(newer_python_other_debian)}. "
            "This may indicate upcoming Python support before it reaches your current Debian base.",
            file=sys.stderr,
        )

    # Build gate should only trigger when a newer stable tag exists for the
    # currently selected Debian stream. Cross-Debian observations are advisory.
    return bool(newer), latest_tag, latest_python, latest_debian


def is_truthy_env(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def resolve_release_and_version(image: str) -> tuple[str, str]:
    """Pull fresh and resolve both release label and image version.
    
    Returns (dev.containers.release, version).
    MCR devcontainers use "version" label for image version stream (e.g., "3.0.7").
    """
    # Always pull fresh to avoid stale cached labels
    pull_fresh(image)
    
    release = get_image_label(image, "dev.containers.release")
    if not release:
        raise RuntimeError(f"Missing dev.containers.release label on image: {image}")
    
    # MCR devcontainers use "version" label (e.g., "3.0.7"), not org.opencontainers.image.version
    version = get_image_label(image, "version")
    # version may be empty if label not present, but release is required
    
    return release, version


def main() -> int:
    stable_image = os.getenv(
        "DEVCONTAINERS_BASE_STABLE",
        "mcr.microsoft.com/devcontainers/python:3.14-trixie",
    )
    dev_image = os.getenv(
        "DEVCONTAINERS_BASE_DEV",
        "mcr.microsoft.com/devcontainers/python:dev-3.14-trixie",
    )

    # Dynamic pre-check: fail by default if newer releases are detected.
    has_newer_release, latest_tag, latest_python, latest_debian = emit_newer_stable_advisory(stable_image)

    ignore_new_releases = is_truthy_env(os.getenv("DEVCONTAINERS_IGNORE_NEW_RELEASES"))
    if has_newer_release and not ignore_new_releases:
        print(
            "[ERROR] Newer devcontainer release(s) detected. "
            "Stopping build. Re-run with --ignore-new-releases to continue intentionally.",
            file=sys.stderr,
        )
        return 3

    if has_newer_release and ignore_new_releases:
        print(
            "[WARN] Newer releases detected but continuing due to DEVCONTAINERS_IGNORE_NEW_RELEASES=true.",
            file=sys.stderr,
        )

    stable_release, stable_version = resolve_release_and_version(stable_image)
    dev_release, dev_version = resolve_release_and_version(dev_image)

    # Export latest stable tuple for downstream bake targets (dynamic, live-derived).
    if latest_tag:
        print(f"DEVCONTAINERS_LATEST_STABLE_TAG={latest_tag}")
    else:
        print("DEVCONTAINERS_LATEST_STABLE_TAG=")

    if latest_python:
        print(f"DEVCONTAINERS_LATEST_STABLE_PYTHON={latest_python}")
    else:
        print("DEVCONTAINERS_LATEST_STABLE_PYTHON=")

    if latest_debian:
        print(f"DEVCONTAINERS_LATEST_STABLE_DEBIAN={latest_debian}")
    else:
        print("DEVCONTAINERS_LATEST_STABLE_DEBIAN=")

    if latest_tag:
        print(f"DEVCONTAINERS_BASE_LATEST_STABLE=mcr.microsoft.com/devcontainers/python:{latest_tag}")
    else:
        print("DEVCONTAINERS_BASE_LATEST_STABLE=")

    print(f"DEVCONTAINERS_RELEASE_STABLE={stable_release}")
    print(f"DEVCONTAINERS_RELEASE_DEV={dev_release}")
    print(f"DEVCONTAINERS_VERSION_STABLE={stable_version}")
    print(f"DEVCONTAINERS_VERSION_DEV={dev_version}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(2)
