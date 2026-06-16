#!/usr/bin/env python3
"""Publish the pwmcp stack bundle to GitHub Releases.

Routes through ciu_forge.release (ciu-forge/src/ciu_forge/release.py).
so the release scheme stays uniform across all vbpub projects.

Required environment (from release.toml or shell):
  GITHUB_PUSH_PAT
  GITHUB_USERNAME
  GITHUB_REPO  (default: vbpub)

Reads PWMCP_VERSION from .release-vars (written by resolve-playwright-version.py).

Publish strategy (delegated to publish_versioned in the keystone):
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
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ciu-forge" / "src"))
from ciu_forge.release import GitHubReleases, publish_versioned

PWMCP_DIR = Path(__file__).resolve().parent.parent
RELEASE_VARS_FILE = PWMCP_DIR / ".release-vars"
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


def load_release_vars(path: Path) -> None:
    if not path.exists():
        fail(f"{path} not found — run resolve-playwright-version.py first")
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value


def load_release_toml_credentials(repo_root: Path) -> None:
    """Populate GITHUB_USERNAME / GITHUB_PUSH_PAT / GITHUB_REPO from release.toml."""
    release_toml = repo_root / "release.toml"
    if not release_toml.exists():
        return
    try:
        import tomllib
        with release_toml.open("rb") as fh:
            config = tomllib.load(fh)
        github = config.get("github", {})
        if not os.environ.get("GITHUB_USERNAME") and github.get("username"):
            os.environ["GITHUB_USERNAME"] = str(github["username"])
        if not os.environ.get("GITHUB_PUSH_PAT") and github.get("token"):
            os.environ["GITHUB_PUSH_PAT"] = str(github["token"])
        if not os.environ.get("GITHUB_REPO") and github.get("repo"):
            os.environ["GITHUB_REPO"] = str(github["repo"])
    except Exception:
        pass


def find_bundle(dist_dir: Path, pwmcp_version: str) -> Path:
    expected = dist_dir / f"pwmcp-{pwmcp_version}.tar.xz"
    if not expected.exists():
        candidates = sorted(dist_dir.glob("pwmcp-*.tar.xz"))
        if not candidates:
            fail(f"No bundle in {dist_dir}. Run build-bundle.py first.")
        if len(candidates) > 1:
            fail(f"Multiple bundles in {dist_dir}: {[c.name for c in candidates]}; clean + rebuild.")
        return candidates[0]
    return expected


def main() -> None:
    load_release_vars(RELEASE_VARS_FILE)

    repo_root = PWMCP_DIR.parent
    for env_path in [repo_root / ".env", PWMCP_DIR / ".env"]:
        load_env_file(env_path)
    load_release_toml_credentials(repo_root)

    pwmcp_version = os.environ.get("PWMCP_VERSION", "")
    if not pwmcp_version:
        fail("PWMCP_VERSION not set — run resolve-playwright-version.py first")

    token = os.environ.get("GITHUB_PUSH_PAT", "")
    if not token:
        fail("GITHUB_PUSH_PAT is required")
    owner = os.environ.get("GITHUB_USERNAME", "")
    repo = os.environ.get("GITHUB_REPO", "vbpub")
    if not owner:
        fail("GITHUB_USERNAME is required")

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
