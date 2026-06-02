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
import urllib.error
import urllib.request
from pathlib import Path

from manifest_sections import netcat_package_for_debian, render_runtime_probe_sections
from stage_tool_artifacts import stage_tool_artifacts


REGISTRY_URL = "https://mcr.microsoft.com/v2/devcontainers/python/tags/list"
STABLE_TAG_RE = re.compile(r"^(?:1-)?(?P<python>\d+\.\d+)-(?P<debian>[a-z0-9][a-z0-9.-]*)$")
PACKAGE_DOCS_ROOT = Path(__file__).resolve().parent.parent / "package-manifests-versioned"
MANIFEST_DIR_IN_IMAGE = "/usr/local/share/modern-debian-tools-python-debug"
TOOL_VERSION_DISPLAY_ORDER = [
    ("aider", "AIDER_VER"),
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
    ("rga", "RGA_VER"),
    ("ripgrep", "RIPGREP_VER"),
    ("shellcheck", "SHELLCHECK_VER"),
    ("vault", "VAULT_VER"),
    ("yq", "YQ_VER"),
]
CIU_VERSION_FROM_WHEEL_RE = re.compile(r"^ciu-(?P<version>.+)-py[0-9].*\.whl$")
SYSTEM_PACKAGE_NAMES = [
    "bash-completion",
    "ca-certificates",
    "curl",
    "bind9-dnsutils",
    "fuse3",
    "git",
    "git-lfs",
    "gnupg",
    "gzip",
    "htop",
    "httpie",
    "iputils-ping",
    "jq",
    "less",
    "lsb-release",
    "lsof",
    "man-db",
    "mc",
    "ncdu",
    "openssl",
    "pandoc",
    "p7zip-full",
    "poppler-utils",
    "python3-venv",
    "psmisc",
    "rsync",
    "sqlite3",
    "strace",
    "sshfs",
    "tar",
    "tree",
    "unzip",
    "vim",
    "wget",
    "xz-utils",
    "postgresql-client",
    "redis-tools",
]


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
        with urllib.request.urlopen(req, timeout=20) as response:
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
        with urllib.request.urlopen(req, timeout=20):
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


def resolve_ciu_wheel_coordinates() -> tuple[str, str, str]:
    owner = (os.getenv("GITHUB_USERNAME") or "").strip()
    repo = (os.getenv("GITHUB_REPO") or "").strip()
    if not owner or not repo:
        print(
            "[WARN] GITHUB_USERNAME/GITHUB_REPO not set; CIU wheel coordinates remain unset",
            file=sys.stderr,
        )
        return "", "", ""

    latest_alias_tag = (os.getenv("CIU_LATEST_TAG") or "ciu-wheel-latest").strip() or "ciu-wheel-latest"
    explicit_asset_name = (os.getenv("CIU_LATEST_ASSET_NAME") or "").strip()

    try:
        latest_release = github_api_json(
            f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{latest_alias_tag}"
        )
    except Exception as exc:  # noqa: BLE001
        print(
            "[WARN] Failed to resolve CIU latest release metadata "
            f"for tag {latest_alias_tag}: {exc}",
            file=sys.stderr,
        )
        return latest_alias_tag, explicit_asset_name, ""

    asset_name = explicit_asset_name or pick_ciu_wheel_asset_name(latest_release)
    if not asset_name:
        print(
            f"[WARN] No wheel asset found for CIU latest tag {latest_alias_tag}",
            file=sys.stderr,
        )
        return latest_alias_tag, "", ""

    wheel_version = derive_ciu_version_from_asset_name(asset_name)
    resolved_tag = latest_alias_tag
    if wheel_version:
        candidate_tag = f"ciu-wheel-{wheel_version}"
        if candidate_tag != latest_alias_tag:
            try:
                if github_release_tag_exists(owner, repo, candidate_tag):
                    resolved_tag = candidate_tag
            except Exception as exc:  # noqa: BLE001
                print(
                    "[WARN] Failed while checking CIU concrete release tag "
                    f"{candidate_tag}: {exc}",
                    file=sys.stderr,
                )

    return resolved_tag, asset_name, wheel_version


