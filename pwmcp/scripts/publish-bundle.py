#!/usr/bin/env python3
"""Publish the pwmcp stack bundle to GitHub Releases.

Routes through cmru.release (cmru/src/cmru/release.py).
so the release scheme stays uniform across all vbpub projects.

Required environment (from CMRU or explicitly exported by the caller):
  GITHUB_PUSH_PAT
  GITHUB_USERNAME
  GITHUB_REPO  (default: vbpub)

Reads PWMCP_VERSION from cmru.vars (written by CMRU's pwmcp prepare phase).

Publish strategy (implemented by publish_versioned in the keystone):
  - Immutable release  pwmcp-v<version>  with the versioned bundle + .sha256 sidecar.
  - Thin pointer       pwmcp-latest       containing only latest.json (no asset dup).
  - SHA256 written to release notes for reproducibility verification.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ── Keystone import ──────────────────────────────────────────────────────────
# parents[2] from pwmcp/scripts/publish-bundle.py == the vbpub repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cmru" / "src"))
from cmru.release import GitHubReleases, publish_versioned  # noqa: E402

# Strict prepared-coordinate loader. Insert the script dir explicitly: the cmru/src
# insert above pushed sys.path[0] off it, so a bare ``import _vars`` would be fragile.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _vars import load_vars  # noqa: E402

PWMCP_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = PWMCP_DIR / "dist"


def log(msg: str) -> None:
    print(f"[INFO] {msg}")


def fail(msg: str, status: int | None = None, body: str | None = None) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    if status is not None:
        print(f"[ERROR] HTTP status: {status}", file=sys.stderr)
    if body:
        print(f"[ERROR] Response body: {body}", file=sys.stderr)
    raise SystemExit(1)


def find_bundle(dist_dir: Path, pwmcp_version: str) -> Path:
    expected = dist_dir / f"pwmcp-{pwmcp_version}.tar.xz"
    if not expected.exists():
        fail(f"Expected prepared bundle {expected}; run CMRU's pwmcp build phase first.")
    return expected


def main() -> None:
    load_vars()

    pwmcp_version = os.environ.get("PWMCP_VERSION", "")
    if not pwmcp_version:
        fail("PWMCP_VERSION not set — run resolve-playwright-version.py first")

    token = os.environ.get("GITHUB_PUSH_PAT", "")
    if not token:
        fail("GITHUB_PUSH_PAT is required")
    owner = os.environ.get("GITHUB_USERNAME", "")
    repo = os.environ.get("GITHUB_REPO", "")
    if not owner or not repo:
        fail("GITHUB_USERNAME and GITHUB_REPO are required")

    bundle_path = find_bundle(DIST_DIR, pwmcp_version)

    gh = GitHubReleases(owner, repo, token)
    result = publish_versioned(
        gh,
        prefix="pwmcp",
        version=pwmcp_version,
        asset_path=bundle_path,
        notes=f"pwmcp {pwmcp_version}",
        latest_pointer=True,
    )

    log(f"PWMCP_BUNDLE_SHA256={result['sha256']}")
    if result.get("asset_url"):
        log(f"PWMCP_BUNDLE_ASSET_URL={result['asset_url']}")
    if result.get("release_tag"):
        log(f"Next step: git tag -a {result['release_tag']} -m 'pwmcp {pwmcp_version}' && git push origin {result['release_tag']}")


if __name__ == "__main__":
    main()
