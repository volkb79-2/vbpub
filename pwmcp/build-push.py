#!/usr/bin/env python3
"""Build and push the pwmcp-playwright Docker image.

Usage:
  python3 build-push.py --build   # Build images locally (docker buildx bake --load)
  python3 build-push.py --push    # Login to GHCR and push images

Reads PLAYWRIGHT_VERSION and PWMCP_VERSION from cmru.vars
(written by scripts/resolve-playwright-version.py).

Credentials for push (from environment or cmru.toml / cmru.secret.toml [github]):
  GITHUB_USERNAME
  GITHUB_PUSH_PAT
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PWMCP_DIR = Path(__file__).resolve().parent

# Shared self-healing vars loader (pwmcp/scripts/_vars.py).
sys.path.insert(0, str(PWMCP_DIR / "scripts"))
from _vars import load_vars  # noqa: E402


def log(msg: str) -> None:
    print(f"[INFO] {msg}")


def fail(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr)
    raise SystemExit(1)


def load_cmru_credentials() -> None:
    """Populate GITHUB_USERNAME / GITHUB_PUSH_PAT from cmru.toml / cmru.secret.toml if not already set.

    Resolution order:
      - GITHUB_USERNAME: env, then cmru.toml [github].owner
      - GITHUB_REPO:     env, then cmru.toml [github].repo
      - GITHUB_PUSH_PAT: env GITHUB_PUSH_PAT, then env GITHUB_TOKEN,
                         then cmru.secret.toml [github].token
    Missing config files are silently skipped.
    """
    if os.environ.get("GITHUB_USERNAME") and os.environ.get("GITHUB_PUSH_PAT"):
        return
    repo_root = PWMCP_DIR.parent
    try:
        import tomllib
        # Load identity from cmru.toml (no token here).
        cmru_toml = repo_root / "cmru.toml"
        if cmru_toml.exists():
            with cmru_toml.open("rb") as fh:
                config = tomllib.load(fh)
            github = config.get("github", {})
            if not os.environ.get("GITHUB_USERNAME") and github.get("owner"):
                os.environ["GITHUB_USERNAME"] = str(github["owner"])
            if not os.environ.get("GITHUB_REPO") and github.get("repo"):
                os.environ["GITHUB_REPO"] = str(github["repo"])
        # Resolve token: env vars first, then cmru.secret.toml.
        if not os.environ.get("GITHUB_PUSH_PAT"):
            token = os.environ.get("GITHUB_TOKEN", "")
            if not token:
                secret_toml = repo_root / "cmru.secret.toml"
                if secret_toml.exists():
                    with secret_toml.open("rb") as fh:
                        secret = tomllib.load(fh)
                    token = str(secret.get("github", {}).get("token", ""))
            if token:
                os.environ["GITHUB_PUSH_PAT"] = token
    except Exception:
        pass


def run(argv: list[str], cwd: Path | None = None) -> None:
    log(f"$ {' '.join(argv)}")
    subprocess.run(argv, check=True, cwd=str(cwd or PWMCP_DIR))


def do_build() -> None:
    load_vars()
    pw_ver = os.environ.get("PLAYWRIGHT_VERSION", "?")
    pwmcp_ver = os.environ.get("PWMCP_VERSION", "?")
    log(f"Building pwmcp-playwright  PW={pw_ver}  PWMCP={pwmcp_ver}")
    run(["docker", "buildx", "bake", "all", "--load"], cwd=PWMCP_DIR)
    log("Build complete.")


def do_push() -> None:
    load_vars()
    load_cmru_credentials()

    username = os.environ.get("GITHUB_USERNAME", "")
    pat = os.environ.get("GITHUB_PUSH_PAT", "")
    if not username or not pat:
        fail("GITHUB_USERNAME and GITHUB_PUSH_PAT are required for push")

    log(f"Logging in to ghcr.io as {username}")
    proc = subprocess.run(
        ["docker", "login", "ghcr.io", "-u", username, "--password-stdin"],
        input=pat.encode(), check=True,
    )
    del proc

    pw_ver = os.environ.get("PLAYWRIGHT_VERSION", "?")
    pwmcp_ver = os.environ.get("PWMCP_VERSION", "?")
    log(f"Pushing pwmcp-playwright  PW={pw_ver}  PWMCP={pwmcp_ver}")
    run(["docker", "buildx", "bake", "all", "--push"], cwd=PWMCP_DIR)
    log("Push complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build/push pwmcp images")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--build", action="store_true", help="Build images locally")
    group.add_argument("--push", action="store_true", help="Push images to GHCR")
    args = parser.parse_args()

    if args.build:
        do_build()
    else:
        do_push()


if __name__ == "__main__":
    main()
