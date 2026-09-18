#!/usr/bin/env python3
"""Build and push the cgprofile daemon image (RG-55 C6, pwmcp shape).

Usage:
  python3 build-push.py --build   # docker buildx bake --load -> cgprofile:local
                                   #   + ghcr.io/volkb79-2/cgprofile:<ver>
  python3 build-push.py --push    # docker buildx bake --push, same two tags

Simpler than pwmcp/build-push.py on purpose: cgprofile has exactly one
externally-resolved coordinate (its own release version, `scm` strategy —
see cmru.toml), not several Playwright-ecosystem pins threaded through a
CMRU prepare phase and a governed buildx builder. `--build` needs nothing
set at all (docker-bake.hcl's own CGPROFILE_VERSION default covers a bare
local build); `--push` requires CGPROFILE_VERSION (the version this release
resolved to) plus GITHUB_USERNAME/GITHUB_PUSH_PAT for the ghcr.io login.

No network fetch happens inside the image build itself beyond what `pip
install -r requirements.txt` and the base image pull need — see the
Dockerfile's own header for the collector/report tier split this mirrors.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The Dockerfile reads scripts/damon-analysis/lib/damon_analysis.py via a
# second, named buildx context (docker-bake.hcl's `contexts` block) — any
# read outside the primary "." context is an entitlement buildx refuses by
# default ("additional privileges requested"), verified live. Granting it
# here (rather than silently disabling entitlement checks estate-wide via
# BUILDX_BAKE_ENTITLEMENTS_FS=0) keeps the ask narrow and visible in the
# actual command line, not an env var a future reader would miss.
_FS_ALLOW = ["--allow=fs.read=../damon-analysis"]


def log(msg: str) -> None:
    print(f"[cgprofile build] {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"[cgprofile build] ERROR: {msg}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def run(argv: list[str], env: dict[str, str] | None = None) -> None:
    log("$ " + " ".join(argv))
    subprocess.run(argv, check=True, cwd=str(HERE), env=env)


def git_revision() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(HERE), check=True,
            capture_output=True, text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"
    return result.stdout.strip()


def do_build() -> None:
    env = dict(os.environ)
    env["GIT_REVISION"] = git_revision()
    log(f"Building cgprofile (CGPROFILE_VERSION={env.get('CGPROFILE_VERSION', '<default>')})")
    run(["docker", "buildx", "bake", *_FS_ALLOW, "all", "--load"], env=env)
    log("Build complete. Local tag: cgprofile:local")


def do_push() -> None:
    version = os.environ.get("CGPROFILE_VERSION", "").strip()
    if not version:
        fail(
            "--push requires CGPROFILE_VERSION set to the version this release "
            "resolved to (scm strategy — run through cmru, or export it explicitly)"
        )
    username = os.environ.get("GITHUB_USERNAME", "").strip()
    pat = os.environ.get("GITHUB_PUSH_PAT", "").strip()
    if not username or not pat:
        fail("--push requires GITHUB_USERNAME and GITHUB_PUSH_PAT (run through cmru, or export both)")

    log(f"Logging in to ghcr.io as {username}")
    subprocess.run(
        ["docker", "login", "ghcr.io", "-u", username, "--password-stdin"],
        input=pat.encode(), check=True,
    )

    env = dict(os.environ)
    env["GIT_REVISION"] = git_revision()
    log(f"Pushing ghcr.io/volkb79-2/cgprofile:{version}")
    run(["docker", "buildx", "bake", *_FS_ALLOW, "all", "--push"], env=env)
    log("Push complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build/push the cgprofile daemon image")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--build", action="store_true", help="Build the image locally (docker buildx bake --load)")
    group.add_argument("--push", action="store_true", help="Push the image to GHCR (docker buildx bake --push)")
    args = parser.parse_args()

    if args.build:
        do_build()
    else:
        do_push()


if __name__ == "__main__":
    main()
