#!/usr/bin/env python3
"""Build and push the pwmcp-playwright Docker image.

Usage:
  python3 build-push.py --build   # Build images locally (docker buildx bake --load)
  python3 build-push.py --push    # Login to GHCR and push images

Reads PLAYWRIGHT_VERSION_PYPI, PLAYWRIGHT_VERSION_NPM, PWMCP_VERSION_PYPI, and
PWMCP_VERSION_NPM from cmru.vars (written by scripts/resolve-playwright-version.py).
PLAYWRIGHT_VERSION and PWMCP_VERSION are kept as aliases for the PyPI variants for
backwards compatibility.

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
sys.path.insert(0, str(PWMCP_DIR.parent / "cmru" / "src"))
from cmru.ghcr import GitHubPackages  # noqa: E402


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
      - GITHUB_OWNER_TYPE: env, then cmru.toml [github].owner_type
      - GITHUB_PUSH_PAT: env GITHUB_PUSH_PAT, then env GITHUB_TOKEN,
                         then cmru.secret.toml [github].token
    Missing config files are silently skipped.
    """
    if (
        os.environ.get("GITHUB_USERNAME")
        and os.environ.get("GITHUB_PUSH_PAT")
        and os.environ.get("GITHUB_OWNER_TYPE")
    ):
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
            if not os.environ.get("GITHUB_OWNER_TYPE") and github.get("owner_type"):
                os.environ["GITHUB_OWNER_TYPE"] = str(github["owner_type"])
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


def sync_ghcr_package_visibility(package_names: list[str]) -> None:
    """Mirror repo visibility onto GHCR packages that this release just pushed."""
    names = [name.strip() for name in package_names if name and str(name).strip()]
    if not names:
        return

    username = os.environ.get("GITHUB_USERNAME", "").strip()
    repo = os.environ.get("GITHUB_REPO", "").strip()
    token = os.environ.get("GITHUB_PUSH_PAT", "").strip()
    owner_type = os.environ.get("GITHUB_OWNER_TYPE", "").strip()
    if not username or not repo or not token or not owner_type:
        log("Skipping GHCR visibility sync (missing GitHub identity/token)")
        return

    ghcr = GitHubPackages(username, repo, token, owner_type)
    repo_visibility = ghcr.repo_visibility()
    log(f"Mirroring GHCR package visibility to {repo_visibility}: {', '.join(names)}")
    for package_name in names:
        ghcr.mirror_package_visibility(package_name, expected_visibility=repo_visibility)
        log(f"Synced {package_name} visibility to {repo_visibility}")


def run(argv: list[str], cwd: Path | None = None) -> None:
    log(f"$ {' '.join(argv)}")
    subprocess.run(argv, check=True, cwd=str(cwd or PWMCP_DIR))


def do_build() -> None:
    load_vars()
    pw_pypi = os.environ.get("PLAYWRIGHT_VERSION_PYPI") or os.environ.get("PLAYWRIGHT_VERSION", "?")
    pw_npm = os.environ.get("PLAYWRIGHT_VERSION_NPM", "?")
    pwmcp_pypi = os.environ.get("PWMCP_VERSION_PYPI") or os.environ.get("PWMCP_VERSION", "?")
    pwmcp_npm = os.environ.get("PWMCP_VERSION_NPM", "?")
    log(
        f"Building pwmcp matrix  "
        f"PW_PYPI={pw_pypi}  PWMCP_PYPI={pwmcp_pypi}  "
        f"PW_NPM={pw_npm}  PWMCP_NPM={pwmcp_npm}"
    )
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

    pw_pypi = os.environ.get("PLAYWRIGHT_VERSION_PYPI") or os.environ.get("PLAYWRIGHT_VERSION", "?")
    pw_npm = os.environ.get("PLAYWRIGHT_VERSION_NPM", "?")
    pwmcp_pypi = os.environ.get("PWMCP_VERSION_PYPI") or os.environ.get("PWMCP_VERSION", "?")
    pwmcp_npm = os.environ.get("PWMCP_VERSION_NPM", "?")
    log(
        f"Pushing pwmcp matrix  "
        f"PW_PYPI={pw_pypi}  PWMCP_PYPI={pwmcp_pypi}  "
        f"PW_NPM={pw_npm}  PWMCP_NPM={pwmcp_npm}"
    )
    run(["docker", "buildx", "bake", "all", "--push"], cwd=PWMCP_DIR)
    package_names = [
        name.strip()
        for name in (os.environ.get("GHCR_PACKAGE_NAMES") or "pwmcp").split(",")
        if name.strip()
    ]
    sync_ghcr_package_visibility(package_names)
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
