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
from pathlib import Path


REGISTRY_URL = "https://mcr.microsoft.com/v2/devcontainers/python/tags/list"
STABLE_TAG_RE = re.compile(r"^(?:1-)?(?P<python>\d+\.\d+)-(?P<debian>[a-z0-9][a-z0-9.-]*)$")
PACKAGE_DOCS_ROOT = Path(__file__).resolve().parent.parent / "package-manifests-versioned"
MANIFEST_DIR_IN_IMAGE = "/usr/local/share/modern-debian-tools-python-debug"


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


def repo_blob_url(username: str, repo: str, relative_path: str) -> str:
    return (
        f"https://github.com/{username}/{repo}/blob/main/modern-debian-tools-python-debug/"
        f"{relative_path}"
    )


def image_reference(username: str, package_name: str, debian: str, python: str, build_date: str) -> str:
    return f"ghcr.io/{username}/{package_name}:{debian}-py{python}-{build_date}"


def manifest_relpath(package_name: str, debian: str, python: str, build_date: str) -> str:
    return f"package-manifests-versioned/{package_name}/{debian}-py{python}-{build_date}.md"


def family_readme_relpath(package_name: str) -> str:
    return f"package-manifests-versioned/{package_name}/README.md"


def package_catalog(latest_python: str | None, latest_debian: str | None) -> list[dict[str, str]]:
    entries = [
        {
            "package_name": "modern-debian-tools-python-debug",
            "family_title": "Modern Debian Tools + Python Debug",
            "family_kind": "base",
            "target": "bookworm-py311",
            "debian": "bookworm",
            "python": "3.11",
        },
        {
            "package_name": "modern-debian-tools-python-debug",
            "family_title": "Modern Debian Tools + Python Debug",
            "family_kind": "base",
            "target": "bookworm-py313",
            "debian": "bookworm",
            "python": "3.13",
        },
        {
            "package_name": "modern-debian-tools-python-debug",
            "family_title": "Modern Debian Tools + Python Debug",
            "family_kind": "base",
            "target": "trixie-py311",
            "debian": "trixie",
            "python": "3.11",
        },
        {
            "package_name": "modern-debian-tools-python-debug",
            "family_title": "Modern Debian Tools + Python Debug",
            "family_kind": "base",
            "target": "trixie-py313",
            "debian": "trixie",
            "python": "3.13",
        },
        {
            "package_name": "modern-debian-tools-python-debug",
            "family_title": "Modern Debian Tools + Python Debug",
            "family_kind": "base",
            "target": "trixie-py314",
            "debian": "trixie",
            "python": "3.14",
        },
        {
            "package_name": "modern-debian-tools-python-debug-vsc-devcontainer",
            "family_title": "Modern Debian Tools + Python Debug VS Code Devcontainer",
            "family_kind": "vsc",
            "target": "trixie-py311-vsc",
            "debian": "trixie",
            "python": "3.11",
        },
        {
            "package_name": "modern-debian-tools-python-debug-vsc-devcontainer",
            "family_title": "Modern Debian Tools + Python Debug VS Code Devcontainer",
            "family_kind": "vsc",
            "target": "trixie-py313-vsc",
            "debian": "trixie",
            "python": "3.13",
        },
        {
            "package_name": "modern-debian-tools-python-debug-vsc-devcontainer",
            "family_title": "Modern Debian Tools + Python Debug VS Code Devcontainer",
            "family_kind": "vsc",
            "target": "trixie-py314-vsc",
            "debian": "trixie",
            "python": "3.14",
        },
    ]

    if latest_python and latest_debian:
        entries.append(
            {
                "package_name": "modern-debian-tools-python-debug-vsc-devcontainer",
                "family_title": "Modern Debian Tools + Python Debug VS Code Devcontainer",
                "family_kind": "vsc",
                "target": "latest-vsc",
                "debian": latest_debian,
                "python": latest_python,
            }
        )

    return entries


