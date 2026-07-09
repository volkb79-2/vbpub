#!/usr/bin/env python3
"""Resolve devcontainers release labels for stable/dev base images.

Outputs KEY=VALUE lines for step_runner env_command consumption.

This script ALWAYS pulls fresh to ensure latest base image labels are used,
not stale cached local copies.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from manifest_sections import (
    AI_CLI_TOOL_NAMES,
    netcat_package_for_debian,
    read_apt_package_names,
    render_runtime_probe_sections,
)
from stage_tool_artifacts import stage_tool_artifacts


REGISTRY_URL = "https://mcr.microsoft.com/v2/devcontainers/python/tags/list"
STABLE_TAG_RE = re.compile(r"^(?:1-)?(?P<python>\d+\.\d+)-(?P<debian>[a-z0-9][a-z0-9.-]*)$")
PACKAGE_DOCS_ROOT = Path(__file__).resolve().parent.parent / "package-manifests-versioned"
TOOL_VERSION_DISPLAY_ORDER = [
    ("aider", "AIDER_VER"),
    ("reasonix", "REASONIX_VER"),
    ("openclaw", "OPENCLAW_VER"),
    ("antigravity", "ANTIGRAVITY_VER"),
    ("awscli", "AWSCLI_VER"),
    ("b2", "B2_VER"),
    ("bat", "BAT_VER"),
    ("claude", "CLAUDE_CODE_VER"),
    ("consul", "CONSUL_VER"),
    ("codex", "CODEX_VER"),
    ("delta", "DELTA_VER"),
    ("fd", "FD_VER"),
    ("fzf", "FZF_VER"),
    ("gh", "GH_VER"),
    ("htop", "HTOP_VER"),
    ("nvim", "NVIM_VER"),
    ("nvchad", "NVCHAD_VER"),
    ("rga", "RGA_VER"),
    ("ripgrep", "RIPGREP_VER"),
    ("shellcheck", "SHELLCHECK_VER"),
    ("vault", "VAULT_VER"),
    ("yq", "YQ_VER"),
]
CIU_VERSION_FROM_WHEEL_RE = re.compile(r"^ciu-(?P<version>.+)-py[0-9].*\.whl$")
SYSTEM_PACKAGE_EXTRAS = ["postgresql-client", "redis-tools"]
TRANSIENT_HTTP_CODES = {429, 500, 502, 503, 504}
DEFAULT_RETRIES = 3


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


def _urlopen_with_retry(
    req: urllib.request.Request,
    *,
    timeout: int,
    label: str,
) -> object:
    """Open a URL with a small transient retry budget."""
    last_exc: Exception | None = None
    for attempt in range(1, DEFAULT_RETRIES + 1):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code in TRANSIENT_HTTP_CODES and attempt < DEFAULT_RETRIES:
                delay = min(2 ** (attempt - 1), 5)
                print(
                    f"[WARN] Transient HTTP {exc.code} for {label}; retrying in {delay}s "
                    f"({attempt}/{DEFAULT_RETRIES})",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            raise
        except urllib.error.URLError as exc:
            last_exc = exc
            if attempt < DEFAULT_RETRIES:
                delay = min(2 ** (attempt - 1), 5)
                print(
                    f"[WARN] Network error for {label}: {exc.reason}; retrying in {delay}s "
                    f"({attempt}/{DEFAULT_RETRIES})",
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            raise
    assert last_exc is not None
    raise last_exc


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
    req = urllib.request.Request(REGISTRY_URL, method="GET")
    with _urlopen_with_retry(req, timeout=20, label="MCR registry tags") as response:
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


def find_newer_debian_tags(
    current_python: str,
    current_debian: str,
    tags: set[str],
) -> list[str]:
    """Return stable tags with newer Debian for the same Python version."""
    candidates: list[tuple[str, str]] = []
    for tag in tags:
        components = parse_stable_tag_components(tag)
        if not components:
            continue
        python_version, debian = components
        if python_version != current_python:
            continue
        if debian == current_debian:
            continue
        if debian > current_debian:
            candidates.append((debian, tag))
    candidates.sort(key=lambda item: item[0])
    return [tag for _, tag in candidates]


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
            f"[WARN] Could not parse tag from DEVCONTAINERS_BASE_PINNED={stable_image}; "
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
        newer_debian = find_newer_debian_tags(current_python, current_debian, tags)
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

    if not newer and not newer_debian and not other_debian_same_python and not newer_python_other_debian:
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

    if newer_debian:
        newest_debian = newer_debian[-1]
        print(
            "[WARN] Newer Debian codename(s) detected for Python "
            f"{current_python}: {', '.join(newer_debian)}. "
            f"Current Debian: {current_debian}. Recommended newest: {newest_debian}.",
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

    # Build gate triggers when a newer Python tag exists for the current Debian
    # stream, OR when a newer Debian codename exists for the current Python version.
    # Cross-Debian/Python observations alone are advisory.
    has_newer = bool(newer) or bool(newer_debian)
    return has_newer, latest_tag, latest_python, latest_debian


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


def github_api_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "modern-debian-tools-python-debug/resolve-devcontainers-release",
    }
    token = (os.getenv("GITHUB_PUSH_PAT") or os.getenv("GITHUB_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_api_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=github_api_headers(), method="GET")
    try:
        with _urlopen_with_retry(req, timeout=20, label=f"GitHub API {url}") as response:
            payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict):
                return payload
            raise RuntimeError(f"Unexpected JSON payload for {url}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace") if exc.fp else ""
        raise RuntimeError(f"GitHub API request failed for {url}: HTTP {exc.code}: {body}") from exc


def github_release_tag_exists(owner: str, repo: str, tag: str) -> bool:
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
    req = urllib.request.Request(url, headers=github_api_headers(), method="GET")
    try:
        with _urlopen_with_retry(req, timeout=20, label=f"GitHub release tag {tag}") as _:
            return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        body = exc.read().decode("utf-8", "replace") if exc.fp else ""
        raise RuntimeError(f"GitHub API request failed for {url}: HTTP {exc.code}: {body}") from exc


def pick_ciu_wheel_asset_name(release_payload: dict) -> str:
    assets = release_payload.get("assets")
    if not isinstance(assets, list):
        return ""
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "").strip()
        if name.endswith(".whl"):
            return name
    return ""


def derive_ciu_version_from_asset_name(asset_name: str) -> str:
    match = CIU_VERSION_FROM_WHEEL_RE.match(asset_name.strip())
    if not match:
        return ""
    return match.group("version")


def fetch_url_bytes(url: str, *, timeout: int = 20) -> bytes:
    """Fetch raw bytes from a URL (no auth headers)."""
    req = urllib.request.Request(url, method="GET")
    with _urlopen_with_retry(req, timeout=timeout, label=url) as response:
        return response.read()


def resolve_ciu_wheel_via_latest_json(owner: str, repo: str) -> tuple[str, str, str, str, str]:
    """Resolve CIU wheel coordinates from the ciu-latest/latest.json pointer file.

    The ciu-latest release now holds ONLY a ``latest.json`` with shape:
        {version, tag, asset, sha256, url}

    The wheel itself lives at the immutable ``ciu-v<semver>`` release, NOT at
    ``ciu-latest``.  Any code that previously downloaded a ``.whl`` directly
    from ``ciu-latest`` would now 404.

    Returns (resolved_tag, asset_name, wheel_version, wheel_url, wheel_sha256).
    Empty strings on failure.
    """
    latest_json_url = (
        f"https://github.com/{owner}/{repo}/releases/download/ciu-latest/latest.json"
    )
    try:
        raw = fetch_url_bytes(latest_json_url)
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(
            f"[WARN] Failed to fetch ciu-latest/latest.json from {latest_json_url}: {exc}",
            file=sys.stderr,
        )
        return "", "", "", "", ""

    if not isinstance(payload, dict):
        print(
            "[WARN] ciu-latest/latest.json returned unexpected structure (expected object)",
            file=sys.stderr,
        )
        return "", "", "", "", ""

    wheel_version = str(payload.get("version") or "").strip()
    resolved_tag = str(payload.get("tag") or "").strip()
    asset_name = str(payload.get("asset") or "").strip()
    wheel_sha256 = str(payload.get("sha256") or "").strip()
    wheel_url = str(payload.get("url") or "").strip()

    if not (wheel_version and resolved_tag and asset_name and wheel_url):
        print(
            "[WARN] ciu-latest/latest.json is missing required fields "
            "(version/tag/asset/url); falling back to ciu-v* scan",
            file=sys.stderr,
        )
        return "", "", "", "", ""

    print(
        f"[INFO] CIU latest resolved via ciu-latest/latest.json: "
        f"tag={resolved_tag}, asset={asset_name}, version={wheel_version}",
        file=sys.stderr,
    )
    return resolved_tag, asset_name, wheel_version, wheel_url, wheel_sha256


def github_api_list(url: str) -> list:
    """Fetch a JSON array from a GitHub API endpoint."""
    req = urllib.request.Request(url, headers=github_api_headers(), method="GET")
    try:
        with _urlopen_with_retry(req, timeout=20, label=f"GitHub API {url}") as response:
            payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, list):
                return payload
            raise RuntimeError(f"Expected JSON array for {url}, got {type(payload).__name__}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace") if exc.fp else ""
        raise RuntimeError(f"GitHub API request failed for {url}: HTTP {exc.code}: {body}") from exc


def resolve_ciu_wheel_via_release_scan(owner: str, repo: str) -> tuple[str, str, str, str, str]:
    """Fallback: scan ciu-v* releases via GitHub Releases API for the highest semver.

    Downloads the sibling ``<wheel>.sha256`` asset if present.

    Returns (resolved_tag, asset_name, wheel_version, wheel_url, wheel_sha256).
    Empty strings on failure.
    """
    semver_re = re.compile(r"^ciu-v(\d+\.\d+\.\d+.*)$")
    try:
        # Fetch up to 100 releases (enough for typical repos; not paginated further)
        releases = github_api_list(
            f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=100"
        )
    except Exception as exc:  # noqa: BLE001
        print(
            f"[WARN] Failed to list GitHub releases for {owner}/{repo}: {exc}",
            file=sys.stderr,
        )
        return "", "", "", "", ""

    best_version_tuple: tuple[int, ...] | None = None
    best: tuple[str, str, str, str] = ("", "", "", "")  # tag, asset_name, wheel_url, sha256_url

    for release in releases:
        if not isinstance(release, dict):
            continue
        tag = str(release.get("tag_name") or "").strip()
        m = semver_re.match(tag)
        if not m:
            continue
        version_str = m.group(1)
        try:
            vtuple = tuple(int(p) for p in version_str.split(".")[:3])
        except ValueError:
            continue

        assets = release.get("assets")
        if not isinstance(assets, list):
            continue
        whl_asset: str = ""
        sha256_asset_url: str = ""
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "").strip()
            durl = str(asset.get("browser_download_url") or "").strip()
            if name.endswith(".whl") and not whl_asset:
                whl_asset = name
                whl_url_candidate = durl
            if name.endswith(".sha256") and name.replace(".sha256", "") == whl_asset:
                sha256_asset_url = durl

        if not whl_asset:
            continue

        if best_version_tuple is None or vtuple > best_version_tuple:
            best_version_tuple = vtuple
            best = (tag, whl_asset, whl_url_candidate, sha256_asset_url)

    if not best_version_tuple:
        print(
            f"[WARN] No ciu-v* release with a .whl asset found in {owner}/{repo}",
            file=sys.stderr,
        )
        return "", "", "", "", ""

    resolved_tag, asset_name, wheel_url, sha256_asset_url = best
    wheel_version = ".".join(str(p) for p in best_version_tuple)

    # Attempt to download the .sha256 sidecar
    wheel_sha256 = ""
    if sha256_asset_url:
        try:
            raw = fetch_url_bytes(sha256_asset_url)
            # Format is typically "<hex>  <filename>" (sha256sum output) or just the hex
            first_token = raw.decode("utf-8", "replace").strip().split()[0]
            if len(first_token) == 64 and all(c in "0123456789abcdefABCDEF" for c in first_token):
                wheel_sha256 = first_token.lower()
        except Exception as exc:  # noqa: BLE001
            print(
                f"[WARN] Failed to fetch CIU wheel sha256 sidecar from {sha256_asset_url}: {exc}",
                file=sys.stderr,
            )

    print(
        f"[INFO] CIU latest resolved via ciu-v* release scan: "
        f"tag={resolved_tag}, asset={asset_name}, version={wheel_version}",
        file=sys.stderr,
    )
    return resolved_tag, asset_name, wheel_version, wheel_url, wheel_sha256


def resolve_ciu_wheel_coordinates() -> tuple[str, str, str, str, str]:
    """Resolve CIU wheel coordinates (tag, asset_name, version, direct_url, sha256).

    Resolution order (new scheme as of ciu-v2.0.0+):

    1. If CIU_WHEEL_TAG is already set to a concrete ``ciu-v<semver>`` tag, use it
       directly — caller has pinned an explicit version.  CIU_WHEEL_ASSET_NAME must
       also be set (or will be discovered from the release API).  CIU_WHEEL_URL and
       CIU_WHEEL_SHA256 may also be provided to skip the API calls entirely.

    2. Fetch ``https://github.com/<owner>/<repo>/releases/download/ciu-latest/latest.json``
       which is the new pointer file (``{version, tag, asset, sha256, url}``).
       The ``ciu-latest`` release itself no longer holds the wheel.

    3. Fallback: scan ``ciu-v*`` releases via the GitHub Releases API and pick the
       highest semver, downloading the sibling ``.sha256`` sidecar if present.

    Returns (resolved_tag, asset_name, wheel_version, wheel_url, wheel_sha256).
    All empty on failure (non-fatal unless CIU_INSTALL_REQUIRED=true).
    """
    owner = (os.getenv("GITHUB_USERNAME") or "").strip()
    repo = (os.getenv("GITHUB_REPO") or "").strip()
    if not owner or not repo:
        print(
            "[WARN] GITHUB_USERNAME/GITHUB_REPO not set; CIU wheel coordinates remain unset",
            file=sys.stderr,
        )
        return "", "", "", "", ""

    # --- Step 1: respect explicit pin ---
    explicit_tag = (os.getenv("CIU_WHEEL_TAG") or "").strip()
    explicit_asset = (os.getenv("CIU_WHEEL_ASSET_NAME") or "").strip()
    explicit_url = (os.getenv("CIU_WHEEL_URL") or "").strip()
    explicit_sha256 = (os.getenv("CIU_WHEEL_SHA256") or "").strip()

    semver_tag_re = re.compile(r"^ciu-v\d+\.\d+")
    if explicit_tag and semver_tag_re.match(explicit_tag):
        # Pinned to a concrete version; resolve asset/url/sha256 if not fully specified
        if not explicit_asset:
            try:
                release_payload = github_api_json(
                    f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{explicit_tag}"
                )
                explicit_asset = pick_ciu_wheel_asset_name(release_payload)
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[WARN] Failed to resolve CIU asset name for pinned tag {explicit_tag}: {exc}",
                    file=sys.stderr,
                )

        wheel_version = derive_ciu_version_from_asset_name(explicit_asset) if explicit_asset else ""
        if not explicit_url and explicit_asset:
            explicit_url = (
                f"https://github.com/{owner}/{repo}/releases/download/{explicit_tag}/{explicit_asset}"
            )
        print(
            f"[INFO] CIU wheel pinned via CIU_WHEEL_TAG={explicit_tag}: "
            f"asset={explicit_asset}, version={wheel_version}",
            file=sys.stderr,
        )
        return explicit_tag, explicit_asset, wheel_version, explicit_url, explicit_sha256

    # --- Step 2: resolve via ciu-latest/latest.json (preferred) ---
    resolved_tag, asset_name, wheel_version, wheel_url, wheel_sha256 = (
        resolve_ciu_wheel_via_latest_json(owner, repo)
    )

    if resolved_tag:
        return resolved_tag, asset_name, wheel_version, wheel_url, wheel_sha256

    # --- Step 3: fallback — scan ciu-v* releases ---
    print(
        "[INFO] Falling back to ciu-v* release scan for CIU wheel resolution",
        file=sys.stderr,
    )
    return resolve_ciu_wheel_via_release_scan(owner, repo)


WHEELS_LIST_PATH = Path(__file__).resolve().parent.parent / "pip" / "wheels.list"
WHEELS_CONTEXT_DIR = Path(__file__).resolve().parent.parent / "pip" / "wheels"


def parse_wheels_list(path: Path) -> list[str]:
    """Parse pip/wheels.list — one package name per line; '#' starts a comment."""
    if not path.exists():
        return []
    names: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            names.append(line)
    return names


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _verify_wheel_sha256(whl_path: Path, expected_sha256: str) -> None:
    """Verify sha256 of a wheel file; raise RuntimeError on mismatch."""
    actual = _sha256_hex(whl_path.read_bytes())
    if actual.lower() != expected_sha256.lower():
        raise RuntimeError(
            f"sha256 mismatch for {whl_path.name}: "
            f"expected {expected_sha256}, got {actual}"
        )


def resolve_first_party_wheels(
    owner: str, repo: str
) -> list[dict[str, str]]:
    """Resolve, download, and verify first-party wheels from pip/wheels.list.

    For each package name ``N`` in wheels.list, calls
    ``cmru.release.GitHubReleases.resolve_latest(N)`` (owner/repo from cmru.toml;
    token optional for public repos) to find the highest-semver ``N-v*`` release,
    then downloads the ``.whl`` and its ``.whl.sha256`` sidecar into
    ``pip/wheels/`` and verifies the checksum.

    A missing release is a **non-fatal skip** (logged as [WARN]) so that the
    existing resolve flow is never broken by an as-yet-unpublished wheel.
    The actual publish happens in the FINAL CUT after cmru's P7 re-release.

    Returns a list of dicts with keys: name, version, sha256, whl_filename.
    """
    # Import cmru.release from the monorepo source tree (editable install or
    # sys.path fallback).  We import at call time to avoid a hard failure when
    # the caller's Python environment lacks the cmru package.
    try:
        # Prefer the editable-installed package (pip install -e cmru/).
        from cmru.release import GitHubReleases  # type: ignore[import-untyped]
    except ImportError:
        # Fallback: add the monorepo cmru/src to sys.path.
        _cmru_src = Path(__file__).resolve().parent.parent.parent / "cmru" / "src"
        if str(_cmru_src) not in sys.path:
            sys.path.insert(0, str(_cmru_src))
        try:
            from cmru.release import GitHubReleases  # type: ignore[import-untyped]
        except ImportError as exc:
            print(
                f"[WARN] cmru.release not importable; skipping first-party wheel resolution: {exc}",
                file=sys.stderr,
            )
            return []

    names = parse_wheels_list(WHEELS_LIST_PATH)
    if not names:
        return []

    token = (os.getenv("GITHUB_PUSH_PAT") or os.getenv("GITHUB_TOKEN") or "").strip()
    gh = GitHubReleases(owner=owner, repo=repo, token=token)
    gh_public = GitHubReleases(owner=owner, repo=repo, token="")

    WHEELS_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)

    resolved: list[dict[str, str]] = []

    for name in names:
        print(f"[INFO] Resolving first-party wheel: {name}", file=sys.stderr)
        try:
            info = gh.resolve_latest(name)
        except Exception as exc:  # noqa: BLE001
            exc_text = str(exc)
            if token and ("401" in exc_text or "Bad credentials" in exc_text or "Unauthorized" in exc_text):
                print(
                    "[WARN] GitHub API auth failed while resolving first-party wheels; "
                    "retrying without token for public release access.",
                    file=sys.stderr,
                )
                try:
                    info = gh_public.resolve_latest(name)
                except Exception as retry_exc:  # noqa: BLE001
                    print(
                        f"[WARN] Failed to resolve latest release for wheel '{name}': {retry_exc} — skipping",
                        file=sys.stderr,
                    )
                    continue
            else:
                print(
                    f"[WARN] Failed to resolve latest release for wheel '{name}': {exc} — skipping",
                    file=sys.stderr,
                )
                continue

        if info is None:
            print(
                f"[WARN] No '{name}-v*' release found on {owner}/{repo}; "
                "skipping (wheel will be available after the FINAL CUT re-release). "
                "See docs/plan-cmru-finish.md §2B.",
                file=sys.stderr,
            )
            continue

        version = info.get("version") or ""
        assets = info.get("assets") or []

        # Find the .whl asset and its .sha256 sidecar.
        whl_asset: dict[str, str] | None = None
        sha256_asset: dict[str, str] | None = None
        for asset in assets:
            asset_name = str(asset.get("name") or "")
            asset_url = str(asset.get("url") or "")
            if asset_name.endswith(".whl") and whl_asset is None:
                whl_asset = {"name": asset_name, "url": asset_url}
            elif asset_name.endswith(".whl.sha256") and sha256_asset is None:
                sha256_asset = {"name": asset_name, "url": asset_url}

        if whl_asset is None:
            print(
                f"[WARN] No .whl asset in release '{name}-v{version}'; skipping",
                file=sys.stderr,
            )
            continue

        whl_filename = whl_asset["name"]
        whl_url = whl_asset["url"]
        whl_dest = WHEELS_CONTEXT_DIR / whl_filename

        # Purge any pre-existing wheels (and their .sha256 sidecars) for this
        # package before staging the new one.  The Dockerfile installs via a
        # bare glob ("pip install *.whl") which cannot tolerate two versions of
        # the same package — pip raises ResolutionImpossible.
        for stale in list(WHEELS_CONTEXT_DIR.glob(f"{name}-*.whl")):
            if stale != whl_dest:
                print(f"[INFO] Removing stale wheel {stale.name}", file=sys.stderr)
                stale.unlink()
        for stale in list(WHEELS_CONTEXT_DIR.glob(f"{name}-*.whl.sha256")):
            stale_whl = stale.with_suffix("")  # strip .sha256 → .whl path
            if stale_whl != whl_dest:
                print(f"[INFO] Removing stale wheel sidecar {stale.name}", file=sys.stderr)
                stale.unlink()

        # Download wheel.
        print(f"[INFO] Downloading {whl_filename} from {whl_url}", file=sys.stderr)
        try:
            req = urllib.request.Request(whl_url, method="GET")
            with urllib.request.urlopen(req, timeout=60) as response:
                whl_bytes = response.read()
        except Exception as exc:  # noqa: BLE001
            print(
                f"[WARN] Failed to download wheel '{whl_filename}': {exc} — skipping",
                file=sys.stderr,
            )
            continue

        actual_sha256 = _sha256_hex(whl_bytes)

        # Download and verify the .sha256 sidecar if present.
        expected_sha256 = ""
        if sha256_asset:
            sha256_filename = sha256_asset["name"]
            sha256_url = sha256_asset["url"]
            sha256_dest = WHEELS_CONTEXT_DIR / sha256_filename
            try:
                req256 = urllib.request.Request(sha256_url, method="GET")
                with urllib.request.urlopen(req256, timeout=20) as response:
                    sha256_raw = response.read().decode("utf-8", "replace").strip()
                # Format: "<hex>  <filename>" (sha256sum output) or just the hex.
                first_token = sha256_raw.split()[0] if sha256_raw else ""
                if len(first_token) == 64 and all(
                    c in "0123456789abcdefABCDEF" for c in first_token
                ):
                    expected_sha256 = first_token.lower()
                sha256_dest.write_bytes(sha256_raw.encode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[WARN] Failed to fetch sha256 sidecar for '{whl_filename}': {exc}",
                    file=sys.stderr,
                )

        if expected_sha256 and actual_sha256.lower() != expected_sha256.lower():
            print(
                f"[WARN] sha256 mismatch for {whl_filename}: "
                f"expected {expected_sha256}, got {actual_sha256} — skipping",
                file=sys.stderr,
            )
            continue

        whl_dest.write_bytes(whl_bytes)
        sha256_used = expected_sha256 or actual_sha256

        if expected_sha256:
            print(
                f"[INFO] Wheel {whl_filename} verified (sha256 {sha256_used[:16]}...)",
                file=sys.stderr,
            )
        else:
            print(
                f"[INFO] Wheel {whl_filename} downloaded (sha256 {sha256_used[:16]}...; "
                "no sidecar — checksum is computed locally)",
                file=sys.stderr,
            )

        resolved.append({
            "name": name,
            "version": version,
            "sha256": sha256_used,
            "whl_filename": whl_filename,
        })

    return resolved


def probe_system_package_candidates(debian: str, python: str) -> list[str]:
    image = f"python:{python}-{debian}"
    packages = read_apt_package_names()
    for package_name in (netcat_package_for_debian(debian), *SYSTEM_PACKAGE_EXTRAS):
        if package_name not in packages:
            packages.append(package_name)
    package_list_expr = " ".join(packages)

    probe_script = (
        "set -euo pipefail\n"
        "apt-get update -qq >/dev/null\n"
        f"for pkg in {package_list_expr}; do\n"
        "  ver=$(apt-cache policy \"$pkg\" | awk '/Candidate:/ {print $2; exit}')\n"
        "  if [ -z \"$ver\" ] || [ \"$ver\" = \"(none)\" ]; then\n"
        "    ver=not-available\n"
        "  fi\n"
        "  printf '%s=%s\\n' \"$pkg\" \"$ver\"\n"
        "done\n"
    )

    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "bash", image, "-lc", probe_script],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(
            "Failed to probe system package versions via docker for "
            f"{image}: {stderr or 'no stderr output'}"
        )

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"System package probe returned no lines for {image}")
    return lines


def build_runtime_custom_tooling_map(
    tool_metadata: dict | None,
    ciu_wheel_version: str,
    first_party_wheels: list[dict[str, str]] | None = None,
) -> dict[str, str]:
    resolved_versions = {}
    if isinstance(tool_metadata, dict):
        raw = tool_metadata.get("resolved_versions")
        if isinstance(raw, dict):
            resolved_versions = raw

    def install_enabled(env_name: str) -> bool:
        value = os.getenv(env_name)
        if value is None:
            return True
        return value.strip().lower() not in {"0", "false", "no", "off"}

    install_aider = install_enabled("INSTALL_AIDER")
    install_reasonix = install_enabled("INSTALL_REASONIX")
    install_openclaw = install_enabled("INSTALL_OPENCLAW")
    install_antigravity = install_enabled("INSTALL_ANTIGRAVITY")
    install_claude = install_enabled("INSTALL_CLAUDE_CODE")
    install_codex = install_enabled("INSTALL_CODEX")

    aider_requested = str(
        resolved_versions.get("AIDER_VER") or (os.getenv("AIDER_VERSION") or "latest")
    ).strip() or "latest"
    if install_aider:
        aider_display = str(resolved_versions.get("AIDER_VER") or aider_requested).strip()
    else:
        aider_display = "not-installed"

    # Derive cmru version from first_party_wheels resolution result.
    cmru_version = ""
    if first_party_wheels:
        for w in first_party_wheels:
            if w.get("name") == "cmru":
                cmru_version = w.get("version") or ""
                break

    tooling = {
        "cmru": cmru_version or "not-installed",
        "CIU": ciu_wheel_version or "not-installed",
        "aider": aider_display,
        "reasonix": (
            str(resolved_versions.get("REASONIX_VER") or "unknown")
            if install_reasonix
            else "not-installed"
        ),
        "openclaw": (
            str(resolved_versions.get("OPENCLAW_VER") or "unknown")
            if install_openclaw
            else "not-installed"
        ),
        "antigravity": (
            str(resolved_versions.get("ANTIGRAVITY_VER") or "unknown")
            if install_antigravity
            else "not-installed"
        ),
        "dtop": str(resolved_versions.get("DTOP_VER") or "unknown"),
        "lazydocker": str(resolved_versions.get("LAZYDOCKER_VER") or "unknown"),
        "glances": str(resolved_versions.get("GLANCES_VER") or "unknown"),
        "dive": str(resolved_versions.get("DIVE_VER") or "unknown"),
        "syft": str(resolved_versions.get("SYFT_VER") or "unknown"),
        "hadolint": str(resolved_versions.get("HADOLINT_VER") or "unknown"),
        "grype": str(resolved_versions.get("GRYPE_VER") or "unknown"),
        "cdebug": str(resolved_versions.get("CDEBUG_VER") or "unknown"),
        "awscli": str(resolved_versions.get("AWSCLI_VER") or "unknown"),
        "b2": str(resolved_versions.get("B2_VER") or "unknown"),
        "bat": str(resolved_versions.get("BAT_VER") or "unknown"),
        "claude": (
            str(resolved_versions.get("CLAUDE_CODE_VER") or "unknown")
            if install_claude
            else "not-installed"
        ),
        "consul": str(resolved_versions.get("CONSUL_VER") or "unknown"),
        "codex": (
            str(resolved_versions.get("CODEX_VER") or "unknown")
            if install_codex
            else "not-installed"
        ),
        "delta": str(resolved_versions.get("DELTA_VER") or "unknown"),
        "fd": str(resolved_versions.get("FD_VER") or "unknown"),
        "fzf": str(resolved_versions.get("FZF_VER") or "unknown"),
        "gh": str(resolved_versions.get("GH_VER") or "unknown"),
        "htop": str(resolved_versions.get("HTOP_VER") or "unknown"),
        "nvim": str(resolved_versions.get("NVIM_VER") or "unknown"),
        "nvchad": str(resolved_versions.get("NVCHAD_VER") or "unknown"),
        "rga": str(resolved_versions.get("RGA_VER") or "unknown"),
        "ripgrep": str(resolved_versions.get("RIPGREP_VER") or "unknown"),
        "shellcheck": str(resolved_versions.get("SHELLCHECK_VER") or "unknown"),
        "vault": str(resolved_versions.get("VAULT_VER") or "unknown"),
        "yq": str(resolved_versions.get("YQ_VER") or "unknown"),
    }

    return tooling


def collect_runtime_probe_sections(
    entries: list[dict[str, str]],
    tool_metadata: dict | None,
    ciu_wheel_version: str,
    first_party_wheels: list[dict[str, str]] | None = None,
) -> dict[str, list[str]]:
    custom_tooling = build_runtime_custom_tooling_map(
        tool_metadata, ciu_wheel_version, first_party_wheels
    )
    probe_cache: dict[tuple[str, str], list[str]] = {}
    runtime_sections: dict[str, list[str]] = {}

    for entry in entries:
        key = (entry["debian"], entry["python"])
        if key not in probe_cache:
            try:
                probe_cache[key] = probe_system_package_candidates(entry["debian"], entry["python"])
            except Exception as exc:  # noqa: BLE001
                print(
                    "[WARN] Runtime package probe failed for "
                    f"{entry['target']} ({entry['python']}-{entry['debian']}): {exc}",
                    file=sys.stderr,
                )
                probe_cache[key] = ["probe-unavailable"]

        runtime_sections[entry["target"]] = render_runtime_probe_sections(
            custom_tooling,
            probe_cache[key],
        )

    return runtime_sections


def repo_blob_url(username: str, repo: str, relative_path: str) -> str:
    return (
        f"https://github.com/{username}/{repo}/blob/main/modern-debian-tools-python-debug/"
        f"{relative_path}"
    )


def build_tag(debian: str, python: str, build_date: str, variant: str = "") -> str:
    """Render the immutable tag, inserting an optional variant (e.g. "php8.5") as a TAG
    dimension between the py<python> segment and the date segment. This is the same
    scheme docker-bake.hcl's base_tag_variant/vsc_tag_variant functions use, so a flavor
    build never gets a separate package name — only an extra tag segment.
    """
    variant_part = f"-{variant}" if variant else ""
    return f"{debian}-py{python}{variant_part}-{build_date}"


def build_latest_tag(debian: str, python: str, variant: str = "") -> str:
    variant_part = f"-{variant}" if variant else ""
    return f"{debian}-py{python}{variant_part}-latest"


def image_reference(
    username: str, package_name: str, debian: str, python: str, build_date: str, variant: str = ""
) -> str:
    return f"ghcr.io/{username}/{package_name}:{build_tag(debian, python, build_date, variant)}"


def manifest_relpath(package_name: str, debian: str, python: str, build_date: str, variant: str = "") -> str:
    return f"package-manifests-versioned/{package_name}/{build_tag(debian, python, build_date, variant)}.md"


def family_readme_relpath(package_name: str) -> str:
    return f"package-manifests-versioned/{package_name}/README.md"


def family_latest_relpath(package_name: str) -> str:
    return f"package-manifests-versioned/{package_name}/latest.md"


BAKE_FILE = PACKAGE_DOCS_ROOT.parent / "docker-bake.hcl"

_FAMILY_TITLES = {
    "modern-debian-tools-python-debug": "Modern Debian Tools + Python Debug",
    "modern-debian-tools-python-debug-vsc-devcontainer": (
        "Modern Debian Tools + Python Debug VS Code Devcontainer"
    ),
}

# Historical package names retired 2026-07-07: the PHP 8.5 flavor used to publish under
# its own GHCR package families ("...-php85" / "...-php85-vsc-devcontainer"). It now
# publishes into the base families above with "-php8.5-" as a TAG segment instead (see
# _tag_variant_from_args and docker-bake.hcl's base_tag_variant/vsc_tag_variant). The old
# package-manifests-versioned/*-php85*/ directories are left in place, unmodified, as a
# frozen historical record — already-published images still have OCI labels pointing at
# those exact paths. See php-addition.md / README.md "PHP 8.5 flavor" section for the
# migration note.


def _tag_variant_from_args(args: dict) -> str:
    """Derive the tag-variant segment (e.g. "php8.5") from a bake target's args.

    A flavor is expressed as a TAG dimension, not a package name, so this is the single
    place that decides whether/what variant segment a target's tags and manifest path
    carry. Currently the only flavor is PHP; add further ``elif`` branches here (not new
    package names) if another flavor is introduced.
    """
    if str(args.get("INSTALL_PHP", "")).strip().lower() == "true":
        php_version = str(args.get("PHP_VERSION") or "").strip()
        if php_version:
            return f"php{php_version}"
    return ""


def _package_name_from_target(name: str, spec: dict) -> str:
    """Derive the GHCR package name for a bake target.

    Preferred source is the target's own ``PACKAGE_MANIFEST_SOURCE`` arg
    (``package-manifests-versioned/<package_name>/...``) so the manifest filename
    and the built image can never disagree. Falls back to the image refs / target
    name suffix for the (vsc vs base) distinction.

    Flavors (currently: PHP 8.5) are NOT a package-name dimension — see
    ``_tag_variant_from_args``. A target named e.g. ``trixie-py314-php85-vsc`` still
    resolves to the plain ``modern-debian-tools-python-debug-vsc-devcontainer`` package
    name here; its flavor only shows up in the tag/manifest-filename variant segment.
    """
    args = spec.get("args") or {}
    manifest_source = args.get("PACKAGE_MANIFEST_SOURCE") or ""
    parts = manifest_source.split("/")
    if len(parts) >= 2 and parts[0] == "package-manifests-versioned" and parts[1]:
        return parts[1]
    tags = spec.get("tags") or []
    is_vsc = any("-vsc-devcontainer" in str(tag) for tag in tags) or name.endswith("-vsc")
    return (
        "modern-debian-tools-python-debug-vsc-devcontainer"
        if is_vsc
        else "modern-debian-tools-python-debug"
    )


def built_target_entries() -> list[dict[str, str]]:
    """The authoritative documentation matrix = the bake ``all`` build matrix.

    We enumerate exactly the targets in ``group "all"`` via
    ``docker buildx bake ... all --print`` (the same group the build/push steps
    bake) and emit one entry per *distinct* manifest. Multi-Python targets that
    share a primary (e.g. ``trixie-py314-vsc`` and ``trixie-py314-py311-vsc``)
    collapse to one entry. This guarantees manifest/build parity: a manifest is
    only ever generated for an image that is actually built and pushed.
    """
    try:
        result = subprocess.run(
            ["docker", "buildx", "bake", "-f", str(BAKE_FILE), "all", "--print"],
            cwd=str(BAKE_FILE.parent),
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "docker buildx is required to enumerate built targets for manifest "
            f"generation but was not found: {exc}"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"`docker buildx bake -f {BAKE_FILE.name} all --print` failed "
            f"(exit {exc.returncode}): {exc.stderr.strip()}"
        ) from exc

    bake = json.loads(result.stdout)
    target_names = ((bake.get("group") or {}).get("all") or {}).get("targets") or []
    targets = bake.get("target") or {}

    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for name in target_names:
        spec = targets.get(name) or {}
        args = spec.get("args") or {}
        debian = args.get("DEBIAN_VERSION")
        python = args.get("PYTHON_VERSION")
        if not debian or not python:
            sys.stderr.write(
                f"[WARN] bake target {name!r} has no DEBIAN_VERSION/PYTHON_VERSION; "
                "skipping manifest entry\n"
            )
            continue
        package_name = _package_name_from_target(name, spec)
        variant = _tag_variant_from_args(args)
        # variant is part of the identity key: a flavor build (e.g. php8.5) shares
        # (package_name, debian, python) with the plain build and must NOT collapse
        # into it the way same-primary multi-Python targets intentionally do.
        key = (package_name, debian, python, variant)
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            {
                "package_name": package_name,
                "family_title": _FAMILY_TITLES.get(package_name, package_name),
                "family_kind": "vsc" if package_name.endswith("-vsc-devcontainer") else "base",
                "target": name,
                "debian": debian,
                "python": python,
                "variant": variant,
            }
        )

    if not entries:
        raise RuntimeError(
            f"no buildable targets in the 'all' group of {BAKE_FILE.name}; "
            "nothing to document (did the group get fully commented out?)"
        )
    return entries


def package_catalog(latest_python: str | None, latest_debian: str | None) -> list[dict[str, str]]:
    """Products to document — derived dynamically from the bake build matrix.

    ``latest_python`` / ``latest_debian`` are accepted for call-site compatibility
    (latest-pointer selection lives in :func:`choose_latest_entry`); the catalog
    itself is now driven entirely by :func:`built_target_entries`, so it can never
    drift from what was built.
    """
    return built_target_entries()


def normalize_description_text(description: str) -> str:
    parts = [line.strip() for line in description.splitlines() if line.strip()]
    return " ".join(parts)


def build_display_description(
    description: str,
) -> str:
    base = normalize_description_text(description) or "Versioned package documentation is available in the source repository."
    max_len = 512

    if len(base) <= max_len:
        return base

    if max_len <= 4:
        return base[:max_len]

    return base[: max_len - 3].rstrip() + "..."


def load_tool_artifact_metadata(metadata_path: Path) -> dict:
    if not metadata_path.exists():
        raise RuntimeError(f"Missing tool artifact metadata file: {metadata_path}")
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid tool artifact metadata JSON: {metadata_path}") from exc


def render_tool_artifact_lines(tool_metadata: dict | None) -> list[str]:
    """Render '## Staged Tool Artifacts' section (resolved versions only; no sha256 list).

    The full sha256 digest listing is moved to an appendix at the end of the manifest
    via :func:`render_artifact_digests_appendix` so the top of the document stays
    readable at a glance.
    """
    if not tool_metadata:
        return []

    resolved_versions = tool_metadata.get("resolved_versions")
    lines: list[str] = [
        "## Staged Tool Artifacts",
        "",
    ]

    if isinstance(resolved_versions, dict):
        ai_lines: list[str] = []
        support_lines: list[str] = []
        for display_name, version_key in sorted(
            TOOL_VERSION_DISPLAY_ORDER, key=lambda item: item[0].lower()
        ):
            version_value = str(resolved_versions.get(version_key) or "").strip()
            if not version_value:
                continue
            target_lines = ai_lines if display_name in AI_CLI_TOOL_NAMES else support_lines
            target_lines.append(f"- {display_name}: `{version_value}`")

        if ai_lines:
            lines.extend(["### AI CLI Tools", ""])
            lines.extend(sorted(ai_lines, key=lambda item: item.lower()))
            lines.append("")

        if support_lines:
            lines.extend(["### Supporting Tool Versions", ""])
            lines.extend(sorted(support_lines, key=lambda item: item.lower()))
            lines.append("")

    lines.append(
        "Generated from local pre-staging metadata to make release documentation auditable and reproducible."
    )
    lines.append("")
    return lines


def render_artifact_digests_appendix(tool_metadata: dict | None) -> list[str]:
    """Render the appendix section with full sha256 digest listing.

    Placed at the END of the manifest to keep the top readable. Consumers who need
    to verify an artifact can find the digests here without cluttering the summary.
    """
    if not tool_metadata:
        return []

    artifacts = tool_metadata.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        return []

    lines: list[str] = [
        "## Appendix: Artifact Sources and Digests",
        "",
        "Full sha256 digests for all staged artifacts. Use these to verify reproducibility.",
        "",
    ]
    for raw_item in artifacts:
        if not isinstance(raw_item, dict):
            continue
        tool = str(raw_item.get("tool") or "unknown").strip()
        version = str(raw_item.get("version") or "").strip()
        kind = str(raw_item.get("kind") or "artifact").strip()
        digest = str(raw_item.get("sha256") or "").strip()
        source_url = str(raw_item.get("source_url") or "").strip()
        lines.append(
            f"- {tool} {version} ({kind}): sha256 `{digest}`; source `{source_url}`"
        )
    lines.append("")
    return lines


def choose_latest_entry(
    entries: list[dict[str, str]],
    *,
    latest_python: str | None,
    latest_debian: str | None,
) -> dict[str, str]:
    # Flavor builds (variant != "", e.g. php8.5) are specialized add-ons, not the family's
    # main recommended release — never let one become the "latest.md" pointer as long as
    # a plain (variant == "") entry exists in the same family.
    plain_entries = [entry for entry in entries if not entry.get("variant")]
    candidates = plain_entries or entries

    family_kind = candidates[0]["family_kind"]
    if family_kind == "vsc" and latest_python and latest_debian:
        for entry in candidates:
            if entry["python"] == latest_python and entry["debian"] == latest_debian:
                return entry

    return sorted(
        candidates,
        key=lambda item: (to_version_tuple(item["python"]), item["debian"], item["target"]),
    )[-1]


def render_first_party_wheels_section(
    wheels: list[dict[str, str]] | None,
) -> list[str]:
    """Render the '## First-Party Wheels' section lines for a manifest."""
    if not wheels:
        return []
    lines = ["## First-Party Wheels", ""]
    for w in sorted(wheels, key=lambda item: str(item.get("name") or "").lower()):
        lines.append(
            f"- {w['name']} `{w['version']}` — sha256: `{w['sha256']}`"
        )
    lines.append("")
    return lines


def render_manifest(
    entry: dict[str, str],
    *,
    username: str,
    repo: str,
    build_date: str,
    description: str,
    tool_metadata: dict | None,
    runtime_probe_sections: list[str] | None,
    first_party_wheels: list[dict[str, str]] | None = None,
) -> str:
    """Placeholder manifest — the real manifest is generated at build time.

    ``manifest_sections.py`` inside the Dockerfile generates the canonical
    manifest from live probes (pip freeze, dpkg-query, php -m, tool versions).
    The post-build extraction step in ``build-push.py`` copies that in-image
    manifest into this directory, overwriting this placeholder.
    """
    package_name = entry["package_name"]
    family_title = entry["family_title"]
    tag = build_tag(entry["debian"], entry["python"], build_date, entry.get("variant", ""))
    return (
        f"# {family_title}\n\n"
        f"Versioned package manifest for `{package_name}`.\n\n"
        "## Release\n\n"
        f"- Build date: `{build_date}`\n"
        f"- Target: `{entry['target']}`\n"
        f"- Immutable image tag: `{tag}`\n\n"
        "## Manifest\n\n"
        "This manifest is generated at build time from live probes inside the "
        "Docker image. The file is extracted post-build by ``build-push.py`` "
        "from the freshly built image and committed here so the repo and image "
        "always agree.\n\n"
        "To view the manifest for this release, pull the image and run:\n\n"
        "```bash\n"
        f"docker pull ghcr.io/{username}/{package_name}:{tag}\n"
        "docker run --rm "
        f"ghcr.io/{username}/{package_name}:{tag} \\n"
        "  cat /usr/local/share/modern-debian-tools-python-debug/manifest.md\n"
        "```\n"
    )


def render_family_readme(
    package_name: str,
    family_title: str,
    entries: list[dict[str, str]],
    *,
    username: str,
    repo: str,
    build_date: str,
) -> str:
    latest_url = repo_blob_url(username, repo, family_latest_relpath(package_name))
    lines = [
        f"# {family_title}",
        "",
        f"Versioned Markdown pages for `{package_name}` that are used as GHCR-friendly rich documentation targets.",
        "",
        f"Stable current-docs link: {latest_url}",
        "",
        "## Current Release Pages",
        "",
    ]

    for entry in sorted(
        entries, key=lambda item: (item["debian"], item["python"], item.get("variant", ""), item["target"])
    ):
        variant = entry.get("variant", "")
        relpath = manifest_relpath(package_name, entry["debian"], entry["python"], build_date, variant)
        url = repo_blob_url(username, repo, relpath)
        tag = build_tag(entry["debian"], entry["python"], build_date, variant)
        lines.append(f"- `{entry['target']}`: [{tag}]({url})")

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


def render_family_latest(
    package_name: str,
    family_title: str,
    latest_entry: dict[str, str],
    entries: list[dict[str, str]],
    *,
    username: str,
    repo: str,
    build_date: str,
) -> str:
    latest_manifest_relpath = manifest_relpath(
        package_name,
        latest_entry["debian"],
        latest_entry["python"],
        build_date,
        latest_entry.get("variant", ""),
    )
    latest_manifest_url = repo_blob_url(username, repo, latest_manifest_relpath)
    family_readme_url = repo_blob_url(username, repo, family_readme_relpath(package_name))

    lines = [
        f"# {family_title} Latest Docs",
        "",
        f"Stable documentation landing page for `{package_name}`.",
        "",
        "## Current Recommended Release",
        "",
        f"- Target: `{latest_entry['target']}`",
        f"- Debian: `{latest_entry['debian']}`",
        f"- Python: `{latest_entry['python']}`",
        f"- Build date: `{build_date}`",
        f"- Versioned manifest: {latest_manifest_url}",
        f"- Family index: {family_readme_url}",
        "",
        "## Other Release Pages",
        "",
    ]

    for entry in sorted(
        entries, key=lambda item: (item["debian"], item["python"], item.get("variant", ""), item["target"])
    ):
        relpath = manifest_relpath(
            package_name, entry["debian"], entry["python"], build_date, entry.get("variant", "")
        )
        url = repo_blob_url(username, repo, relpath)
        lines.append(f"- `{entry['target']}`: {url}")

    lines.extend(
        [
            "",
            "This stable page exists so OCI metadata can point to one durable documentation URL while the release-specific manifests remain versioned.",
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
        latest_url = repo_blob_url(username, repo, family_latest_relpath(package_name))
        lines.append(f"- [{package_name}]({url}) - stable current docs: {latest_url}")
    lines.extend(
        [
            "",
            "Each family directory contains a landing page plus versioned release manifests that can also be copied into the image build output.",
            "",
        ]
    )
    return "\n".join(lines)


def write_package_docs(
    build_date: str,
    latest_python: str | None,
    latest_debian: str | None,
    *,
    tool_metadata: dict | None,
    ciu_wheel_version: str,
    first_party_wheels: list[dict[str, str]] | None = None,
) -> list[str]:
    username = os.getenv("GITHUB_USERNAME") or "volkb79-2"
    repo = os.getenv("GITHUB_REPO") or "vbpub"
    description_base = os.getenv("OCI_DESCRIPTION_BASE") or os.getenv("OCI_DESCRIPTION") or ""
    description_vsc = os.getenv("OCI_DESCRIPTION_VSC") or os.getenv("OCI_DESCRIPTION") or ""

    entries = package_catalog(latest_python, latest_debian)
    runtime_probe_sections = collect_runtime_probe_sections(
        entries,
        tool_metadata,
        ciu_wheel_version,
        first_party_wheels,
    )
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
        latest_entry = choose_latest_entry(
            package_entries,
            latest_python=latest_python,
            latest_debian=latest_debian,
        )
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
        (package_dir / "latest.md").write_text(
            render_family_latest(
                package_name,
                package_entries[0]["family_title"],
                latest_entry,
                package_entries,
                username=username,
                repo=repo,
                build_date=build_date,
            ),
            encoding="utf-8",
        )
        for entry in package_entries:
            description = description_vsc if entry["family_kind"] == "vsc" else description_base
            manifest_filename = build_tag(entry["debian"], entry["python"], build_date, entry.get("variant", ""))
            (package_dir / f"{manifest_filename}.md").write_text(
                render_manifest(
                    entry,
                    username=username,
                    repo=repo,
                    build_date=build_date,
                    description=description,
                    tool_metadata=tool_metadata,
                    runtime_probe_sections=runtime_probe_sections.get(entry["target"]),
                    first_party_wheels=first_party_wheels,
                ),
                encoding="utf-8",
            )

    return sorted(grouped)


def main() -> int:
    stable_image = (
        os.getenv("DEVCONTAINERS_BASE_PINNED")
        or os.getenv("DEVCONTAINERS_BASE_STABLE")
        or "mcr.microsoft.com/devcontainers/python:3.14-trixie"
    )
    dev_image = os.getenv(
        "DEVCONTAINERS_BASE_DEV",
        "mcr.microsoft.com/devcontainers/python:dev-3.14-trixie",
    )

    # Startup progress
    sys.stderr.write("[INFO] Checking MCR registry for newer devcontainers/python releases...\n")
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

    build_date = os.getenv("BUILD_DATE")
    if not build_date:
        raise RuntimeError("BUILD_DATE must be set before generating package manifests")

    sys.stderr.write("[INFO] Staging tool artifacts (checking/tool-versions, download URLs...) ...\n")
    staging_result = stage_tool_artifacts(build_date=build_date)
    ciu_wheel_tag, ciu_wheel_asset_name, ciu_wheel_version, ciu_wheel_url, ciu_wheel_sha256 = (
        resolve_ciu_wheel_coordinates()
    )
    ciu_install_required = is_truthy_env(os.getenv("CIU_INSTALL_REQUIRED"))
    if ciu_install_required and (not ciu_wheel_tag or not ciu_wheel_asset_name):
        raise RuntimeError(
            "CIU_INSTALL_REQUIRED=true but CIU wheel coordinates could not be resolved "
            "(CIU_WHEEL_TAG/CIU_WHEEL_ASSET_NAME)"
        )

    sys.stderr.write(f"[INFO] Pulling base image {stable_image} to inspect labels...\n")
    stable_release, stable_version = resolve_release_and_version(stable_image)
    sys.stderr.write(f"[INFO] Pulling base image {dev_image} to inspect labels...\n")
    dev_release, dev_version = resolve_release_and_version(dev_image)

    description_base = os.getenv("OCI_DESCRIPTION_BASE") or os.getenv("OCI_DESCRIPTION") or ""
    description_vsc = os.getenv("OCI_DESCRIPTION_VSC") or os.getenv("OCI_DESCRIPTION") or ""

    tool_metadata = load_tool_artifact_metadata(staging_result.metadata_path)

    # Resolve first-party wheels (cmru and any future entries in pip/wheels.list).
    # Non-fatal when a release is not yet published (skip with [WARN]).
    # The actual rebuild happens in the FINAL CUT after cmru's P7 re-release.
    _github_owner = os.getenv("GITHUB_USERNAME") or "volkb79-2"
    _github_repo = os.getenv("GITHUB_REPO") or "vbpub"
    sys.stderr.write("[INFO] Resolving first-party wheels from pip/wheels.list...\n")
    first_party_wheels = resolve_first_party_wheels(_github_owner, _github_repo)

    package_names = write_package_docs(
        build_date,
        latest_python,
        latest_debian,
        tool_metadata=tool_metadata,
        ciu_wheel_version=ciu_wheel_version,
        first_party_wheels=first_party_wheels,
    )

    print(
        "OCI_DESCRIPTION_BASE=" + build_display_description(description_base)
    )
    print(
        "OCI_DESCRIPTION_VSC=" + build_display_description(description_vsc)
    )
    print(f"TOOL_ARTIFACTS_STAGE_ROOT={staging_result.stage_root}")
    print(f"TOOL_ARTIFACTS_METADATA={staging_result.metadata_path}")
    print(f"TOOL_ARTIFACTS_VERSIONS_ENV={staging_result.versions_env_path}")
    print(f"GHCR_PACKAGE_NAMES={','.join(package_names)}")
    print(f"CIU_WHEEL_TAG={ciu_wheel_tag}")
    print(f"CIU_WHEEL_ASSET_NAME={ciu_wheel_asset_name}")
    print(f"CIU_WHEEL_VERSION={ciu_wheel_version}")
    # Direct download URL and sha256 resolved from ciu-latest/latest.json or ciu-v* scan.
    # The Dockerfile uses CIU_WHEEL_URL to download directly from the immutable ciu-v<semver>
    # release and CIU_WHEEL_SHA256 to verify the download.  Both were absent before v2.0.0.
    print(f"CIU_WHEEL_URL={ciu_wheel_url}")
    print(f"CIU_WHEEL_SHA256={ciu_wheel_sha256}")
    print(f"NVIM_VER={tool_metadata.get('resolved_versions', {}).get('NVIM_VER', '') if isinstance(tool_metadata, dict) else ''}")
    print(f"NVCHAD_VER={tool_metadata.get('resolved_versions', {}).get('NVCHAD_VER', '') if isinstance(tool_metadata, dict) else ''}")

    # Export first-party wheel results (from pip/wheels.list) for bake/downstream use.
    # One var per resolved wheel: FIRST_PARTY_WHEEL_<NAME>_VERSION and _SHA256.
    # Empty when a wheel was skipped (release not yet published).
    for _w in first_party_wheels:
        _wname = _w["name"].upper().replace("-", "_")
        print(f"FIRST_PARTY_WHEEL_{_wname}_VERSION={_w['version']}")
        print(f"FIRST_PARTY_WHEEL_{_wname}_SHA256={_w['sha256']}")
        print(f"FIRST_PARTY_WHEEL_{_wname}_FILENAME={_w['whl_filename']}")

    # Export latest stable tuple for downstream bake targets (dynamic, live-derived).
    if latest_tag:
        print(f"DEVCONTAINERS_DYNAMIC_LATEST_TAG={latest_tag}")
    else:
        print("DEVCONTAINERS_DYNAMIC_LATEST_TAG=")

    if latest_python:
        print(f"DEVCONTAINERS_DYNAMIC_LATEST_PYTHON={latest_python}")
    else:
        print("DEVCONTAINERS_DYNAMIC_LATEST_PYTHON=")

    if latest_debian:
        print(f"DEVCONTAINERS_DYNAMIC_LATEST_DEBIAN={latest_debian}")
    else:
        print("DEVCONTAINERS_DYNAMIC_LATEST_DEBIAN=")

    if latest_tag:
        print(f"DEVCONTAINERS_BASE_DYNAMIC_LATEST=mcr.microsoft.com/devcontainers/python:{latest_tag}")
    else:
        print("DEVCONTAINERS_BASE_DYNAMIC_LATEST=")

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
