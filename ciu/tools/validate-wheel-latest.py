#!/usr/bin/env python3
"""Validate the latest CIU wheel via the resolver contract.

Resolves "latest ciu wheel" = highest-semver ``ciu-v*`` release (the
monorepo-safe definition), asserts it carries both a ``.whl`` asset and a
matching ``.whl.sha256`` sidecar, and prints the resolved version + download URL.

Required environment:
- GITHUB_USERNAME
- GITHUB_REPO

Optional environment:
- GH_TOKEN or GITHUB_PUSH_PAT (unauthenticated works for public repos, but
  authenticated avoids rate limits)
- CIU_ENV_FILE (default: repo-root/.env)
"""
from __future__ import annotations

import os
import sys
import pathlib
from pathlib import Path

# ── keystone import (stdlib-only; no install needed) ─────────────────────────
# parents[2] from ciu/tools/validate-wheel-latest.py  →  vbpub repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "cmru" / "src"))
from cmru.release import GitHubReleases  # noqa: E402


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
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent.parent

    env_file = Path(os.getenv("CIU_ENV_FILE", str(repo_root / ".env")))
    if env_file.exists():
        load_env_file(env_file)
    else:
        fallback_env = script_dir.parent / ".env"
        if fallback_env.exists():
            load_env_file(fallback_env)

    owner = os.getenv("GITHUB_USERNAME")
    repo = os.getenv("GITHUB_REPO")
    if not owner or not repo:
        print("[ERROR] GITHUB_USERNAME and GITHUB_REPO are required", file=sys.stderr)
        raise SystemExit(1)

    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_PUSH_PAT") or ""

    gh = GitHubReleases(owner, repo, token)

    # Resolve via the contract: highest-semver ciu-v* release (not the thin
    # ciu-latest pointer, which holds only latest.json since the refactor).
    info = gh.resolve_latest("ciu")
    if info is None:
        print("[ERROR] No ciu-v* releases found in the repository", file=sys.stderr)
        raise SystemExit(1)

    version = info["version"]
    assets = {a["name"]: a["url"] for a in info["assets"]}

    # Locate the wheel asset
    wheels = [name for name in assets if name.endswith(".whl")]
    if not wheels:
        print(f"[ERROR] Release ciu-v{version} has no .whl asset", file=sys.stderr)
        raise SystemExit(1)
    if len(wheels) > 1:
        print(f"[WARN] Multiple .whl assets in ciu-v{version}: {wheels}; using first")
    wheel_name = wheels[0]

    # Assert the sha256 sidecar is present
    sidecar_name = wheel_name + ".sha256"
    if sidecar_name not in assets:
        print(
            f"[ERROR] Release ciu-v{version} is missing the .sha256 sidecar "
            f"({sidecar_name}); cannot verify integrity",
            file=sys.stderr,
        )
        raise SystemExit(1)

    download_url = assets[wheel_name]
    sha256_url = assets[sidecar_name]

    print(f"[INFO] CIU latest version: {version} (resolved from highest ciu-v* release)")
    print(f"[INFO] CIU_WHEEL_NAME={wheel_name}")
    print(f"[INFO] CIU_WHEEL_LATEST_URL={download_url}")
    print(f"[INFO] CIU_WHEEL_SHA256_URL={sha256_url}")
    print(f"[INFO] Verify: curl -LO {download_url} && curl -LO {sha256_url} && sha256sum -c {sidecar_name}")


if __name__ == "__main__":
    main()