def probe_system_package_candidates(debian: str, python: str) -> list[str]:
    image = f"python:{python}-{debian}"
    netcat_pkg = netcat_package_for_debian(debian)
    packages = [*SYSTEM_PACKAGE_NAMES]
    packages.insert(18, netcat_pkg)
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


def build_runtime_custom_tooling_map(tool_metadata: dict | None, ciu_wheel_version: str) -> dict[str, str]:
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
    install_antigravity = install_enabled("INSTALL_ANTIGRAVITY")
    install_claude = install_enabled("INSTALL_CLAUDE_CODE")
    install_codex = install_enabled("INSTALL_CODEX")

    aider_requested = str(
        resolved_versions.get("AIDER_VER") or (os.getenv("AIDER_VERSION") or "latest")
    ).strip() or "latest"
    if install_aider:
        aider_display = (
            "latest (resolved at image build time)"
            if aider_requested == "latest"
            else aider_requested
        )
    else:
        aider_display = "not-installed"

    tooling = {
        "CIU": ciu_wheel_version or "not-installed",
        "aider": aider_display,
        "antigravity": (
            str(resolved_versions.get("ANTIGRAVITY_VER") or "unknown")
            if install_antigravity
            else "not-installed"
        ),
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
        "rga": str(resolved_versions.get("RGA_VER") or "unknown"),
        "ripgrep": str(resolved_versions.get("RIPGREP_VER") or "unknown"),
        "shellcheck": str(resolved_versions.get("SHELLCHECK_VER") or "unknown"),
        "vault": str(resolved_versions.get("VAULT_VER") or "unknown"),
        "yq": str(resolved_versions.get("YQ_VER") or "unknown"),
    }

    psql_version = (os.getenv("POSTGRESQL_CLIENT_VERSION") or "latest").strip() or "latest"
    redis_tools_version = (os.getenv("REDIS_TOOLS_VERSION") or "latest").strip() or "latest"
    tooling["psql"] = psql_version if psql_version != "latest" else "latest (see apt probe below)"
    tooling["redis-cli"] = (
        redis_tools_version
        if redis_tools_version != "latest"
        else "latest (see apt probe below)"
    )
    return tooling


def collect_runtime_probe_sections(
    entries: list[dict[str, str]],
    tool_metadata: dict | None,
    ciu_wheel_version: str,
) -> dict[str, list[str]]:
    custom_tooling = build_runtime_custom_tooling_map(tool_metadata, ciu_wheel_version)
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


def image_reference(username: str, package_name: str, debian: str, python: str, build_date: str) -> str:
    return f"ghcr.io/{username}/{package_name}:{debian}-py{python}-{build_date}"


def manifest_relpath(package_name: str, debian: str, python: str, build_date: str) -> str:
    return f"package-manifests-versioned/{package_name}/{debian}-py{python}-{build_date}.md"


def family_readme_relpath(package_name: str) -> str:
    return f"package-manifests-versioned/{package_name}/README.md"


def family_latest_relpath(package_name: str) -> str:
    return f"package-manifests-versioned/{package_name}/latest.md"


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

    return entries


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
    if not tool_metadata:
        return []

    resolved_versions = tool_metadata.get("resolved_versions")
    artifacts = tool_metadata.get("artifacts")
    lines: list[str] = [
        "## Staged Tool Artifacts",
        "",
    ]

    if isinstance(resolved_versions, dict):
        lines.append("### Resolved Tool Versions")
        lines.append("")
        emitted_keys: set[str] = set()
        for display_name, version_key in TOOL_VERSION_DISPLAY_ORDER:
            version_value = str(resolved_versions.get(version_key) or "").strip()
            if not version_value:
                continue
            emitted_keys.add(version_key)
            lines.append(f"- {display_name}: `{version_value}`")

        for version_key in sorted(resolved_versions):
            if version_key in emitted_keys:
                continue
            version_value = str(resolved_versions.get(version_key) or "").strip()
            if not version_value:
                continue
            lines.append(f"- {version_key}: `{version_value}`")

        lines.append("")

    if isinstance(artifacts, list) and artifacts:
        lines.append("### Artifact Sources and Digests")
        lines.append("")
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

    lines.append(
        "Generated from local pre-staging metadata to make release documentation auditable and reproducible."
    )
    lines.append("")
    return lines