def render_manifest(entry: dict[str, str], *, username: str, repo: str, build_date: str, description: str) -> str:
    package_name = entry["package_name"]
    family_title = entry["family_title"]
    debian = entry["debian"]
    python = entry["python"]
    tag = f"{debian}-py{python}-{build_date}"
    package_readme_url = repo_blob_url(username, repo, family_readme_relpath(package_name))
    release_manifest_url = repo_blob_url(
        username,
        repo,
        manifest_relpath(package_name, debian, python, build_date),
    )
    source_url = f"https://github.com/{username}/{repo}/tree/main/modern-debian-tools-python-debug"
    image_ref = image_reference(username, package_name, debian, python, build_date)
    latest_tag = f"{debian}-py{python}-latest"

    lines = [
        f"# {family_title}",
        "",
        f"Versioned package manifest for `{package_name}`.",
        "",
        "## Release",
        "",
        f"- Build date: `{build_date}`",
        f"- Target: `{entry['target']}`",
        f"- Debian: `{debian}`",
        f"- Python: `{python}`",
        f"- Immutable image tag: `{tag}`",
        f"- Floating image tag: `{latest_tag}`",
        "",
        "## Pull",
        "",
        "```bash",
        f"docker pull {image_ref}",
        "```",
        "",
        "## Purpose",
        "",
    ]
    lines.extend((description.strip() or "No description provided.").splitlines())
    lines.extend(
        [
            "",
            "## Rich Documentation Links",
            "",
            f"- Family overview: {package_readme_url}",
            f"- This release page: {release_manifest_url}",
            f"- Source tree: {source_url}",
            "",
            "## In-Image Files",
            "",
            f"- Release manifest: `{MANIFEST_DIR_IN_IMAGE}/manifest.md`",
            f"- Installed tool inventory: `{MANIFEST_DIR_IN_IMAGE}/installed-tools-manifest.md`",
            "",
            "## Notes",
            "",
            "This repository-hosted page exists because GHCR package descriptions render as flattened plain text.",
            "The image labels therefore point to GitHub-hosted Markdown for richer, package-specific release notes.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_family_readme(
    package_name: str,
    family_title: str,
    entries: list[dict[str, str]],
    *,
    username: str,
    repo: str,
    build_date: str,
) -> str:
    lines = [
        f"# {family_title}",
        "",
        f"Versioned Markdown pages for `{package_name}` that are used as GHCR-friendly rich documentation targets.",
        "",
        "## Current Release Pages",
        "",
    ]

    for entry in sorted(entries, key=lambda item: (item["debian"], item["python"], item["target"])):
        relpath = manifest_relpath(package_name, entry["debian"], entry["python"], build_date)
        url = repo_blob_url(username, repo, relpath)
        lines.append(f"- `{entry['target']}`: [{entry['debian']}-py{entry['python']}-{build_date}]({url})")

    lines.extend(
        [
            "",
            "## Why These Pages Exist",
            "",
            "GHCR stores the plain-text OCI description but does not provide a rich README surface for container packages.",
            "These GitHub-hosted Markdown pages are the richer per-package documentation target linked from OCI labels.",
            "",
            f"Repository root: https://github.com/{username}/{repo}/tree/main/modern-debian-tools-python-debug",
            "",
        ]
    )
    return "\n".join(lines)


def render_root_readme(grouped_entries: dict[str, list[dict[str, str]]], *, username: str, repo: str) -> str:
    lines = [
        "# Versioned Package Manifests",
        "",
        "Repository-hosted Markdown targets for GHCR package metadata.",
        "",
        "## Package Families",
        "",
    ]
    for package_name in sorted(grouped_entries):
        relpath = family_readme_relpath(package_name)
        url = repo_blob_url(username, repo, relpath)
        lines.append(f"- [{package_name}]({url})")
    lines.extend(
        [
            "",
            "Each family directory contains a landing page plus versioned release manifests that can also be copied into the image build output.",
            "",
        ]
    )
    return "\n".join(lines)


def write_package_docs(build_date: str, latest_python: str | None, latest_debian: str | None) -> None:
    username = os.getenv("GITHUB_USERNAME") or "volkb79-2"
    repo = os.getenv("GITHUB_REPO") or "vbpub"
    description_base = os.getenv("OCI_DESCRIPTION_BASE") or os.getenv("OCI_DESCRIPTION") or ""
    description_vsc = os.getenv("OCI_DESCRIPTION_VSC") or os.getenv("OCI_DESCRIPTION") or ""

    entries = package_catalog(latest_python, latest_debian)
    grouped: dict[str, list[dict[str, str]]] = {}
    for entry in entries:
        grouped.setdefault(entry["package_name"], []).append(entry)

    PACKAGE_DOCS_ROOT.mkdir(parents=True, exist_ok=True)
    (PACKAGE_DOCS_ROOT / "README.md").write_text(
        render_root_readme(grouped, username=username, repo=repo),
        encoding="utf-8",
    )

    for package_name, package_entries in grouped.items():
        package_dir = PACKAGE_DOCS_ROOT / package_name
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / "README.md").write_text(
            render_family_readme(
                package_name,
                package_entries[0]["family_title"],
                package_entries,
                username=username,
                repo=repo,
                build_date=build_date,
            ),
            encoding="utf-8",
        )
        for entry in package_entries:
            description = description_vsc if entry["family_kind"] == "vsc" else description_base
            (package_dir / f"{entry['debian']}-py{entry['python']}-{build_date}.md").write_text(
                render_manifest(
                    entry,
                    username=username,
                    repo=repo,
                    build_date=build_date,
                    description=description,
                ),
                encoding="utf-8",
            )


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

    build_date = os.getenv("BUILD_DATE")
    if not build_date:
        raise RuntimeError("BUILD_DATE must be set before generating package manifests")

    write_package_docs(build_date, latest_python, latest_debian)

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
