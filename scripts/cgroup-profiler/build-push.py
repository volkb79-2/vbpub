#!/usr/bin/env python3
"""Build and push the cgprofile daemon image (RG-55 C6, pwmcp shape).

Usage:
  python3 build-push.py --build   # docker buildx bake --load -> cgprofile:local
                                   #   (the local-only development target)
  python3 build-push.py --push    # docker buildx bake --push -> the versioned
                                   #   ghcr.io/volkb79-2/cgprofile:<ver> target

Simpler than pwmcp/build-push.py on purpose: cgprofile has exactly one
externally-resolved coordinate (its own release version, `scm` strategy —
see cmru.toml), not several Playwright-ecosystem pins threaded through a
CMRU prepare phase and a governed buildx builder. CMRU passes its protected
release tag and commit to both artifact steps. Those values, the checked-out
HEAD, and the local tag must identify the same clean commit; the image is then
built from a temporary worktree at that commit. An untagged local build uses
`0.0.0-dev`. A push without CMRU's release identity refuses, even if an
ambient `CGPROFILE_VERSION` is present.

No network fetch happens inside the image build itself beyond what `pip
install -r requirements.txt` and the base image pull need — see the
Dockerfile's own header for the collector/report tier split this mirrors.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterator

from lib.version import resolve_build_identity, resolve_cmru_release_identity

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


def run(
    argv: list[str], env: dict[str, str] | None = None, *, cwd: Path = HERE,
) -> None:
    log("$ " + " ".join(argv))
    subprocess.run(argv, check=True, cwd=str(cwd), env=env)


def git_revision(project_root: Path = HERE) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD^{commit}"],
        cwd=str(project_root), capture_output=True, text=True, check=False,
    )
    if result.returncode or not result.stdout.strip():
        detail = result.stderr.strip() or f"exit {result.returncode}"
        raise RuntimeError(f"cannot resolve build source commit: {detail}")
    return result.stdout.strip()


@contextmanager
def exact_build_source(commit: str | None) -> Iterator[Path]:
    """Yield the project root, using an isolated full-repository tree for releases."""
    if commit is None:
        yield HERE
        return

    repo_result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=str(HERE),
        capture_output=True, text=True, check=False,
    )
    if repo_result.returncode or not repo_result.stdout.strip():
        detail = repo_result.stderr.strip() or f"exit {repo_result.returncode}"
        raise RuntimeError(f"cannot locate repository for exact build context: {detail}")
    repo_root = Path(repo_result.stdout.strip())
    project_relative = HERE.relative_to(repo_root)

    with tempfile.TemporaryDirectory(prefix="cgprofile-release-source-") as parent:
        worktree = Path(parent) / "source"
        added = subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), commit],
            cwd=str(repo_root), capture_output=True, text=True, check=False,
        )
        if added.returncode:
            detail = added.stderr.strip() or f"exit {added.returncode}"
            raise RuntimeError(f"cannot create exact release build context: {detail}")
        try:
            source = worktree / project_relative
            actual = git_revision(source)
            if actual != commit:
                raise RuntimeError(
                    f"exact build worktree HEAD is {actual}, expected CMRU commit {commit}"
                )
            status = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=str(source), capture_output=True, text=True, check=False,
            )
            if status.returncode or status.stdout:
                detail = status.stderr.strip() or status.stdout.strip() or f"exit {status.returncode}"
                raise RuntimeError(f"exact release build context is not clean: {detail}")
            yield source
        finally:
            removed = subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree)],
                cwd=str(repo_root), capture_output=True, text=True, check=False,
            )
            if removed.returncode:
                detail = removed.stderr.strip() or f"exit {removed.returncode}"
                raise RuntimeError(f"cannot remove exact release build context: {detail}")


def cmru_release_identity(*, required: bool) -> tuple[str, str, str] | None:
    names = ("CMRU_RELEASE_COMMIT", "CMRU_RELEASE_TAG")
    if not any(name in os.environ for name in names) and not required:
        return None
    try:
        return resolve_cmru_release_identity(HERE)
    except (OSError, RuntimeError, ValueError) as exc:
        fail(f"invalid CMRU release identity: {exc}")


def do_build() -> None:
    env = dict(os.environ)
    identity = cmru_release_identity(required=False)
    try:
        if identity is None:
            version, revision = resolve_build_identity(
                HERE, require_release_tag=False,
            )
        else:
            version, revision, _tag = identity
    except (OSError, RuntimeError, ValueError) as exc:
        fail(f"cannot determine build identity: {exc}")
    env.pop("CGPROFILE_VERSION", None)
    env.pop("GIT_REVISION", None)
    env.pop("CMRU_RELEASE_COMMIT", None)
    env.pop("CMRU_RELEASE_TAG", None)
    log(f"Building cgprofile (CGPROFILE_VERSION={version})")
    try:
        with exact_build_source(revision if identity is not None else None) as source:
            run([
                "docker", "buildx", "bake", *_FS_ALLOW, "all",
                "--set", f"cgprofile-local.args.CGPROFILE_VERSION={version}",
                "--set", f"cgprofile-local.args.GIT_REVISION={revision}",
                "--load",
            ], env=env, cwd=source)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        fail(f"build failed: {exc}")
    log("Build complete. Local tag: cgprofile:local")


def do_push() -> None:
    env = dict(os.environ)
    identity = cmru_release_identity(required=True)
    if identity is None:
        fail("--push requires the protected CMRU release identity")
    version, revision, _tag = identity
    username = os.environ.get("GITHUB_USERNAME", "").strip()
    pat = os.environ.get("GITHUB_PUSH_PAT", "").strip()
    if not username or not pat:
        fail("--push requires GITHUB_USERNAME and GITHUB_PUSH_PAT from the CMRU release configuration")
    env.pop("CGPROFILE_VERSION", None)
    env.pop("GIT_REVISION", None)
    env.pop("CMRU_RELEASE_COMMIT", None)
    env.pop("CMRU_RELEASE_TAG", None)
    env.pop("GITHUB_PUSH_PAT", None)
    log(f"Pushing ghcr.io/volkb79-2/cgprofile:{version}")
    # The local alias is a separate Bake target and must never be included in
    # a registry export: an unqualified `cgprofile:local` name resolves to
    # Docker Hub, not to a private local-only namespace.
    try:
        with exact_build_source(revision) as source:
            log(f"Logging in to ghcr.io as {username}")
            subprocess.run(
                ["docker", "login", "ghcr.io", "-u", username, "--password-stdin"],
                input=pat.encode(), check=True, env=env,
            )
            run([
                "docker", "buildx", "bake", *_FS_ALLOW, "cgprofile-release",
                "--set", f"cgprofile-release.args.CGPROFILE_VERSION={version}",
                "--set", f"cgprofile-release.args.GIT_REVISION={revision}",
                "--set", f"cgprofile-release.tags=ghcr.io/volkb79-2/cgprofile:{version}",
                "--push",
            ], env=env, cwd=source)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        fail(f"push failed: {exc}")
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