def choose_latest_entry(
    entries: list[dict[str, str]],
    *,
    latest_python: str | None,
    latest_debian: str | None,
) -> dict[str, str]:
    family_kind = entries[0]["family_kind"]
    if family_kind == "vsc" and latest_python and latest_debian:
        for entry in entries:
            if entry["python"] == latest_python and entry["debian"] == latest_debian:
                return entry

    return sorted(
        entries,
        key=lambda item: (to_version_tuple(item["python"]), item["debian"], item["target"]),
    )[-1]


def render_manifest(
    entry: dict[str, str],
    *,
    username: str,
    repo: str,
    build_date: str,
    description: str,
    tool_metadata: dict | None,
    runtime_probe_sections: list[str] | None,
) -> str:
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
    lines.extend(render_tool_artifact_lines(tool_metadata))
    if runtime_probe_sections:
        lines.extend(runtime_probe_sections)
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

    for entry in sorted(entries, key=lambda item: (item["debian"], item["python"], item["target"])):
        relpath = manifest_relpath(package_name, entry["debian"], entry["python"], build_date)
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
) -> None:
    username = os.getenv("GITHUB_USERNAME") or "volkb79-2"
    repo = os.getenv("GITHUB_REPO") or "vbpub"
    description_base = os.getenv("OCI_DESCRIPTION_BASE") or os.getenv("OCI_DESCRIPTION") or ""
    description_vsc = os.getenv("OCI_DESCRIPTION_VSC") or os.getenv("OCI_DESCRIPTION") or ""

    entries = package_catalog(latest_python, latest_debian)
    runtime_probe_sections = collect_runtime_probe_sections(
        entries,
        tool_metadata,
        ciu_wheel_version,
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
            (package_dir / f"{entry['debian']}-py{entry['python']}-{build_date}.md").write_text(
                render_manifest(
                    entry,
                    username=username,
                    repo=repo,
                    build_date=build_date,
                    description=description,
                    tool_metadata=tool_metadata,
                    runtime_probe_sections=runtime_probe_sections.get(entry["target"]),
                ),
                encoding="utf-8",
            )


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

    staging_result = stage_tool_artifacts(build_date=build_date)
    ciu_wheel_tag, ciu_wheel_asset_name, ciu_wheel_version = resolve_ciu_wheel_coordinates()
    ciu_install_required = is_truthy_env(os.getenv("CIU_INSTALL_REQUIRED"))
    if ciu_install_required and (not ciu_wheel_tag or not ciu_wheel_asset_name):
        raise RuntimeError(
            "CIU_INSTALL_REQUIRED=true but CIU wheel coordinates could not be resolved "
            "(CIU_WHEEL_TAG/CIU_WHEEL_ASSET_NAME)"
        )

    stable_release, stable_version = resolve_release_and_version(stable_image)
    dev_release, dev_version = resolve_release_and_version(dev_image)

    description_base = os.getenv("OCI_DESCRIPTION_BASE") or os.getenv("OCI_DESCRIPTION") or ""
    description_vsc = os.getenv("OCI_DESCRIPTION_VSC") or os.getenv("OCI_DESCRIPTION") or ""

    tool_metadata = load_tool_artifact_metadata(staging_result.metadata_path)
    write_package_docs(
        build_date,
        latest_python,
        latest_debian,
        tool_metadata=tool_metadata,
        ciu_wheel_version=ciu_wheel_version,
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
    print(f"CIU_WHEEL_TAG={ciu_wheel_tag}")
    print(f"CIU_WHEEL_ASSET_NAME={ciu_wheel_asset_name}")
    print(f"CIU_WHEEL_VERSION={ciu_wheel_version}")

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
