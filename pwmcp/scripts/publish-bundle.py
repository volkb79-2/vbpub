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
from cmru.release import GitHubReleases, publish_versioned, sha256_file, write_sha256_sidecar

# Strict prepared-coordinate loader and shared script registration. Insert the script
# dir explicitly so a bare ``import _vars`` also works when this file is loaded by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _vars import load_vars, run_script, script_cli  # noqa: E402

PWMCP_DIR = Path(__file__).resolve().parent.parent
DIST_DIR = PWMCP_DIR / "dist"
GET_PY = PWMCP_DIR / "get.py"
CLIENT_INSTALLER = "pwmcp-client-install.py"
REQUIREMENTS_ASSET = "pwmcp-client-requirements.txt"


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
    expected = dist_dir / f"pwmcp-v{pwmcp_version}.tar.xz"  # the documented contract: <tag><suffix> (PWMCP-07)
    if not expected.exists():
        fail(f"Expected prepared bundle {expected}; run CMRU's pwmcp build phase first.")
    return expected


def stage_client_assets(dist_dir: Path, installer: Path, base_url: str = "") -> list[Path]:
    """Client-only release assets: the pwmcp-client wheel, its pinned dependency wheels, a hash-pinned
    requirements file and the client-only installer (the wheel and installer with SHA256 sidecars).

    Every wheel is the exact file the bundle ships (``dist/bundle/wheels``), so the standalone assets and the
    bundle's copies are byte-identical. A consumer that only talks to a server elsewhere runs
    ``pip install --require-hashes -r pwmcp-client-requirements.txt`` (each line is ``name @ <asset URL>
    --hash=sha256:...``; ``cli-extended`` is GitHub-only, so its wheel is an asset too) or
    ``pwmcp-client-install.py`` (README "Client install and upgrade").
    """
    wheel_dir = dist_dir / "bundle" / "wheels"
    wheels = sorted(wheel_dir.glob("*.whl"))
    clients = [w for w in wheels if w.name.startswith("pwmcp_client-")]
    if len(clients) != 1:
        fail(f"expected exactly one pwmcp_client wheel in {wheel_dir}, found {len(clients)}; run the build phase")
    if not installer.is_file():
        fail(f"{installer} is missing")
    staged: list[Path] = []
    requirements = [f"# pwmcp client: hash-pinned. Use: pip install --require-hashes -r {REQUIREMENTS_ASSET}"]
    for wheel in wheels:
        target = dist_dir / wheel.name
        if target.resolve() != wheel.resolve():
            shutil.copy2(wheel, target)
        staged.append(target)
        distribution = wheel.name.split("-")[0].replace("_", "-")
        requirements.append(f"{distribution} @ {base_url}/{wheel.name} --hash=sha256:{sha256_file(wheel)}")
    requirements_file = dist_dir / REQUIREMENTS_ASSET
    requirements_file.write_text("\n".join(requirements) + "\n", encoding="utf-8")
    staged.append(requirements_file)
    target = dist_dir / installer.name
    if target.resolve() != installer.resolve():
        shutil.copy2(installer, target)
    staged.append(target)
    for source in (clients[0], installer):
        staged.append(write_sha256_sidecar(dist_dir / source.name))
    return staged


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

    client_assets = stage_client_assets(
        DIST_DIR, PWMCP_DIR / CLIENT_INSTALLER,
        f"https://github.com/{owner}/{repo}/releases/download/pwmcp-v{pwmcp_version}",
    )

    gh = GitHubReleases(owner, repo, token)
    result = publish_versioned(
        gh,
        prefix="pwmcp",
        version=pwmcp_version,
        asset_path=bundle_path,
        notes=f"pwmcp {pwmcp_version}",
        extra_assets=[get_py_asset, get_py_sidecar, *client_assets],
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
        preview=lambda _args: "would publish the PWMCP bundle, get.py, the pwmcp-client wheel and pwmcp-client-install.py (+ SHA256 sidecars) as the immutable release and refresh pwmcp-latest",
    )


def main(argv: list[str] | None = None) -> None:
    run_script(build_cli(), argv)


if __name__ == "__main__":
    main()
