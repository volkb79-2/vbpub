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

import argparse
import os
import shutil
import sys
from pathlib import Path

from cli_extended import RegisteredCli
# Keystone import: the INSTALLED cmru package (CX-D3 / PWMCP-02), not cmru/src.
from cmru.release import GitHubReleases, publish_versioned, write_sha256_sidecar

# Strict prepared-coordinate loader and shared script registration. Insert the script
# dir explicitly so a bare ``import _vars`` also works when this file is loaded by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _vars import load_vars, run_script, script_cli  # noqa: E402

PWMCP_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = PWMCP_DIR / "dist"
GET_PY = PWMCP_DIR / "get.py"


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


def _publish(_args: argparse.Namespace) -> None:
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
    if not GET_PY.is_file():
        fail("pwmcp/get.py is missing; render it with `cmru get-py pwmcp` and commit it first")
    get_py_asset = DIST_DIR / "get.py"
    shutil.copy2(GET_PY, get_py_asset)
    get_py_sidecar = write_sha256_sidecar(get_py_asset)

    gh = GitHubReleases(owner, repo, token)
    result = publish_versioned(
        gh,
        prefix="pwmcp",
        version=pwmcp_version,
        asset_path=bundle_path,
        notes=f"pwmcp {pwmcp_version}",
        extra_assets=[get_py_asset, get_py_sidecar],
        latest_pointer=True,
    )

    log(f"PWMCP_BUNDLE_SHA256={result['sha256']}")
    if result.get("asset_url"):
        log(f"PWMCP_BUNDLE_ASSET_URL={result['asset_url']}")
    if result.get("release_tag"):
        log(f"Next step: git tag -a {result['release_tag']} -m 'pwmcp {pwmcp_version}' && git push origin {result['release_tag']}")


def build_cli() -> RegisteredCli:
    """Import-safe registration: this script takes no operands."""
    return script_cli(
        "publish-bundle.py",
        long_name="stack bundle publish",
        description="Publish the prepared pwmcp stack bundle to GitHub Releases (immutable release plus latest pointer).",
        handler=_publish,
        preview=lambda _args: "would publish the PWMCP bundle and get.py (+ SHA256 sidecars) as the immutable release and refresh pwmcp-latest",
    )


def main(argv: list[str] | None = None) -> None:
    run_script(build_cli(), argv)


if __name__ == "__main__":
    main()
