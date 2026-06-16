#!/usr/bin/env python3
"""Publish ciu-forge wheel to GitHub Releases.

Required environment (set by release.toml / step runner):
- GITHUB_PUSH_PAT
- GITHUB_USERNAME
- GITHUB_REPO
"""
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = ROOT.parent
CIU_FORGE_SRC = ROOT / "src"
sys.path.insert(0, str(CIU_FORGE_SRC))

from ciu_forge.release import GitHubReleases, publish_versioned  # noqa: E402


def fail(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(1)


def find_wheel(dist_dir: Path) -> Path:
    wheels = sorted(dist_dir.glob("ciu_forge-*.whl"))
    if not wheels:
        fail(f"No wheel found in {dist_dir}. Run build step first.")
    if len(wheels) > 1:
        fail(f"Multiple wheels found in {dist_dir}: {[w.name for w in wheels]}; clean dist/ and rebuild.")
    return wheels[0]


def read_wheel_version(wheel_path: Path) -> str:
    with zipfile.ZipFile(wheel_path) as zf:
        meta = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        for line in zf.read(meta).decode("utf-8").splitlines():
            if line.startswith("Version:"):
                return line.split(":", 1)[1].strip()
    fail(f"No Version field in {wheel_path.name} METADATA")
    return ""


def main() -> None:
    token = os.getenv("GITHUB_PUSH_PAT")
    if not token:
        fail("GITHUB_PUSH_PAT is required")
    owner = os.getenv("GITHUB_USERNAME")
    repo = os.getenv("GITHUB_REPO")
    if not owner or not repo:
        fail("GITHUB_USERNAME and GITHUB_REPO are required")

    dist_dir = ROOT / "dist"
    wheel = find_wheel(dist_dir)
    version = read_wheel_version(wheel)
    notes = os.getenv("CIU_FORGE_RELEASE_NOTES", f"ciu-forge {version}")

    gh = GitHubReleases(owner=owner, repo=repo, token=token)
    result = publish_versioned(
        gh,
        prefix="ciu-forge",
        version=version,
        asset_path=wheel,
        notes=notes,
        latest_pointer=True,
    )

    print(f"[INFO] Published ciu-forge {version}")
    print(f"[INFO] CIU_FORGE_WHEEL_SHA256={result['sha256']}")
    if result.get("asset_url"):
        print(f"[INFO] CIU_FORGE_WHEEL_ASSET_URL={result['asset_url']}")


if __name__ == "__main__":
    main()
