#!/usr/bin/env python3
"""Publish CIU wheel to GitHub Releases via the shared release-manager keystone.

Required environment:
- GITHUB_PUSH_PAT
- GITHUB_USERNAME
- GITHUB_REPO

Optional environment:
- CIU_PROJECT_ROOT (default: repo-root/ciu)
- CIU_PACKAGE_NAME (default: project.name from pyproject.toml)
- CIU_WHEEL_GLOB   (default: <dist_name>-*.whl)
- CIU_RELEASE_NOTES (default: "ciu <version>")
- CIU_ENV_FILE     (default: repo-root/.env)
- CIU_DEBUG_API=1  (passed through to GitHubReleases for verbose logging)

Removed knobs (superseded by the keystone):
- CIU_LATEST_TAG         — always "ciu-latest" (keystone handles it)
- CIU_LATEST_ASSET_NAME  — latest release now holds only latest.json (thin pointer)
- CIU_RELEASE_TITLE / CIU_LATEST_TITLE / CIU_LATEST_NOTES — keystone owns release titles
"""
from __future__ import annotations

import os
import sys
import pathlib
from pathlib import Path

# ── keystone import (stdlib-only; no install needed) ─────────────────────────
# parents[2] from ciu/tools/publish-wheel-release.py  →  vbpub repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "release-manager" / "src"))
from release_manager.github_release import GitHubReleases, publish_versioned, is_release_version, version_to_tag  # noqa: E402


def log_debug(message: str) -> None:
    if os.getenv("CIU_DEBUG_API") == "1":
        print(f"[DEBUG] {message}")


def fail(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(1)


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value


def find_built_wheel(dist_dir: Path, wheel_glob: str) -> Path:
    """Return the wheel the build step produced (never rebuilds — a rebuild would
    differ in bytes/version from what was tested)."""
    wheels = sorted(dist_dir.glob(wheel_glob))
    if not wheels:
        fail(f"No wheel in {dist_dir} (glob: {wheel_glob}). Run the build step first.")
    if len(wheels) > 1:
        fail(f"Multiple wheels in {dist_dir}: {[w.name for w in wheels]}; clean + rebuild.")
    return wheels[0]


def read_wheel_version(wheel_path: Path) -> str:
    """Canonical version from the wheel METADATA (single source of truth)."""
    import zipfile

    with zipfile.ZipFile(wheel_path) as zf:
        meta = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        for line in zf.read(meta).decode("utf-8").splitlines():
            if line.startswith("Version:"):
                return line.split(":", 1)[1].strip()
    fail(f"No Version field in {wheel_path.name} METADATA")
    return ""


def main() -> None:
    default_project_root = Path(__file__).resolve().parent.parent
    project_root = Path(os.getenv("CIU_PROJECT_ROOT", str(default_project_root))).resolve()
    repo_root = project_root.parent

    env_file = Path(os.getenv("CIU_ENV_FILE", str(repo_root / ".env")))
    if env_file.exists():
        load_env_file(env_file)
    else:
        fallback_env = project_root / ".env"
        if fallback_env.exists():
            load_env_file(fallback_env)

    import tomllib

    data = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    project_meta = data.get("project", {})

    package_name = os.getenv("CIU_PACKAGE_NAME", project_meta.get("name", "ciu"))
    if not package_name:
        fail("Unable to read project.name from pyproject.toml")
    dist_name = package_name.replace("-", "_")
    wheel_glob = os.getenv("CIU_WHEEL_GLOB", f"{dist_name}-*.whl")

    # Upload the artifact the build step produced — never rebuild. Version is the
    # wheel's own METADATA version (matches exactly what was built/tested).
    dist_dir = project_root / "dist"
    wheel_path = find_built_wheel(dist_dir, wheel_glob)
    version = read_wheel_version(wheel_path)

    token = os.getenv("GITHUB_PUSH_PAT")
    if not token:
        fail("GITHUB_PUSH_PAT is required")
    owner = os.getenv("GITHUB_USERNAME")
    repo = os.getenv("GITHUB_REPO")
    if not owner or not repo:
        fail("GITHUB_USERNAME and GITHUB_REPO are required")

    notes = os.getenv("CIU_RELEASE_NOTES", f"ciu {version}")

    # Route through the shared keystone:
    #   - clean version → immutable ciu-v<version> release + thin ciu-latest pointer
    #   - dev/dirty     → moves ciu-latest only (no per-commit tag spam)
    gh = GitHubReleases(owner, repo, token)
    result = publish_versioned(
        gh,
        prefix="ciu",
        version=version,
        asset_path=wheel_path,
        notes=notes,
        latest_pointer=True,
    )

    print(f"[INFO] Published ciu {version}")
    print(f"[INFO] CIU_WHEEL_SHA256={result['sha256']}")
    if result.get("asset_url"):
        print(f"[INFO] CIU_WHEEL_ASSET_URL={result['asset_url']}")


if __name__ == "__main__":
    main()
