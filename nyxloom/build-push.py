#!/usr/bin/env python3
"""Build and push the nyxloom OCI images (nyxloomd daemon + agent-cli bundle).

Usage:
  python3 build-push.py --build   # docker buildx bake all --load  (local images)
  python3 build-push.py --push    # login to GHCR + bake all --push (release)

The estate's "one recipe, two drivers" split: `ciu` drives --build to run the
stack locally; `cmru`'s oci-image profile drives --build then --push for release.
Both bake the SAME docker-bake.hcl.

Version: resolved from `git describe --tags --match 'nyxloom-v*'` (the tag CMRU
mints and setuptools-scm reads), prefix stripped, injected as NYXLOOM_VERSION into
docker-bake.hcl — which both tags the images and passes it as the daemon wheel's
setuptools-scm pretend-version. A checkout without a matching tag must supply
NYXLOOM_VERSION explicitly; CMRU release always supplies a tagged source revision.

Credentials for --push: explicit environment only
  GITHUB_USERNAME, GITHUB_REPO, GITHUB_OWNER_TYPE, GITHUB_PUSH_PAT (or GITHUB_TOKEN)
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
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
    a docker-tag-safe string. A checkout without a matching tag has no derivable
    image version, so it fails rather than inventing a sentinel."""
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
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Cannot derive NYXLOOM_VERSION: no nyxloom-v* tag is reachable. "
            "Set NYXLOOM_VERSION explicitly for this non-release build."
        ) from exc
    ver = desc[len("nyxloom-v"):] if desc.startswith("nyxloom-v") else desc
    # docker tags allow [A-Za-z0-9_.-]; git describe already uses '-', but guard.
    sanitized = re.sub(r"[^A-Za-z0-9_.-]", "-", ver)
    if not sanitized:
        raise RuntimeError("Cannot derive NYXLOOM_VERSION from the matching Git tag")
    return sanitized


def require_push_environment() -> None:
    """Require the explicit release context CMRU supplies to project commands."""
    token = (os.environ.get("GITHUB_PUSH_PAT") or os.environ.get("GITHUB_TOKEN") or "").strip()
    if token:
        os.environ["GITHUB_PUSH_PAT"] = token
    required = ("GITHUB_USERNAME", "GITHUB_REPO", "GITHUB_OWNER_TYPE", "GITHUB_PUSH_PAT")
    missing = [name for name in required if not os.environ.get(name, "").strip()]
    if missing:
        fail(
            "nyxloom OCI push requires explicit environment: " + ", ".join(missing) + ". "
            "Run through CMRU or set these values before invoking build-push.py --push."
        )


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
    require_push_environment()
    username = os.environ["GITHUB_USERNAME"]
    pat = os.environ["GITHUB_PUSH_PAT"]
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
