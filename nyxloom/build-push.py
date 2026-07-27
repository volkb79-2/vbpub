#!/usr/bin/env python3
"""Build and push the nyxloom OCI images (nyxloomd daemon + agent-cli bundle).

Usage:
  python3 build-push.py --build   # docker buildx bake all --load  (local images)
  python3 build-push.py --push    # login to GHCR + bake all --push (release)

The estate's "one recipe, two drivers" split: `ciu` drives --build to run the
stack locally; `cmru`'s oci-image profile drives --build then --push for release.
Both bake the SAME docker-bake.hcl.

Version: resolved from `git describe --tags --match 'nyxloom-v*'` (the tag cmru
mints and setuptools-scm reads), prefix stripped, injected as NYXLOOM_VERSION into
docker-bake.hcl — which both tags the images and passes it as the daemon wheel's
setuptools-scm pretend-version. Falls back to 0.0.0-dev when no tag exists yet.

Credentials for --push (env first, then cmru.toml / cmru.secret.toml [github]):
  GITHUB_USERNAME, GITHUB_PUSH_PAT
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

NYXLOOM_DIR = Path(__file__).resolve().parent
REPO_ROOT = NYXLOOM_DIR.parent
BAKE_FILE = NYXLOOM_DIR / "docker-bake.hcl"
# GHCR package names this release publishes (for post-push visibility mirroring).
GHCR_PACKAGE_NAMES = ["nyxloomd", "nyxloom-agent-cli"]


def log(msg: str) -> None:
    print(f"[INFO] {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def resolve_version() -> str:
    """nyxloom image version, matching the wheel's setuptools-scm version.

    Honours an explicit NYXLOOM_VERSION env override; otherwise derives it from
    the `nyxloom-v*` git tag (`git describe`), stripping the prefix. Sanitised to
    a docker-tag-safe string. No tag yet → 0.0.0-dev (same sentinel the bake and
    the local ciu build default to)."""
    override = os.environ.get("NYXLOOM_VERSION")
    if override:
        return override
    try:
        desc = subprocess.check_output(
            ["git", "describe", "--tags", "--match", "nyxloom-v*", "--dirty"],
            cwd=str(REPO_ROOT),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        return "0.0.0-dev"
    ver = desc[len("nyxloom-v"):] if desc.startswith("nyxloom-v") else desc
    # docker tags allow [A-Za-z0-9_.-]; git describe already uses '-', but guard.
    return re.sub(r"[^A-Za-z0-9_.-]", "-", ver) or "0.0.0-dev"


def load_cmru_credentials() -> None:
    """Populate GITHUB_USERNAME / GITHUB_PUSH_PAT / GITHUB_REPO / GITHUB_OWNER_TYPE
    from cmru.toml / cmru.secret.toml when not already in the environment (mirrors
    pwmcp/build-push.py). Missing files are silently skipped."""
    if (
        os.environ.get("GITHUB_USERNAME")
        and os.environ.get("GITHUB_PUSH_PAT")
        and os.environ.get("GITHUB_OWNER_TYPE")
    ):
        return
    try:
        cmru_toml = REPO_ROOT / "cmru.toml"
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
        if not os.environ.get("GITHUB_PUSH_PAT"):
            token = os.environ.get("GITHUB_TOKEN", "")
            if not token:
                secret_toml = REPO_ROOT / "cmru.secret.toml"
                if secret_toml.exists():
                    with secret_toml.open("rb") as fh:
                        secret = tomllib.load(fh)
                    token = str(secret.get("github", {}).get("token", ""))
            if token:
                os.environ["GITHUB_PUSH_PAT"] = token
    except Exception:
        pass


def sync_ghcr_package_visibility(package_names: list[str]) -> None:
    """Mirror the repo's visibility onto the GHCR packages this release pushed.
    Uses cmru's GitHubPackages helper; a no-op (with a note) when identity/token
    are missing."""
    username = os.environ.get("GITHUB_USERNAME", "").strip()
    repo = os.environ.get("GITHUB_REPO", "").strip()
    token = os.environ.get("GITHUB_PUSH_PAT", "").strip()
    owner_type = os.environ.get("GITHUB_OWNER_TYPE", "").strip()
    if not username or not repo or not token or not owner_type:
        log("Skipping GHCR visibility sync (missing GitHub identity/token)")
        return
    sys.path.insert(0, str(REPO_ROOT / "cmru" / "src"))
    from cmru.ghcr import GitHubPackages  # noqa: E402

    ghcr = GitHubPackages(username, repo, token, owner_type)
    repo_visibility = ghcr.repo_visibility()
    log(f"Mirroring GHCR package visibility to {repo_visibility}: {', '.join(package_names)}")
    for package_name in package_names:
        ghcr.mirror_package_visibility(package_name, expected_visibility=repo_visibility)
        log(f"Synced {package_name} visibility to {repo_visibility}")


def _bake(extra: list[str], version: str) -> None:
    env = {**os.environ, "NYXLOOM_VERSION": version}
    argv = ["docker", "buildx", "bake", "-f", str(BAKE_FILE), "all", *extra]
    log(f"$ NYXLOOM_VERSION={version} {' '.join(argv)}")
    subprocess.run(argv, check=True, cwd=str(NYXLOOM_DIR), env=env)


def do_build() -> None:
    version = resolve_version()
    log(f"Building nyxloom images  NYXLOOM_VERSION={version}")
    _bake(["--load"], version)
    log("Build complete.")


def do_push() -> None:
    version = resolve_version()
    load_cmru_credentials()
    username = os.environ.get("GITHUB_USERNAME", "")
    pat = os.environ.get("GITHUB_PUSH_PAT", "")
    if not username or not pat:
        fail("GITHUB_USERNAME and GITHUB_PUSH_PAT are required for push")
    log(f"Logging in to ghcr.io as {username}")
    subprocess.run(
        ["docker", "login", "ghcr.io", "-u", username, "--password-stdin"],
        input=pat.encode(),
        check=True,
    )
    log(f"Pushing nyxloom images  NYXLOOM_VERSION={version}")
    _bake(["--push"], version)
    sync_ghcr_package_visibility(GHCR_PACKAGE_NAMES)
    log("Push complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build/push nyxloom OCI images")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--build", action="store_true", help="Build images locally (bake --load)")
    group.add_argument("--push", action="store_true", help="Push images to GHCR (bake --push)")
    args = parser.parse_args()
    if args.build:
        do_build()
    else:
        do_push()


if __name__ == "__main__":
    main()
